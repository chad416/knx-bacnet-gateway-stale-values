"""Virtual installation: KNX group objects -> gateway BACnet server -> building management
system, with one injected fault, run event by event over a time window.

The result of a run is an `Evidence` record: what each evidence level E1..E4 can see at
the end of the window, when the operator reports the complaint about one point.
"""
from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field

from . import knx
from .client import Bms, ClientConfig, Point, SubRecord, point_type
from .server import AV, BV, DEVICE, SMALL_EXPORT_LIMIT, BacnetServer, Error, KnxObject

T_WINDOW = 4 * 3600.0                  # s, length of the observed window (assumption A7)
GATEWAY_ADDR, OTHER_ADDR = "192.168.1.20:47808", "192.168.1.31:47808"
POLL_LIMIT = 300.0                     # s, longest acceptable polling interval (assumption A8)

FAULTS = ["HEALTHY", "COV_INC_COARSE", "COV_SUB_LOST_SAVE", "COV_LIFETIME_EXPIRED",
          "NOT_EXPORTED", "TYPE_NOT_EXPORTABLE", "OVER_LIMIT", "GA_CHANGED",
          "CMD_MASKS_KNX", "CMD_NO_EFFECT", "CMD_SAME_SLOT", "DUP_DEVICE", "POLL_TOO_SLOW"]
CMD_FAULTS = {"CMD_MASKS_KNX", "CMD_NO_EFFECT", "CMD_SAME_SLOT"}
BMS_PRIORITY = 8                       # the BMS's commands in all classes except CMD_NO_EFFECT
# Slot the controller writes KNX values to (its "object priority", G2). The guide makes it
# configurable and gives no default (assumption A4). Outside the command classes it is below
# the BMS's slot 8, so that an ordinary BMS command takes effect.
KNX_PRIORITY_CHOICES = [9, 10, 12, 14, 16]

DESCRIPTION = {
    "HEALTHY": "no fault, the complaint point behaves normally",
    "COV_INC_COARSE": "COV increment left at the default 1 on a temperature that moves by tenths",
    "COV_SUB_LOST_SAVE": "COV subscriptions cancelled by saving the gateway's COV settings",
    "COV_LIFETIME_EXPIRED": "COV subscription lifetime expired, the client does not renew",
    "NOT_EXPORTED": "export flag missing (never set, or removed by a project change)",
    "TYPE_NOT_EXPORTABLE": "group object of a datapoint type that is not exported (date, text)",
    "OVER_LIMIT": "more exportable objects than the small variant's limit; this one dropped",
    "GA_CHANGED": "group address changed, so the BACnet instance number changed",
    "CMD_MASKS_KNX": "BMS command held at a priority above the controller's KNX slot; later KNX changes are masked",
    "CMD_NO_EFFECT": "BMS command written at a priority below the controller's KNX slot; it never becomes the value",
    "CMD_SAME_SLOT": "BMS command written into the controller's own KNX slot and relinquished; the value "
                     "falls back to the frozen Relinquish_Default",
    "DUP_DEVICE": "a second BACnet device with the same device instance on the network",
    "POLL_TOO_SLOW": "client polling interval too long for the signal",
}

# main group per signal kind; static (not on the BMS) objects fill main groups 0..5
MAIN_OF = {"switch": 1, "blind": 2, "temperature": 3, "setpoint": 3, "power": 4, "energy": 4,
           "date": 5, "text": 5}
DYNAMIC_MIX = [("temperature", 4), ("setpoint", 2), ("switch", 4), ("blind", 2), ("power", 1),
               ("energy", 1)]


@dataclass
class Evidence:
    fault: str
    target: str                              # point name of the complaint
    t_end: float
    # E1: value history on the BMS
    points: dict                             # name -> {"kind", "type", "instance"}
    history: dict                            # name -> PointLog
    commands: list                           # BMS command log
    # E2: direct ReadProperty of the target's present value
    direct_read: object = None
    # E3: Who-Is answers, object lists, target properties, client state
    i_am: list = field(default_factory=list)
    object_lists: dict = field(default_factory=dict)    # address -> list of keys
    new_object_names: dict = field(default_factory=dict) # key -> name for objects not known at commissioning
    known_objects: list = field(default_factory=list)
    known_names: dict = field(default_factory=dict)      # key -> name at commissioning
    model_name: str = ""
    priority_array: object = None
    relinquish_default: object = None
    client: ClientConfig | None = None
    sub_records: dict = field(default_factory=dict)
    # E4: export configuration, BACnet settings and KNX group monitor
    config: list = field(default_factory=list)
    limit: int | None = None
    knx_priority: int | None = None                      # the controller's "object priority" (G2)
    knx_monitor: list = field(default_factory=list)      # (t, ga, value)
    truth: dict = field(default_factory=dict)            # never shown to the engine


