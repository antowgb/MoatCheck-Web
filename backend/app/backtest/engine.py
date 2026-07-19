"""Simple backtest (V1).

At a given past start date:
1. Scores each ticker using ONLY data available as of that date:
   - risk: prices strictly prior to the start date;
   - fundamentals: real POINT-IN-TIME scoring via ``composite_score_at``
     (app/scoring/composite.py) — only uses the most recent `fundamentals`
     snapshot whose ``know_date`` had already passed by the start date. A
     ticker with no snapshot known as of that date is EXCLUDED from the basket
     (never a silent fallback to risk alone: that's the core of the anti-look-ahead).
2. Selects the top N by composite score, equal-weighted basket, among the
   remaining tickers. If fewer than N tickers are scorable, the basket is
   smaller than requested and the response flags it explicitly (``tickers_excluded``).
3. Compares total return and Sharpe of the basket vs. the benchmark up to
   today.
"""

import logging
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from app.data.supabase_client import execute_with_retry, get_supabase
from app.scoring.composite import composite_score, composite_score_at
from app.scoring.risk import (
    PERIODS_PER_YEAR,
    RISK_FREE_RATE,
    annualized_volatility,
    max_drawdown,
    risk_score,
    sharpe_ratio,
    sortino_ratio,
)

logger = logging.getLogger(__name__)


def _load_closes(ticker: str) -> pd.Series:
    sb = get_supabase()
    rows: list[dict] = []
    offset = 0
    while True:  # Supabase pagination (~1000 rows per request limit)
        query = (
            sb.table("price_history")
            .select("date, close")
            .eq("ticker", ticker)
            .order("date")
            .range(offset, offset + 999)
        )
        page = execute_with_retry(query, context=ticker).data
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    if not rows:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = pd.Series(
        [r["close"] for r in rows],
        index=pd.to_datetime([r["date"] for r in rows]),
    )
    return s.sort_index()


def _series_metrics(closes: pd.Series) -> dict[str, float | None]:
    if len(closes) < 2:
        return {"total_return": None, "sharpe": None}
    total_return = float(closes.iloc[-1] / closes.iloc[0] - 1.0)
    returns = closes.pct_change().dropna()
    vol = float(returns.std() * np.sqrt(PERIODS_PER_YEAR))
    sharpe = None
    if vol > 0:
        annual = float(returns.mean() * PERIODS_PER_YEAR)
        sharpe = round((annual - RISK_FREE_RATE) / vol, 3)
    return {"total_return": round(total_return, 4), "sharpe": sharpe}


def _aligned_return(reference_closes: pd.Series, stock_index: pd.DatetimeIndex) -> float | None:
    """Total return of ``reference_closes`` over exactly ``stock_index``'s dates.

    Reindexes (forward-fill) onto the stock's own price dates before computing
    the return, so the comparison never drifts to a different date than the
    stock's (e.g. a benchmark's weekly close landing a day off).
    """
    if reference_closes.empty or len(stock_index) < 2:
        return None
    aligned = reference_closes.reindex(stock_index, method="ffill").dropna()
    if len(aligned) < 2:
        return None
    return round(float(aligned.iloc[-1] / aligned.iloc[0] - 1.0), 4)


def earliest_price_date() -> date | None:
    """Earliest date present in `price_history`, across every ticker (incl. benchmarks).

    Used to give a specific error when `start_date` predates all stored data,
    rather than the generic "no ticker scorable" (which also covers unrelated
    causes, e.g. a ticker with a late IPO).
    """
    sb = get_supabase()
    rows = execute_with_retry(sb.table("price_history").select("date").order("date").limit(1)).data
    return pd.Timestamp(rows[0]["date"]).date() if rows else None


def _low_sample_warning(
    basket_size: int, top_n: int, scorable_count: int, total_universe_count: int
) -> tuple[bool, str | None]:
    """Flags a basket built from too thin a sample to be representative.

    Two independent triggers: the requested basket couldn't be filled, or
    less than half of the tracked universe was scorable at this date (e.g.
    early in the dataset's history, before most tickers have fundamentals).
    """
    reasons = []
    if basket_size < top_n:
        reasons.append(f"only {basket_size} of {top_n} requested stocks were scorable")
    universe_ratio = scorable_count / total_universe_count if total_universe_count else 0.0
    if total_universe_count and universe_ratio < 0.5:
        reasons.append(
            f"only {scorable_count} of {total_universe_count} tickers in the universe "
            "had usable data at this date"
        )
    if not reasons:
        return False, None
    message = ", and ".join(reasons)
    message = message[0].upper() + message[1:] + ". Results may not be representative."
    return True, message


