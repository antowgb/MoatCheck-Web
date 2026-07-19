"""Tasks 2 & 3: debt_to_ebitda bounded near-zero EBITDA (app/data/fetch.py),
and excluded entirely for FINANCIAL SERVICES tickers (app/scoring/fundamentals.py).
Run with pytest if available, else directly:
    python3 tests/test_debt_to_ebitda_and_sector_exclusion.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.fetch import _compute_debt_to_ebitda, normalize_sector
from app.scoring.fundamentals import fundamental_score


def test_debt_to_ebitda_null_when_ebitda_near_zero_vs_revenue() -> None:
    # DDOG-like case: tiny positive EBITDA vs. a large net-cash position ->
    # would otherwise produce a huge, misleading negative ratio.
    value, too_small = _compute_debt_to_ebitda(debt=100.0, cash=500.0, ebitda_ttm=2.0, revenue_ttm=1000.0)
    assert value is None
    assert too_small is True


def test_debt_to_ebitda_computed_normally_above_threshold() -> None:
    value, too_small = _compute_debt_to_ebitda(debt=400.0, cash=100.0, ebitda_ttm=100.0, revenue_ttm=1000.0)
    assert value == 3.0
    assert too_small is False


def test_debt_to_ebitda_null_without_revenue_reference_stays_conservative() -> None:
    # No revenue_ttm available: can't judge "too close to zero", so fall back
    # to the plain positive-EBITDA rule (never invent a threshold check).
    value, too_small = _compute_debt_to_ebitda(debt=100.0, cash=50.0, ebitda_ttm=10.0, revenue_ttm=None)
    assert value == 5.0
    assert too_small is False


def test_fundamental_score_reproportions_when_debt_to_ebitda_missing() -> None:
    base = {
        "revenue_growth_yoy": 0.30,
        "operating_margin": 0.35,
        "roe": 0.30,
        "debt_to_ebitda": 1.0,
        "free_cash_flow": 10.0,
    }
    without = dict(base)
    without["debt_to_ebitda"] = None

    score_with, breakdown_with = fundamental_score(base)
    score_without, breakdown_without = fundamental_score(without)

    assert "debt_to_ebitda" in breakdown_with["components"]
    assert "debt_to_ebitda" not in breakdown_without["components"]
    assert "debt_to_ebitda" in breakdown_without["missing"]
    # No phantom perfect score: excluding a component changes the composite,
    # it isn't silently treated as normalized=1.0.
    assert score_without != score_with
    # Reproportioned onto the 4 remaining components (revenue_growth_yoy,
    # operating_margin, roe_or_roic, fcf_positive).
    assert len(breakdown_without["components"]) == 4
    assert abs(sum(c["weight"] for c in breakdown_without["components"].values()) - 0.85) < 1e-9


def test_fundamental_score_excludes_debt_to_ebitda_for_financial_services() -> None:
    f = {
        "revenue_growth_yoy": 0.20,
        "operating_margin": 0.30,
        "roe": 0.25,
        "debt_to_ebitda": 10.22,  # e.g. JPM — present but should be ignored
        "free_cash_flow": 5.0,
    }
    score, breakdown = fundamental_score(f, sector="Financial Services")

    assert "debt_to_ebitda" not in breakdown["components"]
    assert "debt_to_ebitda" not in breakdown["missing"]
    assert breakdown["excluded"]["debt_to_ebitda"] == {"excluded": True, "reason": "not_meaningful_for_sector"}

    assert len(breakdown["components"]) == 4
    assert abs(sum(c["weight"] for c in breakdown["components"].values()) - 0.85) < 1e-9

    # Sector match is case-insensitive (normalize_sector may produce Title Case).
    _, breakdown_upper = fundamental_score(f, sector="FINANCIAL SERVICES")
    assert "debt_to_ebitda" not in breakdown_upper["components"]


def test_fundamental_score_keeps_debt_to_ebitda_for_other_sectors() -> None:
    f = {
        "revenue_growth_yoy": 0.20,
        "operating_margin": 0.30,
        "roe": 0.25,
        "debt_to_ebitda": 1.0,
        "free_cash_flow": 5.0,
    }
    _, breakdown = fundamental_score(f, sector="Technology")
    assert "debt_to_ebitda" in breakdown["components"]


def test_financial_services_score_strictly_improves_via_real_normalize_sector() -> None:
    """Regression for the deployed-but-not-applied bug: uses the sector
    string exactly as normalize_sector() produces it (not a hand-typed
    "Financial Services" constant in the test), for a JPM-like ticker with
    debt_to_ebitda >= 4x (normalized score 0.0 pre-exclusion). Excluding a
    weight-0.15 component that scored 0 and reproportioning the rest must
    strictly INCREASE the score — if this ever regresses (e.g. sector
    comparison silently no-ops), this assertion catches it directly on the
    score delta, not just on breakdown keys."""
    raw_sector_from_provider = "FINANCIAL SERVICES"  # as seen pre-normalization (task 4 symptom)
    normalized = normalize_sector(raw_sector_from_provider)
    assert normalized == "Financial Services"

    jpm_like = {
        "revenue_growth_yoy": 0.20,
        "operating_margin": 0.30,
        "roe": 0.25,
        "debt_to_ebitda": 10.22,  # JPM's real value: >= 4x -> normalized 0.0
        "free_cash_flow": None,
    }

    score_without_exclusion, _ = fundamental_score(jpm_like, sector=None)
    score_with_exclusion, breakdown = fundamental_score(jpm_like, sector=normalized)

    assert breakdown["excluded"].get("debt_to_ebitda", {}).get("excluded") is True
    assert score_with_exclusion > score_without_exclusion, (
        f"expected exclusion to raise the score (0-normalized 15%-weight component dropped), "
        f"got {score_with_exclusion} vs {score_without_exclusion} without exclusion"
    )


def test_end_to_end_pipeline_normalizes_raw_sector_before_scoring() -> None:
    """Full ingestion-shape check: a ticker stored with the RAW,
    non-normalized sector string ("FINANCIAL SERVICES", as Alpha Vantage's
    OVERVIEW/the pre-task-4 database returned it) must, once passed through
    normalize_sector() (as app/data/fetch.py does at ingestion) and then into
    fundamental_score, have debt_to_ebitda excluded — proving the
    normalize_sector -> fundamental_score wiring works end to end, not just
    each function in isolation."""
    stock_row = {"ticker": "JPMLIKE", "sector": "FINANCIAL SERVICES"}
    fundamentals_row = {
        "revenue_growth_yoy": 0.20,
        "operating_margin": 0.30,
        "roe": 0.25,
        "debt_to_ebitda": 10.22,
        "free_cash_flow": None,
    }

    # Mirrors app/data/fetch.py's ingestion-time normalization.
    normalized_sector = normalize_sector(stock_row["sector"])
    # Mirrors app/api/routes.py::recompute_scores passing stocks.sector through.
    score, breakdown = fundamental_score(fundamentals_row, sector=normalized_sector)

    assert breakdown["excluded"].get("debt_to_ebitda", {}).get("excluded") is True
    assert "debt_to_ebitda" not in breakdown["components"]


if __name__ == "__main__":
    test_debt_to_ebitda_null_when_ebitda_near_zero_vs_revenue()
    test_debt_to_ebitda_computed_normally_above_threshold()
    test_debt_to_ebitda_null_without_revenue_reference_stays_conservative()
    test_fundamental_score_reproportions_when_debt_to_ebitda_missing()
    test_fundamental_score_excludes_debt_to_ebitda_for_financial_services()
    test_fundamental_score_keeps_debt_to_ebitda_for_other_sectors()
    test_financial_services_score_strictly_improves_via_real_normalize_sector()
    test_end_to_end_pipeline_normalizes_raw_sector_before_scoring()
    print("OK: debt_to_ebitda threshold + financial-services exclusion")
