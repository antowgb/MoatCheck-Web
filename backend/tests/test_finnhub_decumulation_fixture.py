"""Validates the YTD -> isolated-quarter decumulation logic needed to use
Finnhub's `financials-reported` endpoint (whose 10-Q figures are cumulative
year-to-date, not single-quarter) against a real payload fixture
(fixtures/finnhub_decumulation_case.json, captured from JNJ on 2026-07-18).

Standalone / exploratory: `_decumulate` below is NOT app production code
(nothing in app/data/fetch.py imports it) — it exists only to prove the
decumulation approach is correct before it is ever wired into the pipeline.
Run with pytest if available, else directly:
    python3 tests/test_finnhub_decumulation_fixture.py
"""

import json
from pathlib import Path

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "finnhub_decumulation_case.json"

_METRICS = ("revenue", "net_income", "ocf", "capex")


def _decumulate(quarters_ytd: list[dict]) -> list[dict]:
    """quarters_ytd must be ordered ascending by (year, quarter). Quarter 1
    of a fiscal year is never cumulative (YTD == isolated quarter); quarter
    2/3 are decumulated against the immediately preceding quarter of the
    same fiscal year.
    """
    isolated = []
    prev = None
    for q in quarters_ytd:
        if q["quarter"] == 1 or prev is None or prev["year"] != q["year"]:
            row = {m: q[f"{m}_ytd"] for m in _METRICS}
        else:
            row = {m: q[f"{m}_ytd"] - prev[f"{m}_ytd"] for m in _METRICS}
        isolated.append({"year": q["year"], "quarter": q["quarter"], **row})
        prev = q
    return isolated


def test_decumulation_matches_fixture() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text())
    result = _decumulate(fixture["quarters_ytd"])
    assert result == fixture["expected_isolated"], (
        f"_decumulate returned {result}, fixture expects "
        f"{fixture['expected_isolated']}"
    )


def test_fiscal_q1_is_not_decumulated() -> None:
    """Q1 YTD must equal the isolated quarter as-is — the classic pitfall
    would be to always subtract the previous filing (which for Q1 is the
    prior fiscal year's Q3, a different fiscal year and not subtractable)."""
    fixture = json.loads(FIXTURE_PATH.read_text())
    q1_rows = [q for q in fixture["quarters_ytd"] if q["quarter"] == 1]
    assert len(q1_rows) >= 2
    result = {(r["year"], r["quarter"]): r for r in _decumulate(fixture["quarters_ytd"])}
    for q in q1_rows:
        isolated = result[(q["year"], q["quarter"])]
        for m in _METRICS:
            assert isolated[m] == q[f"{m}_ytd"], (
                f"Q1 {q['year']} {m}: isolated value must equal YTD "
                f"({q[f'{m}_ytd']}), got {isolated[m]}"
            )


if __name__ == "__main__":
    test_decumulation_matches_fixture()
    test_fiscal_q1_is_not_decumulated()
    print("OK: _decumulate matches fixtures/finnhub_decumulation_case.json")
