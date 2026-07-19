"""Final composite score: fundamental / risk weighting."""

from datetime import date
from typing import Any

from app.scoring.fundamentals import fundamental_score_at

# Final score weighting (adjustable). Must sum to 1.
WEIGHT_FUNDAMENTALS = 0.60
WEIGHT_RISK = 0.40


def composite_score(
    fundamental: float | None, risk: float | None
) -> tuple[float | None, dict]:
    """Combines the two 0-100 sub-scores.

    If one is missing, the other counts for 100% (flagged in the breakdown).
    """
    parts: dict[str, dict] = {}
    if fundamental is not None:
        parts["fundamental"] = {"weight": WEIGHT_FUNDAMENTALS, "score": fundamental}
    if risk is not None:
        parts["risk"] = {"weight": WEIGHT_RISK, "score": risk}

    breakdown = {
        "weights": {"fundamental": WEIGHT_FUNDAMENTALS, "risk": WEIGHT_RISK},
        "missing": [k for k in ("fundamental", "risk") if k not in parts],
    }
    if not parts:
        return None, breakdown

    total_weight = sum(p["weight"] for p in parts.values())
    score = sum(p["weight"] * p["score"] for p in parts.values()) / total_weight
    return round(score, 2), breakdown


def composite_score_at(
    fundamentals_snapshots: list[dict[str, Any]],
    risk: float | None,
    as_of: date,
    sector: str | None = None,
) -> tuple[float | None, dict]:
    """POINT-IN-TIME composite score for a backtest without look-ahead.

    Selects the fundamental score via ``fundamental_score_at`` (only uses
    the snapshot whose ``know_date <= as_of``, never a more recent report).
    If no fundamental snapshot is yet known as of this date, the caller
    (``app/backtest/engine.py::_score_at``) EXCLUDES the ticker from the
    basket entirely rather than falling back to risk alone — this function
    only reports that absence via ``fundamental_detail.missing`` (see
    ``composite_score``), it doesn't decide the exclusion itself.
    """
    f_score, f_breakdown = fundamental_score_at(fundamentals_snapshots, as_of, sector=sector)
    score, breakdown = composite_score(f_score, risk)
    breakdown["fundamental_detail"] = f_breakdown
    return score, breakdown
