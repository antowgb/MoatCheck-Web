"""Composite fundamental score 0-100.

The weights below are relative: if a component is missing, it is
excluded and the remaining weights are re-normalized (never a punitive 0 for
a missing data point). The detail is returned in the breakdown.
"""

from datetime import date
from typing import Any

# --- Fundamental score component weights (adjustable) -----------------
WEIGHT_REVENUE_GROWTH = 0.25   # revenue growth YoY
WEIGHT_OPERATING_MARGIN = 0.20
WEIGHT_ROE = 0.25              # Alpha Vantage exposes ROE, not ROIC
WEIGHT_DEBT_TO_EBITDA = 0.15   # inverted: less debt = better score
WEIGHT_FCF_POSITIVE = 0.15

# --- Linear normalization bounds (value -> 0..1) ------------------------
REVENUE_GROWTH_FLOOR, REVENUE_GROWTH_CAP = 0.0, 0.30   # 0% -> 0, >=30% YoY -> 1
OPERATING_MARGIN_FLOOR, OPERATING_MARGIN_CAP = 0.0, 0.35
# ROE_FLOOR/CAP assume an annualized (TTM) ROE, whether it comes from a current
# OVERVIEW snapshot (ReturnOnEquityTTM) or a rebuilt quarterly snapshot
# (app/data/fetch.py::build_quarterly_fundamentals also computes ROE in TTM,
# net income accumulated over 4 quarters / equity) — fundamental_score() applies
# the same formula and the same bounds in both cases, with no distinction by
# source; only the scale normalization (TTM, never a single quarter)
# needs to be correct upstream.
ROE_FLOOR, ROE_CAP = 0.0, 0.30
DEBT_EBITDA_BEST, DEBT_EBITDA_WORST = 0.0, 4.0         # <=0 (net cash) -> 1, >=4x -> 0 (EBITDA in TTM)

# Debt/EBITDA has no economic meaning for banks/insurers (their balance
# sheet is inherently leveraged by deposits/float, not operating debt), so
# the indicator is excluded for this sector — same reproportioning
# mechanism as a missing value, never a punitive score.
SECTOR_EXCLUDED_FROM_DEBT_TO_EBITDA = {"FINANCIAL SERVICES"}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _normalize(value: float, floor: float, cap: float) -> float:
    return _clamp01((value - floor) / (cap - floor))


def fundamental_score(f: dict, sector: str | None = None) -> tuple[float | None, dict]:
    """Computes the 0-100 fundamental score from a `fundamentals` row.

    ``sector`` (the stock's normalized sector, if known) lets a sector-specific
    indicator be excluded rather than scored — currently only debt_to_ebitda
    for financial-services companies (see SECTOR_EXCLUDED_FROM_DEBT_TO_EBITDA).

    Returns (score, breakdown). Score is None if no component is available.
    """
    weighted: dict[str, dict] = {}
    missing: list[str] = []
    excluded: dict[str, dict] = {}

    def add(name: str, weight: float, normalized: float | None) -> None:
        if normalized is None:
            missing.append(name)
        else:
            weighted[name] = {"weight": weight, "normalized": round(normalized, 4)}

    g = f.get("revenue_growth_yoy")
    add("revenue_growth_yoy", WEIGHT_REVENUE_GROWTH,
        _normalize(g, REVENUE_GROWTH_FLOOR, REVENUE_GROWTH_CAP) if g is not None else None)

    m = f.get("operating_margin")
    add("operating_margin", WEIGHT_OPERATING_MARGIN,
        _normalize(m, OPERATING_MARGIN_FLOOR, OPERATING_MARGIN_CAP) if m is not None else None)

    # ROIC takes priority if it ever exists, otherwise ROE
    profitability = f.get("roic") if f.get("roic") is not None else f.get("roe")
    add("roe_or_roic", WEIGHT_ROE,
        _normalize(profitability, ROE_FLOOR, ROE_CAP) if profitability is not None else None)

    if (sector or "").strip().upper() in SECTOR_EXCLUDED_FROM_DEBT_TO_EBITDA:
        excluded["debt_to_ebitda"] = {"excluded": True, "reason": "not_meaningful_for_sector"}
    else:
        d = f.get("debt_to_ebitda")
        add("debt_to_ebitda", WEIGHT_DEBT_TO_EBITDA,
            _normalize(DEBT_EBITDA_WORST - d, 0.0, DEBT_EBITDA_WORST - DEBT_EBITDA_BEST) if d is not None else None)

    fcf = f.get("free_cash_flow")
    add("fcf_positive", WEIGHT_FCF_POSITIVE,
        (1.0 if fcf > 0 else 0.0) if fcf is not None else None)

    breakdown = {"components": weighted, "missing": missing, "excluded": excluded}
    if not weighted:
        return None, breakdown

    total_weight = sum(c["weight"] for c in weighted.values())
    score = sum(c["weight"] * c["normalized"] for c in weighted.values()) / total_weight * 100
    return round(score, 2), breakdown


def select_snapshot_at(snapshots: list[dict[str, Any]], as_of: date) -> dict[str, Any] | None:
    """Selects, among several `fundamentals` rows for a ticker, the most
    recent one whose ``know_date`` is already known as of the ``as_of`` date.

    Core of the anti-look-ahead safeguard: a snapshot whose ``know_date`` is later
    than ``as_of`` was not yet published at that date and must NEVER be
    used. Returns None if no snapshot is yet known as of ``as_of``
    (the ticker must then be excluded from the basket at that date).
    """
    as_of_str = as_of.isoformat()
    eligible = [s for s in snapshots if s.get("know_date") and s["know_date"] <= as_of_str]
    if not eligible:
        return None
    return max(eligible, key=lambda s: s["know_date"])


def fundamental_score_at(
    snapshots: list[dict[str, Any]], as_of: date, sector: str | None = None
) -> tuple[float | None, dict]:
    """Point-in-time ``fundamental_score``: first selects the snapshot
    known as of ``as_of`` (via ``select_snapshot_at``), then scores it.

    Returns (None, breakdown) if no snapshot is yet known as of this
    date — never falls back to a more recent report.
    """
    snapshot = select_snapshot_at(snapshots, as_of)
    if snapshot is None:
        return None, {"missing": ["no_snapshot_known_before_as_of"], "as_of": as_of.isoformat()}
    score, breakdown = fundamental_score(snapshot, sector=sector)
    breakdown["snapshot_report_date"] = snapshot.get("report_date")
    breakdown["snapshot_know_date"] = snapshot.get("know_date")
    return score, breakdown
