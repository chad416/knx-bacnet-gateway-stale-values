"""COV trade study, COV against polling, and the effect of saving the COV settings.

Everything here replays KNX telegram sequences from `knx.py` through the COV rule of
`server.py` (or through polling) and compares what the client shows with the true value.
"""
from __future__ import annotations

import math
import random

import numpy as np

from . import knx
from .server import AV, BV, BacnetServer, KnxObject

DAY = 24 * 3600.0


# ------------------------------------------------------------------ replays
def cov_replay(events, inc: float | None, renew: float | None = None, t_end: float | None = None):
    """Client view under COV. inc None = binary object (every change is reported).
    Same rule as BObject.cov_due: report when |v - last reported| >= inc. A renewal sends
    the current value but does not move the server's reference value."""
    shown = [events[0]]
    ref = events[0][1]
    cur = events[0][1]
    nxt_renew = renew if renew else math.inf
    for t, v in events[1:]:
        while nxt_renew <= t:
            if shown[-1][1] != cur:
                shown.append((nxt_renew, cur))
            nxt_renew += renew
        cur = v
        due = (v != ref) if inc is None else abs(v - ref) >= inc - 1e-9
        if due:
            ref = v
            shown.append((t, v))
    if renew and t_end:
        while nxt_renew <= t_end:
            if shown[-1][1] != cur:
                shown.append((nxt_renew, cur))
            nxt_renew += renew
    return shown


def count_notifications(events, inc: float | None) -> int:
    ref, n = events[0][1], 0
    for _, v in events[1:]:
        if (v != ref) if inc is None else abs(v - ref) >= inc - 1e-9:
            ref, n = v, n + 1
    return n


def poll_replay(events, period: float, t_end: float, phase: float = 0.0):
    ts = np.array([t for t, _ in events])
    vs = np.array([v for _, v in events])
    shown = []
    t = phase
    while t <= t_end:
        i = np.searchsorted(ts, t, side="right") - 1
        shown.append((t, float(vs[max(i, 0)])))
        t += period
    return shown


def compare(events, shown, t_end: float) -> dict:
    """missed: share of KNX value changes whose value the client never showed while it was
    current. stale_max: longest time the client showed a value other than the current one.
    err_max: largest difference between shown and true value."""
    changes = [(t, v) for (t, v), (_, p) in zip(events[1:], events) if v != p]
    # merged step functions
    pts = sorted({t for t, _ in events} | {t for t, _ in shown} | {t_end})
    ei = si = 0
    true_v, show_v = events[0][1], shown[0][1]
    stale_start, stale_max, err_max = None, 0.0, 0.0
    seen_in = {}
    seg_t, seg_e, prev_t = [], [], None
    for t in pts:
        if prev_t is not None:
            seg_t.append(t - prev_t)
            seg_e.append(abs(true_v - show_v))
        prev_t = t
        while ei < len(events) and events[ei][0] <= t:
            true_v = events[ei][1]
            ei += 1
        while si < len(shown) and shown[si][0] <= t:
            show_v = shown[si][1]
            si += 1
        err_max = max(err_max, abs(true_v - show_v))
        if show_v != true_v and stale_start is None:
            stale_start = t
        elif show_v == true_v and stale_start is not None:
            stale_max = max(stale_max, t - stale_start)
            stale_start = None
        if show_v == true_v:
            seen_in[ei - 1] = True
    if stale_start is not None:
        stale_max = max(stale_max, t_end - stale_start)
    idx_changes = [i for i in range(1, len(events)) if events[i][1] != events[i - 1][1]]
    missed = sum(1 for i in idx_changes if i not in seen_in)
    return {"changes": len(changes), "missed": missed,
            "missed_share": missed / max(1, len(changes)),
            "stale_max_s": stale_max, "err_max": err_max, "seg_t": seg_t, "seg_e": seg_e}


def weighted_quantile(durations, errors, q: float) -> float:
    if not durations:
        return 0.0
    order = np.argsort(errors)
    e = np.asarray(errors)[order]
    w = np.cumsum(np.asarray(durations)[order])
    return float(e[min(len(e) - 1, np.searchsorted(w, q * w[-1]))])


# ------------------------------------------------------------------ signals
SIGNALS = {"temperature": dict(kind="temperature", binary=False, unit="K"),
           "power": dict(kind="power", binary=False, unit="W"),
           "switch": dict(kind="switch", binary=True, unit="")}
INCREMENTS = {"temperature": [0.1, 0.2, 0.3, 0.5, 1.0, 2.0],
              "power": [1.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0]}
POLL_PERIODS = [10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 900.0, 1800.0, 3600.0]


def signal_set(kind: str, seeds: int, t_end: float = DAY, p: knx.SignalParams | None = None):
    return [knx.generate(kind, random.Random(1000 + s), t_end, p) for s in range(seeds)]


