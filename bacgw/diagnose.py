"""Diagnosis engine: from the evidence available at one level, rank the fault classes.

Levels (each includes the ones before it):
  E1  value history of all points of the device on the BMS, fault flag per point,
      the BMS's own command log
  E2  one direct ReadProperty of the target's Present_Value (value or error)
  E3  Who-Is answers, the device object list (and names of objects that are new since
      commissioning), the target's Priority_Array and Relinquish_Default, the device model
      name, the client's polling / subscription configuration and records
  E4  the gateway's export configuration, its BACnet "object priority" setting (the slot it
      writes KNX values to) and a KNX group monitor recording of the window

A hypothesis is "certain" only when the evidence contains something that no other class of
the catalogue can produce under the premises P1..P4 (DOSSIER section 4). "likely" means
strong evidence that another class would need a coincidence to produce. "possible" is the rest.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from . import knx
from .server import AV, BV, DEVICE, Error

LEVELS = ["E1", "E2", "E3", "E4"]
CERT = {"certain": 2, "likely": 1, "possible": 0}
POLL_LIMIT = 300.0
CMD_CLASSES = {"CMD_MASKS_KNX", "CMD_NO_EFFECT", "CMD_SAME_SLOT"}
STALE_CLASSES = {"HEALTHY", "COV_INC_COARSE", "COV_SUB_LOST_SAVE", "COV_LIFETIME_EXPIRED",
                 "POLL_TOO_SLOW"} | CMD_CLASSES
# step a KNX value of this kind normally moves by, used when the window shows no step (A9)
NOMINAL_STEP = {"temperature": 0.1, "setpoint": 0.5, "power": 10.0, "blind": 25.0, "energy": 10.0}
MISSING_CLASSES = {"NOT_EXPORTED", "TYPE_NOT_EXPORTABLE", "OVER_LIMIT", "GA_CHANGED"}


@dataclass
class Hypothesis:
    cls: str
    score: float
    certainty: str = "possible"
    why: list = field(default_factory=list)


class Board:
    def __init__(self):
        self.h: dict[str, Hypothesis] = {}

    def add(self, cls: str, score: float, why: str, certainty: str = "possible") -> None:
        h = self.h.setdefault(cls, Hypothesis(cls, 0.0))
        h.score += score
        h.why.append(why)
        if CERT[certainty] > CERT[h.certainty]:
            h.certainty = certainty

    def drop(self, classes, why: str) -> None:
        for c in classes:
            self.h.pop(c, None)
        self.dropped = getattr(self, "dropped", []) + [why]

    def ranked(self) -> list[Hypothesis]:
        return sorted(self.h.values(), key=lambda h: (-CERT[h.certainty], -h.score, h.cls))


# ------------------------------------------------------------------ E1 features
def _gaps(updates):
    return [b[0] - a[0] for a, b in zip(updates, updates[1:])]


def poll_interval_guess(ev) -> float | None:
    """Polling leaves exactly periodic timestamps on every point."""
    gaps = [g for lg in ev.history.values() for g in _gaps(lg.updates)]
    if len(gaps) < 8:
        return None
    gaps.sort()
    med = gaps[len(gaps) // 2]
    if med < 1:
        return None
    share = sum(1 for g in gaps if abs(g - med) <= 0.01 * med) / len(gaps)
    return med if share >= 0.9 else None


def renewal_bursts(ev) -> list[float]:
    """Times at which most points got an update within one second (subscribe or renew)."""
    n = len(ev.points)
    times = sorted(t for lg in ev.history.values() for t, _ in lg.updates)
    bursts, i = [], 0
    while i < len(times):
        j = i
        while j < len(times) and times[j] - times[i] <= 1.5:
            j += 1
        if j - i >= max(3, 0.8 * n):
            bursts.append(times[i])
        i = j
    return bursts


def device_silence(ev) -> tuple[float, float]:
    """(start of the silence of all points, chance that it is a coincidence)."""
    lasts, rates = [], []
    for name, lg in ev.history.items():
        if not lg.updates:
            continue
        lasts.append(lg.updates[-1][0])
    if not lasts:
        return ev.t_end, 1.0
    t_sil = max(lasts)
    lam = 0.0
    for name, lg in ev.history.items():
        n = sum(1 for t, _ in lg.updates if 60 < t <= t_sil)
        if t_sil > 600:
            lam += n / (t_sil - 60)
    p = math.exp(-lam * (ev.t_end - t_sil))
    return t_sil, p


def device_gap(ev) -> tuple[float, float, float]:
    """Longest stretch with no update on any point that ended before the window end:
    (start, end, chance that it is a coincidence). The silence still running at the end is
    handled by device_silence."""
    times = sorted(t for lg in ev.history.values() for t, _ in lg.updates if t > 60)
    if len(times) < 10:
        return 0.0, 0.0, 1.0
    gaps = [(b - a, a, b) for a, b in zip(times, times[1:])]
    g, a, b = max(gaps)
    lam = (len(times) - 1) / max(1.0, (times[-1] - times[0]) - g)
    return a, b, math.exp(-lam * g)


def features(ev) -> dict:
    tp = ev.points[ev.target]
    lg = ev.history[ev.target]
    ups = lg.updates
    f = {"kind": tp["kind"], "type": tp["type"], "has_data": bool(ups),
         "in_fault": bool(lg.faults) and (not lg.ok_after_fault or lg.ok_after_fault[-1] < lg.faults[-1][0]),
         "fault_t": lg.faults[0][0] if lg.faults else None,
         "last_value": ups[-1][1] if ups else None,
         "last_t": ups[-1][0] if ups else None}
    f["poll"] = poll_interval_guess(ev)
    f["bursts"] = renewal_bursts(ev)
    f["t_sil"], f["p_sil"] = device_silence(ev)
    f["gap_a"], f["gap_b"], f["p_gap"] = device_gap(ev)
    f["gap_renewed"] = any(abs(t - f["gap_b"]) < 2.0 for t in f["bursts"])
    steps = [abs(b[1] - a[1]) for a, b in zip(ups, ups[1:]) if b[1] != a[1]]
    f["steps"] = steps
    # value changed between consecutive polls (share of polls)
    f["change_share"] = (sum(1 for a, b in zip(ups, ups[1:]) if a[1] != b[1]) / max(1, len(ups) - 1))
    cmds = [c for c in ev.commands if c[1] == ev.target]
    f["cmd_active"] = bool(cmds) and cmds[-1][2] is not None
    f["cmd_value"] = cmds[-1][2] if cmds else None
    f["cmd_t"] = cmds[-1][0] if cmds else None
    f["cmd_prio"] = cmds[-1][3] if cmds else None
    writes = [c for c in cmds if c[2] is not None]
    f["write_value"] = writes[-1][2] if writes else None
    # was the BMS's last written value ever shown after it was written?
    f["cmd_shown"] = bool(writes) and any(t >= writes[-1][0] - 1e-6 and v == writes[-1][2] for t, v in ups)
    # value shown right after a relinquish (COV: same instant; polling: the next poll)
    f["rel_t"] = cmds[-1][0] if cmds and cmds[-1][2] is None and writes else None
    f["after_rel"] = None
    if f["rel_t"] is not None:
        wait = (f["poll"] or 0.0) + 1.5
        f["after_rel"] = next((v for t, v in ups if f["rel_t"] - 1e-6 <= t <= f["rel_t"] + wait), None)
    f["first_value"] = ups[0][1] if ups else None
    # others still updating in the last hour
    others = [n for n in ev.history if n != ev.target]
    f["others_recent"] = sum(1 for n in others if ev.history[n].updates
                             and ev.history[n].updates[-1][0] > ev.t_end - 3600)
    f["n_others"] = len(others)
    return f


# ------------------------------------------------------------------ the rules
def diagnose(ev, level: str) -> list[Hypothesis]:
    lv = LEVELS.index(level)
    f = features(ev)
    b = Board()
    T = ev.t_end
    analog = f["type"] == AV

    # ---------------------------------------------------------- E1
    if f["in_fault"]:
        if not f["has_data"]:
            b.add("TYPE_NOT_EXPORTABLE", 3.0 if f["kind"] in ("date", "text") else 0.3,
                  "point never had a value; its group object is a date or text" if f["kind"] in ("date", "text")
                  else "point never had a value")
            b.add("NOT_EXPORTED", 2.0, "point in fault since commissioning")
            b.add("OVER_LIMIT", 1.5, "point in fault since commissioning")
            b.add("DUP_DEVICE", 0.3, "point in fault")
        else:
            b.add("GA_CHANGED", 2.0, f"point had values, in fault since t = {f['fault_t']:.0f} s")
            b.add("NOT_EXPORTED", 1.8, "point had values, then fault")
            b.add("OVER_LIMIT", 1.6, "point had values, then fault")
            b.add("DUP_DEVICE", 1.0, "point had values, then fault")
    else:
        if f["poll"] is not None and f["poll"] > POLL_LIMIT:
            b.add("POLL_TOO_SLOW", 3.0, f"regular updates every {f['poll']:.0f} s (polling)")
        silent_long = T - f["t_sil"] > 900 and f["p_sil"] < 1e-3 and f["poll"] is None
        if silent_long:
            renews = [t for t in f["bursts"] if t > 60]
            b.add("COV_SUB_LOST_SAVE", 2.2 if renews else 2.0,
                  f"all points silent since t = {f['t_sil']:.0f} s (chance {f['p_sil']:.1e})")
            b.add("COV_LIFETIME_EXPIRED", 1.0 if renews else 2.1,
                  "all points silent at the same time" + ("; client was seen renewing" if renews else ""))
        healed = (not silent_long and f["p_gap"] < 1e-3 and f["gap_b"] - f["gap_a"] > 900
                  and f["gap_renewed"] and f["poll"] is None)
        if healed:
            b.add("COV_SUB_LOST_SAVE", 2.5, f"all points silent from t = {f['gap_a']:.0f} s to "
                                            f"{f['gap_b']:.0f} s, then all updated together (client renewal)")
            b.add("COV_LIFETIME_EXPIRED", 0.5, "a common silence that ended with a renewal")
        if f["cmd_active"] and f["last_value"] == f["cmd_value"]:
            b.add("CMD_MASKS_KNX", 2.5, f"BMS command {f['cmd_value']} at priority {f['cmd_prio']}, "
                                        f"t = {f['cmd_t']:.0f} s, never relinquished; the point still shows it")
        elif f["cmd_active"] and not f["cmd_shown"] and f["has_data"]:
            b.add("CMD_NO_EFFECT", 2.5, f"BMS command {f['cmd_value']} at priority {f['cmd_prio']}, "
                                        f"t = {f['cmd_t']:.0f} s, acknowledged but never shown on the point")
        if f["after_rel"] is not None and f["after_rel"] != f["write_value"] \
                and f["after_rel"] == f["first_value"]:
            b.add("CMD_SAME_SLOT", 2.5, f"at the BMS's relinquish (t = {f['rel_t']:.0f} s) the point went to "
                                        f"{f['after_rel']}, its value at commissioning")
        if analog and f["kind"] in ("temperature", "setpoint") and f["poll"] is None and not silent_long:
            if f["steps"] and min(f["steps"]) >= 0.95:
                b.add("COV_INC_COARSE", 2.4, f"value moves only in steps >= {min(f['steps']):.2f}")
            elif len(f["steps"]) <= 1 and f["others_recent"] >= 0.5 * f["n_others"]:
                b.add("COV_INC_COARSE", 1.5, f"{f['kind']} hardly ever updates while other points do")
        if f["poll"] is None and not silent_long and f["last_t"] is not None \
                and T - f["last_t"] > 3600 and f["others_recent"] >= 0.5 * f["n_others"]:
            for c in ("NOT_EXPORTED", "GA_CHANGED", "OVER_LIMIT"):
                b.add(c, 0.8, "only this point stale under COV (a removed object stops reporting silently)")
        b.add("DUP_DEVICE", 0.2, "cannot be excluded from the history")
        b.add("HEALTHY", 1.0, "nothing abnormal in the history")

    if lv < 1:
        return b.ranked()

    # ---------------------------------------------------------- E2: direct read
    dr = ev.direct_read
    if isinstance(dr, Error):
        b.drop(STALE_CLASSES, f"direct read: {dr}")
        for c, s in (("NOT_EXPORTED", 1.0), ("GA_CHANGED", 1.0), ("OVER_LIMIT", 1.0), ("DUP_DEVICE", 0.3)):
            b.add(c, s, f"direct read returns {dr}")
        if f["kind"] in ("date", "text"):
            b.add("TYPE_NOT_EXPORTABLE", 1.0, f"direct read returns {dr}")
    else:
        b.drop(MISSING_CLASSES, "direct read returns a value: the object exists")
        bms = f["last_value"]
        diff = None if bms is None else abs(dr - bms)
        if diff is not None and diff < 1e-9:
            if f["cmd_active"] and dr == f["cmd_value"]:
                b.add("CMD_MASKS_KNX", 0.5, "direct read equals the BMS command: the server itself holds it")
            elif f["cmd_active"]:
                b.add("CMD_NO_EFFECT", 0.5, f"direct read {dr} is not the BMS command {f['cmd_value']}")
            if f["after_rel"] is not None and dr == f["after_rel"]:
                b.add("CMD_SAME_SLOT", 0.5, "direct read equals the value shown since the relinquish")
            b.add("HEALTHY", 0.5, "direct read equals the BMS value")
        elif diff is not None:
            if analog and diff < 1.0:
                b.add("COV_INC_COARSE", 1.5, f"direct read differs by {diff:.2f}, less than 1")
            b.add("COV_SUB_LOST_SAVE", 1.0, f"direct read {dr} differs from BMS value {bms}")
            b.add("COV_LIFETIME_EXPIRED", 1.0, f"direct read {dr} differs from BMS value {bms}")
            b.add("POLL_TOO_SLOW", 0.5, "direct read differs from BMS value")
            b.add("DUP_DEVICE", 0.5, "direct read differs from BMS value")
            if "HEALTHY" in b.h:
                b.h["HEALTHY"].score -= 0.8
            b.drop({"CMD_MASKS_KNX", "CMD_NO_EFFECT"} if not f["cmd_active"] else set(), "")

    if lv < 2:
        return b.ranked()

    # ---------------------------------------------------------- E3
    dev_instances = [d for d, _ in ev.i_am]
    dup = len(dev_instances) > 1 and len(set(dev_instances)) < len(dev_instances)
    if dup:
        b.add("DUP_DEVICE", 10.0, f"{len(ev.i_am)} devices answer Who-Is with device instance "
                                  f"{dev_instances[0]}: {[a for _, a in ev.i_am]}", "certain")
        return b.ranked()
    b.drop({"DUP_DEVICE"}, "exactly one I-Am for the device instance")
    key = (f["type"], ev.points[ev.target]["instance"])
    olist = next(iter(ev.object_lists.values()))
    present = key in olist
    n_obj = len([k for k in olist if k[0] != DEVICE])
    if not present:
        b.drop(STALE_CLASSES, "object absent from the object list")
        known_name = ev.known_names.get(key, ev.target)
        moved = [k for k, n in ev.new_object_names.items() if n == known_name]
        if moved:
            b.add("GA_CHANGED", 10.0, f"object '{known_name}' is now {moved[0][0]} {moved[0][1]} "
                                      f"(group address {knx.instance_to_ga(moved[0][1])})", "certain")
        else:
            b.drop({"GA_CHANGED"}, "no new object with the point's name")
            if ev.model_name == "small" and n_obj >= 150:
                b.add("OVER_LIMIT", 3.0, f"object list holds {n_obj} objects, the small variant's limit", "likely")
            else:
                b.drop({"OVER_LIMIT"}, f"object list holds {n_obj} objects, below any limit")
            b.add("NOT_EXPORTED", 1.0, "object absent from the object list")
            if f["kind"] in ("date", "text"):
                b.add("TYPE_NOT_EXPORTABLE", 1.0, "object absent; its group object is a date or text")
    else:
        b.drop(MISSING_CLASSES, "object present in the object list")
        command_evidence(ev, f, b)
        cfg = ev.client
        if cfg.mode == "poll":
            b.drop({"COV_INC_COARSE", "COV_SUB_LOST_SAVE", "COV_LIFETIME_EXPIRED"}, "client polls")
            if cfg.poll_interval > POLL_LIMIT:
                cert = "certain" if f["change_share"] >= 0.3 else "likely"
                b.add("POLL_TOO_SLOW", 3.0, f"client polls every {cfg.poll_interval:.0f} s and the value changed "
                                            f"between {100 * f['change_share']:.0f} % of consecutive polls", cert)
            else:
                b.drop({"POLL_TOO_SLOW"}, f"polling interval {cfg.poll_interval:.0f} s")
        else:
            b.drop({"POLL_TOO_SLOW"}, "client uses COV")
            rec = ev.sub_records.get(ev.target)
            if rec and rec.lifetime > 0 and rec.last_renewal + rec.lifetime <= T:
                b.add("COV_LIFETIME_EXPIRED", 10.0, f"subscription made at t = {rec.last_renewal:.0f} s with "
                                                    f"lifetime {rec.lifetime:.0f} s, never renewed", "certain")
            else:
                b.drop({"COV_LIFETIME_EXPIRED"}, "client subscription still inside its lifetime")
                if "COV_SUB_LOST_SAVE" in b.h and isinstance(dr, (int, float)) and f["last_value"] is not None \
                        and dr != f["last_value"] and not (analog and abs(dr - f["last_value"]) < 1.0):
                    b.add("COV_SUB_LOST_SAVE", 1.5, "subscription valid on the client, value stale", "likely")

    if lv < 3:
        return b.ranked()

    # ---------------------------------------------------------- E4
    cfg_obj = next((o for o in ev.config if o.name == ev.known_names.get(key, ev.target)), None)
    if cfg_obj is None:
        return b.ranked()
    mon_t = [(t, v) for t, g, v in ev.knx_monitor if g == cfg_obj.ga]
    if not present:
        if not cfg_obj.export:
            b.add("NOT_EXPORTED", 10.0, "export flag of the group object is off", "certain")
        elif cfg_obj.kind == "other":
            b.add("TYPE_NOT_EXPORTABLE", 10.0, f"datapoint type {cfg_obj.dpt} is not exported", "certain")
        else:
            n_exp = sum(1 for o in ev.config if o.export and o.kind != "other")
            if ev.limit is not None and n_exp > ev.limit:
                b.add("OVER_LIMIT", 10.0, f"{n_exp} objects flagged for export, limit {ev.limit}; "
                                          "this one is flagged and of an exported type but absent", "certain")
        for c in ("NOT_EXPORTED", "TYPE_NOT_EXPORTABLE", "OVER_LIMIT"):
            if c in b.h and b.h[c].certainty != "certain":
                b.drop({c}, "export configuration rules it out")
        return b.ranked()

    c = ev.knx_priority
    if c is not None and f["cmd_prio"] is not None:
        p = f["cmd_prio"]
        if p == c:
            b.add("CMD_SAME_SLOT", 10.0, f"the controller's object priority is {c}, the slot the BMS commands: "
                                         "KNX values and BMS commands overwrite each other, and a relinquish "
                                         "empties the controller's slot", "certain")
        elif p > c and f["cmd_active"]:
            b.add("CMD_NO_EFFECT", 10.0, f"the controller's object priority is {c}; the BMS commands at {p}, "
                                         "a lower priority, so its command never wins", "certain")
        elif "CMD_SAME_SLOT" in b.h:
            b.drop({"CMD_SAME_SLOT"}, f"the controller writes slot {c}, the BMS slot {p}")

    cfg = ev.client
    if cfg.mode == "cov":
        # KNX changes of at least the COV increment that the client never got (whole window,
        # periods with a BMS command held on the point excluded)
        unseen_big, unseen_small = [], []
        for name, meta in ev.points.items():
            o = next((x for x in ev.config if x.name == name), None)
            lg = ev.history[name]
            if o is None or not lg.updates:
                continue
            for t in unreported(ev, name, o, meta["type"]):
                unseen_big.append((name, t))
                break
        if ev.history[ev.target].updates:
            t0, v0 = ev.history[ev.target].updates[-1]
            unseen_small = [(t, v) for t, v in mon_t if t > t0 + 1 and v != v0 and abs(v - v0) < cfg_obj.cov_increment]
        steps = sorted(abs(b2[1] - a[1]) for a, b2 in zip(mon_t, mon_t[1:]) if b2[1] != a[1])
        med_step = steps[len(steps) // 2] if steps else None
        if unseen_big and "COV_LIFETIME_EXPIRED" not in b.h:
            names = sorted({n for n, _ in unseen_big})
            if len(names) >= 2:
                t_first = min(t for _, t in unseen_big)
                b.add("COV_SUB_LOST_SAVE", 10.0, f"{len(names)} points changed on KNX by more than their COV "
                                                 f"increment allows with no notification (first at t = {t_first:.0f} s), "
                                                 "while the client's subscriptions were valid and the objects "
                                                 "exist", "certain")
                b.drop({"COV_INC_COARSE", "HEALTHY"}, "")
        elif unseen_small and med_step is not None and cfg_obj.cov_increment > med_step and not unseen_big:
            b.add("COV_INC_COARSE", 10.0, f"KNX value moved {len(unseen_small)} times after the last "
                                          f"notification, each time less than the COV increment "
                                          f"{cfg_obj.cov_increment:g} (typical step {med_step:.2f})", "certain")
            b.drop({"HEALTHY", "COV_SUB_LOST_SAVE"}, "")
        elif analog and not unseen_big and cfg_obj.cov_increment > (med_step or NOMINAL_STEP.get(f["kind"], 1.0)) + 1e-9:
            ref = med_step or NOMINAL_STEP.get(f["kind"], 1.0)
            b.add("COV_INC_COARSE", 2.0, f"COV increment {cfg_obj.cov_increment:g} is larger than the "
                                         f"KNX step of the signal ({ref:g}), no missed change seen yet", "likely")
            b.drop({"HEALTHY"}, "COV increment not shown to be fine enough for the signal")
        elif not unseen_big and not unseen_small and not CMD_CLASSES & b.h.keys() \
                and "COV_LIFETIME_EXPIRED" not in b.h:
            b.add("HEALTHY", 10.0, "every KNX change was reported; object exported, one device, "
                                   "subscription valid, no command held", "certain")
            b.drop({"COV_INC_COARSE", "COV_SUB_LOST_SAVE"}, "")
        elif not unseen_big and "COV_SUB_LOST_SAVE" in b.h:
            b.drop({"COV_SUB_LOST_SAVE"}, "no change of at least the increment went unreported")
    else:
        polls = [t for t, _ in ev.history[ev.target].updates]
        missed = 0
        for a, c in zip(polls, polls[1:]):
            vals = [v for t, v in mon_t if a < t < c]
            if len(set(vals)) >= 2 or (vals and len(vals) >= 2):
                missed += 1
        if cfg.poll_interval > POLL_LIMIT and missed:
            b.add("POLL_TOO_SLOW", 10.0, f"{missed} polling intervals contained more than one KNX value", "certain")
        elif cfg.poll_interval <= POLL_LIMIT and not CMD_CLASSES & b.h.keys():
            dr_ok = isinstance(dr, (int, float)) and mon_t and abs(dr - mon_t[-1][1]) < 1e-9
            if dr_ok:
                b.add("HEALTHY", 10.0, f"polling every {cfg.poll_interval:.0f} s, direct read equals the "
                                       "last KNX value, object exported, one device, no command held", "certain")
    return b.ranked()


def command_evidence(ev, f, b) -> None:
    """E3: read the target's Priority_Array against the BMS's own command log.

    Standard rule (B2, B4): the non-empty slot with the lowest number is Present_Value.
    Documented (G2): the controller writes KNX values into one slot of its own. Under P1 the
    BMS is the only other writer, so a non-empty slot that the BMS did not fill is the
    controller's slot.
    """
    pa = ev.priority_array if isinstance(ev.priority_array, list) else [None] * 16
    if f["cmd_prio"] is None:
        b.drop(CMD_CLASSES, "the BMS never commanded the point")
        return
    p = f["cmd_prio"]
    mine = pa[p - 1]
    higher = [(i + 1, v) for i, v in enumerate(pa[:p - 1]) if v is not None]
    lower = [(i + p + 1, v) for i, v in enumerate(pa[p:]) if v is not None]
    if f["cmd_active"] and mine == f["cmd_value"]:
        if higher or lower:
            b.drop({"CMD_SAME_SLOT"}, f"another slot than the BMS's slot {p} is in use")
        if higher:
            q, v = higher[0]
            b.add("CMD_NO_EFFECT", 10.0, f"Priority_Array slot {p} holds the BMS command {mine}, but slot {q} "
                                         f"holds {v}, which the BMS did not write: the controller's KNX slot "
                                         "is above the command, so the command never becomes the value", "certain")
            b.drop({"CMD_MASKS_KNX"}, "a slot above the BMS command is in use")
        elif lower:
            b.add("CMD_MASKS_KNX", 10.0, f"Priority_Array slot {p} holds {mine}, written by the BMS at "
                                         f"t = {f['cmd_t']:.0f} s and never relinquished; slot {lower[0][0]} "
                                         f"below it holds {lower[0][1]}, the controller's KNX slot", "certain")
            b.drop({"CMD_NO_EFFECT"}, "no slot above the BMS command is in use")
        else:
            # no other slot in use: the controller's KNX value can only be in the BMS's own
            # slot (or the arrays were reset since); the object priority setting decides (E4)
            b.add("CMD_SAME_SLOT", 3.0, f"Priority_Array slot {p} holds the BMS command {mine} and no other "
                                        "slot is in use: the controller's KNX slot is likely the same slot",
                  "likely")
            b.drop({"CMD_NO_EFFECT"}, "no slot above the BMS command is in use")
    elif mine is not None and mine != f["cmd_value"]:
        what = "relinquished it" if f["cmd_value"] is None else f"wrote {f['cmd_value']}"
        rd = ev.relinquish_default
        b.add("CMD_SAME_SLOT", 10.0, f"Priority_Array slot {p} holds {mine}, but the BMS last {what} there "
                                     f"at t = {f['cmd_t']:.0f} s: the controller writes its KNX values into the "
                                     f"BMS's own slot (Relinquish_Default {rd})", "certain")
        b.drop({"CMD_MASKS_KNX", "CMD_NO_EFFECT"}, "")
    elif f["cmd_value"] is None and all(v is None for v in pa):
        # the controller's slot holds a value whenever the object has one, so an empty array
        # means its slot was emptied: by this relinquish, or by a reset of all arrays
        b.add("CMD_SAME_SLOT", 3.0, f"every Priority_Array slot is empty after the BMS relinquished slot {p} "
                                    f"at t = {f['cmd_t']:.0f} s: the relinquish likely emptied the controller's "
                                    f"KNX slot; Present_Value is Relinquish_Default {ev.relinquish_default}", "likely")
        b.drop({"CMD_MASKS_KNX", "CMD_NO_EFFECT"}, "")
    else:
        b.drop(CMD_CLASSES, f"Priority_Array slot {p} is empty, as the BMS left it")


def _held(ev, name):
    """Intervals in which a BMS command was held in the point's priority array."""
    out, start = [], None
    for t, n, v, prio, res in ev.commands:
        if n != name:
            continue
        if v is not None and start is None:
            start = t
        elif v is None and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, ev.t_end))
    return out