def _score_at(ticker: str, closes_before: pd.Series, as_of: date) -> tuple[float | None, str | None]:
    """Point-in-time composite score as of ``as_of``. Returns (score, exclusion_reason).

    ``exclusion_reason`` is None if the ticker is scorable; otherwise the score
    is None and the reason explains why it's excluded from the basket (never a
    silent fallback to risk alone).
    """
    vol = annualized_volatility(closes_before)
    sr = sharpe_ratio(closes_before)
    mdd = max_drawdown(closes_before)
    sortino = sortino_ratio(closes_before)
    r_score, _ = risk_score(vol, sr, mdd, sortino)
    if r_score is None:
        return None, "insufficient price history before the start date"

    sb = get_supabase()
    fund_rows = execute_with_retry(sb.table("fundamentals").select("*").eq("ticker", ticker), context=ticker).data
    stock_rows = execute_with_retry(sb.table("stocks").select("sector").eq("ticker", ticker), context=ticker).data
    sector = stock_rows[0].get("sector") if stock_rows else None
    score, breakdown = composite_score_at(fund_rows or [], r_score, as_of, sector=sector)

    # Anti-look-ahead: if fundamental_score_at found no snapshot whose
    # know_date <= as_of, we EXCLUDE the ticker rather than falling back to
    # risk alone (see app/scoring/fundamentals.py::fundamental_score_at).
    fundamental_missing = breakdown.get("fundamental_detail", {}).get("missing", [])
    if "no_snapshot_known_before_as_of" in fundamental_missing:
        return None, "no fundamental report published (know_date) before the start date"

    return score, None


def _select_basket_at(
    tickers: list[str],
    closes_full: dict[str, pd.Series],
    as_of: date,
    top_n: int,
    exact: bool,
) -> tuple[list[str], list[tuple[str, float]], list[dict[str, str]]]:
    """Scores every ticker as of ``as_of`` (using only price history strictly
    before it, same anti-look-ahead as the initial selection) and picks the
    basket: top-N by score, or every scorable ticker if ``exact``.

    Shared by the initial basket construction and every subsequent
    rebalance — same selection logic re-run at a later date, nothing new.
    """
    as_of_ts = pd.Timestamp(as_of)
    scored: list[tuple[str, float]] = []
    excluded: list[dict[str, str]] = []
    for t in tickers:
        before = closes_full[t][closes_full[t].index < as_of_ts]
        score, exclude_reason = _score_at(t, before, as_of)
        if score is not None:
            scored.append((t, score))
        else:
            reason = exclude_reason or "score not computable"
            excluded.append({"ticker": t, "reason": reason})

    scored.sort(key=lambda x: x[1], reverse=True)
    selected = [t for t, _ in scored] if exact else [t for t, _ in scored[:top_n]]
    return selected, scored, excluded


def _rebalance_dates(start_date: date, frequency: str, end_date: date) -> list[date]:
    """Rebalance dates strictly after ``start_date``, every 1 (monthly) or 3
    (quarterly) months, up to ``end_date``."""
    months = {"monthly": 1, "quarterly": 3}[frequency]
    dates: list[date] = []
    current = pd.Timestamp(start_date) + pd.DateOffset(months=months)
    end_ts = pd.Timestamp(end_date)
    while current <= end_ts:
        dates.append(current.date())
        current = current + pd.DateOffset(months=months)
    return dates


def _period_relative_prices(
    basket: list[str],
    closes_full: dict[str, pd.Series],
    period_start_ts: pd.Timestamp,
    period_end_ts: pd.Timestamp | None,
) -> pd.DataFrame:
    """Each basket ticker's price normalized to 1.0 at the period's first
    available price on/after ``period_start_ts``, restricted to
    [period_start_ts, period_end_ts) (or to the end of data if
    ``period_end_ts`` is None). Columns with no data in the window are
    dropped (a ticker delisted mid-period simply stops contributing)."""
    columns: dict[str, pd.Series] = {}
    for t in basket:
        s = closes_full[t]
        s = s[s.index >= period_start_ts]
        if period_end_ts is not None:
            s = s[s.index < period_end_ts]
        if len(s) == 0:
            continue
        columns[t] = s / s.iloc[0]
    return pd.DataFrame(columns)