def _agg(rows: list[dict]) -> dict:
    ch = sum(r["changes"] for r in rows)
    return {"changes_per_h": ch / len(rows) / 24.0,
            "missed_share": sum(r["missed"] for r in rows) / max(1, ch),
            "stale_max_s": max(r["stale_max_s"] for r in rows),
            "stale_max_median_s": float(np.median([r["stale_max_s"] for r in rows])),
            "err_max": max(r["err_max"] for r in rows),
            "err_p99": weighted_quantile([d for r in rows for d in r["seg_t"]],
                                         [e for r in rows for e in r["seg_e"]], 0.99),
            "err_mean": float(np.average([e for r in rows for e in r["seg_e"]],
                                         weights=[d for r in rows for d in r["seg_t"]]))}


def increment_sweep(seeds: int = 20, renew: float | None = None, p=None) -> dict:
    out = {}
    for name, inc_list in INCREMENTS.items():
        sigs = signal_set(name, seeds, DAY, p)
        rows = []
        for inc in inc_list:
            res = [compare(e, cov_replay(e, inc, renew, DAY), DAY) for e in sigs]
            notes = sum(count_notifications(e, inc) for e in sigs) / len(sigs) / 24.0
            a = _agg(res)
            a.update({"increment": inc, "notifications_per_h": notes})
            rows.append(a)
        out[name] = rows
    sigs = signal_set("switch", seeds, DAY, p)
    res = [compare(e, cov_replay(e, None, renew, DAY), DAY) for e in sigs]
    a = _agg(res)
    a.update({"increment": None, "notifications_per_h": sum(count_notifications(e, None) for e in sigs) / len(sigs) / 24.0})
    out["switch"] = [a]
    return out


def polling_sweep(seeds: int = 20, p=None) -> dict:
    out = {}
    for name in ("temperature", "power", "switch"):
        sigs = signal_set(name, seeds, DAY, p)
        rows = []
        for P in POLL_PERIODS:
            res = [compare(e, poll_replay(e, P, DAY, phase=random.Random(i).uniform(0, P)), DAY)
                   for i, e in enumerate(sigs)]
            a = _agg(res)
            a.update({"period_s": P, "requests_per_h": 3600.0 / P})
            rows.append(a)
        out[name] = rows
    return out


def crossover(poll_rows, cov_row, key: str = "missed_share") -> float | None:
    """Shortest polling period at which polling is worse than COV in `key`
    (missed_share: share of changes never shown; err_p99: 99th percentile of the error)."""
    for r in poll_rows:
        if r[key] > cov_row[key] + 1e-12:
            return r["period_s"]
    return None


# ------------------------------------------------------------------ analytic check
def brownian_check(sigma: float = 0.004, incs=(0.05, 0.1, 0.2, 0.4), t_end: float = 20 * DAY,
                   dt: float = 1.0, seed: int = 7) -> list[dict]:
    """For a driftless random walk with diffusion sigma (unit per sqrt(s)) the mean time to
    move by +-a from the last reported value is a^2 / sigma^2 (first exit time of Brownian
    motion from an interval). The COV rule must reproduce this rate."""
    rng = np.random.default_rng(seed)
    n = int(t_end / dt)
    x = np.cumsum(rng.normal(0, sigma * math.sqrt(dt), n))
    out = []
    for a in incs:
        ref, cnt = 0.0, 0
        for v in x:
            if abs(v - ref) >= a:
                ref = v
                cnt += 1
        sim = cnt / t_end
        theory = sigma ** 2 / a ** 2
        out.append({"increment": a, "rate_sim_per_h": sim * 3600, "rate_theory_per_h": theory * 3600,
                    "relative_error": sim / theory - 1})
    return out


# ------------------------------------------------------------------ traffic
MIX = {"temperature": 0.35, "power": 0.10, "switch": 0.35, "quiet": 0.20}   # assumption A10
QUIET_PER_H = 1.0                      # notifications per hour of a setpoint / blind object


def traffic(sweep: dict, counts=(150, 500, 2000), choices=None) -> dict:
    """Notification messages per second for an installation of n exported objects."""
    choices = choices or {"fine (0.1 K, 10 W)": (0.1, 10.0), "default 1 (1 K, 1 W)": (1.0, 1.0),
                          "coarse (0.5 K, 100 W)": (0.5, 100.0)}
    rate = {name: {r["increment"]: r["notifications_per_h"] for r in sweep[name]}
            for name in ("temperature", "power")}
    sw = sweep["switch"][0]["notifications_per_h"]
    out = {}
    for label, (ti, pi) in choices.items():
        per_obj_h = (MIX["temperature"] * rate["temperature"][ti] + MIX["power"] * rate["power"][pi]
                     + MIX["switch"] * sw + MIX["quiet"] * QUIET_PER_H)
        out[label] = {str(n): {"unconfirmed_msg_per_s": n * per_obj_h / 3600.0,
                               "confirmed_msg_per_s": 2 * n * per_obj_h / 3600.0}
                      for n in counts}
        out[label]["per_object_per_h"] = per_obj_h
    out["polling_60s_rp"] = {str(n): 2 * n / 60.0 for n in counts}
    out["polling_60s_rpm_20"] = {str(n): 2 * math.ceil(n / 20) / 60.0 for n in counts}
    return out


