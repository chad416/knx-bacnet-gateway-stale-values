"""Fail if a number in README.md cannot be found in results/summary.json.

Every number token in README.md (outside code blocks and links) must equal a value of
summary.json under one of a few fixed presentations: the value itself, as a percentage
(x 100), in minutes (/ 60) or in hours (/ 3600), rounded to 0 to 3 decimals. Numbers that
are only names (E1..E4, R-xx, A-xx, file names, the column labels of a table) are not
tokens because they touch a letter.

    python scripts/check_readme_numbers.py [README.md] [results/summary.json]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"(?<![\w./\\-])(\d+(?:\.\d+)?)(?![\w/\\]|\.\d)")


def values(obj):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from values(v)
            try:
                yield float(k)
            except ValueError:
                pass
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from values(v)


def presentations(vals) -> set[str]:
    out = set()
    for v in vals:
        for x in (abs(v), 100 * abs(v), abs(v) / 60, abs(v) / 3600):
            for nd in range(4):
                s = f"{x:.{nd}f}"
                out.add(s)
                if "." in s:
                    out.add(s.rstrip("0").rstrip("."))
    return out


def readme_tokens(text: str) -> list[tuple[int, str]]:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"\]\([^)]*\)", "]", text)          # link targets
    text = re.sub(r"`[^`]*`", "", text)               # inline code
    toks = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in TOKEN.finditer(line):
            toks.append((n, m.group(1)))
    return toks


def check(readme: Path, summary: Path) -> list[tuple[int, str]]:
    d = json.loads(summary.read_text(encoding="utf-8"))
    ok = presentations(values(d))
    return [(n, t) for n, t in readme_tokens(readme.read_text(encoding="utf-8")) if t not in ok]


def main() -> int:
    readme = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "README.md"
    summary = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "results" / "summary.json"
    bad = check(readme, summary)
    for n, t in bad:
        print(f"README line {n}: {t} not found in {summary.name}")
    print(f"{len(readme_tokens(readme.read_text(encoding='utf-8')))} numbers checked, {len(bad)} not found")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
