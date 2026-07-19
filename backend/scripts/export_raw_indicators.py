#!/usr/bin/env python3
"""Diagnostic export: raw fundamental-indicator values (not the aggregated
fundamental_score) for every active ticker, plus each continuous indicator's
normalized 0-1 value — computed by importing and calling the SAME
normalization primitives used by app/scoring/fundamentals.py::fundamental_score
(not a re-implementation of the bounds), so this analysis can never silently
diverge from production scoring behavior if the bounds change later.

Read-only: only ever SELECTs from `stocks` and `fundamentals`, never writes.

Usage:
    python3 scripts/export_raw_indicators.py [--output path/to/raw_indicators.csv]

Prints a saturation summary (% at the normalized ceiling/floor, min/max/
median/quartiles of both the raw and normalized value) per continuous
indicator, plus the free_cash_flow sign breakdown, and writes the full
per-ticker CSV.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from app.data.supabase_client import get_supabase
from app.scoring.fundamentals import (
    DEBT_EBITDA_BEST,
    DEBT_EBITDA_WORST,
    OPERATING_MARGIN_CAP,
    OPERATING_MARGIN_FLOOR,
    REVENUE_GROWTH_CAP,
    REVENUE_GROWTH_FLOOR,
    ROE_CAP,
    ROE_FLOOR,
    SECTOR_EXCLUDED_FROM_DEBT_TO_EBITDA,
    _normalize,
)

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "raw_indicators.csv"

# (raw field name, floor, cap) — same triples fundamental_score() normalizes
# with, imported directly from app.scoring.fundamentals (see import above).
CONTINUOUS_INDICATORS = {
    "revenue_growth_yoy": (REVENUE_GROWTH_FLOOR, REVENUE_GROWTH_CAP),
    "operating_margin": (OPERATING_MARGIN_FLOOR, OPERATING_MARGIN_CAP),
    "roe": (ROE_FLOOR, ROE_CAP),
}


def _normalized_debt_to_ebitda(value: float | None) -> float | None:
    """Same transform as fundamental_score(): inverted (less debt is
    better), normalized against DEBT_EBITDA_BEST/WORST."""
    if value is None:
        return None
    return _normalize(DEBT_EBITDA_WORST - value, 0.0, DEBT_EBITDA_WORST - DEBT_EBITDA_BEST)


def _latest_fundamentals(sb, ticker: str) -> dict | None:
    """Most recent fundamentals snapshot by report_date — same query used in
    production by app/api/routes.py::_latest_fundamentals (screener,
    recompute_scores). Deliberately NOT know_date-gated: that point-in-time
    anti-look-ahead rule (select_snapshot_at) is specific to the backtest
    engine, not to what the live screener/dashboard actually expose — using
    it here would undercount vs. the values this diagnostic is meant to
    explain (many fundamentals rows have know_date left NULL and would be
    silently dropped, understating the population versus screener.csv)."""
    rows = (
        sb.table("fundamentals").select("*").eq("ticker", ticker)
        .order("report_date", desc=True).limit(1)
        .execute().data
    )
    return rows[0] if rows else None


def fetch_rows() -> list[dict]:
    sb = get_supabase()
    # ETFs never have fundamentals (see app/data/fetch.py::refresh_ticker) and
    # are structurally excluded from scoring in production (recompute_scores,
    # screener) — excluded here for the same reason, not a new rule.
    stocks = (
        sb.table("stocks")
        .select("ticker, sector, status, asset_type")
        .eq("status", "active")
        .neq("asset_type", "etf")
        .execute()
        .data
    )

    rows: list[dict] = []
    for s in stocks:
        ticker = s["ticker"]
        snapshot = _latest_fundamentals(sb, ticker)
        if snapshot is None:
            continue

        sector = s.get("sector")
        g = snapshot.get("revenue_growth_yoy")
        m = snapshot.get("operating_margin")
        # ROIC takes priority if it ever exists, otherwise ROE — same rule as
        # fundamental_score().
        profitability = snapshot.get("roic") if snapshot.get("roic") is not None else snapshot.get("roe")
        d = snapshot.get("debt_to_ebitda")
        fcf = snapshot.get("free_cash_flow")

        debt_to_ebitda_excluded = (sector or "").strip().upper() in SECTOR_EXCLUDED_FROM_DEBT_TO_EBITDA

        rows.append(
            {
                "ticker": ticker,
                "sector": sector,
                "report_date": snapshot.get("report_date"),
                "revenue_growth_yoy": g,
                "revenue_growth_yoy_normalized": _normalize(g, *CONTINUOUS_INDICATORS["revenue_growth_yoy"])
                if g is not None
                else None,
                "operating_margin": m,
                "operating_margin_normalized": _normalize(m, *CONTINUOUS_INDICATORS["operating_margin"])
                if m is not None
                else None,
                "roe_or_roic": profitability,
                "roe_or_roic_normalized": _normalize(profitability, *CONTINUOUS_INDICATORS["roe"])
                if profitability is not None
                else None,
                "debt_to_ebitda": d,
                "debt_to_ebitda_normalized": _normalized_debt_to_ebitda(d),
                "debt_to_ebitda_excluded_sector": debt_to_ebitda_excluded,
                "free_cash_flow": fcf,
                "free_cash_flow_sign": ("positive" if fcf > 0 else "negative") if fcf is not None else None,
            }
        )
    return rows


def print_summary(df: pd.DataFrame) -> None:
    print(f"\n{len(df)} active ticker(s) with a known fundamentals snapshot.\n")

    summary_lines = []
    header = f"{'indicator':<22}{'% ceiling':>10}{'% floor':>10}{'raw median':>14}{'norm median':>14}"
    print(header)
    print("-" * len(header))

    for name, norm_col, raw_col in [
        ("revenue_growth_yoy", "revenue_growth_yoy_normalized", "revenue_growth_yoy"),
        ("operating_margin", "operating_margin_normalized", "operating_margin"),
        ("roe_or_roic", "roe_or_roic_normalized", "roe_or_roic"),
        ("debt_to_ebitda", "debt_to_ebitda_normalized", "debt_to_ebitda"),
    ]:
        norm = df[norm_col].dropna()
        raw = df[raw_col].dropna()
        if norm.empty:
            print(f"{name:<22}{'n/a':>10}{'n/a':>10}{'n/a':>14}{'n/a':>14}")
            continue

        pct_ceiling = round(100 * (norm >= 1.0 - 1e-9).sum() / len(norm), 1)
        pct_floor = round(100 * (norm <= 0.0 + 1e-9).sum() / len(norm), 1)
        raw_median = raw.median()
        norm_median = norm.median()
        print(f"{name:<22}{pct_ceiling:>9}%{pct_floor:>9}%{raw_median:>14.4f}{norm_median:>14.4f}")

        print(
            f"    raw:  min={raw.min():.4f}  q1={raw.quantile(.25):.4f}  "
            f"median={raw_median:.4f}  q3={raw.quantile(.75):.4f}  max={raw.max():.4f}  (n={len(raw)})"
        )
        print(
            f"    norm: min={norm.min():.4f}  q1={norm.quantile(.25):.4f}  "
            f"median={norm_median:.4f}  q3={norm.quantile(.75):.4f}  max={norm.max():.4f}  (n={len(norm)})"
        )
        summary_lines.append((name, pct_ceiling, pct_floor, raw_median, norm_median))

    fcf = df["free_cash_flow_sign"]
    n_total = len(fcf)
    n_pos = (fcf == "positive").sum()
    n_neg = (fcf == "negative").sum()
    n_null = fcf.isna().sum()
    print(f"\n{'free_cash_flow':<22}"
          f"{round(100 * n_pos / n_total, 1) if n_total else float('nan'):>9}% positive"
          f"{round(100 * n_neg / n_total, 1) if n_total else float('nan'):>9}% negative"
          f"{round(100 * n_null / n_total, 1) if n_total else float('nan'):>9}% NULL")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="CSV output path.")
    args = parser.parse_args()

    rows = fetch_rows()
    if not rows:
        print("No active ticker with a known fundamentals snapshot found.")
        return 0

    df = pd.DataFrame(rows)
    df.to_csv(args.output, index=False)
    print(f"Wrote {len(df)} row(s) to {args.output}")

    print_summary(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