def _build_rebalanced_curve(
    periods: list[tuple[date, list[str]]],
    closes_full: dict[str, pd.Series],
    transaction_cost_bps: float,
) -> tuple[pd.Series, list[dict[str, Any]], float]:
    """Chains successive equal-weighted baskets into a single equity curve,
    deducting a transaction cost at each rebalance boundary (never at the
    initial construction — that's a buy-in from cash, not a rebalance).

    Transaction cost at a rebalance = transaction_cost_bps * sum of the
    absolute weight change (buys + sells, not netted — a ticker whose weight
    is unchanged contributes 0) across the union of the previous and new
    basket. The previous basket's weights are its *drifted* weights (each
    ticker's own return since the last rebalance, not the stale equal
    weights it started that period with) — that drift is exactly what a
    rebalance corrects, and what it costs to correct.
    """
    cost_fraction = transaction_cost_bps / 10000.0
    cumulative_value = 1.0
    full_curve = pd.Series(dtype=float)
    prev_end_relative: dict[str, float] = {}
    prev_basket: list[str] = []
    rebalances: list[dict[str, Any]] = []
    total_cost = 0.0

    for i, (period_start, basket) in enumerate(periods):
        if not basket:
            # Nothing scorable at this date: hold the previous basket rather
            # than emptying the portfolio (logged, not silent).
            if not prev_basket:
                continue
            basket = prev_basket
            logger.info("Rebalance %s: no ticker scorable, holding previous basket.", period_start)

        if i > 0:
            total_rel = sum(prev_end_relative.get(t, 0.0) for t in prev_basket)
            weights_before = {
                t: (prev_end_relative.get(t, 0.0) / total_rel if total_rel > 0 else 0.0) for t in prev_basket
            }
            target_weights = {t: 1.0 / len(basket) for t in basket}
            turnover = sum(
                abs(target_weights.get(t, 0.0) - weights_before.get(t, 0.0))
                for t in set(weights_before) | set(target_weights)
            )
            period_cost = cost_fraction * turnover
            cumulative_value *= max(0.0, 1.0 - period_cost)
            total_cost += period_cost
            rebalances.append(
                {
                    "date": period_start.isoformat(),
                    "selected_tickers": basket,
                    "turnover": round(turnover, 4),
                    "transaction_cost": round(period_cost, 6),
                }
            )

        period_start_ts = pd.Timestamp(period_start)
        period_end_ts = pd.Timestamp(periods[i + 1][0]) if i + 1 < len(periods) else None
        period_df = _period_relative_prices(basket, closes_full, period_start_ts, period_end_ts)
        if period_df.empty:
            prev_basket = basket
            continue

        period_curve = period_df.ffill().mean(axis=1)
        scaled = period_curve * cumulative_value
        full_curve = pd.concat([full_curve, scaled])

        cumulative_value = float(scaled.iloc[-1])
        last_row = period_df.ffill().iloc[-1]
        prev_end_relative = {t: float(last_row[t]) for t in basket if t in last_row.index and pd.notna(last_row[t])}
        prev_basket = basket

    return full_curve, rebalances, total_cost


