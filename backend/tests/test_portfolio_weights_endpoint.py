"""Verifies GET /api/portfolio/weights returns inverse-volatility weights
that sum to 1.0, using a fake Supabase client (no real network/DB).
Run with pytest if available, else directly:
    python3 tests/test_portfolio_weights_endpoint.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import app


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

    def in_(self, field, values):
        self._filters[field] = set(values)
        return self

    def order(self, *_a, **_kw):
        return self

    def range(self, *_a, **_kw):
        return self

    def execute(self):
        rows = self._tables.get(self._table_name, [])
        for field, value in self._filters.items():
            if isinstance(value, set):
                rows = [r for r in rows if r.get(field) in value]
            else:
                rows = [r for r in rows if r.get(field) == value]
        return _FakeResult(rows)


class _FakeSupabase:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(name, self._tables)


def _price_rows(ticker: str, n: int, seed: int, vol_scale: float) -> list[dict]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="W")
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.02 * vol_scale, n))
    return [{"ticker": ticker, "date": d.date().isoformat(), "close": float(p)} for d, p in zip(dates, prices)]


def _flat_price_rows(ticker: str, n: int) -> list[dict]:
    """Constant price -> zero volatility -> excluded from weighting."""
    dates = pd.date_range("2023-01-01", periods=n, freq="W")
    return [{"ticker": ticker, "date": d.date().isoformat(), "close": 100.0} for d in dates]


def _fake_tables() -> dict:
    # AAPL scaled to be visibly less volatile than MSFT -> should get a
    # larger inverse-volatility weight.
    price_rows = _price_rows("AAPL", 40, seed=1, vol_scale=0.3) + _price_rows("MSFT", 40, seed=2, vol_scale=1.5)
    return {
        "stocks": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
        "price_history": price_rows,
    }


def _fake_tables_with_flat_ticker() -> dict:
    price_rows = (
        _price_rows("AAPL", 40, seed=1, vol_scale=0.3)
        + _price_rows("MSFT", 40, seed=2, vol_scale=1.5)
        + _flat_price_rows("FLAT", 40)
    )
    return {
        "stocks": [{"ticker": "AAPL"}, {"ticker": "MSFT"}, {"ticker": "FLAT"}],
        "price_history": price_rows,
    }


def test_portfolio_weights_sum_to_one_and_favor_lower_volatility() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb), patch(
        "app.backtest.engine.get_supabase", return_value=fake_sb
    ):
        client = TestClient(app)
        resp = client.get("/api/portfolio/weights", params={"tickers": "AAPL,MSFT"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    weights = body["weights"]
    assert set(weights.keys()) == {"AAPL", "MSFT"}
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert weights["AAPL"] > weights["MSFT"]
    assert body["excluded"] == []


def test_portfolio_weights_renormalizes_after_excluding_zero_volatility_ticker() -> None:
    fake_sb = _FakeSupabase(_fake_tables_with_flat_ticker())
    with patch("app.api.routes.get_supabase", return_value=fake_sb), patch(
        "app.backtest.engine.get_supabase", return_value=fake_sb
    ):
        client = TestClient(app)
        resp = client.get("/api/portfolio/weights", params={"tickers": "AAPL,MSFT,FLAT"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    weights = body["weights"]
    excluded = body["excluded"]

    # FLAT (zero volatility) is excluded with a reason, not silently dropped.
    assert excluded == [{"ticker": "FLAT", "reason": "zero or undefined volatility"}]
    # Only the remaining two tickers get a weight, and they alone sum to 1.0
    # (no leftover/orphaned weight from the excluded ticker).
    assert set(weights.keys()) == {"AAPL", "MSFT"}
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_portfolio_weights_rejects_single_ticker() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb):
        client = TestClient(app)
        resp = client.get("/api/portfolio/weights", params={"tickers": "AAPL"})
    assert resp.status_code == 422


def test_portfolio_weights_rejects_unknown_ticker() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb):
        client = TestClient(app)
        resp = client.get("/api/portfolio/weights", params={"tickers": "AAPL,NOPE"})
    assert resp.status_code == 422


if __name__ == "__main__":
    test_portfolio_weights_sum_to_one_and_favor_lower_volatility()
    test_portfolio_weights_renormalizes_after_excluding_zero_volatility_ticker()
    test_portfolio_weights_rejects_single_ticker()
    test_portfolio_weights_rejects_unknown_ticker()
    print("OK: GET /api/portfolio/weights")