def unreported(ev, name, o, otype) -> list[float]:
    """Times of KNX changes on the point's group address after which the client's value
    differed from the KNX value by at least twice the COV increment (any change for binary)."""
    ups = ev.history[name].updates
    times = [t for t, _ in ups]
    held = _held(ev, name)
    out = []
    for t, g, v in ev.knx_monitor:
        if g != o.ga or t < times[0] or any(a - 1 <= t <= b + 1 for a, b in held):
            continue
        i = bisect.bisect_right(times, t + 1e-6) - 1      # notifications carry the telegram time
        shown = ups[i][1]
        # a renewal shows the current value without moving the server's reference, so a
        # working subscription keeps |value - shown| below twice the increment (premise P3)
        if (v != shown) if otype == BV else abs(v - shown) >= 2 * o.cov_increment - 1e-9:
            out.append(t)
    return out


def top(ev, level: str) -> tuple[str, str]:
    r = diagnose(ev, level)
    return (r[0].cls, r[0].certainty) if r else ("NONE", "possible")


def report(ev, level: str, n: int = 4) -> str:
    """Plain-text report of the ranked hypotheses at one level, with their evidence lines."""
    lines = [f"Evidence level {level}, complaint point '{ev.target}'"]
    ranked = diagnose(ev, level)
    if not ranked:
        return lines[0] + "\n  no hypothesis"
    for h in ranked[:n]:
        lines.append(f"  {h.cls:<22} {h.certainty:<8} score {h.score:5.1f}")
        for w in h.why:
            if w:
                lines.append(f"      - {w}")
    return "\n".join(lines)
