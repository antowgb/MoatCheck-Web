"""Two closure checks on the Finnhub quota-gain claim (see
app/data/finnhub_client.py module docstring):

1. The measured revenue/net_income fallback rate over a 5-year history on
   JNJ/AAPL/MSFT (fixtures/finnhub_decumulation_case.json ->
   revenue_net_income_fallback_rate_5y) stays in the expected ~25-45% band —
   above the naive ~25% Q4-only floor, confirming extra Finnhub filing-
   coverage gaps exist (not just the structural Q4/10-K exclusion).
2. build_quarterly_fundamentals makes exactly 2 Finnhub HTTP calls per
   ticker refresh (financials-reported + stock/metric), regardless of how
   many quarters are processed — net_income_ttm's permanent fallback (see
   the ROE diagnostic) does NOT cost an extra network call: it only reuses,
   in memory, the financials-reported response already fetched for
   revenue/net_income.

Standalone / exploratory. Run with pytest if available, else directly:
    python3 tests/test_finnhub_quota_and_fallback.py
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data import finnhub_client
from app.data.fetch import build_quarterly_fundamentals

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "finnhub_decumulation_case.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finnhub_hybrid_exclusion import _av_info  # synthetic 8-quarter Alpha Vantage payload


def test_measured_fallback_rate_exceeds_q4_only_floor() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text())
    rates = fixture["revenue_net_income_fallback_rate_5y"]
    for ticker in ("JNJ", "AAPL", "MSFT"):
        pct = rates[ticker]["fallback_pct"]
        assert 25.0 <= pct <= 45.0, f"{ticker}: fallback_pct {pct} outside the expected 25-45% band"
        assert rates[ticker]["fallback"] == len(rates[ticker]["fallback_dates"])


def test_multi_tag_coverage_v2_documented_and_bounded() -> None:
    """Round-3 measurement (multi-tag candidate list + magnitude guard,
    7 tickers / 6 sectors) must stay a documented, bounded figure — not
    silently drift, and not be presented as if it applied to all 9 tickers
    sampled (XOM/O have no Alpha Vantage ground truth, excluded on purpose)."""
    fixture = json.loads(FIXTURE_PATH.read_text())
    v2 = fixture["revenue_net_income_coverage_v2"]
    agg = v2["aggregate_unfiltered"]
    assert 40.0 <= agg["combined_pct"] <= 65.0, f"combined coverage {agg['combined_pct']}% drifted outside the documented band"
    assert set(v2["per_ticker"].keys()) == {"JNJ", "AAPL", "MSFT", "LLY", "JPM", "V", "UNH"}
    # JPM's revenue=0 and V's net_income=0 are documented, legitimate non-coverage,
    # not silently averaged away without explanation.
    assert v2["per_ticker"]["JPM"]["revenue_match"] == 0 and "note" in v2["per_ticker"]["JPM"]
    assert v2["per_ticker"]["V"]["net_income_match"] == 0 and "note" in v2["per_ticker"]["V"]


def test_debt_cash_conditional_balance_sheet_gain_is_negligible() -> None:
    """Justifies NOT implementing Finnhub-primary debt/cash: the per-quarter
    match rate (~67%) looks promising in isolation, but Alpha Vantage's
    BALANCE_SHEET returns all quarters in one call and every refresh rebuilds
    the full 5-year window — so the call is skippable only if ALL quarters in
    that window match, which happened on 0 of the 3 real tickers tested."""
    fixture = json.loads(FIXTURE_PATH.read_text())
    analysis = fixture["debt_cash_conditional_balance_sheet_analysis"]
    assert analysis["tickers_with_zero_fallback_quarters_in_window"] == 0
    assert analysis["real_balance_sheet_skip_rate"] < 30.0, (
        "real skip rate reached the 30% threshold — Task 2 conclusion should be revisited"
    )
    for pct in analysis["per_quarter_match_rate"].values():
        assert pct > 0  # the naive per-quarter rate itself isn't zero, just misleading here


def test_no_extra_network_call_for_net_income_ttm() -> None:
    """Runs build_quarterly_fundamentals with Finnhub's low-level _get_json
    mocked (never returns data, forcing pure Alpha Vantage fallback — same
    end state as the real permanent net_income_ttm fallback) and counts calls.
    Exactly 2 Finnhub HTTP calls must be made, no matter how many quarters
    (8, in the synthetic fixture) are processed in the per-quarter loop."""
    calls = []

    def fake_get_json(path: str, **params):
        calls.append(path)
        return None

    with patch.object(finnhub_client, "_get_json", side_effect=fake_get_json):
        rows = build_quarterly_fundamentals("TEST", _av_info(), price_rows=[])

    assert len(rows) == 8
    assert calls == ["/stock/financials-reported", "/stock/metric"], (
        f"expected exactly 2 Finnhub calls (1 financials-reported + 1 metric), got {calls}"
    )


if __name__ == "__main__":
    test_measured_fallback_rate_exceeds_q4_only_floor()
    test_multi_tag_coverage_v2_documented_and_bounded()
    test_debt_cash_conditional_balance_sheet_gain_is_negligible()
    test_no_extra_network_call_for_net_income_ttm()
    print("OK: fallback rate documented, no wasted Finnhub network call for net_income_ttm")
