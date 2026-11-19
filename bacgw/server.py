"""The gateway's BACnet/IP server at object and service level.

What is modelled (sources in docs/SOURCES.md, assumptions in DOSSIER.md section 7.1):

* Export rules (G1, B1): only group objects with the export flag are visible; binary
  datapoint types become Binary Value objects, numeric ones Analog Value objects, other
  types are not exported. Instance number = group address read as a 16-bit number (B1;
  the controller's own guide does not state it, assumption A15).
* Export limit of the small controller variant: 150 objects (G1). Which objects are
  dropped when there are more is not documented; the model keeps the lowest instance
  numbers (assumption A2).
* Commandable Present_Value with a 16-slot priority array (B4, B2). The controller writes
  each KNX value into one configurable slot, its "object priority" (G2). Relinquish_Default
  is set once, to the object's value at its first read by a client, and never changed
  afterwards (G2). Which slot is the default is not documented (assumption A4). A change of
  Present_Value caused by a BACnet write is sent to the group address, and the sent value
  becomes the controller's own KNX value, so it also lands in the controller's slot
  (assumption A16).
* COV (B2, B3, B5): per-object COV increment, default 1 (G3); an analog object reports when
  |PV - PV at last report| >= increment, a binary object on every change; a new or renewed
  subscription is answered with an immediate notification; a subscription with a lifetime
  expires, lifetime 0 never expires.
* Saving the COV settings cancels all subscriptions and resets the priority arrays of all
  objects (G3). The model empties all 16 slots, the controller's own slot included, so
  Present_Value falls back to the frozen Relinquish_Default until the next KNX telegram
  (my reading of G3, assumption A5).

Messages are counted, not encoded. No conformance to the BACnet standard is claimed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import knx

AV, BV, DEVICE = "analog-value", "binary-value", "device"
N_PRIORITIES = 16
DEFAULT_COV_INCREMENT = 1.0          # G3
SMALL_EXPORT_LIMIT = 150             # G1
DEFAULT_KNX_PRIORITY = 16            # slot the controller writes KNX values to; default not documented (A4)
MAX_SUBSCRIPTIONS = 256              # B1: default number of COV subscriptions
DEVICE_INSTANCE_MAX = 4194302        # B6: 4194303 is the wildcard


@dataclass(frozen=True)
class Error:
    error_class: str
    error_code: str

    def __str__(self) -> str:
        return f"{self.error_class}/{self.error_code}"


UNKNOWN_OBJECT = Error("OBJECT", "UNKNOWN_OBJECT")
UNKNOWN_PROPERTY = Error("PROPERTY", "UNKNOWN_PROPERTY")
WRITE_ACCESS_DENIED = Error("PROPERTY", "WRITE_ACCESS_DENIED")
NO_SPACE = Error("RESOURCES", "NO_SPACE_TO_ADD_LIST_ELEMENT")


@dataclass
class KnxObject:
    """One group object in the gateway's object list (its export configuration)."""
    name: str
    ga: str
    dpt: str
    export: bool = True
    cov_increment: float = DEFAULT_COV_INCREMENT

    @property
    def kind(self) -> str:
        return knx.DPTS[self.dpt].kind


def bacnet_type(dpt: str) -> str | None:
    kind = knx.DPTS[dpt].kind
    return {"binary": BV, "numeric": AV}.get(kind)


@dataclass
class BObject:
    otype: str
    instance: int
    name: str
    ga: str
    relinquish_default: float | None = None   # None until the first read by a client (G2)
    priority_array: list = field(default_factory=lambda: [None] * N_PRIORITIES)
    cov_increment: float = DEFAULT_COV_INCREMENT
    last_reported: float | None = None

    @property
    def key(self) -> tuple[str, int]:
        return (self.otype, self.instance)

    @property
    def present_value(self) -> float:
        for v in self.priority_array:        # slot 1 (index 0) is the highest priority
            if v is not None:
                return v
        return 0.0 if self.relinquish_default is None else self.relinquish_default

    def cov_due(self) -> bool:
        pv = self.present_value
        if self.last_reported is None:
            return True
        if self.otype == BV:
            return pv != self.last_reported
        return abs(pv - self.last_reported) >= self.cov_increment - 1e-9


@dataclass
class Subscription:
    subscriber: str
    process_id: int
    key: tuple[str, int]
    confirmed: bool
    lifetime: float           # s, 0 = indefinite
    start: float

    def expired(self, t: float) -> bool:
        return self.lifetime > 0 and t >= self.start + self.lifetime