class Installation:
    """Builds and runs one virtual installation. `fault` is one of FAULTS."""

    def __init__(self, fault: str, seed: int, t_end: float = T_WINDOW,
                 sig: knx.SignalParams | None = None):
        if fault not in FAULTS:
            raise ValueError(fault)
        self.fault = fault
        self.rng = random.Random(seed)
        # own stream for the priority slots, so the other draws stay as they were
        self.prio_rng = random.Random(seed * 7919 + 17)
        self.t_end = t_end
        self.sig = sig or knx.SignalParams()
        self.q: list = []
        self.seq = 0
        self.monitor: list = []
        self.truth: dict = {"fault": fault}
        self._build()

    # ------------------------------------------------------------ construction
    def _free_ga(self, main: int, used: set, below: int | None = None) -> str:
        while True:
            ga = knx.format_ga(main, self.rng.randrange(8), self.rng.randrange(1, 256))
            if ga not in used and (below is None or knx.ga_to_instance(ga) < below):
                used.add(ga)
                return ga

    def _build(self) -> None:
        r, f = self.rng, self.fault
        self.small = f == "OVER_LIMIT" or r.random() < 0.6
        limit = SMALL_EXPORT_LIMIT if self.small else None
        used: set = set()
        # dynamic points shown on the BMS
        kinds = [k for k, w in DYNAMIC_MIX for _ in range(w)]
        n_dyn = r.randint(12, 24)
        dyn = [r.choice(kinds) for _ in range(n_dyn)]
        need = {"COV_INC_COARSE": "temperature", "CMD_MASKS_KNX": "setpoint",
                "CMD_NO_EFFECT": "setpoint", "CMD_SAME_SLOT": "setpoint",
                "TYPE_NOT_EXPORTABLE": r.choice(["date", "text"]),
                "POLL_TOO_SLOW": r.choice(["temperature", "switch", "power"])}.get(f)
        if f == "HEALTHY":
            need = r.choice(["temperature", "switch", "setpoint", "power"])
        if need:
            dyn[0] = need
        if f == "OVER_LIMIT" and dyn[0] in ("switch",):
            dyn[0] = "temperature"                       # keep the target above main group 1
        if "setpoint" not in dyn:
            dyn[-1] = "setpoint"
        objs: list[KnxObject] = []
        self.signals: dict[str, list] = {}
        names = []
        for i, kind in enumerate(dyn):
            name = f"{kind} {i + 1:02d}"
            ga = self._free_ga(MAIN_OF[kind], used)
            inc = {"temperature": 0.1, "setpoint": 0.1, "power": 10.0, "blind": 1.0,
                   "energy": 1.0}.get(kind, 1.0)
            if f == "COV_INC_COARSE":
                inc = 1.0                                # never configured: default stays
            objs.append(KnxObject(name, ga, knx.SIGNAL_DPT[kind], True, inc))
            self.signals[name] = knx.generate(kind, r, self.t_end, self.sig)
            names.append((name, kind))
        self.target = names[0][0]
        # static exported objects (not on the BMS), filling up the object list
        n_total = r.randint(60, 140) if self.small else r.randint(150, 600)
        for j in range(n_total - n_dyn):
            kind = r.choice(["switch", "blind", "temperature", "power"])
            ga = self._free_ga(r.choice([0, 1, 2, 5]), used)
            objs.append(KnxObject(f"static {j + 1:03d}", ga, knx.SIGNAL_DPT[kind], True, 1.0))
            self.signals[f"static {j + 1:03d}"] = [(0.0, float(r.randrange(0, 2) if kind == "switch"
                                                               else round(r.uniform(0, 50), 1)))]
        self.objs = objs
        self.obj_by_name = {o.name: o for o in objs}
        pr = self.prio_rng
        if f == "CMD_NO_EFFECT":
            self.knx_priority = pr.choice([6, 8, 10])
            self.bms_priority = pr.choice([p for p in (10, 12, 14, 16) if p > self.knx_priority])
        elif f == "CMD_SAME_SLOT":
            self.knx_priority = self.bms_priority = pr.choice([8, 10, 12, 16])
        else:
            self.knx_priority, self.bms_priority = pr.choice(KNX_PRIORITY_CHOICES), BMS_PRIORITY
        self.truth["knx_priority"], self.truth["bms_priority"] = self.knx_priority, self.bms_priority
        self.gw = BacnetServer(r.randrange(1000, 100000), GATEWAY_ADDR, objs, limit,
                               "small" if self.small else "large", self.knx_priority)
        self.net = {GATEWAY_ADDR: self.gw}

        # client
        if f == "POLL_TOO_SLOW":
            cfg = ClientConfig("poll", poll_interval=r.choice([900.0, 1800.0, 3600.0]))
        elif f in ("COV_INC_COARSE", "COV_SUB_LOST_SAVE", "COV_LIFETIME_EXPIRED"):
            cfg = ClientConfig("cov")
        else:
            cfg = ClientConfig("cov" if r.random() < 0.65 else "poll",
                               poll_interval=r.choice([30.0, 60.0, 120.0]))
        if cfg.mode == "cov":
            cfg.lifetime, cfg.renew_interval = r.choice([(0.0, None), (3600.0, 1800.0),
                                                         (28800.0, 14400.0)])
            cfg.confirmed = r.random() < 0.5
            if f == "COV_SUB_LOST_SAVE":
                cfg.lifetime, cfg.renew_interval = r.choice([(0.0, None), (28800.0, 14400.0)])
            if f == "COV_LIFETIME_EXPIRED":
                cfg.lifetime, cfg.renew_interval = round(r.uniform(1800, 0.6 * self.t_end)), None
        self.points = [Point(n, k, self.gw.device_instance,
                             (point_type(knx.DPTS[knx.SIGNAL_DPT[k]].kind),
                              knx.ga_to_instance(self.obj_by_name[n].ga))) for n, k in names]
        self.bms = Bms(self.points, cfg)

        # faults present from the start (before commissioning)
        t_f = r.uniform(0.25, 0.6) * self.t_end
        self.truth["t_fault"] = None
        tgt = self.obj_by_name[self.target]
        if f == "TYPE_NOT_EXPORTABLE":
            self.gw.apply_config()
        elif f == "NOT_EXPORTED":
            if r.random() < 0.5:
                tgt.export = False
                self.gw.apply_config()
            else:
                self._at(t_f, "fault", self._remove_export)
                self.truth["t_fault"] = t_f
        elif f == "OVER_LIMIT":
            if r.random() < 0.3:
                self._add_objects_over_limit(used)
            else:
                self._at(t_f, "fault", lambda: self._add_objects_over_limit(used))
                self.truth["t_fault"] = t_f
        elif f == "GA_CHANGED":
            self._at(t_f, "fault", lambda: self._change_ga(used))
            self.truth["t_fault"] = t_f
        elif f == "COV_SUB_LOST_SAVE":
            self._at(t_f, "fault", self.gw.save_cov_settings)
            self.truth["t_fault"] = t_f
        elif f == "DUP_DEVICE":
            self._make_duplicate(used)
            self._at(t_f, "fault", lambda: self.bms.i_am(self.gw.device_instance, OTHER_ADDR))
            self.truth["t_fault"] = t_f

        # BMS commands on a setpoint
        sp = [p for p in self.points if p.kind == "setpoint"]
        bp = self.bms_priority
        if f == "CMD_MASKS_KNX":
            p = self.points[0]
            t_w = r.uniform(0.1, 0.4) * self.t_end
            self._at(t_w, "write", (p, self._other_setpoint(p.name), bp))
            # somebody changes the setpoint at the room panel later (KNX side)
            for _ in range(r.randint(1, 3)):
                t_k = r.uniform(t_w + 300, self.t_end - 600)
                self.signals[p.name].append((t_k, r.choice([19.0, 19.5, 22.5, 23.0])))
            self.signals[p.name].sort()
            self.truth["t_fault"] = t_w
        elif f == "CMD_NO_EFFECT":
            p = self.points[0]
            t_w = r.uniform(0.1, 0.6) * self.t_end
            self._at(t_w, "write", (p, self._other_setpoint(p.name), bp))
            self.truth["t_fault"] = t_w
        elif f == "CMD_SAME_SLOT":
            p = self.points[0]
            t_w = r.uniform(0.1, 0.4) * self.t_end
            t_r = t_w + r.uniform(900, 3600)
            self._at(t_w, "write", (p, self._other_setpoint(p.name), bp))
            self._at(t_r, "write", (p, None, bp))
            # nobody touches the room panel after the relinquish, so the fallback lasts until
            # the complaint (a later KNX telegram would end it)
            self.signals[p.name] = [e for e in self.signals[p.name] if e[0] < t_r]
            self.truth["t_fault"] = t_r
        elif sp and r.random() < 0.5:
            p = r.choice(sp)
            t_w = r.uniform(0.1, 0.5) * self.t_end
            self._at(t_w, "write", (p, self._other_setpoint(p.name), bp))
            self._at(min(t_w + r.uniform(900, 3600), 0.8 * self.t_end), "write", (p, None, bp))

    def _other_setpoint(self, name: str) -> float:
        return self.rng.choice([18.0, 18.5, 24.0, 24.5])

    def _remove_export(self) -> None:
        self.obj_by_name[self.target].export = False
        self.gw.apply_config()

    def _add_objects_over_limit(self, used: set) -> None:
        tinst = knx.ga_to_instance(self.obj_by_name[self.target].ga)
        below = sum(1 for o in self.gw.exportable() if knx.ga_to_instance(o.ga) < tinst)
        n_add = max(0, SMALL_EXPORT_LIMIT - below) + self.rng.randint(1, 20)
        for j in range(n_add):
            while True:                       # any free address below the target's
                ga = knx.instance_to_ga(self.rng.randrange(1, tinst))
                if ga not in used and ga.split("/")[2] != "0":
                    used.add(ga)
                    break
            o = KnxObject(f"added {j + 1:03d}", ga, "1.001", True, 1.0)
            self.objs.append(o)
        self.gw.apply_config()
        self.truth["exportable"] = len(self.gw.exportable())

    def _change_ga(self, used: set) -> None:
        o = self.obj_by_name[self.target]
        old = o.ga
        o.ga = self._free_ga(MAIN_OF[self.points[0].kind], used)
        self.truth["old_ga"], self.truth["new_ga"] = old, o.ga
        self.gw.knx_value[o.ga] = self.gw.knx_value.get(old, 0.0)
        self.gw.apply_config()

    def _make_duplicate(self, used: set) -> None:
        """Second KNX controller of another floor, same device instance, same address scheme."""
        objs = []
        for p in self.points:
            if self.rng.random() < 0.5:
                o = self.obj_by_name[p.name]
                objs.append(KnxObject("other " + p.name, o.ga, o.dpt, True, 1.0))
                self.signals["other " + p.name] = knx.generate(p.kind, self.rng, self.t_end, self.sig)
        self.other = BacnetServer(self.gw.device_instance, OTHER_ADDR, objs, SMALL_EXPORT_LIMIT, "small",
                                  self.knx_priority)
        self.other_by_name = {o.name: o for o in objs}
        self.net[OTHER_ADDR] = self.other

    # ------------------------------------------------------------ event loop
    def _at(self, t: float, kind: str, data) -> None:
        self.seq += 1
        heapq.heappush(self.q, (t, self.seq, kind, data))

    def _deliver(self, notes) -> None:
        for n in notes:
            self.bms.notification(n)

    def run(self) -> Evidence:
        r, bms, cfg = self.rng, self.bms, self.bms.cfg
        for name, events in self.signals.items():
            server = self.other if name.startswith("other ") else self.gw
            for t, v in events:
                self._at(t, "knx", (server, name, v))
        # commissioning: Who-Is, object list, then subscriptions or polling
        bms.count["who_is"] += 1
        bms.i_am(*self.gw.who_is())
        bms.known_objects[self.gw.device_instance] = self.gw.object_list()
        bms.known_names = {k: o.name for k, o in self.gw.objects.items()}
        for i, p in enumerate(self.points):
            if cfg.mode == "cov":
                self._at(2.0 + 0.05 * i, "subscribe", p)
            else:
                self._at(2.0 + r.uniform(0, cfg.poll_interval), "poll", p)

        while self.q:
            t, _, kind, data = heapq.heappop(self.q)
            if t > self.t_end:
                break
            if kind == "knx":
                server, name, v = data
                obj = (self.other if server is not self.gw else self).obj_by_name[name] \
                    if server is self.gw else next(o for o in server.config if o.name == name)
                if server is self.gw:
                    self.monitor.append((t, obj.ga, v))
                self._deliver(server.knx_telegram(t, obj.ga, v))
            elif kind == "poll":
                srv = self.net[bms.binding[data.device]]
                bms.count["requests"] += 1
                bms.record_value(t, data, srv.read_property(data.key, "present-value"))
                self._at(t + cfg.poll_interval, "poll", data)
            elif kind == "subscribe":
                srv = self.net[bms.binding[data.device]]
                bms.count["requests"] += 1
                res, notes = srv.subscribe_cov(t, Bms.NAME, 1, data.key, cfg.confirmed, cfg.lifetime)
                rec = bms.subs.get(data.name)
                if isinstance(res, Error):
                    bms.record_value(t, data, res)
                if rec is None:
                    bms.subs[data.name] = SubRecord(t, t, cfg.lifetime, cfg.renew_interval, str(res))
                else:
                    rec.last_renewal, rec.last_result = t, str(res)
                self._deliver(notes)
                if cfg.renew_interval:
                    self._at(t + cfg.renew_interval, "subscribe", data)
            elif kind == "write":
                p, v, prio = data
                srv = self.net[bms.binding[p.device]]
                res, telegram, notes = srv.write_property(t, p.key, "present-value", v, prio)
                bms.commands.append((t, p.name, v, prio, str(res)))
                if telegram and srv is self.gw:
                    self.monitor.append(telegram)
                self._deliver(notes)
            elif kind == "fault":
                data()
        return self._evidence()

    # ------------------------------------------------------------ evidence at the complaint
    def _evidence(self) -> Evidence:
        r, bms, T = self.rng, self.bms, self.t_end
        p = self.points[0]
        ev = Evidence(self.fault, p.name, T,
                      points={q.name: {"kind": q.kind, "type": q.key[0], "instance": q.key[1]}
                              for q in self.points},
                      history=bms.log, commands=bms.commands)
        # an engineer's tool: Who-Is for the device instance, binds to the first answer
        answers = [s.who_is(p.device, p.device) for s in self.net.values()]
        answers = [a for a in answers if a]
        r.shuffle(answers)
        ev.i_am = answers
        srv = self.net[answers[0][1]]
        ev.direct_read = srv.read_property(p.key, "present-value")
        for dev, addr in answers:
            s = self.net[addr]
            ev.object_lists[addr] = s.read_property((DEVICE, dev), "object-list")
        known = set(bms.known_objects[p.device])
        for k in ev.object_lists[answers[0][1]]:
            if k not in known and k[0] != DEVICE:
                ev.new_object_names[k] = srv.read_property(k, "object-name")
        ev.known_objects = sorted(known)
        ev.known_names = dict(bms.known_names)
        ev.model_name = srv.read_property((DEVICE, p.device), "model-name")
        ev.priority_array = srv.read_property(p.key, "priority-array")
        ev.relinquish_default = srv.read_property(p.key, "relinquish-default")
        ev.client = bms.cfg
        ev.sub_records = bms.subs
        # E4: the real gateway's configuration and the KNX group monitor recording
        ev.config = [KnxObject(o.name, o.ga, o.dpt, o.export, o.cov_increment) for o in self.gw.config]
        ev.limit = self.gw.limit
        ev.knx_priority = self.gw.knx_priority
        ev.knx_monitor = list(self.monitor)
        self.truth["bms_messages"] = dict(bms.count)
        self.truth["server_messages"] = dict(self.gw.count)
        ev.truth = self.truth
        return ev


def simulate(fault: str, seed: int, **kw) -> Evidence:
    return Installation(fault, seed, **kw).run()
