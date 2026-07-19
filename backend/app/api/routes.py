"""FastAPI endpoints."""

import logging
import os
from datetime import date, datetime, timezone
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from app.backtest.compare import run_compare
from app.backtest.engine import _load_closes, earliest_price_date, run_backtest
from app.data.fetch import refresh_due, refresh_tickers
from app.data.supabase_client import RETRYABLE_EXC, execute_with_retry, get_supabase
from app.scoring.composite import composite_score
from app.scoring.fundamentals import fundamental_score
from app.scoring.portfolio import efficient_frontier, inverse_volatility_weights
from app.scoring.risk import (
    MIN_PRICE_POINTS,
    annualized_volatility,
    correlation_matrix,
    max_drawdown,
    risk_score,
    sharpe_ratio,
    sortino_ratio,
)
from scripts.backfill_sector_benchmarks import SECTOR_TO_ETF

logger = logging.getLogger(__name__)
router = APIRouter()

# Sector ETF ticker -> readable GICS sector name, e.g. "XLK" -> "Technology".
# Inverse of SECTOR_TO_ETF; first sector name mapped to a given ETF wins,
# which for the current entries is always the canonical GICS spelling
# (HEALTH CARE/FINANCIALS/CONSUMER STAPLES/CONSUMER DISCRETIONARY/MATERIALS
# each precede their Alpha-Vantage-casing-variant duplicate in that dict).
ETF_TO_SECTOR_NAME: dict[str, str] = {}
for _sector, _etf in SECTOR_TO_ETF.items():
    ETF_TO_SECTOR_NAME.setdefault(_etf, _sector.title())

_RETRYABLE_EXC = RETRYABLE_EXC


def _execute_with_retry(query: Any, context: str = "") -> Any:
    return execute_with_retry(query, context=context)


def _insert_idempotent(query: Any, context: str = "") -> None:
    """Runs an insert, tolerating a duplicate-key error as a no-op.

    Guards against retries in _execute_with_retry re-sending an insert whose
    first attempt actually succeeded server-side but timed out on the
    response — the retry then hits the row it already created.
    """
    try:
        _execute_with_retry(query, context=context)
    except APIError as exc:
        if exc.code != "23505":
            raise
        logger.info("Duplicate-key on insert%s treated as already-applied: %s", f" [{context}]" if context else "", exc)


# --- Schemas -----------------------------------------------------------------

class RefreshRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list, max_length=50)
    benchmarks: list[str] = Field(default_factory=list, max_length=10)  # e.g. ["SPY"] — prices stored, excluded from the universe


class BacktestRequest(BaseModel):
    start_date: date
    top_n: int | None = Field(
        default=None, ge=1, le=50, description="Automatic top-N ranking. Mutually exclusive with `tickers`."
    )
    benchmark: str = "SPY"
    tickers: list[str] | None = Field(
        default=None,
        max_length=50,
        description="Exact manual basket (no ranking/top_n applied). Mutually exclusive with `top_n`.",
    )
    rebalance_frequency: Literal["monthly", "quarterly"] | None = Field(
        default=None,
        description=(
            "Periodic rebalancing: re-select the basket at every interval and chain the "
            "resulting baskets, deducting transaction_cost_bps at each rebalance. "
            "Omit (default None) for the original static basket (built once, held to today, no cost)."
        ),
    )
    transaction_cost_bps: float = Field(
        default=10.0,
        ge=0,
        le=1000,
        description="Transaction cost in basis points, applied to turnover at each rebalance. Ignored if rebalance_frequency is not set.",
    )


class AddStockRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    asset_type: Literal["equity", "etf"] = "equity"


class UpdateStockRequest(BaseModel):
    sector_benchmark_ticker: str | None = Field(default=None, max_length=10)


# --- Helpers -----------------------------------------------------------------