def run_backtest(
    start_date: date,
    top_n: int,
    benchmark: str = "SPY",
    universe_tickers: list[str] | None = None,
    exact: bool = False,
    rebalance_frequency: str | None = None,
    transaction_cost_bps: float = 10.0,
) -> dict[str, Any]:
    """``universe_tickers``, if given, restricts the scored universe (and thus
    ``total_universe_count``/``universe_scorable_ratio``) to that subset —
    validated (exists, not is_benchmark) by the caller (routes.py::backtest)
    before this runs.

    ``rebalance_frequency`` (``None``, ``"monthly"`` or ``"quarterly"``):
    ``None`` (default) is the original static basket — built once at
    ``start_date``, held unchanged to today, no transaction cost. Set to
    re-run the same selection (``_select_basket_at``) at every rebalance
    date, chaining the resulting baskets into a single curve
    (``_build_rebalanced_curve``) and deducting ``transaction_cost_bps``
    (basis points) on the turnover at each rebalance boundary. ``basket``/
    ``basket_curve`` become net of these costs when rebalancing is active;
    ``selected_tickers``/``scores_at_start``/``tickers_excluded`` still
    describe the *initial* basket only, not every subsequent rebalance
    (see the new ``rebalances`` list for that detail).

    ``exact=True`` (only set when ``universe_tickers`` is an explicit manual
    selection) means the basket is exactly those tickers, not a top-N ranking
    over them: every ticker that passes the existing know_date/scorable
    checks is kept, and ``top_n`` is not used to truncate the basket — it is
    only still used for basket_note/low_sample_warning wording (the
    "requested count" becomes ``len(universe_tickers)`` instead).
    """
    sb = get_supabase()
    tickers = [
        r["ticker"]
        for r in execute_with_retry(sb.table("stocks").select("ticker").eq("is_benchmark", False)).data
    ]
    if universe_tickers:
        tickers = [t for t in tickers if t in set(universe_tickers)]
    if not tickers:
        return {"error": "No ticker in the database — run /refresh first."}

    start_ts = pd.Timestamp(start_date)
    closes_full: dict[str, pd.Series] = {t: _load_closes(t) for t in tickers}

    selected0, scored, excluded = _select_basket_at(tickers, closes_full, start_date, top_n, exact)
    for e in excluded:
        logger.info("Backtest: %s excluded (%s)", e["ticker"], e["reason"])

    if not scored:
        return {
            "error": f"No ticker scorable before {start_date} (insufficient history).",
            "tickers_excluded": excluded,
            "tickers_excluded_count": len(excluded),
        }

    selected = selected0
    requested_count = len(universe_tickers) if exact else top_n

    # Explicit flag (never silent) if the requested basket (top_n, or the
    # exact manual selection) can't be filled for lack of scorable tickers
    # at this date.
    basket_note = None
    if len(selected) < requested_count:
        basket_note = (
            f"Only {len(scored)} ticker(s) out of {len(tickers)} had a "
            f"fundamental report known (know_date) before {start_date}: the basket "
            f"only contains {len(selected)}/{requested_count} requested stocks."
        )

    rebalance_count = 0
    total_transaction_cost = 0.0
    rebalances: list[dict[str, Any]] = []

    if rebalance_frequency is None:
        # Equal-weighted basket performance since start_date (unchanged from
        # before rebalancing existed — no rebalance_frequency, no behavior change).
        basket_returns: list[pd.Series] = []
        for t in selected:
            after = closes_full[t][closes_full[t].index >= start_ts]
            if len(after) >= 2:
                basket_returns.append(after / after.iloc[0])
        if not basket_returns:
            return {"error": "No price data after the start date."}
        basket_curve = pd.concat(basket_returns, axis=1).ffill().mean(axis=1).dropna()
    else:
        reb_dates = _rebalance_dates(start_date, rebalance_frequency, date.today())
        periods: list[tuple[date, list[str]]] = [(start_date, selected)]
        for reb_date in reb_dates:
            reb_selected, _, reb_excluded = _select_basket_at(tickers, closes_full, reb_date, top_n, exact)
            for e in reb_excluded:
                logger.info("Rebalance %s: %s excluded (%s)", reb_date, e["ticker"], e["reason"])
            periods.append((reb_date, reb_selected))

        basket_curve, rebalances, total_transaction_cost = _build_rebalanced_curve(
            periods, closes_full, transaction_cost_bps
        )
        rebalance_count = len(rebalances)
        if basket_curve.empty:
            return {"error": "No price data after the start date."}

    # Benchmark: first from Supabase (works everywhere, including on
    # Render's datacenter IP where yfinance is blocked), else fall back to
    # yfinance (useful locally if the benchmark hasn't been seeded in the DB yet).
    bench_full = _load_closes(benchmark)
    bench_closes = bench_full[bench_full.index >= start_ts]
    if bench_closes.empty:
        logger.info("Benchmark %s absent from Supabase — falling back to yfinance.", benchmark)
        try:
            bench_hist = yf.Ticker(benchmark).history(start=start_date.isoformat(), auto_adjust=True)
            bench_closes = bench_hist["Close"]
            bench_closes.index = bench_closes.index.tz_localize(None)
            # Supabase prices are WEEKLY: resample the yfinance fallback
            # (daily) to weekly to stay consistent with _series_metrics.
            bench_closes = bench_closes.resample("W").last().dropna()
        except Exception as exc:
            logger.error("Failed to fetch benchmark %s: %s", benchmark, exc)
            bench_closes = pd.Series(dtype=float)

    # True only if BOTH Supabase and the yfinance fallback failed to produce
    # any benchmark price — lets the frontend explain an empty benchmark
    # curve/metrics instead of showing it silently blank.
    benchmark_data_unavailable = bench_closes.empty

    basket_metrics = _series_metrics(basket_curve)
    bench_metrics = _series_metrics(bench_closes)

    # Per-stock comparison vs. SPY and vs. the sector benchmark ETF
    # (stocks.sector_benchmark_ticker), aligned to each stock's own price dates.
    stock_meta = {
        r["ticker"]: r
        for r in execute_with_retry(
            sb.table("stocks").select("ticker, asset_type, sector_benchmark_ticker").in_("ticker", selected)
        ).data
    }
    sector_closes_cache: dict[str, pd.Series] = {}
    per_stock_vs_benchmarks: list[dict[str, Any]] = []
    for t in selected:
        meta = stock_meta.get(t, {})
        if meta.get("asset_type") not in (None, "equity"):
            continue  # ETFs have no benchmark comparison to compute

        stock_after = closes_full[t][closes_full[t].index >= start_ts]
        stock_return = _series_metrics(stock_after)["total_return"]
        benchmark_return = _aligned_return(bench_full, stock_after.index)

        sector_ticker = meta.get("sector_benchmark_ticker")
        sector_return = None
        if sector_ticker:
            if sector_ticker not in sector_closes_cache:
                sector_closes_cache[sector_ticker] = _load_closes(sector_ticker)
            sector_return = _aligned_return(sector_closes_cache[sector_ticker], stock_after.index)

        per_stock_vs_benchmarks.append({
            "ticker": t,
            "stock_return": stock_return,
            "benchmark_ticker": benchmark,
            "benchmark_return": benchmark_return,
            "vs_benchmark": (
                round(stock_return - benchmark_return, 4)
                if stock_return is not None and benchmark_return is not None else None
            ),
            "sector_benchmark_ticker": sector_ticker,
            "sector_benchmark_available": sector_return is not None,
            "sector_return": sector_return,
            "vs_sector_benchmark": (
                round(stock_return - sector_return, 4)
                if stock_return is not None and sector_return is not None else None
            ),
        })

    total_universe_count = len(tickers)
    scorable_count = len(scored)
    basket_size = len(selected)
    low_sample_warning, low_sample_warning_message = _low_sample_warning(
        basket_size, requested_count, scorable_count, total_universe_count
    )

    return {
        "start_date": start_date.isoformat(),
        "top_n": None if exact else top_n,
        "exact_tickers": exact,
        "selected_tickers": selected,
        "scores_at_start": {t: s for t, s in scored},
        "tickers_excluded": excluded,
        "tickers_excluded_count": len(excluded),
        "tickers_scorable_count": scorable_count,
        "total_universe_count": total_universe_count,
        "basket_coverage_ratio": round(basket_size / requested_count, 3) if requested_count else None,
        "universe_scorable_ratio": round(scorable_count / total_universe_count, 3) if total_universe_count else None,
        "low_sample_warning": low_sample_warning,
        "low_sample_warning_message": low_sample_warning_message,
        "note": basket_note,
        "rebalance_frequency": rebalance_frequency,
        "transaction_cost_bps": transaction_cost_bps if rebalance_frequency else None,
        "rebalance_count": rebalance_count,
        "total_transaction_cost": round(total_transaction_cost, 6),
        "rebalances": rebalances,
        "basket": basket_metrics,
        "benchmark": {"ticker": benchmark, **bench_metrics},
        "benchmark_data_unavailable": benchmark_data_unavailable,
        "per_stock_vs_benchmarks": per_stock_vs_benchmarks,
        "basket_curve": [
            {"date": d.date().isoformat(), "value": round(float(v), 4)}
            for d, v in basket_curve.items()
        ],
        "benchmark_curve": [
            {"date": d.date().isoformat(), "value": round(float(v / bench_closes.iloc[0]), 4)}
            for d, v in bench_closes.items()
        ] if len(bench_closes) else [],
    }
