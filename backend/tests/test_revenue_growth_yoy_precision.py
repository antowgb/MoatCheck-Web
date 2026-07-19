"""Task 6: revenue_growth_yoy is written with uniform precision
(round(value, 4)), regardless of the exact division result. Uses the same
synthetic Alpha Vantage payload harness as test_finnhub_hybrid_exclusion.py.
Run with pytest if available, else directly:
    python3 tests/test_revenue_growth_yoy_precision.py
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
    def report(fde: str, revenue: str) -> dict:
        return {
            "fiscalDateEnding": fde,
            "totalRevenue": revenue,
            "operatingIncome": "200",
            "netIncome": "100",
            "ebitda": "250",
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

    # Revenue chosen so revenue/prev_rev - 1.0 has a long non-terminating
    # decimal expansion (1300/837 - 1 != a clean 4-decimal number).
    revenues = ["837", "900", "950", "1000", "1300", "1200", "1150", "1400"]

    return {
        "income_statement": {"quarterlyReports": [report(q, r) for q, r in zip(_QUARTERS, revenues)]},
        "balance_sheet": {"quarterlyReports": [balance(q) for q in _QUARTERS]},
        "cash_flow": {"quarterlyReports": [cashflow(q) for q in _QUARTERS]},
        "earnings": {"quarterlyEarnings": [earnings(q) for q in _QUARTERS]},
    }


def test_revenue_growth_yoy_rounded_to_4_decimals() -> None:
    with patch.object(finnhub_client, "fetch_isolated_quarters", return_value={}), patch.object(
        finnhub_client, "fetch_quarterly_ebitda", return_value={}
    ):
        rows = build_quarterly_fundamentals("TEST", _av_info(), price_rows=[])

    checked = 0
    for row in rows:
        g = row["revenue_growth_yoy"]
        if g is None:
            continue
        checked += 1
        assert g == round(g, 4), f"{row['report_date']}: revenue_growth_yoy {g!r} has more than 4 decimals"

    assert checked > 0, "expected at least one row with a non-null revenue_growth_yoy"

    # 2025-03-31: revenue=1300 (index 4), prev (2024-03-31, index 0)=837.
    row = next(r for r in rows if r["report_date"] == "2025-03-31")
    assert row["revenue_growth_yoy"] == round(1300 / 837 - 1.0, 4)


if __name__ == "__main__":
    test_revenue_growth_yoy_rounded_to_4_decimals()
    print("OK: revenue_growth_yoy is uniformly rounded to 4 decimals")