@dataclass
class Notification:
    t: float
    source: str               # network address of the sending server
    device: int
    key: tuple[str, int]
    value: float
    subscriber: str
    confirmed: bool


class BacnetServer:
    def __init__(self, device_instance: int, address: str, objects: list[KnxObject],
                 limit: int | None = SMALL_EXPORT_LIMIT, model_name: str = "small",
                 knx_priority: int = DEFAULT_KNX_PRIORITY):
        if not 0 <= device_instance <= DEVICE_INSTANCE_MAX:
            raise ValueError("device instance out of range")
        if not 1 <= knx_priority <= N_PRIORITIES:
            raise ValueError("knx_priority must be 1..16")
        self.device_instance = device_instance
        self.knx_priority = knx_priority
        self.address = address
        self.limit = limit
        self.model_name = model_name
        self.config: list[KnxObject] = objects
        self.objects: dict[tuple[str, int], BObject] = {}
        self.by_ga: dict[str, BObject] = {}
        self.knx_value: dict[str, float] = {}           # last value seen per group address
        self.subs: dict[tuple, Subscription] = {}
        self.count = {"i_am": 0, "rp": 0, "rpm": 0, "wp": 0, "subscribe": 0,
                      "cov_confirmed": 0, "cov_unconfirmed": 0, "error": 0}
        self.apply_config()

    # ---------------------------------------------------------- export
    def exportable(self) -> list[KnxObject]:
        """Objects that pass the export rules, before the limit, lowest instance first."""
        ok = [o for o in self.config if o.export and bacnet_type(o.dpt) is not None]
        return sorted(ok, key=lambda o: knx.ga_to_instance(o.ga))

    def apply_config(self) -> None:
        """(Re)build the object table from the export configuration. Existing objects keep
        their state. Subscriptions to objects that disappear are dropped silently."""
        chosen = self.exportable()
        if self.limit is not None:
            chosen = chosen[: self.limit]                       # assumption A2
        new: dict[tuple[str, int], BObject] = {}
        for o in chosen:
            key = (bacnet_type(o.dpt), knx.ga_to_instance(o.ga))
            obj = self.objects.get(key)
            if obj is None or obj.ga != o.ga:
                obj = BObject(key[0], key[1], o.name, o.ga)
                if o.ga in self.knx_value:                      # the object holds its KNX value
                    obj.priority_array[self.knx_priority - 1] = self.knx_value[o.ga]
            obj.name = o.name
            obj.cov_increment = o.cov_increment
            new[key] = obj
        self.objects = new
        self.by_ga = {o.ga: o for o in new.values()}
        self.subs = {k: s for k, s in self.subs.items() if s.key in new}

    def object_list(self) -> list[tuple[str, int]]:
        return [(DEVICE, self.device_instance)] + sorted(self.objects)

    # ---------------------------------------------------------- KNX side
    def knx_telegram(self, t: float, ga: str, value: float) -> list[Notification]:
        self.knx_value[ga] = value
        obj = self.by_ga.get(ga)
        if obj is None:
            return []
        obj.priority_array[self.knx_priority - 1] = value      # G2: the controller's own slot
        return self._cov_check(t, obj)

    def _first_read(self, obj: BObject) -> None:
        """G2: "The controller writes to the Relinquish Default (RD) property by first reading
        (upload from BMS) only. It takes the current value of the object. The value of the RD
        property cannot be changed afterward." The current value is taken as the controller's
        KNX value of the object; in every simulated case this equals Present_Value, because
        the first read happens at commissioning, before any BACnet command."""
        if obj.relinquish_default is None:
            obj.relinquish_default = self.knx_value.get(obj.ga, obj.present_value)

    # ---------------------------------------------------------- services
    def who_is(self, low: int | None = None, high: int | None = None) -> tuple[int, str] | None:
        if low is not None and not low <= self.device_instance <= high:
            return None
        self.count["i_am"] += 1
        return (self.device_instance, self.address)

    def _err(self, e: Error) -> Error:
        self.count["error"] += 1
        return e

    def read_property(self, key: tuple[str, int], prop: str):
        self.count["rp"] += 1
        if key == (DEVICE, self.device_instance):
            if prop == "object-list":
                return self.object_list()
            if prop == "model-name":
                return self.model_name
            if prop == "object-name":
                return f"gateway {self.device_instance}"
            return self._err(UNKNOWN_PROPERTY)
        obj = self.objects.get(key)
        if obj is None:
            return self._err(UNKNOWN_OBJECT)
        self._first_read(obj)
        if prop == "present-value":
            return obj.present_value
        if prop == "priority-array":
            return list(obj.priority_array)
        if prop == "relinquish-default":
            return obj.relinquish_default
        if prop == "object-name":
            return obj.name
        if prop == "cov-increment" and obj.otype == AV:
            return obj.cov_increment
        return self._err(UNKNOWN_PROPERTY)

    def read_property_multiple(self, requests: list[tuple[tuple[str, int], str]]) -> list:
        self.count["rpm"] += 1
        self.count["rp"] -= len(requests)            # counted once as one RPM
        return [self.read_property(k, p) for k, p in requests]

    def write_property(self, t: float, key: tuple[str, int], prop: str, value: float | None,
                       priority: int = 16):
        """Returns (result, knx_telegram or None, notifications). value None relinquishes."""
        self.count["wp"] += 1
        if not 1 <= priority <= N_PRIORITIES:
            raise ValueError("priority must be 1..16")
        obj = self.objects.get(key)
        if obj is None:
            return self._err(UNKNOWN_OBJECT), None, []
        if prop != "present-value":
            return self._err(WRITE_ACCESS_DENIED if prop in ("priority-array", "object-name")
                             else UNKNOWN_PROPERTY), None, []
        before = obj.present_value
        obj.priority_array[priority - 1] = value
        after = obj.present_value
        telegram = None
        if after != before:
            # the gateway sends the resolved value to the group address, and the sent value
            # becomes its own KNX value, so it lands in the controller's slot (assumption A16)
            telegram = (t, obj.ga, after)
            self.knx_value[obj.ga] = after
            obj.priority_array[self.knx_priority - 1] = after
        return "ack", telegram, self._cov_check(t, obj)

    def subscribe_cov(self, t: float, subscriber: str, process_id: int, key: tuple[str, int],
                      confirmed: bool = False, lifetime: float = 0.0):
        """Returns (result, notifications). A renewal of the same subscription refreshes it and
        triggers a notification, as a new one does (B3)."""
        self.count["subscribe"] += 1
        obj = self.objects.get(key)
        if obj is None:
            return self._err(UNKNOWN_OBJECT), []
        self._first_read(obj)                       # the initial notification uploads the value
        sk = (subscriber, process_id, key)
        if sk not in self.subs:
            self._expire(t)
            if len(self.subs) >= MAX_SUBSCRIPTIONS:
                return self._err(NO_SPACE), []
        sub = Subscription(subscriber, process_id, key, confirmed, lifetime, t)
        self.subs[sk] = sub
        return "ack", [self._notify(t, obj, sub)]

    def cancel_cov(self, subscriber: str, process_id: int, key: tuple[str, int]) -> str:
        self.count["subscribe"] += 1
        self.subs.pop((subscriber, process_id, key), None)
        return "ack"

    # ---------------------------------------------------------- configuration actions
    def save_cov_settings(self, increments: dict[str, float] | None = None) -> None:
        """Changing and saving COV increments: all subscriptions are cancelled and the priority
        arrays of all objects are reset (G3). All 16 slots are emptied, the controller's own
        slot included (assumption A5), so Present_Value is the frozen Relinquish_Default until
        the next KNX telegram. The reset is not sent to KNX (nothing documents that it is)."""
        for o in self.config:
            if increments and o.ga in increments:
                o.cov_increment = increments[o.ga]
        for obj in self.objects.values():
            obj.priority_array = [None] * N_PRIORITIES
            obj.last_reported = obj.present_value
        self.subs.clear()
        self.apply_config()

    def active_subscriptions(self, t: float) -> int:
        self._expire(t)
        return len(self.subs)

    # ---------------------------------------------------------- internals
    def _expire(self, t: float) -> None:
        for k in [k for k, s in self.subs.items() if s.expired(t)]:
            del self.subs[k]

    def _notify(self, t: float, obj: BObject, sub: Subscription) -> Notification:
        self.count["cov_confirmed" if sub.confirmed else "cov_unconfirmed"] += 1
        return Notification(t, self.address, self.device_instance, obj.key, obj.present_value,
                            sub.subscriber, sub.confirmed)

    def _cov_check(self, t: float, obj: BObject) -> list[Notification]:
        if not obj.cov_due():
            return []
        obj.last_reported = obj.present_value
        self._expire(t)
        return [self._notify(t, obj, s) for s in self.subs.values() if s.key == obj.key]
