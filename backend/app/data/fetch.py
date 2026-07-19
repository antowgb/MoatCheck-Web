"""Data pipeline: Alpha Vantage fetch -> Supabase.

Replaces yfinance (blocked on Render's datacenter IPs) with the official
Alpha Vantage API, allowed from the cloud, and rebuilds a QUARTERLY history
of fundamentals (instead of a single current snapshot) to enable point-in-time
scoring without look-ahead in the backtest. `stocks` / `price_history` keep
the same contract as before; `fundamentals` now receives MULTIPLE rows per
ticker (one per available quarter, each with its own `report_date` and
`know_date`) via ``build_quarterly_fundamentals`` (replaces the old
``build_fundamentals_row`` which only returned a single snapshot).

Alpha Vantage endpoints used (1 request each) — 7 requests per equity
(OVERVIEW, INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW, EARNINGS, SPLITS,
TIME_SERIES_WEEKLY), 2 per benchmark (SPLITS + TIME_SERIES_WEEKLY, prices
only), and 1 per ETF (`stocks.asset_type == 'etf'`: TIME_SERIES_WEEKLY only,
no split-adjustment, no fundamentals, no composite score):
- TIME_SERIES_WEEKLY: WEEKLY prices, filtered to the last 5 years. Daily
  history over 5 years (outputsize=full) is a PREMIUM endpoint at Alpha Vantage;
  weekly is free with full history. The free tier returns the RAW close
  (adjusted close is premium); we RE-ADJUST it ourselves for splits via
  the SPLITS endpoint (see below) so as not to distort volatility / Sharpe /
  max drawdown. Dividends are not re-adjusted (not exposed for free)
  — minor impact, difference accepted vs yfinance's auto_adjust. Consequence: the
  scoring annualizes over 52 periods/year (see app/scoring/risk.py), not 252.
- SPLITS: list of splits (effective_date, split_factor) used to back-adjust
  historical prices to the ticker's current scale.
- OVERVIEW: ticker identity (name, sector, industry, currency). Its ratios are
  CURRENT TTM only — NOT used for historical fundamentals.
- INCOME_STATEMENT / BALANCE_SHEET / CASH_FLOW / EARNINGS: we parse the entire
  ``quarterlyReports`` array (several years) and cross-reference it by
  ``fiscalDateEnding`` to rebuild ONE fundamentals snapshot PER QUARTER
  (point-in-time scoring without look-ahead), not just the latest one.

Anti-look-ahead: each snapshot carries a ``know_date = fiscalDateEnding +
REPORTING_LAG_DAYS`` (an approximation of the 10-Q/10-K publication delay, since
Alpha Vantage doesn't provide an exact filing date). Point-in-time scoring only
uses a snapshot if ``know_date <= reference date``.

Fields deliberately left as NULL (never a made-up default value):
- ``roic``: Alpha Vantage doesn't expose ROIC and it can't be computed
  properly without reconstructing NOPAT and invested capital (out of scope for V1).
- ``pe_forward`` on HISTORICAL snapshots: Alpha Vantage doesn't expose
  a past forward estimate (only the current forward estimate in OVERVIEW) —
  left as None to avoid introducing look-ahead.

Any missing value ("None"/absent from Alpha Vantage) stays None and is
logged — never a silent 0.

Rate limiting (free tier): 5 requests/minute and 25 requests/day. A throttle
(sleep) spaces out calls; a persistent daily quota tracker
(``refresh_progress.json``) remembers already-fetched responses so an
interrupted refresh can *resume* without re-consuming quota, and cleanly
stops ("daily quota reached, retry tomorrow") once the 25 requests
are exhausted.
"""

import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.data import finnhub_client
from app.data.supabase_client import get_supabase

logger = logging.getLogger(__name__)

# --- Alpha Vantage / rate limiting configuration ---------------------------
_API_URL = "https://www.alphavantage.co/query"
_API_KEY_ENV = "ALPHA_VANTAGE_API_KEY"

MAX_REQUESTS_PER_MINUTE = 5
MAX_REQUESTS_PER_DAY = 25
# 5 req/min => >= 12s between two calls; small safety margin (also covers
# the free tier's "1 request per second" limit).
_THROTTLE_SECONDS = 13.0
# Number of retries on a transient "per second" limit.
_MAX_BURST_RETRIES = 3

# 5 years of prices, like the old PRICE_HISTORY_PERIOD = "5y".
PRICE_HISTORY_YEARS = 5

# Incremental maintenance refresh: an already-fully-backfilled equity ticker
# (>= MIN_QUARTERS_FOR_INCREMENTAL fundamentals rows already in DB — a legacy
# single-snapshot ticker with fewer rows still needs a full statement fetch)
# skips INCOME_STATEMENT/BALANCE_SHEET/CASH_FLOW/EARNINGS entirely for this
# refresh cycle if no new quarter is expected yet. NEW_QUARTER_GRACE_DAYS is
# deliberately BELOW a typical ~91-day quarter cycle: better to make a
# superfluous Alpha Vantage call (nothing new found) than to delay noticing a
# new quarter — see fetch_ticker's fail-closed comment.
MIN_QUARTERS_FOR_INCREMENTAL = 15
NEW_QUARTER_GRACE_DAYS = 80

# Assumed delay between a quarter's close (fiscalDateEnding) and the
# report's actual publication (10-Q ~40 days, 10-K ~60-75 days after close).
# Alpha Vantage does NOT provide the exact filing date for financial statements,
# so we approximate the date at which the info becomes publicly known as
# fiscalDateEnding + REPORTING_LAG_DAYS. This is the anti-look-ahead guardrail:
# a snapshot is only usable in the backtest once past this date.
REPORTING_LAG_DAYS = 60

