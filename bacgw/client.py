"""The building management system (BACnet client) as far as the gateway sees it.

It binds a device instance to a network address with Who-Is / I-Am (the last I-Am heard
wins, B6), then either polls the present value of every point at a fixed interval or
subscribes to COV and renews the subscriptions at an interval (or never).
It keeps what an operator can see later: a value history per point, a fault flag per
point, a log of its own commands, and its subscription records.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .server import AV, BV, Error


@dataclass
class Point:
    name: str
    kind: str                  # signal kind, part of the point name for the operator
    device: int
    key: tuple[str, int]


@dataclass
class ClientConfig:
    mode: str = "cov"                     # "cov" or "poll"
    poll_interval: float = 60.0           # s
    lifetime: float = 3600.0              # s, 0 = indefinite
    renew_interval: float | None = 1800.0 # s, None = never renews
    confirmed: bool = False


@dataclass
class PointLog:
    updates: list = field(default_factory=list)        # (t, value)
    faults: list = field(default_factory=list)         # (t, Error) when a request failed
    ok_after_fault: list = field(default_factory=list) # (t) a later request succeeded


@dataclass
class SubRecord:
    first: float
    last_renewal: float
    lifetime: float
    renew_interval: float | None
    last_result: str = "ack"


class Bms:
    NAME = "bms"

    def __init__(self, points: list[Point], cfg: ClientConfig):
        self.points = points
        self.cfg = cfg
        self.by_key = {(p.device, p.key): p for p in points}
        self.binding: dict[int, str] = {}
        self.log = {p.name: PointLog() for p in points}
        self.commands: list = []                        # (t, point, value or None, priority)
        self.subs: dict[str, SubRecord] = {}
        self.known_objects: dict[int, list] = {}        # object list read at commissioning
        self.known_names: dict[tuple, str] = {}
        self.count = {"who_is": 0, "requests": 0}

    # ---------------------------------------------------------- binding
    def i_am(self, device: int, address: str) -> None:
        self.binding[device] = address                  # last I-Am wins (B6)

    # ---------------------------------------------------------- results coming back
    def record_value(self, t: float, p: Point, value) -> None:
        lg = self.log[p.name]
        if isinstance(value, Error):
            if not lg.faults or (lg.ok_after_fault and lg.ok_after_fault[-1] > lg.faults[-1][0]):
                lg.faults.append((t, value))
            return
        if lg.faults and (not lg.ok_after_fault or lg.ok_after_fault[-1] < lg.faults[-1][0]):
            lg.ok_after_fault.append(t)
        lg.updates.append((t, float(value)))

    def notification(self, n) -> None:
        p = self.by_key.get((n.device, n.key))
        if p is not None and n.subscriber == self.NAME:
            self.record_value(n.t, p, n.value)

    def in_fault(self, name: str) -> bool:
        lg = self.log[name]
        return bool(lg.faults) and (not lg.ok_after_fault or lg.ok_after_fault[-1] < lg.faults[-1][0])


def point_type(dpt_kind: str) -> str:
    return BV if dpt_kind == "binary" else AV