# ------------------------------------------------------------------ saving the COV settings
def save_study(renew_options=(300.0, 900.0, 1800.0, 3600.0, 14400.0, None), n_objects: int = 150,
               runs: int = 20, seed: int = 3) -> list[dict]:
    """A running BMS with COV subscriptions to n objects; the COV settings are saved at a
    random moment. Measured: how long values stay stale, how many KNX changes the BMS never
    sees, and what happens to a BMS command held in the priority array."""
    out = []
    t_end = 6 * 3600.0
    for R in renew_options:
        stale, lost, changes, cmd_unseen, renew_msgs = [], 0, 0, [], 0
        for k in range(runs):
            rng = random.Random(seed * 1000 + k)
            kinds = rng.choices(["temperature", "power", "switch", "setpoint"],
                                weights=[35, 10, 35, 20], k=n_objects)
            objs, sigs = [], {}
            for i, kind in enumerate(kinds):
                ga = knx.format_ga(1 + i // 256, 0, i % 256)
                inc = {"temperature": 0.1, "setpoint": 0.1, "power": 10.0}.get(kind, 1.0)
                objs.append(KnxObject(f"o{i}", ga, knx.SIGNAL_DPT[kind], True, inc))
                sigs[ga] = knx.generate(kind, rng, t_end)
            srv = BacnetServer(100, "gw", objs, None, "large")
            for ga, ev in sigs.items():
                srv.knx_telegram(0.0, ga, ev[0][1])
            keys = {o.ga: (AV if o.kind == "numeric" else BV, knx.ga_to_instance(o.ga)) for o in objs}
            shown = {}
            lifetime = 0.0 if R is None else 2 * R
            for ga, key in keys.items():
                _, notes = srv.subscribe_cov(1.0, "bms", 1, key, False, lifetime)
                shown[ga] = notes[0].value
            sp_ga = next(o.ga for o in objs if o.dpt == "9.001" and o.name and
                         kinds[int(o.name[1:])] == "setpoint")
            t_save = rng.uniform(2 * 3600, 3 * 3600)
            t_cmd = t_save - 600
            events = sorted((t, ga, v) for ga, ev in sigs.items() for t, v in ev[1:])
            events.append((t_cmd, "cmd", 24.0))
            events.append((t_save, "save", None))
            if R:
                t = 1.0 + R
                while t < t_end:
                    events.append((t, "renew", None))
                    t += R
            events.sort(key=lambda e: e[0])
            true = {ga: ev[0][1] for ga, ev in sigs.items()}
            stale_since = {}
            restored = None
            for t, ga, v in events:
                if ga == "cmd":
                    _, _, notes = srv.write_property(t, keys[sp_ga], "present-value", v, 8)
                    for n in notes:
                        shown[sp_ga] = n.value
                elif ga == "save":
                    srv.save_cov_settings()
                elif ga == "renew":
                    renew_msgs += 2 * len(keys)
                    for g, key in keys.items():
                        _, notes = srv.subscribe_cov(t, "bms", 1, key, False, lifetime)
                        shown[g] = notes[0].value
                        if g in stale_since and t > t_save:
                            stale.append(t - stale_since.pop(g))
                    if restored is None and t > t_save:
                        restored = t
                else:
                    true[ga] = v
                    if t > t_save:
                        changes += 1
                    notes = srv.knx_telegram(t, ga, v)
                    if notes:
                        shown[ga] = notes[0].value
                    elif t > t_save and srv.active_subscriptions(t) == 0:
                        lost += 1
                        stale_since.setdefault(ga, t)
            for g, t0 in stale_since.items():
                stale.append(t_end - t0)
            # the BMS command was reset at t_save; the BMS learns it at the first renewal
            cmd_unseen.append((restored or t_end) - t_save)
        out.append({"renew_s": R, "objects": n_objects,
                    "stale_mean_s": float(np.mean(stale)) if stale else 0.0,
                    "stale_max_s": float(np.max(stale)) if stale else 0.0,
                    "changes_lost_share": lost / max(1, changes),
                    "command_reset_unseen_mean_s": float(np.mean(cmd_unseen)),
                    "renewal_msg_per_s": (2 * n_objects / R) if R else 0.0})
    return out
