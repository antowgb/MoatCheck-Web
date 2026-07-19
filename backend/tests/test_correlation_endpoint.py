"""Verifies GET /api/correlation returns a coherent correlation matrix for
known tickers, using a fake Supabase client (no real network/DB).
Run with pytest if available, else directly:
    python3 tests/test_correlation_endpoint.py
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


def _price_rows(ticker: str, n: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="W")
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.02, n))
    return [{"ticker": ticker, "date": d.date().isoformat(), "close": float(p)} for d, p in zip(dates, prices)]


def _fake_tables() -> dict:
    tickers = ["AAPL", "MSFT", "GOOGL"]
    price_rows = []
    for i, t in enumerate(tickers):
        price_rows.extend(_price_rows(t, 40, seed=i))
    return {
        "stocks": [{"ticker": t} for t in tickers],
        "price_history": price_rows,
    }


def test_correlation_endpoint_returns_coherent_matrix() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb), patch(
        "app.backtest.engine.get_supabase", return_value=fake_sb
    ):
        client = TestClient(app)
        resp = client.get("/api/correlation", params={"tickers": "AAPL,MSFT,GOOGL"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tickers"] == ["AAPL", "MSFT", "GOOGL"]
    matrix = body["matrix"]
    assert len(matrix) == 3
    for row in matrix:
        assert len(row) == 3
    for i in range(3):
        assert matrix[i][i] == 1.0
    for i in range(3):
        for j in range(3):
            assert matrix[i][j] == matrix[j][i]


def test_correlation_endpoint_rejects_single_ticker() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb):
        client = TestClient(app)
        resp = client.get("/api/correlation", params={"tickers": "AAPL"})
    assert resp.status_code == 422


def test_correlation_endpoint_rejects_unknown_ticker() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb):
        client = TestClient(app)
        resp = client.get("/api/correlation", params={"tickers": "AAPL,NOPE"})
    assert resp.status_code == 422


if __name__ == "__main__":
    test_correlation_endpoint_returns_coherent_matrix()
    test_correlation_endpoint_rejects_single_ticker()
    test_correlation_endpoint_rejects_unknown_ticker()
    print("OK: GET /api/correlation")
