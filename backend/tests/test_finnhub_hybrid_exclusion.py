"""Verifies the two fail-closed gates wired into ``build_quarterly_fundamentals``
(app/data/fetch.py): a Finnhub value is only substituted for Alpha Vantage's
when (1) the two sources' period-end dates are within
``finnhub_client.FISCAL_DATE_TOLERANCE_DAYS`` of each other, AND (2) the value
itself is within ``finnhub_client.MAGNITUDE_TOLERANCE`` of Alpha Vantage's own
same-quarter figure (catches a wrong-tag match like JPM's
RevenuesNetOfInterestExpense, which passes the date check but reports a
fundamentally different quantity). Either failure keeps Alpha Vantage's value
and ``data_source`` records why — never a silent mix.

Uses synthetic Alpha Vantage payloads (not real API calls) and monkeypatches
``finnhub_client.fetch_isolated_quarters``/``fetch_quarterly_ebitda`` to control
the exact gap_days per quarter. Run with pytest if available, else directly:
    python3 tests/test_finnhub_hybrid_exclusion.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data import finnhub_client
from app.data.fetch import build_quarterly_fundamentals

_QUARTERS = [
    "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
    "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
]


def _av_info() -> dict:
    def report(fde: str, i: int) -> dict:
        return {
            "fiscalDateEnding": fde,
            "totalRevenue": str(1000 + i * 100),
            "operatingIncome": str(200 + i * 10),
            "netIncome": str(100 + i * 10),
            "ebitda": str(250 + i * 10),
        }

    def balance(fde: str) -> dict:
        return {
            "fiscalDateEnding": fde,
            "totalShareholderEquity": "500",
            "cashAndCashEquivalentsAtCarryingValue": "50",
            "shortLongTermDebtTotal": "300",
            "commonStockSharesOutstanding": "10",
        }

    def cashflow(fde: str) -> dict:
        return {"fiscalDateEnding": fde, "operatingCashflow": "150", "capitalExpenditures": "30"}

    def earnings(fde: str) -> dict:
        return {"fiscalDateEnding": fde, "reportedEPS": "1.0"}

    return {
        "income_statement": {"quarterlyReports": [report(q, i) for i, q in enumerate(_QUARTERS)]},
        "balance_sheet": {"quarterlyReports": [balance(q) for q in _QUARTERS]},
        "cash_flow": {"quarterlyReports": [cashflow(q) for q in _QUARTERS]},
        "earnings": {"quarterlyEarnings": [earnings(q) for q in _QUARTERS]},
    }


def _rows_by_report_date(ticker: str = "TEST") -> dict[str, dict]:
    with patch.object(
        finnhub_client, "fetch_isolated_quarters",
        return_value={
            # 2 days from 2025-12-31 (AV revenue=1700/net_income=170) -> within
            # date tolerance AND within the 25% magnitude tolerance -> substituted.
            "2025-12-29": {"revenue": 1750.0, "revenue_tag": "us-gaap_Revenues",
                           "net_income": 175.0, "net_income_tag": "us-gaap_NetIncomeLoss"},
            # 10 days from 2024-12-31 -> beyond fiscal-date tolerance -> excluded
            # regardless of value.
            "2024-12-21": {"revenue": 1310.0, "revenue_tag": "us-gaap_Revenues",
                           "net_income": 131.0, "net_income_tag": "us-gaap_NetIncomeLoss"},
            # 2 days from 2025-09-30 (AV revenue=1600/net_income=160) -> WITHIN
            # date tolerance but wildly off value -> magnitude guard must reject
            # (simulates a tag mismatch like JPM's RevenuesNetOfInterestExpense).
            "2025-09-28": {"revenue": 9999.0, "revenue_tag": "us-gaap_SomeWrongTag",
                           "net_income": 4999.0, "net_income_tag": "us-gaap_SomeWrongTag"},
        },
    ), patch.object(finnhub_client, "fetch_quarterly_ebitda", return_value={}):
        rows = build_quarterly_fundamentals(ticker, _av_info(), price_rows=[])
    return {r["report_date"]: r for r in rows}


def test_finnhub_used_within_tolerance_and_magnitude() -> None:
    rows = _rows_by_report_date()
    row = rows["2025-12-31"]
    assert row["data_source"]["revenue"] == "finnhub"
    assert row["data_source"]["net_income"] == "finnhub"
    assert row["data_source"]["revenue_tag"] == "us-gaap_Revenues"
    assert row["revenue"] == 1750.0  # substituted value, not Alpha Vantage's 1700
    assert row["data_source"]["fiscal_date_match"]["gap_days"] == 2
    assert row["data_source"]["fiscal_date_match"]["within_tolerance"] is True


def test_finnhub_excluded_beyond_date_tolerance() -> None:
    rows = _rows_by_report_date()
    row = rows["2024-12-31"]
    assert row["data_source"]["revenue"] == "alpha_vantage"
    assert row["data_source"]["net_income"] == "alpha_vantage"
    # Alpha Vantage's own value (index 3 in _QUARTERS: 1000 + 3*100), not the
    # nearby-but-too-far Finnhub value (1310.0) — no silent mix.
    assert row["revenue"] == 1300.0
    assert "fiscal_date_match" not in row["data_source"]


def test_finnhub_rejected_on_magnitude_mismatch() -> None:
    """Date-aligned (2 days) but the value is wildly inconsistent with Alpha
    Vantage's own same-quarter figure — the magnitude guard must reject it
    even though the fiscal-date gate alone would have allowed it (this is
    the real JPM RevenuesNetOfInterestExpense scenario)."""
    rows = _rows_by_report_date()
    row = rows["2025-09-30"]
    assert row["data_source"]["revenue"] == "alpha_vantage"
    assert row["data_source"]["net_income"] == "alpha_vantage"
    assert row["revenue"] == 1600.0  # Alpha Vantage's own value, not the rejected 9999.0
    assert "fiscal_date_match" not in row["data_source"]


def test_ebitda_and_debt_cash_always_alpha_vantage_when_finnhub_ebitda_absent() -> None:
    rows = _rows_by_report_date()
    for row in rows.values():
        assert row["data_source"]["ebitda_ttm"] == "alpha_vantage"
        assert row["data_source"]["debt"] == "alpha_vantage"
        assert row["data_source"]["cash"] == "alpha_vantage"
        assert row["data_source"]["free_cash_flow"] == "alpha_vantage"
        assert row["data_source"]["eps_ttm"] == "alpha_vantage"


if __name__ == "__main__":
    test_finnhub_used_within_tolerance_and_magnitude()
    test_finnhub_excluded_beyond_date_tolerance()
    test_finnhub_rejected_on_magnitude_mismatch()
    test_ebitda_and_debt_cash_always_alpha_vantage_when_finnhub_ebitda_absent()
    print("OK: Finnhub hybrid fiscal-date + magnitude gates behave as expected")
