"""Validates two things ahead of any debt_to_ebitda-from-Finnhub decision, using
the real JNJ payloads captured in fixtures/finnhub_decumulation_case.json
(balance_sheet + debt_to_ebitda_impact sections):

1. Balance sheet items (debt, cash) need NO decumulation — they are point-in-time
   balances, not YTD flows like revenue/net_income/ocf/capex — and Finnhub's
   values match Alpha Vantage's exactly (0% deviation) for JNJ on 4 quarters.
2. Alpha Vantage's `fiscalDateEnding` and Finnhub's `endDate` do NOT align
   day-for-day for JNJ on any of the 4 quarters tested (1-2 day gap every
   time) — a naive cross-source join on that date field would never match.
   `fiscal_date_match` below is the proposed fail-closed guard: it says
   whether two dates from different sources are close enough to treat as
   "the same quarter" (mirrors the existing know_date anti-look-ahead
   fail-closed pattern in app/scoring/fundamentals.select_snapshot_at).

Standalone / exploratory: nothing here is imported by app production code.
Run with pytest if available, else directly:
    python3 tests/test_finnhub_balance_sheet_fixture.py
"""

import json
from datetime import date
from pathlib import Path

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "finnhub_decumulation_case.json"

_TOLERANCE_PCT = 0.05


def fiscal_date_match(date_a: str, date_b: str, tolerance_days: int = 5) -> bool:
    """Proposed guard rule: two sources' period-end dates for "the same quarter"
    are usable together only if within `tolerance_days` of each other. If not,
    the caller must exclude that field/quarter rather than silently mixing
    mismatched periods (fail-closed, same spirit as know_date <= as_of)."""
    return abs((date.fromisoformat(date_a) - date.fromisoformat(date_b)).days) <= tolerance_days


def test_balance_sheet_matches_within_tolerance() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text())
    for q in fixture["balance_sheet"]["quarters"]:
        av, fh = q["alpha_vantage"], q["finnhub"]
        for field in ("debt_short", "debt_long", "cash"):
            base = av[field]
            pct = abs(fh[field] - base) / base if base else 0.0
            assert pct <= _TOLERANCE_PCT, (
                f"{q['fiscal_date_ending_av']} {field}: {pct:.2%} deviation "
                f"exceeds {_TOLERANCE_PCT:.0%} tolerance (AV={base}, Finnhub={fh[field]})"
            )


def test_debt_to_ebitda_unaffected_by_source() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text())
    for q in fixture["debt_to_ebitda_impact"]["quarters"]:
        assert q["normalized_av"] == q["normalized_finnhub_sourced"], (
            f"{q['report_date']}: normalized debt_to_ebitda differs between "
            f"sources ({q['normalized_av']} vs {q['normalized_finnhub_sourced']})"
        )


def test_fiscal_date_ending_misaligned_across_sources() -> None:
    """Documents the real finding: for JNJ, AV and Finnhub never report the
    exact same fiscalDateEnding/endDate for the same quarter (1-2 day gap on
    all 4 quarters tested) — still within the 5-day guard tolerance here, but
    a naive exact-string join would fail on every single quarter."""
    fixture = json.loads(FIXTURE_PATH.read_text())
    gaps = []
    for q in fixture["balance_sheet"]["quarters"]:
        av_d, fh_d = q["fiscal_date_ending_av"], q["fiscal_date_ending_finnhub"]
        assert av_d != fh_d, f"expected a date mismatch for {av_d}, found none"
        assert fiscal_date_match(av_d, fh_d), (
            f"{av_d} vs {fh_d}: gap exceeds the guard tolerance — "
            f"this quarter's cross-source fields should be excluded, not mixed"
        )
        gaps.append(abs((date.fromisoformat(av_d) - date.fromisoformat(fh_d)).days))
    assert gaps == [1, 1, 2, 2], f"unexpected gap pattern: {gaps}"


def test_fiscal_date_match_rejects_beyond_tolerance() -> None:
    assert fiscal_date_match("2025-03-31", "2025-03-30") is True
    assert fiscal_date_match("2025-03-31", "2025-03-24") is False  # 7 days > 5-day guard


def test_hybrid_debt_to_ebitda_real_combination_negligible() -> None:
    """The ACTUAL prod-shape hybrid (debt/cash from AV, EBITDA from Finnhub):
    every quarter's EBITDA deviation stays under the 5% tolerance and the
    resulting score-point difference stays well under 1 point (out of 100)."""
    fixture = json.loads(FIXTURE_PATH.read_text())
    for q in fixture["debt_to_ebitda_hybrid_real"]["quarters"]:
        assert abs(q["ebitda_pct_diff"]) <= 5.0, f"{q['report_date']}: EBITDA deviation exceeds 5%"
        assert q["score_point_diff"] < 1.0, f"{q['report_date']}: score impact >= 1 point"


def test_fiscal_date_gap_stays_within_tolerance_across_calendars() -> None:
    """gap_days varies by fiscal calendar (0 for MSFT, up to 4 for AAPL) but
    never exceeds the 5-day guard on any of the 16 quarters sampled across
    3 tickers — the tolerance is empirically justified, not widened blindly."""
    fixture = json.loads(FIXTURE_PATH.read_text())
    all_gaps = [
        q["gap_days"]
        for ticker_rows in fixture["fiscal_date_gap_multi_ticker"].values()
        if isinstance(ticker_rows, list)
        for q in ticker_rows
    ]
    assert all(g <= 5 for g in all_gaps), f"a gap exceeds the 5-day tolerance: {all_gaps}"
    assert max(all_gaps) == 4, f"expected the observed worst case (AAPL, 4 days), got max={max(all_gaps)}"


if __name__ == "__main__":
    test_balance_sheet_matches_within_tolerance()
    test_debt_to_ebitda_unaffected_by_source()
    test_fiscal_date_ending_misaligned_across_sources()
    test_fiscal_date_match_rejects_beyond_tolerance()
    test_hybrid_debt_to_ebitda_real_combination_negligible()
    test_fiscal_date_gap_stays_within_tolerance_across_calendars()
    print("OK: balance sheet + debt_to_ebitda + fiscal-date-alignment checks pass")
