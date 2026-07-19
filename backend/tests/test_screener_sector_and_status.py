"""Tasks 4 & 5: GET /api/screener matches ?sector= case-insensitively against
the normalized value, and excludes tickers not in status='active' (e.g.
pending_refresh tickers with no scores yet), using a fake Supabase client
(no real network/DB).
Run with pytest if available, else directly:
    python3 tests/test_screener_sector_and_status.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.data.fetch import normalize_sector


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, tables):
        self._table_name = table_name
        self._tables = tables
        self._filters = {}
        self._neq = {}
        self._order = None
        self._limit = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, field, value):
        self._filters[field] = value
        return self

    def neq(self, field, value):
        self._neq[field] = value
        return self

    def in_(self, field, values):
        self._filters[field] = set(values)
        return self

    def is_(self, field, _value):
        self._filters[field] = None
        return self

    def order(self, field, desc=False):
        self._order = (field, desc)
        return self

    def limit(self, n):
        self._limit = n
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
        for field, value in self._neq.items():
            rows = [r for r in rows if r.get(field) != value]
        if self._order:
            field, desc = self._order
            rows = sorted(rows, key=lambda r: r.get(field), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _FakeResult(rows)


class _FakeSupabase:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(name, self._tables)


def _fake_tables() -> dict:
    return {
        "stocks": [
            {
                "ticker": "AAPL",
                "name": "Apple",
                "sector": normalize_sector("TECHNOLOGY"),
                "industry": "Consumer Electronics",
                "is_benchmark": False,
                "asset_type": "equity",
                "status": "active",
            },
            {
                "ticker": "NKE",
                "name": "Nike",
                "sector": "Consumer Cyclical",
                "industry": "Apparel",
                "is_benchmark": False,
                "asset_type": "equity",
                "status": "pending_refresh",
            },
        ],
        "scores": [
            {
                "ticker": "AAPL",
                "computed_at": "2026-01-01T00:00:00Z",
                "composite_score": 80.0,
                "risk_score": 70.0,
                "fundamental_score": 85.0,
            },
        ],
        "fundamentals": [],
    }


def test_screener_filters_by_sector_case_insensitively() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb):
        client = TestClient(app)
        resp = client.get("/api/screener", params={"sector": "technology"})

    assert resp.status_code == 200, resp.text
    tickers = {r["ticker"] for r in resp.json()["rows"]}
    assert tickers == {"AAPL"}


def test_screener_excludes_pending_refresh_tickers() -> None:
    fake_sb = _FakeSupabase(_fake_tables())
    with patch("app.api.routes.get_supabase", return_value=fake_sb):
        client = TestClient(app)
        resp = client.get("/api/screener")

    assert resp.status_code == 200, resp.text
    tickers = {r["ticker"] for r in resp.json()["rows"]}
    assert "NKE" not in tickers
    assert tickers == {"AAPL"}


def test_normalize_sector_title_cases_inconsistent_input() -> None:
    assert normalize_sector("FINANCIAL SERVICES") == "Financial Services"
    assert normalize_sector("Technology") == "Technology"
    assert normalize_sector("HEALTHCARE") == "Healthcare"
    assert normalize_sector("  consumer defensive  ") == "Consumer Defensive"
    assert normalize_sector(None) is None
    assert normalize_sector("") is None


if __name__ == "__main__":
    test_screener_filters_by_sector_case_insensitively()
    test_screener_excludes_pending_refresh_tickers()
    test_normalize_sector_title_cases_inconsistent_input()
    print("OK: screener sector case-insensitivity + status='active' filter + normalize_sector()")
