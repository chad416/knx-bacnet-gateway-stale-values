"""Every number in README.md must come from results/summary.json."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "results" / "summary.json"
sys.path.insert(0, str(ROOT / "scripts"))

import check_readme_numbers as C  # noqa: E402

pytestmark = pytest.mark.skipif(not SUMMARY.exists(), reason="results/summary.json not generated")


def test_every_readme_number_is_in_summary():
    bad = C.check(ROOT / "README.md", SUMMARY)
    assert bad == [], bad


def test_checker_catches_a_wrong_number(tmp_path):
    fake = tmp_path / "README.md"
    fake.write_text("The answer is 123456.789 objects.", encoding="utf-8")
    assert C.check(fake, SUMMARY) == [(1, "123456.789")]


def test_headline_claims_hold():
    d = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert d["diagnosis"]["certain_total"]["wrong"] == 0
    assert all(v["wrong"] == 0 for v in d["diagnosis"]["certain_verdicts"].values())