def _require_admin(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> None:
    """FastAPI dependency: protects public write routes (adding a ticker).

    Fails closed if ADMIN_API_KEY isn't configured server-side (no
    bypass possible in case of a misconfigured deployment).
    """
    expected = os.environ.get("ADMIN_API_KEY")
    if not expected or x_admin_key != expected:
        raise HTTPException(401, "Missing or invalid admin key (X-Admin-Key header).")


def _latest_score(ticker: str) -> dict[str, Any] | None:
    sb = get_supabase()
    try:
        query = (
            sb.table("scores").select("*").eq("ticker", ticker)
            .order("computed_at", desc=True).limit(1)
        )
        rows = _execute_with_retry(query, context=ticker).data
    except _RETRYABLE_EXC:
        logger.error("Supabase network error fetching score for %s", ticker, exc_info=True)
        return None
    except Exception:
        logger.error("Unexpected error fetching score for %s", ticker, exc_info=True)
        return None
    return rows[0] if rows else None


def _latest_scores_by_ticker(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """Latest score row per ticker, fetched in a single Supabase round-trip."""
    if not tickers:
        return {}
    try:
        query = (
            get_supabase().table("scores").select("*").in_("ticker", tickers)
            .order("computed_at", desc=True)
        )
        rows = _execute_with_retry(query, context=f"{len(tickers)} tickers").data
    except _RETRYABLE_EXC:
        logger.error("Supabase network error fetching scores for %s", tickers, exc_info=True)
        return {}
    except Exception:
        logger.error("Unexpected error fetching scores for %s", tickers, exc_info=True)
        return {}

    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest.setdefault(row["ticker"], row)
    return latest


def _latest_fundamentals(ticker: str) -> dict[str, Any] | None:
    """Most RECENT fundamentals snapshot (report_date) for a ticker.

    Sorted by report_date, not fetched_at: several quarters now share
    the same fetched_at (a single refresh inserts them all at once),
    so fetched_at can no longer be used to tell the latest quarter apart.
    """
    sb = get_supabase()
    query = (
        sb.table("fundamentals").select("*").eq("ticker", ticker)
        .order("report_date", desc=True).limit(1)
    )
    rows = _execute_with_retry(query, context=ticker).data
    return rows[0] if rows else None


# --- Endpoints ---------------------------------------------------------------

@router.get("/stocks/benchmarks")
def list_benchmarks() -> list[dict[str, Any]]:
    """Tickers usable as a backtest benchmark: the general benchmark
    (is_benchmark=True, e.g. SPY) plus every sector ETF (asset_type='etf')."""
    sb = get_supabase()
    general = _execute_with_retry(
        sb.table("stocks").select("ticker, name, is_benchmark").eq("is_benchmark", True)
    ).data
    etfs = _execute_with_retry(
        sb.table("stocks").select("ticker, name, is_benchmark").eq("asset_type", "etf").order("ticker")
    ).data
    for e in etfs:
        e["sector_name"] = ETF_TO_SECTOR_NAME.get(e["ticker"])
    return general + etfs


@router.get("/stocks")
def list_stocks() -> list[dict[str, Any]]:
    sb = get_supabase()
    stocks = _execute_with_retry(
        sb.table("stocks").select("*").eq("is_benchmark", False).neq("asset_type", "etf").order("ticker")
    ).data
    scores = _latest_scores_by_ticker([s["ticker"] for s in stocks])
    for s in stocks:
        score = scores.get(s["ticker"])
        s["composite_score"] = score["composite_score"] if score else None
        s["computed_at"] = score["computed_at"] if score else None
    return stocks


@router.post("/stocks", status_code=201, dependencies=[Depends(_require_admin)])
def add_stock(body: AddStockRequest) -> dict[str, Any]:
    """Registers a ticker to track, WITHOUT triggering a synchronous scrape.

    Inserts into `stocks` (status='pending_refresh') and a `refresh_queue`
    entry (priority='high'): the actual data fetching happens later,
    via /api/refresh (manual) or a future job consuming `refresh_queue`.
    """
    sb = get_supabase()
    ticker = body.ticker.upper().strip()

    existing = _execute_with_retry(
        sb.table("stocks").select("ticker").eq("ticker", ticker), context=ticker
    ).data
    if existing:
        raise HTTPException(409, f"{ticker} already exists.")

    now = datetime.now(timezone.utc).isoformat()
    _insert_idempotent(
        sb.table("stocks").insert(
            {
                "ticker": ticker,
                "is_benchmark": False,
                "asset_type": body.asset_type,
                "status": "pending_refresh",
                "created_at": now,
                "updated_at": now,
            }
        ),
        context=ticker,
    )
    _insert_idempotent(
        sb.table("refresh_queue").insert({"ticker": ticker, "priority": "high"}), context=ticker
    )

    return {
        "ticker": ticker, "status": "pending_refresh", "asset_type": body.asset_type,
        "queued": True, "priority": "high",
    }


@router.patch("/stocks/{ticker}", dependencies=[Depends(_require_admin)])
def update_stock(ticker: str, body: UpdateStockRequest) -> dict[str, Any]:
    """Sets sector_benchmark_ticker on an existing stock (admin-only)."""
    sb = get_supabase()
    ticker = ticker.upper().strip()

    existing = _execute_with_retry(
        sb.table("stocks").select("ticker").eq("ticker", ticker), context=ticker
    ).data
    if not existing:
        raise HTTPException(404, f"Unknown ticker {ticker}.")

    if body.sector_benchmark_ticker is not None:
        benchmark_ticker = body.sector_benchmark_ticker.upper().strip()
        benchmark = _execute_with_retry(
            sb.table("stocks").select("ticker").eq("ticker", benchmark_ticker), context=benchmark_ticker
        ).data
        if not benchmark:
            raise HTTPException(422, f"sector_benchmark_ticker {benchmark_ticker} does not exist in stocks.")
    else:
        benchmark_ticker = None

    _execute_with_retry(
        sb.table("stocks")
        .update({"sector_benchmark_ticker": benchmark_ticker, "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("ticker", ticker),
        context=ticker,
    )
    return {"ticker": ticker, "sector_benchmark_ticker": benchmark_ticker}


@router.get("/stocks/{ticker}")
def stock_detail(ticker: str) -> dict[str, Any]:
    ticker = ticker.upper()
    sb = get_supabase()
    stock = _execute_with_retry(
        sb.table("stocks").select("*").eq("ticker", ticker), context=ticker
    ).data
    if not stock:
        raise HTTPException(404, f"Unknown ticker {ticker}.")
    prices = _execute_with_retry(
        sb.table("price_history").select("date, close, volume").eq("ticker", ticker)
        .order("date", desc=True).limit(30),
        context=ticker,
    ).data
    return {
        "stock": stock[0],
        "fundamentals": _latest_fundamentals(ticker),
        "score": _latest_score(ticker),
        "recent_prices": list(reversed(prices)),
    }


@router.get("/stocks/{ticker}/history")
def price_history(ticker: str) -> list[dict[str, Any]]:
    closes = _load_closes(ticker.upper())
    if closes.empty:
        raise HTTPException(404, f"No history for {ticker.upper()}.")
    return [{"date": d.date().isoformat(), "close": float(v)} for d, v in closes.items()]


@router.post("/refresh", dependencies=[Depends(_require_admin)])
def refresh(body: RefreshRequest) -> dict[str, Any]:
    """Without explicit tickers/benchmarks: automatic mode (see ``refresh_due``)
    which processes the queue first (``refresh_queue``, high priority
    then FIFO), then already-active tickers for maintenance — within
    the remaining daily quota. With an explicit list: unchanged behavior
    (refreshes exactly those tickers)."""
    if not body.tickers and not body.benchmarks:
        results = refresh_due()
        return {"results": results, "ok": all(r["ok"] for r in results), "mode": "auto"}
    results = refresh_tickers(body.tickers)
    results += refresh_tickers(body.benchmarks, is_benchmark=True)
    return {"results": results, "ok": all(r["ok"] for r in results), "mode": "explicit"}


@router.post("/score/recompute", dependencies=[Depends(_require_admin)])
def recompute_scores() -> dict[str, Any]:
    sb = get_supabase()
    stocks = _execute_with_retry(
        sb.table("stocks").select("ticker, asset_type, sector").eq("is_benchmark", False)
    ).data
    results: list[dict[str, Any]] = []

    for s in stocks:
        t = s["ticker"]
        if s.get("asset_type") == "etf":
            # ETFs have no company fundamentals: no composite score to compute.
            logger.info("%s: skipped (ETF, no composite score).", t)
            results.append({"ticker": t, "skipped": True, "reason": "etf"})
            continue

        closes = _load_closes(t)
        vol = annualized_volatility(closes)
        sr = sharpe_ratio(closes)
        mdd = max_drawdown(closes)
        sortino = sortino_ratio(closes)
        r_score, r_breakdown = risk_score(vol, sr, mdd, sortino)

        fund = _latest_fundamentals(t)
        f_score, f_breakdown = (
            fundamental_score(fund, sector=s.get("sector")) if fund else (None, {"missing": ["no_fundamentals_row"]})
        )

        c_score, c_breakdown = composite_score(f_score, r_score)

        _execute_with_retry(
            sb.table("scores").insert(
                {
                    "ticker": t,
                    "computed_at": datetime.now(timezone.utc).isoformat(),
                    "volatility_annualized": vol,
                    "sharpe_ratio": sr,
                    "max_drawdown": mdd,
                    "fundamental_score": f_score,
                    "risk_score": r_score,
                    "composite_score": c_score,
                    "score_breakdown": {
                        "fundamental": f_breakdown,
                        "risk": r_breakdown,
                        "composite": c_breakdown,
                    },
                }
            ),
            context=t,
        )
        results.append({"ticker": t, "composite_score": c_score})

    return {"results": results}


@router.get("/screener")
def screener(
    sector: str | None = Query(default=None),
    min_score: float | None = Query(default=None, ge=0, le=100),
    min_growth: float | None = Query(default=None, description="Minimum revenue growth YoY, e.g. 0.1 for 10%"),
    min_risk_score: float | None = Query(default=None, ge=0, le=100),
    market_cap_min: float | None = Query(default=None, ge=0),
    market_cap_max: float | None = Query(default=None, ge=0),
    pe_max: float | None = Query(default=None, description="Maximum trailing P/E"),
    debt_to_ebitda_max: float | None = Query(default=None),
) -> dict[str, Any]:
    """ETFs are comparison benchmarks, never investable picks, so they never
    appear in screener results (see recompute_scores, which structurally
    excludes them from scoring).
    """
    sb = get_supabase()
    stocks = _execute_with_retry(
        sb.table("stocks")
        .select("*")
        .eq("is_benchmark", False)
        .neq("asset_type", "etf")
        .eq("status", "active")
    ).data
    rows: list[dict[str, Any]] = []
    for s in stocks:
        if sector and (s.get("sector") or "").strip().casefold() != sector.strip().casefold():
            continue

        score = _latest_score(s["ticker"])
        fund = _latest_fundamentals(s["ticker"])
        composite = score["composite_score"] if score else None
        risk = score["risk_score"] if score else None
        growth = fund.get("revenue_growth_yoy") if fund else None
        market_cap = fund.get("market_cap") if fund else None
        pe_trailing = fund.get("pe_trailing") if fund else None
        debt_to_ebitda = fund.get("debt_to_ebitda") if fund else None

        if min_score is not None and (composite is None or composite < min_score):
            continue
        if min_growth is not None and (growth is None or growth < min_growth):
            continue
        if min_risk_score is not None and (risk is None or risk < min_risk_score):
            continue
        if market_cap_min is not None and (market_cap is None or market_cap < market_cap_min):
            continue
        if market_cap_max is not None and (market_cap is None or market_cap > market_cap_max):
            continue
        if pe_max is not None and (pe_trailing is None or pe_trailing > pe_max):
            continue
        if debt_to_ebitda_max is not None and (debt_to_ebitda is None or debt_to_ebitda > debt_to_ebitda_max):
            continue

        rows.append(
            {
                **s,
                "composite_score": composite,
                "fundamental_score": score["fundamental_score"] if score else None,
                "risk_score": risk,
                "revenue_growth_yoy": growth,
                "market_cap": market_cap,
                "pe_trailing": pe_trailing,
                "debt_to_ebitda": debt_to_ebitda,
            }
        )
    rows.sort(key=lambda r: (r["composite_score"] is None, -(r["composite_score"] or 0)))
    return {"rows": rows}


@router.post("/backtest")
def backtest(body: BacktestRequest) -> dict[str, Any]:
    if body.start_date >= date.today():
        raise HTTPException(422, "start_date must be in the past.")
    earliest = earliest_price_date()
    if earliest is not None and body.start_date < earliest:
        raise HTTPException(422, f"No price data available before {earliest.isoformat()}.")

    # top_n (automatic ranking) and tickers (exact manual basket) are two
    # distinct modes, not composable — reject rather than silently picking one.
    if body.tickers and body.top_n is not None:
        raise HTTPException(
            422, "Provide either top_n (automatic ranking) or tickers (exact manual basket), not both."
        )

    universe_tickers: list[str] | None = None
    exact = False
    if body.tickers:
        universe_tickers = [t.upper().strip() for t in body.tickers]
        sb = get_supabase()
        rows = (
            sb.table("stocks").select("ticker, is_benchmark")
            .in_("ticker", universe_tickers).execute().data
        )
        found = {r["ticker"]: r["is_benchmark"] for r in rows}
        invalid = [
            t for t in universe_tickers if t not in found or found[t]
        ]
        if invalid:
            raise HTTPException(
                422,
                f"Invalid ticker(s) for backtest universe (unknown or is_benchmark): {', '.join(invalid)}.",
            )
        exact = True

    top_n = body.top_n if body.top_n is not None else 5
    return run_backtest(
        body.start_date,
        top_n,
        body.benchmark.upper(),
        universe_tickers,
        exact=exact,
        rebalance_frequency=body.rebalance_frequency,
        transaction_cost_bps=body.transaction_cost_bps,
    )


@router.get("/compare")
def compare(
    tickers: str = Query(..., description="Comma-separated tickers, e.g. AAPL,MSFT,SPY"),
    start_date: date = Query(...),
) -> dict[str, Any]:
    """Raw price comparison (no scoring/composite/know_date involved) — any
    ticker is eligible, including is_benchmark=true (e.g. SPY), since there's
    no notion of "investable universe" here."""
    ticker_list = [t.upper().strip() for t in tickers.split(",") if t.strip()]
    if len(ticker_list) < 2:
        raise HTTPException(422, "At least 2 tickers are required for a comparison.")
    if start_date >= date.today():
        raise HTTPException(422, "start_date must be in the past.")

    sb = get_supabase()
    rows = sb.table("stocks").select("ticker").in_("ticker", ticker_list).execute().data
    found = {r["ticker"] for r in rows}
    invalid = [t for t in ticker_list if t not in found]
    if invalid:
        raise HTTPException(422, f"Unknown ticker(s): {', '.join(invalid)}.")

    return run_compare(ticker_list, start_date)


@router.get("/correlation")
def correlation(
    tickers: str = Query(..., description="Comma-separated tickers, e.g. AAPL,MSFT,GOOGL"),
) -> dict[str, Any]:
    """Correlation matrix of weekly returns across the given tickers."""
    ticker_list = [t.upper().strip() for t in tickers.split(",") if t.strip()]
    if len(ticker_list) < 2:
        raise HTTPException(422, "At least 2 tickers are required for a correlation matrix.")

    sb = get_supabase()
    rows = sb.table("stocks").select("ticker").in_("ticker", ticker_list).execute().data
    found = {r["ticker"] for r in rows}
    invalid = [t for t in ticker_list if t not in found]
    if invalid:
        raise HTTPException(422, f"Unknown ticker(s): {', '.join(invalid)}.")

    closes_by_ticker = {t: _load_closes(t) for t in ticker_list}
    insufficient = [t for t, closes in closes_by_ticker.items() if len(closes) < MIN_PRICE_POINTS]
    if insufficient:
        raise HTTPException(
            422,
            f"Insufficient price history (< {MIN_PRICE_POINTS} points) for: {', '.join(insufficient)}.",
        )

    corr = correlation_matrix(closes_by_ticker)
    corr = corr.reindex(index=ticker_list, columns=ticker_list)
    return {"tickers": ticker_list, "matrix": corr.round(4).values.tolist()}


@router.get("/portfolio/weights")
def portfolio_weights(
    tickers: str = Query(..., description="Comma-separated tickers, e.g. AAPL,MSFT,GOOGL"),
) -> dict[str, Any]:
    """Inverse-volatility position weights across the given tickers."""
    ticker_list = [t.upper().strip() for t in tickers.split(",") if t.strip()]
    if len(ticker_list) < 2:
        raise HTTPException(422, "At least 2 tickers are required for position weighting.")

    sb = get_supabase()
    rows = sb.table("stocks").select("ticker").in_("ticker", ticker_list).execute().data
    found = {r["ticker"] for r in rows}
    invalid = [t for t in ticker_list if t not in found]
    if invalid:
        raise HTTPException(422, f"Unknown ticker(s): {', '.join(invalid)}.")

    closes_by_ticker = {t: _load_closes(t) for t in ticker_list}
    insufficient = [t for t, closes in closes_by_ticker.items() if len(closes) < MIN_PRICE_POINTS]
    if insufficient:
        raise HTTPException(
            422,
            f"Insufficient price history (< {MIN_PRICE_POINTS} points) for: {', '.join(insufficient)}.",
        )

    weights = inverse_volatility_weights(ticker_list)
    excluded = [
        {"ticker": t, "reason": "zero or undefined volatility"} for t in ticker_list if t not in weights
    ]
    return {"weights": weights, "excluded": excluded}


@router.get("/portfolio/efficient-frontier")
def portfolio_efficient_frontier(
    tickers: str = Query(..., description="Comma-separated tickers, e.g. AAPL,MSFT,GOOGL"),
) -> dict[str, Any]:
    """Long-only Markowitz efficient frontier (minimum-variance curve across
    a grid of target returns) plus the max-Sharpe tangency portfolio."""
    ticker_list = [t.upper().strip() for t in tickers.split(",") if t.strip()]
    if len(ticker_list) < 2:
        raise HTTPException(422, "At least 2 tickers are required for an efficient frontier.")

    sb = get_supabase()
    rows = sb.table("stocks").select("ticker").in_("ticker", ticker_list).execute().data
    found = {r["ticker"] for r in rows}
    invalid = [t for t in ticker_list if t not in found]
    if invalid:
        raise HTTPException(422, f"Unknown ticker(s): {', '.join(invalid)}.")

    closes_by_ticker = {t: _load_closes(t) for t in ticker_list}
    insufficient = [t for t, closes in closes_by_ticker.items() if len(closes) < MIN_PRICE_POINTS]
    if insufficient:
        raise HTTPException(
            422,
            f"Insufficient price history (< {MIN_PRICE_POINTS} points) for: {', '.join(insufficient)}.",
        )

    result = efficient_frontier(ticker_list)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result
