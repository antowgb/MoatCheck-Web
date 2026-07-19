"""Verifies the incremental-maintenance-refresh gate in app/data/fetch.py:
an already-active, fully-backfilled equity ticker skips the 4 Alpha Vantage
statement endpoints (INCOME_STATEMENT/BALANCE_SHEET/CASH_FLOW/EARNINGS) when
no new quarter is plausibly available yet, but still fetches them when one
is likely due — and a legacy ticker with too few rows always fetches (never
silently stuck on a partial backfill).

Uses a minimal fake Supabase client (no real network/DB) and monkeypatches
fetch_stock_info/fetch_price_history to observe whether they were called.
Run with pytest if available, else directly:
    python3 tests/test_incremental_refresh_gate.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data import fetch


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, tables):
        self._table_name = table_name
        self._tables = tables

    def select(self, *_a, **_kw):
        return self

    def eq(self, *_a, **_kw):
        return self

    def upsert(self, *_a, **_kw):
        return self

    def update(self, *_a, **_kw):
        return self

    def execute(self):
        return _FakeResult(self._tables.get(self._table_name, []))


class _FakeSupabase:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(name, self._tables)


def _fundamentals_rows(n: int, latest_report_date: str) -> list[dict]:
    return [{"report_date": latest_report_date}] + [
        {"report_date": (date.fromisoformat(latest_report_date) - timedelta(days=90 * i)).isoformat()}
        for i in range(1, n)
    ]


_FAKE_INFO = {
    "overview": {"Name": "Test Inc", "Sector": "Technology", "Industry": "Software", "Currency": "USD"},
    "income_statement": {"quarterlyReports": []},
    "balance_sheet": {"quarterlyReports": []},
    "cash_flow": {"quarterlyReports": []},
    "earnings": {"quarterlyEarnings": []},
}


def _run_refresh_ticker(status: str, fundamentals_rows: list[dict]):
    fake_sb = _FakeSupabase({
        "stocks": [{"asset_type": "equity", "status": status}],
        "fundamentals": fundamentals_rows,
    })
    with patch.object(fetch, "get_supabase", return_value=fake_sb), \
         patch.object(fetch, "fetch_stock_info", return_value=_FAKE_INFO) as mock_info, \
         patch.object(fetch, "fetch_price_history", return_value=[]) as mock_prices:
        fetch.refresh_ticker("TEST")
    return mock_info, mock_prices


def test_no_av_call_when_no_new_quarter_expected() -> None:
    recent = (date.today() - timedelta(days=10)).isoformat()  # well within the 80-day grace window
    mock_info, mock_prices = _run_refresh_ticker("active", _fundamentals_rows(20, recent))
    mock_info.assert_not_called()
    mock_prices.assert_called_once()  # price history still refreshed every cycle


def test_av_call_made_when_new_quarter_likely() -> None:
    old = (date.today() - timedelta(days=100)).isoformat()  # past the 80-day grace window
    with patch.object(fetch, "build_quarterly_fundamentals", return_value=[]):
        mock_info, _ = _run_refresh_ticker("active", _fundamentals_rows(20, old))
    mock_info.assert_called_once_with("TEST")


def test_av_call_made_for_legacy_partial_backfill_regardless_of_date() -> None:
    """A ticker with < MIN_QUARTERS_FOR_INCREMENTAL rows (e.g. a legacy
    single-snapshot ticker) must never be gated, even if its one row's
    report_date is recent — it still needs the full historical rebuild."""
    recent = (date.today() - timedelta(days=10)).isoformat()
    with patch.object(fetch, "build_quarterly_fundamentals", return_value=[]):
        mock_info, _ = _run_refresh_ticker("active", _fundamentals_rows(1, recent))
    mock_info.assert_called_once_with("TEST")


if __name__ == "__main__":
    test_no_av_call_when_no_new_quarter_expected()
    test_av_call_made_when_new_quarter_likely()
    test_av_call_made_for_legacy_partial_backfill_regardless_of_date()
    print("OK: incremental refresh gate skips/fetches correctly")
