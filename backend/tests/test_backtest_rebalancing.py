"""Verifies periodic rebalancing + transaction costs in
app.backtest.engine.run_backtest, using a fake Supabase client (no real
network/DB, no yfinance fallback needed since SPY is seeded in fake
price_history).
Run with pytest if available, else directly:
    python3 tests/test_backtest_rebalancing.py
"""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.engine import run_backtest


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, tables):
        self._table_name = table_name
        self._tables = tables
        self._filters = {}

    def select(self, *_a, **_kw):
        return self

    def eq(self, field, value):
        self._filters[field] = value
        return self

    def neq(self, field, value):
        self._filters[("neq", field)] = value
        return self

    def in_(self, field, values):
        self._filters[field] = set(values)
        return self

    def order(self, *_a, **_kw):
        return self

    def range(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def execute(self):
        rows = self._tables.get(self._table_name, [])
        for key, value in self._filters.items():
            if isinstance(key, tuple) and key[0] == "neq":
                rows = [r for r in rows if r.get(key[1]) != value]
            elif isinstance(value, set):
                rows = [r for r in rows if r.get(key) in value]
            else:
                rows = [r for r in rows if r.get(key) == value]
        return _FakeResult(rows)


class _FakeSupabase:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(name, self._tables)


def _price_rows(ticker: str, start: str, end: str, seed: int, weekly_drift: float, vol_scale: float) -> list[dict]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, end, freq="W")
    prices = 100 * np.cumprod(1 + weekly_drift + rng.normal(0, 0.02 * vol_scale, len(dates)))
    return [{"ticker": ticker, "date": d.date().isoformat(), "close": float(p)} for d, p in zip(dates, prices)]


def _fundamentals_row(ticker: str, know_date: str) -> dict:
    return {
        "ticker": ticker,
        "report_date": know_date,
        "know_date": know_date,
        "revenue_growth_yoy": 0.15,
        "operating_margin": 0.20,
        "roe": 0.18,
        "debt_to_ebitda": 1.5,
        "free_cash_flow": 1_000_000.0,
    }


def _fake_tables() -> dict:
    # Two tickers with visibly different drift/volatility so their relative
    # weights diverge between rebalances (non-zero turnover expected).
    price_rows = (
        _price_rows("AAA", "2023-01-01", "2026-07-19", seed=1, weekly_drift=0.001, vol_scale=0.4)
        + _price_rows("BBB", "2023-01-01", "2026-07-19", seed=2, weekly_drift=0.005, vol_scale=1.6)
        + _price_rows("SPY", "2023-01-01", "2026-07-19", seed=3, weekly_drift=0.002, vol_scale=0.5)
    )
    return {
        "stocks": [
            {"ticker": "AAA", "is_benchmark": False, "asset_type": "equity", "sector_benchmark_ticker": None},
            {"ticker": "BBB", "is_benchmark": False, "asset_type": "equity", "sector_benchmark_ticker": None},
        ],
        "price_history": price_rows,
        "fundamentals": [
            _fundamentals_row("AAA", "2020-01-01"),
            _fundamentals_row("BBB", "2020-01-01"),
        ],
    }


def test_static_mode_unchanged_when_rebalance_frequency_omitted() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.backtest.engine.get_supabase", return_value=fake_sb):
        result = run_backtest(date(2024, 1, 1), top_n=2, benchmark="SPY")

    assert "error" not in result
    assert result["rebalance_frequency"] is None
    assert result["transaction_cost_bps"] is None
    assert result["rebalance_count"] == 0
    assert result["total_transaction_cost"] == 0.0
    assert result["rebalances"] == []
    assert set(result["selected_tickers"]) == {"AAA", "BBB"}


def test_quarterly_rebalancing_produces_rebalances_and_cost() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.backtest.engine.get_supabase", return_value=fake_sb):
        result = run_backtest(
            date(2024, 1, 1),
            top_n=2,
            benchmark="SPY",
            rebalance_frequency="quarterly",
            transaction_cost_bps=10.0,
        )

    assert "error" not in result
    assert result["rebalance_frequency"] == "quarterly"
    assert result["transaction_cost_bps"] == 10.0
    assert result["rebalance_count"] > 0
    assert len(result["rebalances"]) == result["rebalance_count"]
    # Divergent drift/vol between AAA and BBB guarantees some drift each
    # quarter, so at least one rebalance should have non-zero turnover/cost.
    assert any(r["turnover"] > 0 for r in result["rebalances"])
    assert result["total_transaction_cost"] > 0
    # Individually-rounded per-rebalance costs can differ from the overall
    # rounded total by a hair of floating-point rounding — compare loosely.
    assert abs(result["total_transaction_cost"] - sum(r["transaction_cost"] for r in result["rebalances"])) < 1e-4
    # Rebalance dates are strictly increasing and after start_date.
    reb_dates = [r["date"] for r in result["rebalances"]]
    assert reb_dates == sorted(reb_dates)
    assert all(d > "2024-01-01" for d in reb_dates)


def test_monthly_produces_more_rebalances_than_quarterly() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.backtest.engine.get_supabase", return_value=fake_sb):
        monthly = run_backtest(date(2024, 1, 1), top_n=2, benchmark="SPY", rebalance_frequency="monthly")
        quarterly = run_backtest(date(2024, 1, 1), top_n=2, benchmark="SPY", rebalance_frequency="quarterly")

    assert monthly["rebalance_count"] > quarterly["rebalance_count"]


if __name__ == "__main__":
    test_static_mode_unchanged_when_rebalance_frequency_omitted()
    test_quarterly_rebalancing_produces_rebalances_and_cost()
    test_monthly_produces_more_rebalances_than_quarterly()
    print("OK: run_backtest periodic rebalancing")
