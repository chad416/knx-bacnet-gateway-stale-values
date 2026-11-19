"""Confusion matrices of the diagnosis engine over randomised virtual installations."""
from __future__ import annotations

import zlib
from collections import Counter, defaultdict

from .diagnose import LEVELS, diagnose
from .sim import FAULTS, simulate


def seed_of(fault: str, i: int, salt: str = "") -> int:
    return zlib.crc32(f"{salt}{fault}-{i}".encode())


def evaluate(runs: int, t_end: float | None = None, salt: str = "") -> dict:
    conf = {lv: {f: Counter() for f in FAULTS} for lv in LEVELS}
    certain = {lv: {"right": 0, "wrong": 0} for lv in LEVELS}
    cert_right = {lv: Counter() for lv in LEVELS}
    examples = defaultdict(dict)
    wrong_cases = []
    mode = Counter()
    for f in FAULTS:
        for i in range(runs):
            kw = {"t_end": t_end} if t_end else {}
            ev = simulate(f, seed_of(f, i, salt), **kw)
            mode[(f, ev.client.mode)] += 1
            for lv in LEVELS:
                r = diagnose(ev, lv)
                top = r[0] if r else None
                cls = top.cls if top else "NONE"
                conf[lv][f][cls] += 1
                if top and top.certainty == "certain":
                    if cls == f:
                        certain[lv]["right"] += 1
                        cert_right[lv][f] += 1
                        examples[f].setdefault(lv, top.why[-1])
                    else:
                        certain[lv]["wrong"] += 1
                        wrong_cases.append({"fault": f, "run": i, "level": lv, "said": cls})
    recall = {lv: {f: conf[lv][f][f] / runs for f in FAULTS} for lv in LEVELS}
    cshare = {lv: {f: cert_right[lv][f] / runs for f in FAULTS} for lv in LEVELS}
    return {"runs_per_class": runs, "faults": FAULTS, "levels": LEVELS,
            "confusion": {lv: {f: dict(conf[lv][f]) for f in FAULTS} for lv in LEVELS},
            "recall": recall, "certain_and_right": cshare, "certain_verdicts": certain,
            "wrong_certain_cases": wrong_cases[:20],
            "first_certain_reason": {f: examples[f] for f in FAULTS},
            "client_mode": {f"{f}|{m}": n for (f, m), n in sorted(mode.items())},
            "mean_recall": {lv: sum(recall[lv].values()) / len(FAULTS) for lv in LEVELS}}


def lookalikes(conf_level: dict, threshold: float = 0.2) -> list:
    """Pairs (true, said, share) where a fault is ranked as another one in >= threshold of runs."""
    out = []
    for f, row in conf_level.items():
        n = sum(row.values())
        for g, k in row.items():
            if g != f and k / n >= threshold:
                out.append((f, g, round(k / n, 3)))
    return sorted(out, key=lambda x: -x[2])
