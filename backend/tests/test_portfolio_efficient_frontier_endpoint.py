"""Verifies GET /api/portfolio/efficient-frontier returns a coherent
minimum-variance curve plus a max-Sharpe tangency point, using a fake
Supabase client (no real network/DB).
Run with pytest if available, else directly:
    python3 tests/test_portfolio_efficient_frontier_endpoint.py
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


def _trending_price_rows(ticker: str, n: int, seed: int, weekly_drift: float, vol_scale: float) -> list[dict]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="W")
    prices = 100 * np.cumprod(1 + weekly_drift + rng.normal(0, 0.02 * vol_scale, n))
    return [{"ticker": ticker, "date": d.date().isoformat(), "close": float(p)} for d, p in zip(dates, prices)]


def _flat_price_rows(ticker: str, n: int) -> list[dict]:
    dates = pd.date_range("2023-01-01", periods=n, freq="W")
    return [{"ticker": ticker, "date": d.date().isoformat(), "close": 100.0} for d in dates]


def _fake_tables() -> dict:
    # Three tickers with visibly distinct drift/volatility so the frontier
    # spans a real (non-degenerate) range of target returns.
    price_rows = (
        _trending_price_rows("LOW", 60, seed=1, weekly_drift=0.001, vol_scale=0.3)
        + _trending_price_rows("MID", 60, seed=2, weekly_drift=0.003, vol_scale=0.8)
        + _trending_price_rows("HIGH", 60, seed=3, weekly_drift=0.006, vol_scale=1.5)
    )
    return {
        "stocks": [{"ticker": "LOW"}, {"ticker": "MID"}, {"ticker": "HIGH"}],
        "price_history": price_rows,
    }


def _fake_tables_with_flat_ticker() -> dict:
    price_rows = (
        _trending_price_rows("LOW", 60, seed=1, weekly_drift=0.001, vol_scale=0.3)
        + _trending_price_rows("MID", 60, seed=2, weekly_drift=0.003, vol_scale=0.8)
        + _flat_price_rows("FLAT", 60)
    )
    return {
        "stocks": [{"ticker": "LOW"}, {"ticker": "MID"}, {"ticker": "FLAT"}],
        "price_history": price_rows,
    }


def test_efficient_frontier_returns_coherent_curve_and_tangency_point() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb), patch(
        "app.backtest.engine.get_supabase", return_value=fake_sb
    ):
        client = TestClient(app)
        resp = client.get("/api/portfolio/efficient-frontier", params={"tickers": "LOW,MID,HIGH"})

    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body["tickers"]) == {"LOW", "MID", "HIGH"}
    assert body["excluded"] == []

    frontier = body["frontier"]
    assert len(frontier) >= 3

    for point in frontier:
        assert set(point["weights"].keys()) == {"LOW", "MID", "HIGH"}
        assert abs(sum(point["weights"].values()) - 1.0) < 1e-3
        for w in point["weights"].values():
            assert w >= -1e-6  # long-only: no negative weights

    # Sorted by target_return, volatility should broadly increase (classic
    # upward-sloping efficient frontier) — check the extremes, not strict
    # monotonicity point-to-point (numerical noise near-adjacent points).
    by_return = sorted(frontier, key=lambda p: p["target_return"])
    assert by_return[0]["volatility"] <= by_return[-1]["volatility"] + 1e-6

    max_sharpe = body["max_sharpe_point"]
    assert set(max_sharpe["weights"].keys()) == {"LOW", "MID", "HIGH"}
    assert abs(sum(max_sharpe["weights"].values()) - 1.0) < 1e-3
    assert max_sharpe["sharpe_ratio"] is not None

    min_var = body["min_variance_point"]
    assert set(min_var["weights"].keys()) == {"LOW", "MID", "HIGH"}
    assert abs(sum(min_var["weights"].values()) - 1.0) < 1e-3

    # No point in the returned frontier may be dominated by another: a
    # strictly higher (or equal) return paired with a strictly lower (or
    # equal) volatility, with at least one strict inequality.
    for i, p in enumerate(frontier):
        for j, q in enumerate(frontier):
            if i == j:
                continue
            dominates = (
                q["target_return"] >= p["target_return"]
                and q["volatility"] <= p["volatility"]
                and (q["target_return"] > p["target_return"] or q["volatility"] < p["volatility"])
            )
            assert not dominates, f"{p} is dominated by {q}"


def test_efficient_frontier_excludes_zero_volatility_ticker() -> None:
    fake_sb = _FakeSupabase(_fake_tables_with_flat_ticker())
    with patch("app.api.routes.get_supabase", return_value=fake_sb), patch(
        "app.backtest.engine.get_supabase", return_value=fake_sb
    ):
        client = TestClient(app)
        resp = client.get("/api/portfolio/efficient-frontier", params={"tickers": "LOW,MID,FLAT"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["excluded"] == [{"ticker": "FLAT", "reason": "zero or undefined volatility"}]
    assert set(body["tickers"]) == {"LOW", "MID"}
    for point in body["frontier"]:
        assert set(point["weights"].keys()) == {"LOW", "MID"}


def test_efficient_frontier_rejects_single_ticker() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb):
        client = TestClient(app)
        resp = client.get("/api/portfolio/efficient-frontier", params={"tickers": "LOW"})
    assert resp.status_code == 422


def test_efficient_frontier_rejects_unknown_ticker() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb):
        client = TestClient(app)
        resp = client.get("/api/portfolio/efficient-frontier", params={"tickers": "LOW,NOPE"})
    assert resp.status_code == 422


if __name__ == "__main__":
    test_efficient_frontier_returns_coherent_curve_and_tangency_point()
    test_efficient_frontier_excludes_zero_volatility_ticker()
    test_efficient_frontier_rejects_single_ticker()
    test_efficient_frontier_rejects_unknown_ticker()
    print("OK: GET /api/portfolio/efficient-frontier")
