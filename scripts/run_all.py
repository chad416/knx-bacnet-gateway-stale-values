"""Regenerate everything in results/: summary.json, tables.md, the figures and the support case.

    python scripts/run_all.py            full run (2 to 4 minutes)
    python scripts/run_all.py --quick    reduced run for CI, writes summary_quick.json only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib                                     # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt                       # noqa: E402
import numpy as np                                    # noqa: E402

from bacgw import covstudy as C                       # noqa: E402
from bacgw import knx                                 # noqa: E402
from bacgw.diagnose import LEVELS, report             # noqa: E402
from bacgw.evaluate import evaluate, lookalikes, seed_of   # noqa: E402
from bacgw.sim import BMS_PRIORITY, DESCRIPTION, FAULTS, KNX_PRIORITY_CHOICES, Installation   # noqa: E402

RES = ROOT / "results"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
SEQ = matplotlib.colors.LinearSegmentedColormap.from_list("seq", ["#f3f7fd", "#9ec5f4", "#2a78d6", "#0d366b"])
SHORT = {"HEALTHY": "healthy", "COV_INC_COARSE": "COV incr. too coarse", "COV_SUB_LOST_SAVE": "subs lost (COV save)",
         "COV_LIFETIME_EXPIRED": "lifetime expired", "NOT_EXPORTED": "not exported",
         "TYPE_NOT_EXPORTABLE": "type not exportable", "OVER_LIMIT": "over export limit",
         "GA_CHANGED": "group address changed", "CMD_MASKS_KNX": "command masks KNX",
         "CMD_NO_EFFECT": "command has no effect", "CMD_SAME_SLOT": "command in KNX slot",
         "DUP_DEVICE": "duplicate device inst.", "POLL_TOO_SLOW": "polling too slow"}


def style(ax, xlabel="", ylabel="", title=""):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlabel(xlabel, color=INK2, fontsize=10)
    ax.set_ylabel(ylabel, color=INK2, fontsize=10)
    if title:
        ax.set_title(title, color=INK, fontsize=11, loc="left")


def rnd(obj, nd=4):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {str(k): rnd(v, nd) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [rnd(v, nd) for v in obj]
    return obj


# ------------------------------------------------------------------ diagnosis
def diag_section(quick: bool) -> dict:
    runs = 20 if quick else 100
    d = evaluate(runs)
    d["installations"] = runs * len(FAULTS)
    d["lookalikes"] = {lv: lookalikes(d["confusion"][lv], 0.2) for lv in LEVELS}
    d["certain_total"] = {"right": sum(v["right"] for v in d["certain_verdicts"].values()),
                          "wrong": sum(v["wrong"] for v in d["certain_verdicts"].values())}
    d["descriptions"] = DESCRIPTION
    # first level at which a fault is right and certain in at least 90 % of runs
    d["first_certain_level"] = {f: next((lv for lv in LEVELS if d["certain_and_right"][lv][f] >= 0.9), None)
                                for f in FAULTS}
    # sensitivity: length of the observed window
    sruns = 10 if quick else 40
    d["window_sensitivity"] = {}
    for hours in (1, 8):
        s = evaluate(sruns, t_end=hours * 3600.0, salt=f"w{hours}")
        d["window_sensitivity"][f"{hours}h"] = {"runs_per_class": sruns, "mean_recall": s["mean_recall"],
                                               "certain_verdicts": s["certain_verdicts"],
                                               "recall_E1": s["recall"]["E1"]}
    return d


def fig_diag(d: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 6.2), facecolor=SURFACE, sharey=True)
    for ax, key, title in ((axes[0], "recall", "Right class ranked first"),
                           (axes[1], "certain_and_right", "Right class and reported as certain")):
        m = np.array([[d[key][lv][f] for lv in LEVELS] for f in FAULTS])
        ax.imshow(m, cmap=SEQ, vmin=0, vmax=1, aspect="auto")
        for i in range(len(FAULTS)):
            for j in range(len(LEVELS)):
                ax.text(j, i, f"{100 * m[i, j]:.0f}", ha="center", va="center", fontsize=9,
                        color="white" if m[i, j] > 0.55 else INK)
        ax.set_xticks(range(len(LEVELS)), ["E1\nhistory", "E2\n+ direct read", "E3\n+ object list,\nclient state",
                                           "E4\n+ export config,\nKNX monitor"], fontsize=8.5, color=INK2)
        ax.set_yticks(range(len(FAULTS)), [SHORT[f] for f in FAULTS], fontsize=9, color=INK2)
        ax.set_title(title + " (% of runs)", color=INK, fontsize=11, loc="left")
        for s in ax.spines.values():
            s.set_visible(False)
    fig.suptitle(f"Diagnosis by evidence level, {d['runs_per_class']} virtual installations per class",
                 color=INK, fontsize=12, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(RES / "diagnosis_by_evidence.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(14.5, 7.0), facecolor=SURFACE)
    for ax, lv in zip(axes, ("E1", "E2")):
        cls = FAULTS + ["NONE"]
        m = np.array([[d["confusion"][lv][f].get(g, 0) for g in FAULTS] for f in FAULTS])
        ax.imshow(m, cmap=SEQ, vmin=0, vmax=d["runs_per_class"], aspect="auto")
        for i in range(len(FAULTS)):
            for j in range(len(FAULTS)):
                if m[i, j]:
                    ax.text(j, i, str(m[i, j]), ha="center", va="center", fontsize=8,
                            color="white" if m[i, j] > 0.55 * d["runs_per_class"] else INK)
        ax.set_xticks(range(len(FAULTS)), [SHORT[f] for f in FAULTS], rotation=60, ha="right", fontsize=8, color=INK2)
        ax.set_yticks(range(len(FAULTS)), [SHORT[f] for f in FAULTS], fontsize=8, color=INK2)
        ax.set_xlabel("ranked first by the engine", color=INK2)
        ax.set_ylabel("injected fault", color=INK2)
        ax.set_title({"E1": "E1: value history only", "E2": "E2: plus one direct ReadProperty"}[lv],
                     color=INK, fontsize=11, loc="left")
        for s in ax.spines.values():
            s.set_visible(False)
    fig.tight_layout()
    fig.savefig(RES / "confusion_matrices.png", dpi=150)
    plt.close(fig)


# ------------------------------------------------------------------ COV studies
def cov_section(quick: bool) -> dict:
    seeds = 5 if quick else 20
    out = {"seeds": seeds, "days_per_seed": 1, "signal_params": knx.SignalParams().__dict__}
    out["brownian_check"] = C.brownian_check(t_end=(3 if quick else 20) * C.DAY)
    out["increment_sweep"] = C.increment_sweep(seeds)
    out["increment_sweep_renew_1800s"] = C.increment_sweep(seeds, renew=1800.0)
    out["polling_sweep"] = C.polling_sweep(seeds)
    out["traffic"] = C.traffic(out["increment_sweep"])
    out["traffic_mix_assumption"] = C.MIX
    sens = {}
    for label, p in (("temp_std_0.125K", knx.SignalParams(temp_std=0.125)),
                     ("temp_std_0.5K", knx.SignalParams(temp_std=0.5)),
                     ("temp_sensor_delta_0.2K", knx.SignalParams(temp_send_delta=0.2)),
                     ("switch_gap_300s", knx.SignalParams(switch_mean_gap=300.0))):
        sens[label] = {"increment_sweep": C.increment_sweep(seeds, p=p), "polling_sweep": C.polling_sweep(seeds, p=p)}
    out["sensitivity"] = sens
    # COV against polling
    inc = {r["increment"]: r for r in out["increment_sweep"]["temperature"]}
    pinc = {r["increment"]: r for r in out["increment_sweep"]["power"]}
    ps = out["polling_sweep"]
    cmp = {}
    for name, row, rows in (("temperature_0.1K", inc[0.1], ps["temperature"]),
                            ("temperature_0.2K", inc[0.2], ps["temperature"]),
                            ("temperature_0.5K", inc[0.5], ps["temperature"]),
                            ("temperature_1K", inc[1.0], ps["temperature"]),
                            ("power_10W", pinc[10.0], ps["power"]),
                            ("power_50W", pinc[50.0], ps["power"]),
                            ("switch", out["increment_sweep"]["switch"][0], ps["switch"])):
        cmp[name] = {"cov_missed_share": row["missed_share"], "cov_err_p99": row["err_p99"],
                     "cov_notifications_per_h": row["notifications_per_h"],
                     "poll_worse_missed_from_s": C.crossover(rows, row, "missed_share"),
                     "poll_worse_err_p99_from_s": C.crossover(rows, row, "err_p99")}
    out["cov_vs_polling"] = cmp
    for label, s in sens.items():
        r = {x["increment"]: x for x in s["increment_sweep"]["temperature"]}
        out["sensitivity"][label]["cov_vs_polling_temperature_0.2K"] = {
            "poll_worse_missed_from_s": C.crossover(s["polling_sweep"]["temperature"], r[0.2], "missed_share"),
            "poll_worse_err_p99_from_s": C.crossover(s["polling_sweep"]["temperature"], r[0.2], "err_p99")}
    out["strict_rule_check"] = C.strict_rule_check(seeds)
    out["save_study"] = C.save_study(runs=5 if quick else 20)
    out["save_fallback"] = C.fallback_after_save(runs=5 if quick else 20)
    out["save_study_priorities"] = {"bms_command": C.BMS_CMD_PRIORITY, "controller_slot": C.SAVE_KNX_PRIORITY}
    return out


def fig_cov(c: dict) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.4), facecolor=SURFACE)
    for ax, name, unit in ((axes[0], "temperature", "K"), (axes[1], "power", "W")):
        rows = c["increment_sweep"][name]
        x = [r["increment"] for r in rows]
        ax.plot(x, [100 * r["missed_share"] for r in rows], "o-", color=BLUE, label="changes never shown (%)")
        rr = c["increment_sweep_renew_1800s"][name]
        ax.plot(x, [100 * r["missed_share"] for r in rr], "o--", color=AQUA,
                label="same, client renews every 1800 s")
        ax2 = ax.twinx()
        ax2.plot(x, [r["notifications_per_h"] for r in rows], "s-", color=ORANGE, label="notifications per hour")
        ax2.set_yscale("log")
        ax2.tick_params(colors=ORANGE, labelsize=9)
        ax2.set_ylabel("notifications per object per hour", color=ORANGE, fontsize=9)
        ax.set_xscale("log")
        style(ax, f"COV increment ({unit})", "changes never shown (%)",
              f"{name}: what the client misses, what it costs")
        ax.set_ylim(-3, 103)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=8, frameon=False, loc="upper center",
                  bbox_to_anchor=(0.5, -0.17), ncol=2)
    ax = axes[2]
    t = c["traffic"]
    counts = ["150", "500", "2000"]
    labels = [k for k in t if not k.startswith("polling")]
    w = 0.18
    cols = [BLUE, ORANGE, AQUA, YELLOW]
    for i, k in enumerate(labels):
        ax.bar(np.arange(3) + (i - 1.5) * w, [t[k][n]["unconfirmed_msg_per_s"] for n in counts], w,
               color=cols[i], label="COV " + k)
    ax.bar(np.arange(3) + 1.5 * w, [t["polling_60s_rpm_20"][n] for n in counts], w, color=MUTED,
           label="polling 60 s, 20 per ReadPropertyMultiple")
    ax.set_xticks(range(3), [f"{n} objects" for n in counts])
    style(ax, "", "messages per second", "Notification traffic (unconfirmed)")
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(RES / "cov_tradeoff.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6), facecolor=SURFACE)
    ax = axes[0]
    for name, col in (("temperature", BLUE), ("power", ORANGE), ("switch", AQUA)):
        rows = c["polling_sweep"][name]
        ax.plot([r["period_s"] for r in rows], [100 * r["missed_share"] for r in rows], "o-", color=col,
                label=f"{name}, polling")
    ci = {r["increment"]: r for r in c["increment_sweep"]["temperature"]}
    for inc, ls in ((0.2, ":"), (0.5, "--")):
        ax.axhline(100 * ci[inc]["missed_share"], color=BLUE, ls=ls, lw=1.2,
                   label=f"temperature, COV {inc} K")
    ax.set_xscale("log")
    style(ax, "polling interval (s)", "changes never shown (%)", "Polling against COV: missed changes")
    ax.legend(fontsize=8, frameon=False)
    ax = axes[1]
    rows = c["save_study"]
    lab = [("never" if r["renew_s"] is None else f"{r['renew_s']:.0f} s") for r in rows]
    ax.bar(range(len(rows)), [r["stale_mean_s"] / 60 for r in rows], color=BLUE, label="mean stale time")
    ax.plot(range(len(rows)), [r["stale_max_s"] / 60 for r in rows], "o", color=ORANGE, label="longest stale time")
    ax.set_xticks(range(len(rows)), lab)
    ax.set_yscale("log")
    style(ax, "client renewal interval", "minutes", "After the COV settings are saved")
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(RES / "polling_and_save.png", dpi=150)
    plt.close(fig)


# ------------------------------------------------------------------ support case
def support_case() -> dict:
    """First COV_SUB_LOST_SAVE installation in which a BMS command was held in the priority
    array when the COV settings were saved."""
    for i in range(400):
        inst = Installation("COV_SUB_LOST_SAVE", seed_of("COV_SUB_LOST_SAVE", i, "case"))
        ev = inst.run()
        t_save = ev.truth["t_fault"]
        cmds = ev.commands
        held = [c for c in cmds if c[2] is not None and c[0] < t_save]
        rel = [c for c in cmds if c[2] is None and c[0] > t_save]
        if held and rel and ev.client.lifetime == 0.0 and len(ev.points) >= 14:
            break
    txt = [f"Support case evidence (simulated installation, seed index {i})", ""]
    for lv in LEVELS:
        txt.append(report(ev, lv, 3))
        txt.append("")
    (RES / "support_case.txt").write_text("\n".join(txt), encoding="utf-8")
    p = ev.history[ev.target]
    held_p = held[0][1]
    ph = ev.history[held_p]
    hobj = inst.gw.by_ga[inst.obj_by_name[held_p].ga]
    knx_held = [(t, v) for t, g, v in ev.knx_monitor if g == hobj.ga]
    case = {"seed_index": i, "target": ev.target, "target_kind": ev.points[ev.target]["kind"],
            "points": len(ev.points), "t_save_s": round(t_save, 1), "client": ev.client.__dict__,
            "last_update_target": rnd(p.updates[-1]), "direct_read": ev.direct_read,
            "held_point": held_p, "held_command": rnd(held[0]), "relinquish": rnd(rel[0]),
            "held_point_bms_last": rnd(ph.updates[-1]),
            "held_point_relinquish_default": hobj.relinquish_default,
            "held_point_present_value_end": hobj.present_value,
            "held_point_knx_after_save": rnd([x for x in knx_held if x[0] > t_save]),
            "held_point_knx_before_save": rnd([x for x in knx_held if x[0] <= t_save][-1:]),
            "knx_priority": ev.knx_priority,
            "messages": ev.truth["server_messages"]}
    return case


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    RES.mkdir(exist_ok=True)
    summary = {"note": "Simulated study, no hardware. Produced by scripts/run_all.py.",
               "quick": a.quick}
    # model constants that the README quotes (sources and assumptions in DOSSIER 7.1)
    summary["constants"] = {
        "window_h": 4, "priority_levels": 16, "manual_operator_priority": 8,
        "export_limit_small": 150, "recommended_large": 2000, "recommended_older_hardware": 500,
        "recommendation_change_interval_s": 60, "object_counts": [150, 500, 2000],
        "bacnet_port_default": 47808, "knx_priority_choices": KNX_PRIORITY_CHOICES,
        "bms_priority": BMS_PRIORITY,
        "default_cov_increment": 1, "poll_limit_s": 300, "max_subscriptions_default": 256,
        "fault_classes": len(FAULTS) - 1, "classes_with_healthy": len(FAULTS),
        "instance_example": {"ga": "1/1/1", "instance": knx.ga_to_instance("1/1/1"),
                             "factors": [2048, 256, 1]},
        "temp_sensor_delta_K": 0.1, "power_meter_delta_W": 10, "evidence_levels": 4,
        "renewal_options_s": [300, 900, 1800, 3600, 14400],
        "polling_periods_s": C.POLL_PERIODS, "lifetime_example_s": 28800,
    }
    summary["diagnosis"] = diag_section(a.quick)
    print(f"diagnosis done {time.time() - t0:.0f} s", flush=True)
    summary["cov"] = cov_section(a.quick)
    print(f"COV studies done {time.time() - t0:.0f} s", flush=True)
    if not a.quick:
        summary["support_case"] = support_case()
        fig_diag(summary["diagnosis"])
        fig_cov(summary["cov"])
    summary["runtime_s"] = round(time.time() - t0, 1)
    out = RES / ("summary_quick.json" if a.quick else "summary.json")
    out.write_text(json.dumps(rnd(summary), indent=1), encoding="utf-8")
    if not a.quick:
        import make_tables
        (RES / "tables.md").write_text(make_tables.build(json.loads(out.read_text(encoding="utf-8"))),
                                       encoding="utf-8")
    print(f"wrote {out.name} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