# debt_to_ebitda is meaningless when EBITDA is near zero (dividing by a
# near-zero denominator produces huge, sign-flippy ratios that pass the
# naive "<=0 -> perfect score" rule despite signaling a marginal EBITDA, not
# real deleveraging). Below this fraction of TTM revenue, the ratio is
# written as NULL instead.
EBITDA_MIN_THRESHOLD_PCT_REVENUE = 0.01

# Quota / resume tracking file (local; add to .gitignore).
_PROGRESS_FILE = Path(
    os.environ.get(
        "REFRESH_PROGRESS_FILE",
        str(Path(__file__).resolve().parents[2] / "refresh_progress.json"),
    )
)


class QuotaExceeded(Exception):
    """Raised when the Alpha Vantage daily quota of 25 requests is reached."""


# --- Persistent quota / resume tracker ---------------------------------
class _AlphaVantageClient:
    """Alpha Vantage HTTP client with a 5/min throttle and a persistent 25/day quota.

    Successful responses are cached by (ticker, endpoint) in the progress
    file: a refresh interrupted by the quota resumes the next day right
    where it left off, without re-paying for calls already made that day.
    """

    def __init__(self) -> None:
        self._last_call_ts: float | None = None
        self._state = self._load_state()

    # -- persistence ---------------------------------------------------------
    def _load_state(self) -> dict[str, Any]:
        today = date.today().isoformat()
        if _PROGRESS_FILE.exists():
            try:
                state = json.loads(_PROGRESS_FILE.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("refresh_progress unreadable (%s) — reset.", exc)
                state = {}
            if state.get("date") == today:
                return state
            logger.info("New day (%s): Alpha Vantage quota reset to 0/%d.",
                        today, MAX_REQUESTS_PER_DAY)
        # New day (or no file): start with a fresh quota.
        return {"date": today, "requests_used": 0, "cache": {}}

    def _save_state(self) -> None:
        try:
            _PROGRESS_FILE.write_text(json.dumps(self._state, indent=2))
        except OSError as exc:
            logger.warning("Could not write refresh_progress (%s).", exc)

    def requests_remaining(self) -> int:
        return MAX_REQUESTS_PER_DAY - self._state["requests_used"]

    # -- fetch with cache / throttle / quota --------------------------
    def get(self, endpoint: str, ticker: str, **params: str) -> dict[str, Any]:
        """Calls an Alpha Vantage endpoint for a ticker.

        Reuses the cached response if already fetched today (no quota
        consumed). Otherwise applies the throttle, checks the quota, makes the
        call and caches the response. Raises ``QuotaExceeded`` if the quota is exhausted.
        """
        cache_key = f"{ticker}:{endpoint}"
        cached = self._state["cache"].get(cache_key)
        if cached is not None:
            logger.info("%s / %s: response already cached today (0 quota consumed).",
                        ticker, endpoint)
            return cached

        query = {"function": endpoint, "symbol": ticker,
                 "apikey": _require_api_key(), **params}
        url = f"{_API_URL}?{urllib.parse.urlencode(query)}"

        # Alpha Vantage has TWO distinct limits, both returned in an HTTP
        # 200 under the "Information"/"Note" key:
        #  - a per-second limit ("1 request per second"): transient, we
        #    wait and retry;
        #  - the daily limit ("25 requests per day"): we stop
        #    cleanly (QuotaExceeded) to resume the next day.
        for attempt in range(_MAX_BURST_RETRIES + 1):
            if self.requests_remaining() <= 0:
                raise QuotaExceeded(
                    f"daily quota reached ({MAX_REQUESTS_PER_DAY}/{MAX_REQUESTS_PER_DAY})"
                )

            self._throttle()
            logger.info("%s / %s: Alpha Vantage call (quota remaining before call: %d/%d).",
                        ticker, endpoint, self.requests_remaining(), MAX_REQUESTS_PER_DAY)

            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:  # HTTP, JSON, network…
                logger.error("%s / %s: Alpha Vantage call failed: %s", ticker, endpoint, exc)
                self._state["requests_used"] += 1  # a call was indeed made
                self._save_state()
                return {}

            self._state["requests_used"] += 1
            self._last_call_ts = time.monotonic()

            if isinstance(payload, dict) and ("Note" in payload or "Information" in payload):
                msg = payload.get("Note") or payload.get("Information") or ""
                low = msg.lower()
                if "per second" in low or "spreading out" in low:
                    # Per-second limit: transient → wait longer.
                    logger.warning("%s / %s: Alpha Vantage per-second limit, "
                                   "retrying (%d/%d) after a pause.",
                                   ticker, endpoint, attempt + 1, _MAX_BURST_RETRIES)
                    self._save_state()
                    time.sleep(_THROTTLE_SECONDS)
                    continue
                if "per day" in low or "25 requests" in low:
                    # Daily limit: clean stop, resume tomorrow.
                    logger.warning("%s / %s: Alpha Vantage daily quota reached: %s",
                                   ticker, endpoint, msg)
                    self._state["requests_used"] = MAX_REQUESTS_PER_DAY
                    self._save_state()
                    raise QuotaExceeded(f"Alpha Vantage: {msg}")
                # Unexpected message: neither per-second nor daily — don't
                # poison the quota, return empty (missing field).
                logger.warning("%s / %s: unclassified Alpha Vantage message: %s",
                               ticker, endpoint, msg)
                self._save_state()
                return {}

            if isinstance(payload, dict) and "Error Message" in payload:
                logger.warning("%s / %s: Alpha Vantage returned an error: %s",
                               ticker, endpoint, payload["Error Message"])
                self._save_state()
                return {}

            self._state["cache"][cache_key] = payload
            self._save_state()
            return payload

        logger.warning("%s / %s: persistent per-second limit after %d attempts — giving up.",
                       ticker, endpoint, _MAX_BURST_RETRIES)
        return {}

    def _throttle(self) -> None:
        if self._last_call_ts is None:
            return
        elapsed = time.monotonic() - self._last_call_ts
        wait = _THROTTLE_SECONDS - elapsed
        if wait > 0:
            logger.info("Alpha Vantage throttle: waiting %.0fs (5 req/min).", wait)
            time.sleep(wait)


# Lazy instance shared across a whole refresh cycle.
_client: _AlphaVantageClient | None = None


def _get_client() -> _AlphaVantageClient:
    global _client
    if _client is None:
        _client = _AlphaVantageClient()
    return _client


def _require_api_key() -> str:
    key = os.environ.get(_API_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{_API_KEY_ENV} must be set (see .env.example)."
        )
    return key


def _clean_number(value: Any) -> float | None:
    """Converts to float, filtering out None/NaN/inf/non-numeric strings.

    Alpha Vantage returns numbers as strings and uses the literal
    string "None" (or "-") for missing fields: all these cases
    cleanly fall back to None.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def normalize_sector(value: str | None) -> str | None:
    """Normalizes sector/industry casing to Title Case (e.g. "FINANCIAL SERVICES",
    "Technology", "HEALTHCARE" -> "Financial Services", "Technology", "Healthcare").

    Providers (Alpha Vantage OVERVIEW) return inconsistent casing across
    tickers; without normalization at ingestion, the same sector ends up
    stored under multiple distinct string values, breaking exact-match
    filters/grouping elsewhere (screener, /compare).
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped.title()


def fetch_stock_info(ticker: str) -> dict[str, Any] | None:
    """Fetches OVERVIEW + INCOME_STATEMENT + BALANCE_SHEET + CASH_FLOW + EARNINGS.

    None if the ticker is invalid (empty OVERVIEW). Returns a combined dict whose
    4 financial statements are used to rebuild the quarterly history. Raises
    ``QuotaExceeded`` if the daily quota is exhausted along the way.
    """
    client = _get_client()
    overview = client.get("OVERVIEW", ticker)
    # Empty OVERVIEW ({} or no Symbol) => invalid/not found ticker.
    if not overview or not overview.get("Symbol"):
        logger.warning("Ticker %s: empty OVERVIEW — likely invalid.", ticker)
        return None

    return {
        "overview": overview,
        "income_statement": client.get("INCOME_STATEMENT", ticker),
        "balance_sheet": client.get("BALANCE_SHEET", ticker),
        "cash_flow": client.get("CASH_FLOW", ticker),
        "earnings": client.get("EARNINGS", ticker),
    }


def _quarterly(payload: Any, key: str = "quarterlyReports") -> dict[str, dict[str, Any]]:
    """Indexes an endpoint's quarterly reports by ``fiscalDateEnding``."""
    reports = payload.get(key) if isinstance(payload, dict) else None
    return {r["fiscalDateEnding"]: r for r in (reports or []) if r.get("fiscalDateEnding")}


def _total_debt(bs: dict[str, Any]) -> float | None:
    """Total debt: shortLongTermDebtTotal, else short + long term."""
    debt = _clean_number(bs.get("shortLongTermDebtTotal"))
    if debt is None:
        st = _clean_number(bs.get("shortTermDebt"))
        lt = _clean_number(bs.get("longTermDebt"))
        if st is not None or lt is not None:
            debt = (st or 0.0) + (lt or 0.0)
    return debt


def _compute_debt_to_ebitda(
    debt: float | None, cash: float | None, ebitda_ttm: float | None, revenue_ttm: float | None
) -> tuple[float | None, bool]:
    """(net debt) / EBITDA in TTM, or (None, ebitda_too_small) if not computable.

    ``ebitda_too_small`` is True when ebitda_ttm is under
    EBITDA_MIN_THRESHOLD_PCT_REVENUE of revenue_ttm — near-zero EBITDA makes
    the ratio a meaningless, sign-flippy number rather than a real signal, so
    it's left NULL instead (see EBITDA_MIN_THRESHOLD_PCT_REVENUE docstring).
    """
    if debt is None or cash is None or not ebitda_ttm:
        return None, False
    if revenue_ttm and abs(ebitda_ttm) < EBITDA_MIN_THRESHOLD_PCT_REVENUE * abs(revenue_ttm):
        return None, True
    if ebitda_ttm > 0:
        return (debt - cash) / ebitda_ttm, False
    return None, False


def _nearest_close(price_rows: list[dict[str, Any]], target: str) -> float | None:
    """Weekly close whose date is closest to ``target`` (YYYY-MM-DD)."""
    if not price_rows:
        return None
    td = date.fromisoformat(target)
    best_close, best_diff = None, None
    for r in price_rows:
        diff = abs((date.fromisoformat(r["date"]) - td).days)
        if best_diff is None or diff < best_diff:
            best_diff, best_close = diff, r["close"]
    return best_close


def build_quarterly_fundamentals(
    ticker: str, info: dict[str, Any], price_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rebuilds ONE `fundamentals` snapshot per available quarter.

    Cross-references INCOME_STATEMENT (revenue, margins, EBITDA), BALANCE_SHEET
    (debt, cash, equity), CASH_FLOW (FCF) and EARNINGS (EPS) by ``fiscalDateEnding``.
    Metrics computed POINT-IN-TIME as of the quarter's close date (not
    OVERVIEW's current TTM), but always on an ANNUAL scale when the
    quantity depends on it (ROE, EBITDA), to stay comparable to an
    OVERVIEW snapshot and let ``fundamental_score()`` apply the same formula and
    the same bounds regardless of the snapshot's source:

    - revenue / operating_margin / net_margin: from the quarter (a margin
      is a ratio within the same period, its value doesn't depend on the
      period's length — no TTM needed).
    - revenue_growth_yoy: quarter's revenue vs. the same quarter last year (t-4).
    - roe: net income accumulated over the last 4 known quarters (TTM) / equity
      from the latest balance sheet — comparable to a classic annual ROE
      (e.g. OVERVIEW.ReturnOnEquityTTM), NOT a raw quarterly ROE.
    - debt_to_ebitda: (total debt - cash) from the latest balance sheet / EBITDA in TTM.
    - free_cash_flow: operatingCashflow - capitalExpenditures in TTM (like ROE/
      EBITDA/EPS) — a non-annualized flow would be too noisy from quarterly
      capex/OCF seasonality for a reliable sign in the scoring.
    - pe_trailing: weekly close closest to fiscalDateEnding / EPS TTM
      (sum of the last 4 quarterly EPS known at that date).
    - pe_forward: None on history (no past forward estimate available from AV).
    - market_cap: shares outstanding (balance sheet) × closest close, if available.

    Each row carries ``report_date = fiscalDateEnding`` and
    ``know_date = fiscalDateEnding + REPORTING_LAG_DAYS``. Missing values = None,
    logged, never 0.

    Hybrid sourcing (see app.data.finnhub_client module docstring for the
    validated scope): this quarter's revenue/net_income and the trailing
    4-quarter EBITDA window are substituted with Finnhub's own figures ONLY
    if Finnhub's period-end date is within ``finnhub_client.fiscal_date_match``
    tolerance of Alpha Vantage's ``fiscalDateEnding`` for every quarter
    involved — otherwise Alpha Vantage's own value is kept, never a silent
    mix of two different reporting periods. Every row's ``data_source`` field
    records, per hybrid-eligible metric, which source was actually used and
    the fiscal-date gap that gated it.
    """
    inc = _quarterly(info.get("income_statement"))
    bal = _quarterly(info.get("balance_sheet"))
    cfs = _quarterly(info.get("cash_flow"))
    eps = _quarterly(info.get("earnings"), key="quarterlyEarnings")

    # Best-effort: empty dicts (no FINNHUB_API_KEY, call failure, no data)
    # simply mean nothing is available to substitute — never fail the
    # Alpha-Vantage-based pipeline over it.
    finnhub_quarters = finnhub_client.fetch_isolated_quarters(ticker)
    finnhub_ebitda = finnhub_client.fetch_quarterly_ebitda(ticker)

    def _finnhub_window(field: str, quarter_dates: list[date]) -> tuple[float, list[int]] | None:
        """Sums a field over `quarter_dates`, each matched independently
        against Finnhub within tolerance; None if any quarter has no match
        (fail-closed at the window level — no partial/inconsistent TTM)."""
        if len(quarter_dates) != 4:
            return None
        total, gaps = 0.0, []
        for qd in quarter_dates:
            source = finnhub_ebitda if field == "ebitda" else finnhub_quarters
            match = finnhub_client.nearest_within_tolerance(source, qd)
            if match is None:
                return None
            _, value, gap = match
            if isinstance(value, dict):
                field_value = value.get(field)
                if field_value is None:  # e.g. a quarter that resolved revenue but not net_income
                    return None
                total += field_value
            else:
                total += value
            gaps.append(gap)
        return total, gaps

    # Quarters anchored on the income statement, most recent to oldest.
    quarters = sorted(inc.keys(), reverse=True)
    cutoff = (date.today() - timedelta(days=365 * PRICE_HISTORY_YEARS)).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    rows: list[dict[str, Any]] = []
    for idx, q in enumerate(quarters):
        if q < cutoff:
            continue
        i, b = inc.get(q, {}), bal.get(q, {})
        q_date = date.fromisoformat(q)
        data_source: dict[str, Any] = {}

        revenue_av = _clean_number(i.get("totalRevenue"))
        op_income = _clean_number(i.get("operatingIncome"))
        net_income_av = _clean_number(i.get("netIncome"))

        finnhub_match = finnhub_client.nearest_within_tolerance(finnhub_quarters, q_date)
        fh_row, gap_days = (finnhub_match[1], finnhub_match[2]) if finnhub_match is not None else ({}, None)

        def _resolve_hybrid(field: str, av_value: float | None) -> float | None:
            """Per-field Finnhub substitution: requires the field to have
            resolved a tag for this quarter AND pass the magnitude-consistency
            guard against Alpha Vantage's own same-quarter value — either
            failure falls back to Alpha Vantage, never a guessed value."""
            fh_value = fh_row.get(field)
            if fh_value is None:
                data_source[field] = "alpha_vantage"
                if finnhub_match is not None:
                    logger.info("%s %s: Finnhub %s tag not found this quarter — Alpha Vantage fallback.", ticker, q, field)
                return av_value
            if not finnhub_client.magnitude_consistent(fh_value, av_value):
                data_source[field] = "alpha_vantage"
                logger.warning(
                    "%s %s: Finnhub %s (%.4g, tag=%s) rejected — deviates >%.0f%% from "
                    "Alpha Vantage's %.4g — likely tag mismatch, not substituting.",
                    ticker, q, field, fh_value, fh_row.get(f"{field}_tag"),
                    finnhub_client.MAGNITUDE_TOLERANCE * 100, av_value if av_value is not None else float("nan"),
                )
                return av_value
            data_source[field] = "finnhub"
            data_source[f"{field}_tag"] = fh_row.get(f"{field}_tag")
            data_source["fiscal_date_match"] = {"gap_days": gap_days, "within_tolerance": True}
            return fh_value

        revenue = _resolve_hybrid("revenue", revenue_av)
        net_income = _resolve_hybrid("net_income", net_income_av)
        if finnhub_match is None:
            if finnhub_quarters:
                logger.info(
                    "%s %s: Finnhub revenue/net_income excluded (no quarter within "
                    "%d-day fiscal-date tolerance) — falling back to Alpha Vantage.",
                    ticker, q, finnhub_client.FISCAL_DATE_TOLERANCE_DAYS,
                )

        operating_margin = op_income / revenue if op_income is not None and revenue else None
        net_margin = net_income / revenue if net_income is not None and revenue else None

        # YoY growth: quarter's revenue 4 slots further back (same quarter, year -1).
        revenue_growth_yoy = None
        if idx + 4 < len(quarters):
            prev_rev = _clean_number(inc.get(quarters[idx + 4], {}).get("totalRevenue"))
            if revenue is not None and prev_rev:
                revenue_growth_yoy = round(revenue / prev_rev - 1.0, 4)

        # ROE in TTM (net income accumulated over the last 4 known quarters as of
        # this date / equity from the latest balance sheet) — same window as EBITDA
        # and EPS TTM below. fundamental_score() applies the same formula and
        # the same bounds as an annual ROE (e.g. OVERVIEW.ReturnOnEquityTTM) regardless
        # of the snapshot's source: only a correct scale normalization
        # (TTM, not a single quarter) makes the two comparable.
        window_dates = [date.fromisoformat(quarters[j]) for j in range(idx, min(idx + 4, len(quarters)))]

        net_income_window = [_clean_number(inc.get(quarters[j], {}).get("netIncome"))
                             for j in range(idx, min(idx + 4, len(quarters)))]
        net_income_ttm_av = (sum(net_income_window)
                             if len(net_income_window) == 4 and all(x is not None for x in net_income_window)
                             else None)
        finnhub_ni_ttm = _finnhub_window("net_income", window_dates)
        if finnhub_ni_ttm is not None:
            net_income_ttm, gaps = finnhub_ni_ttm
            data_source["net_income_ttm"] = "finnhub"
            data_source["net_income_ttm_fiscal_date_match"] = {"gap_days": gaps, "within_tolerance": True}
        else:
            net_income_ttm = net_income_ttm_av
            data_source["net_income_ttm"] = "alpha_vantage"
            if finnhub_quarters and net_income_ttm_av is not None:
                logger.info(
                    "%s %s: Finnhub net_income_ttm (4-quarter window) excluded — "
                    "not every quarter matched within tolerance. Falling back to Alpha Vantage.",
                    ticker, q,
                )
        equity = _clean_number(b.get("totalShareholderEquity"))
        roe = net_income_ttm / equity if net_income_ttm is not None and equity else None

        # EBITDA in TTM (sum of the last 4 known quarters as of this date, same
        # window as EPS TTM below): the net debt/EBITDA ratio is conventionally read
        # on an annual basis, not over a single quarter.
        ebitda_window = [_clean_number(inc.get(quarters[j], {}).get("ebitda"))
                         for j in range(idx, min(idx + 4, len(quarters)))]
        ebitda_ttm_av = sum(ebitda_window) if len(ebitda_window) == 4 and all(x is not None for x in ebitda_window) else None
        finnhub_ebitda_ttm = _finnhub_window("ebitda", window_dates)
        if finnhub_ebitda_ttm is not None:
            ebitda_ttm, gaps = finnhub_ebitda_ttm
            data_source["ebitda_ttm"] = "finnhub"
            data_source["ebitda_ttm_fiscal_date_match"] = {"gap_days": gaps, "within_tolerance": True}
        else:
            ebitda_ttm = ebitda_ttm_av
            data_source["ebitda_ttm"] = "alpha_vantage"
            if finnhub_ebitda and ebitda_ttm_av is not None:
                logger.info(
                    "%s %s: Finnhub ebitda_ttm (4-quarter window) excluded — "
                    "not every quarter matched within tolerance. Falling back to Alpha Vantage.",
                    ticker, q,
                )

        # debt/cash: kept on Alpha Vantage by default (validated in isolation
        # against Finnhub — 0% deviation on JNJ — but not migrated; see
        # app.data.finnhub_client module docstring).
        cash = _clean_number(b.get("cashAndCashEquivalentsAtCarryingValue"))
        debt = _total_debt(b)
        data_source["debt"] = "alpha_vantage"
        data_source["cash"] = "alpha_vantage"

        # Revenue TTM over the same 4-quarter window as ebitda_ttm, used only
        # to judge whether ebitda_ttm is too close to zero for debt_to_ebitda
        # to be meaningful (see EBITDA_MIN_THRESHOLD_PCT_REVENUE below).
        revenue_window = [_clean_number(inc.get(quarters[j], {}).get("totalRevenue"))
                          for j in range(idx, min(idx + 4, len(quarters)))]
        revenue_ttm = sum(revenue_window) if len(revenue_window) == 4 and all(x is not None for x in revenue_window) else None

        debt_to_ebitda, ebitda_too_small = _compute_debt_to_ebitda(debt, cash, ebitda_ttm, revenue_ttm)
        if ebitda_too_small:
            logger.info(
                "%s %s: debt_to_ebitda left NULL — ebitda_ttm (%.4g) is below %.0f%% of revenue_ttm (%.4g), "
                "too close to zero for a meaningful ratio.",
                ticker, q, ebitda_ttm, EBITDA_MIN_THRESHOLD_PCT_REVENUE * 100, revenue_ttm,
            )

        # FCF in TTM (same window as ROE/EBITDA/EPS above): a single
        # quarter of OCF/capex would be well below the expected annual
        # order of magnitude (seasonal capex/OCF), skewing the sign used by
        # fundamental_score() far more often than a flow smoothed over 4 quarters.
        ocf_window = [_clean_number(cfs.get(quarters[j], {}).get("operatingCashflow"))
                     for j in range(idx, min(idx + 4, len(quarters)))]
        capex_window = [_clean_number(cfs.get(quarters[j], {}).get("capitalExpenditures"))
                        for j in range(idx, min(idx + 4, len(quarters)))]
        free_cash_flow = None
        if (len(ocf_window) == 4 and all(x is not None for x in ocf_window)
                and len(capex_window) == 4 and all(x is not None for x in capex_window)):
            free_cash_flow = sum(ocf_window) - sum(capex_window)
        # capex rejected from Finnhub (25-46% deviation vs Alpha Vantage — see
        # module docstring); ocf-from-Finnhub + capex-from-Alpha-Vantage would
        # itself be an untested cross-source combination, so free_cash_flow
        # stays 100% Alpha Vantage until that specific pairing is validated.
        data_source["free_cash_flow"] = "alpha_vantage"

        # EPS TTM = sum of the last 4 quarterly EPS known at this date (q and the 3 before).
        # Kept on Alpha Vantage: combining Finnhub EPS with Alpha-Vantage weekly
        # closes (pe_trailing) is an untested cross-source pairing, same reasoning
        # as free_cash_flow above.
        eps_ttm = None
        window = [_clean_number(eps.get(quarters[j], {}).get("reportedEPS"))
                  for j in range(idx, min(idx + 4, len(quarters)))]
        if len(window) == 4 and all(x is not None for x in window):
            eps_ttm = sum(window)
        data_source["eps_ttm"] = "alpha_vantage"

        close = _nearest_close(price_rows, q)
        pe_trailing = close / eps_ttm if close is not None and eps_ttm and eps_ttm > 0 else None

        shares = _clean_number(b.get("commonStockSharesOutstanding"))
        market_cap = shares * close if shares is not None and close is not None else None

        row = {
            "ticker": ticker,
            "report_date": q,
            "know_date": (date.fromisoformat(q) + timedelta(days=REPORTING_LAG_DAYS)).isoformat(),
            "revenue": revenue,
            "revenue_growth_yoy": revenue_growth_yoy,
            "operating_margin": operating_margin,
            "net_margin": net_margin,
            "roe": roe,
            "roic": None,  # not properly computable (see module docstring)
            "debt_to_ebitda": debt_to_ebitda,
            "free_cash_flow": free_cash_flow,
            "pe_trailing": pe_trailing,
            "pe_forward": None,  # no historical forward estimate (anti-look-ahead)
            "market_cap": market_cap,
            "fetched_at": now,
            "data_source": data_source,
        }
        missing = [k for k in ("revenue", "revenue_growth_yoy", "operating_margin",
                               "net_margin", "roe", "debt_to_ebitda", "free_cash_flow",
                               "pe_trailing") if row[k] is None]
        if missing:
            logger.info("%s %s: unavailable fields -> None: %s", ticker, q, ", ".join(missing))
        rows.append(row)

    logger.info("%s: %d quarterly snapshots rebuilt (over %d years).",
                ticker, len(rows), PRICE_HISTORY_YEARS)
    return rows


def fetch_splits(ticker: str) -> list[tuple[str, float]]:
    """Returns splits [(effective_date, split_factor), …] via the SPLITS endpoint.

    Available on the free tier. Empty list if no splits. Raises ``QuotaExceeded``
    if the daily quota is exhausted.
    """
    payload = _get_client().get("SPLITS", ticker)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not data:
        return []
    splits: list[tuple[str, float]] = []
    for item in data:
        eff = item.get("effective_date")
        factor = _clean_number(item.get("split_factor"))
        if eff and factor is not None and factor > 0:
            splits.append((eff, factor))
    return splits


def fetch_price_history(ticker: str, adjust_for_splits: bool = True) -> list[dict[str, Any]]:
    """WEEKLY prices (close, volume) over the last PRICE_HISTORY_YEARS years.

    Uses TIME_SERIES_WEEKLY (daily outputsize=full is premium at Alpha
    Vantage; weekly is free with full history), then back-adjusts the RAW
    close for splits (via SPLITS) to bring the whole history back to the
    ticker's current scale: a close dated before a split is divided by the
    product of the split factors AFTER that date (volume is
    multiplied accordingly). Without this, a 10:1 split would create a fake 90%
    drop that would distort volatility / Sharpe / max drawdown.

    ``adjust_for_splits=False`` skips the SPLITS call entirely (ETFs: kept to
    a single Alpha Vantage request — see ``refresh_ticker``). Accepted
    trade-off: an ETF split would go unadjusted until the next full refresh.

    NB: the cadence is WEEKLY — the scoring (``app/scoring/risk.py``) therefore
    annualizes with 52 periods/year, not 252. Raises ``QuotaExceeded`` if the daily
    quota is exhausted.
    """
    splits = fetch_splits(ticker) if adjust_for_splits else []  # SPLITS first (may raise QuotaExceeded)
    payload = _get_client().get("TIME_SERIES_WEEKLY", ticker)
    series = payload.get("Weekly Time Series") if isinstance(payload, dict) else None
    if not series:
        logger.warning("%s: no price history returned.", ticker)
        return []

    # splits sorted from most recent to oldest.
    splits_sorted = sorted(splits, key=lambda s: s[0], reverse=True)
    if splits_sorted:
        logger.info("%s: %d split(s) applied to price adjustment: %s",
                    ticker, len(splits_sorted), splits_sorted)

    def cumulative_factor(day: str) -> float:
        # Product of the factors of splits whose effective_date is AFTER
        # the day in question (prices >= effective_date are already at the new scale).
        factor = 1.0
        for eff, f in splits_sorted:
            if eff > day:
                factor *= f
        return factor

    cutoff = (date.today() - timedelta(days=365 * PRICE_HISTORY_YEARS)).isoformat()
    rows: list[dict[str, Any]] = []
    for day, values in series.items():
        if day < cutoff:
            continue
        close = _clean_number(values.get("4. close"))
        if close is None:
            continue
        volume = _clean_number(values.get("5. volume"))
        factor = cumulative_factor(day)
        adj_close = close / factor
        adj_volume = volume * factor if volume is not None else None
        rows.append(
            {
                "ticker": ticker,
                "date": day,
                "close": adj_close,
                "volume": int(adj_volume) if adj_volume is not None else None,
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows


def _new_quarter_unlikely(ticker: str) -> bool:
    """True only if it's safe to skip this cycle's INCOME_STATEMENT/
    BALANCE_SHEET/CASH_FLOW/EARNINGS calls for an already-active equity
    ticker: it must already be fully backfilled (>= MIN_QUARTERS_FOR_INCREMENTAL
    rows — a legacy single-snapshot ticker always returns False, forcing a
    full fetch) AND not enough time has passed since its latest known
    report_date for a new quarter to plausibly exist yet. Fails closed
    (returns False = fetch anyway) on any missing/ambiguous data — a
    superfluous Alpha Vantage call is always preferred over silently
    missing a new quarter.
    """
    sb = get_supabase()
    rows = sb.table("fundamentals").select("report_date").eq("ticker", ticker).execute().data
    report_dates = [r["report_date"] for r in rows if r.get("report_date")]
    if len(report_dates) < MIN_QUARTERS_FOR_INCREMENTAL:
        return False
    grace_until = date.fromisoformat(max(report_dates)) + timedelta(days=NEW_QUARTER_GRACE_DAYS)
    return date.today() < grace_until


def refresh_ticker(ticker: str, is_benchmark: bool = False) -> dict[str, Any]:
    """Full pipeline for a ticker: stocks + fundamentals + price_history.

    If ``is_benchmark`` is true (e.g. SPY), the ticker is marked as a reference
    index: its prices are stored for the backtest but it is excluded from
    the investable universe (dashboard, screener, selection). Fundamentals
    are not inserted in this case (not relevant for an index) — the
    fundamentals endpoints are therefore not called, which saves 5 quota
    requests per benchmark (7 per normal ticker vs. 2 per benchmark).

    An already-active, fully-backfilled equity ticker also gets an
    incremental maintenance refresh (see ``_new_quarter_unlikely``): if no
    new quarter is plausibly available yet, INCOME_STATEMENT/BALANCE_SHEET/
    CASH_FLOW/EARNINGS are skipped entirely (2 requests instead of 7 —
    OVERVIEW is skipped too since it's fetched together with the other 4
    statements in ``fetch_stock_info``). First loads (``pending_refresh``)
    always get the full 7-request fetch — the incremental path never
    applies there, by construction (status must already be ``active``).
    The Alpha Vantage quota tracker (``_AlphaVantageClient``) already counts
    actual calls made, not a fixed per-ticker count, so no change was needed
    there for this variable call count.

    Returns a summary {ticker, ok, prices_upserted, error?}. Propagates
    ``QuotaExceeded`` if the daily quota is reached along the way.
    """
    sb = get_supabase()
    ticker = ticker.upper().strip()

    if is_benchmark:
        # An index: prices only (1 request), no fundamentals.
        prices = fetch_price_history(ticker)
        now = datetime.now(timezone.utc).isoformat()
        sb.table("stocks").upsert(
            {"ticker": ticker, "is_benchmark": True, "updated_at": now}
        ).execute()
    else:
        existing = sb.table("stocks").select("asset_type,status").eq("ticker", ticker).execute().data
        asset_type = (existing[0].get("asset_type") if existing else None) or "equity"
        status = existing[0].get("status") if existing else None

        if asset_type == "etf":
            # ETF: no company fundamentals, no composite score (see
            # POST /api/score/recompute) — price history only, 1 request,
            # no split-adjustment (see fetch_price_history).
            now = datetime.now(timezone.utc).isoformat()
            sb.table("stocks").upsert(
                {
                    "ticker": ticker,
                    "is_benchmark": False,
                    "asset_type": "etf",
                    "status": "active",  # clears any 'pending_refresh' (POST /api/stocks)
                    "updated_at": now,
                }
            ).execute()
            prices = fetch_price_history(ticker, adjust_for_splits=False)
        elif status == "active" and _new_quarter_unlikely(ticker):
            # Incremental maintenance refresh: already fully backfilled, no
            # new quarter plausibly published yet since the latest known
            # report_date — skip the 4 statement endpoints entirely this
            # cycle (existing fundamentals rows are already correct, nothing
            # to rebuild) and only refresh price history + updated_at.
            logger.info(
                "%s: no new quarter expected yet (incremental skip) — "
                "reusing existing fundamentals, refreshing price history only.",
                ticker,
            )
            now = datetime.now(timezone.utc).isoformat()
            sb.table("stocks").upsert({"ticker": ticker, "updated_at": now}).execute()
            prices = fetch_price_history(ticker)
        else:
            info = fetch_stock_info(ticker)
            if info is None:
                # Without this, an invalid ticker would stay 'pending' forever and
                # would be retried at high priority (thus using quota) on every run.
                sb.table("refresh_queue").update(
                    {"status": "failed", "processed_at": datetime.now(timezone.utc).isoformat()}
                ).eq("ticker", ticker).eq("status", "pending").execute()
                return {"ticker": ticker, "ok": False,
                        "error": "invalid ticker or Alpha Vantage data unavailable"}
            overview = info["overview"]
            now = datetime.now(timezone.utc).isoformat()
            sb.table("stocks").upsert(
                {
                    "ticker": ticker,
                    "name": overview.get("Name"),
                    "sector": normalize_sector(overview.get("Sector")),
                    "industry": normalize_sector(overview.get("Industry")),
                    "currency": overview.get("Currency"),
                    "is_benchmark": False,
                    "asset_type": "equity",
                    "status": "active",  # clears any 'pending_refresh' (POST /api/stocks)
                    "updated_at": now,
                }
            ).execute()
            prices = fetch_price_history(ticker)
            quarterly_rows = build_quarterly_fundamentals(ticker, info, prices)
            # One insert per report_date: the table is designed for multiple
            # snapshots per ticker (UNIQUE(ticker, report_date), see SQL migration).
            # upsert (not insert) to make a refresh idempotent on retry.
            for i in range(0, len(quarterly_rows), 500):
                sb.table("fundamentals").upsert(
                    quarterly_rows[i : i + 500], on_conflict="ticker,report_date"
                ).execute()

    # Upsert in batches to stay under payload limits.
    for i in range(0, len(prices), 500):
        sb.table("price_history").upsert(
            prices[i : i + 500], on_conflict="ticker,date"
        ).execute()

    if not is_benchmark:
        # Closes the loop with POST /api/stocks: a successful refresh closes
        # any refresh_queue entries still pending for this ticker (the
        # 'pending_refresh' status is already cleared by the stocks upsert above).
        sb.table("refresh_queue").update(
            {"status": "done", "processed_at": datetime.now(timezone.utc).isoformat()}
        ).eq("ticker", ticker).eq("status", "pending").execute()

    logger.info("%s: refresh OK (%d price points)", ticker, len(prices))
    return {"ticker": ticker, "ok": True, "prices_upserted": len(prices)}


def refresh_tickers(tickers: list[str], is_benchmark: bool = False) -> list[dict[str, Any]]:
    """Refreshes a list of tickers, without stopping at the first failure.

    Stops cleanly (no crash) if the Alpha Vantage daily quota is
    reached: tickers already refreshed are kept, the rest are
    marked as deferred. Re-running the same call the next day resumes right
    where the refresh left off (responses already fetched are cached).
    """
    results: list[dict[str, Any]] = []
    for t in tickers:
        try:
            results.append(refresh_ticker(t, is_benchmark=is_benchmark))
        except QuotaExceeded as exc:
            logger.warning(
                "Alpha Vantage daily quota reached (%s) on %s: "
                "clean stop. Daily quota reached, retry tomorrow.",
                exc, t.upper(),
            )
            results.append({
                "ticker": t.upper(),
                "ok": False,
                "error": "daily quota reached, retry tomorrow",
                "quota_exhausted": True,
            })
            break
        except Exception as exc:
            logger.exception("Unexpected failure refreshing %s", t)
            results.append({"ticker": t.upper(), "ok": False, "error": str(exc)})
    return results


def _pending_queue_tickers() -> list[str]:
    """Tickers with `refresh_queue.status = 'pending'`, high priority first then
    in order of request age (FIFO) — never the reverse."""
    sb = get_supabase()
    rows = (
        sb.table("refresh_queue")
        .select("ticker, priority, created_at")
        .eq("status", "pending")
        .order("created_at")
        .execute()
        .data
    )
    # Explicit sort in Python (no dependency on an accidental alphabetical
    # order between 'high'/'normal' on the Supabase query side).
    rows.sort(key=lambda r: (0 if r["priority"] == "high" else 1, r["created_at"]))
    seen: set[str] = set()
    ordered: list[str] = []
    for r in rows:
        t = r["ticker"].upper()
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def _active_tickers_by_staleness() -> list[str]:
    """Already-active tickers (outside the queue), from oldest refresh to
    most recent — prioritizes maintenance of the most "stale" tickers."""
    sb = get_supabase()
    rows = (
        sb.table("stocks")
        .select("ticker, updated_at")
        .eq("is_benchmark", False)
        .eq("status", "active")
        .order("updated_at")
        .execute()
        .data
    )
    return [r["ticker"].upper() for r in rows]


def refresh_due() -> list[dict[str, Any]]:
    """Automatic mode for ``POST /api/refresh`` (called without a ticker list):

    1. First processes ALL pending tickers (``refresh_queue.status =
       'pending'``, see ``POST /api/stocks``), sorted high priority first then FIFO
       (``created_at`` ascending).
    2. Then processes already-active tickers for their maintenance refresh,
       from the most "stale" (oldest ``updated_at``) to the most recent.

    All within the remaining Alpha Vantage daily quota: the
    queue therefore always goes before maintenance, but nothing
    is lost — ``refresh_tickers`` stops cleanly on ``QuotaExceeded``
    and the next call resumes right where this one stopped (see the
    ``refresh_progress.json`` cache and the ``pending`` status still in the
    database for tickers not reached).
    """
    pending = _pending_queue_tickers()
    active = _active_tickers_by_staleness()
    ordered = pending + [t for t in active if t not in pending]

    if not ordered:
        logger.info("refresh_due: no ticker to process (empty queue, no active ticker).")
        return []

    logger.info(
        "refresh_due: %d pending ticker(s) (high priority) + %d active ticker(s) "
        "for maintenance, within the remaining daily quota.",
        len(pending), len(active),
    )
    results = refresh_tickers(ordered)

    attempted = {r["ticker"] for r in results}
    pending_processed = [t for t in pending if t in attempted]
    pending_remaining = [t for t in pending if t not in attempted]
    logger.info(
        "refresh_due: %d/%d pending ticker(s) processed this run (%s) — "
        "%d still pending_refresh after this run (%s).",
        len(pending_processed), len(pending), pending_processed,
        len(pending_remaining), pending_remaining,
    )
    return results
