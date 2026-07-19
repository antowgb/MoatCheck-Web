"""Finnhub client for the hybrid-source fundamentals pipeline.

Scope validated so far (see conversation record / fixtures/finnhub_decumulation_case.json):
- revenue / net_income: decumulated YTD 10-Q figures, matched via a
  MULTI-TAG candidate list (see _REVENUE_CONCEPTS/_NET_INCOME_CONCEPTS
  below), not a single hardcoded tag — a single-tag design (validated only
  on JNJ/AAPL/MSFT, ~65-68% match) scored 0% in real production use on
  LLY/JPM/V, each using a different/incompatible XBRL tag. Re-measured with
  the multi-tag list + magnitude-consistency guard across 7 tickers / 6
  sectors (tech, pharma, banking, payments, managed-care, + XOM/O tag-only):
  ~50-58% combined (see fixtures/finnhub_decumulation_case.json ->
  revenue_net_income_coverage_v2) — includes GENUINE non-coverage (bank
  revenue by design, a real Finnhub data gap on V's net income, an ASC-606
  scope nuance on UNH's revenue tag — not matching-logic bugs). Based on 7
  tickers: a directional signal, not a portfolio-wide guarantee.
- quarterly EBITDA (stock/metric series): within ~2.4% of the Alpha-Vantage-
  derived TTM EBITDA used for debt_to_ebitda — well under the 5% tolerance,
  negligible (<0.1pt) impact on the normalized score. Unaffected by the
  multi-tag work above (different endpoint, not a raw XBRL concept match).
- capex: REJECTED (25-46% deviation vs Alpha Vantage) — never sourced from
  Finnhub here. debt/cash: kept on Alpha Vantage by default (validated in
  isolation, not migrated).
- fiscalDateEnding (Alpha Vantage) vs endDate (Finnhub) never align exactly:
  observed gap_days ranges from 0 (MSFT, calendar fiscal year) to 4 (AAPL,
  4-4-5 retail calendar) across 3 tickers — FISCAL_DATE_TOLERANCE_DAYS below
  is set at 5 days, one day above the worst case observed, not an arbitrary
  guess.

Only revenue / net_income / ebitda_ttm are wired into build_quarterly_fundamentals
(app/data/fetch.py) via this module. ocf-for-free_cash_flow (would mix with
Alpha-Vantage capex) and eps/pe (would mix with Alpha-Vantage price dates)
are NOT yet substituted — that specific cross-source combination hasn't been
validated and is left on Alpha Vantage until it is.

net_income_ttm (ROE) is ALSO wired in (same _finnhub_window mechanism as
ebitda_ttm), but never actually fires: fetch_isolated_quarters() draws on
financials-reported?freq=quarterly, which never returns the fiscal-Q4/10-K
filing (confirmed on JNJ/AAPL/MSFT) — and a 4-quarter TTM window always
spans exactly one Q4. Every net_income_ttm window is therefore permanently
None here, falling back to Alpha Vantage — harmlessly (fail-closed as
designed) but with no benefit. This does NOT cost an extra network call:
fetch_isolated_quarters() is called once per ticker refresh regardless,
already needed for (and shared with) the working revenue/net_income
single-quarter substitution below.

No Alpha Vantage quota is saved by any of this: fetch_stock_info still
calls all 5 Alpha Vantage statement endpoints unconditionally for every
ticker (balance_sheet/cash_flow/earnings are always needed for the
Alpha-Vantage-only fields, income_statement is both the row anchor and the
fallback source) — Finnhub's 2 calls/ticker are purely additive. The
revenue/net_income substitution itself also falls back to Alpha Vantage
often even with the multi-tag list — see the ~50-58%/7-ticker figure above
(a mix of the Q4-only structural gap, extra Finnhub filing-coverage holes
like a JNJ 2024-Q2 filing entirely absent from financials-reported, and
genuine sector-specific non-coverage).

debt/cash were considered for the same Finnhub-primary treatment (they
already match Alpha Vantage exactly when a fiscal date does align — see
fixtures/finnhub_decumulation_case.json). REJECTED, not implemented: the
per-quarter fiscal_date_match success rate is ~67% (same order as
revenue/net_income), but Alpha Vantage's BALANCE_SHEET endpoint returns ALL
quarters in a single call and build_quarterly_fundamentals rebuilds the
full 5-year/20-quarter history on every refresh (no incremental mode) — so
BALANCE_SHEET is only skippable when EVERY quarter in that window matches,
which happened on 0 of the 3 tickers tested (each already has >=6 fallback
quarters of its own) — real measured gain 0%, not the naively-expected
~67%. See fixtures/finnhub_decumulation_case.json ->
debt_cash_conditional_balance_sheet_analysis.
"""

import logging
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_API_URL = "https://finnhub.io/api/v1"
_API_KEY_ENV = "FINNHUB_API_KEY"

# One day above the worst gap_days observed across JNJ/AAPL/MSFT (max 4, on
# AAPL's 4-4-5 fiscal calendar) — see module docstring.
FISCAL_DATE_TOLERANCE_DAYS = 5

# Candidate XBRL concepts per field, most-standard first. A single hardcoded
# tag (validated only on JNJ/AAPL/MSFT) turned out to cover 0% of LLY/JPM/V
# in real production use — each uses a different revenue tag. These lists
# are built from the ACTUAL tags observed across 9 real tickers (JNJ, AAPL,
# MSFT, LLY, JPM, V, UNH, XOM, O — tech, pharma, banking, payments,
# managed-care, energy, REIT); see fixtures/finnhub_decumulation_case.json
# -> revenue_net_income_tag_coverage for the full mapping.
#
# Deliberately EXCLUDED, not added as fallbacks: JPM's bank-specific revenue
# tags (PrincipalTransactionsRevenue, InvestmentBankingRevenue,
# RevenuesNetOfInterestExpense) — verified these are NOT equivalent to Alpha
# Vantage's total-revenue convention for a bank (RevenuesNetOfInterestExpense
# came in ~34% below Alpha Vantage's totalRevenue for JPM, consistently, not
# a one-off) — a magnitude-consistency check alone would NOT reliably catch
# this (it's systematically wrong every quarter, not an erratic jump), so
# the safe choice is to never guess a sector-specific proxy. Financial-sector
# revenue is therefore a legitimate permanent Alpha Vantage fallback here,
# same treatment as ebitda_ttm's bank non-coverage (see module docstring).
_REVENUE_CONCEPTS = (
    "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",  # ASC 606 standard (JNJ/AAPL/MSFT/JPM/V/UNH/XOM, partial)
    "us-gaap_Revenues",  # general-purpose total revenue (LLY/JPM/V/UNH/XOM/O/MSFT, broadest coverage)
    "us-gaap_SalesRevenueNet",  # pre-2018 standard (AAPL/MSFT)
    "us-gaap_SalesRevenueGoodsNet",  # goods-only variant (JNJ/MSFT/UNH)
)
_NET_INCOME_CONCEPTS = (
    "us-gaap_NetIncomeLoss",  # present in the large majority of quarters across all 9 tickers tested
    "us-gaap_NetIncomeLossAvailableToCommonStockholdersBasic",  # fallback for filings that only report this variant (JPM/O)
)

# Magnitude-consistency guard: reject a Finnhub-sourced value if it deviates
# from Alpha Vantage's OWN value for the same quarter (already computed by
# build_quarterly_fundamentals before any substitution) by more than this
# fraction. Compared directly against the same-quarter Alpha Vantage figure
# rather than only the prior quarter's Finnhub figure: a quarter-over-quarter
# check alone would NOT have caught JPM's RevenuesNetOfInterestExpense case
# (systematically ~34% low every quarter, so no quarter-to-quarter jump to
# detect) — comparing to Alpha Vantage's own same-quarter value, which is
# already available at this point in the pipeline, catches both a one-off
# tag-drift (the V case) and a consistently-wrong-definition tag (the JPM
# case) with the same mechanism.
MAGNITUDE_TOLERANCE = 0.25


def magnitude_consistent(finnhub_value: float, alpha_vantage_value: float | None, tolerance: float = MAGNITUDE_TOLERANCE) -> bool:
    """True if finnhub_value is within `tolerance` (relative) of
    alpha_vantage_value. If no Alpha Vantage value is available to compare
    against, fails closed — returns False (never trust an unverifiable
    Finnhub value over silence)."""
    if alpha_vantage_value is None or alpha_vantage_value == 0:
        return False
    return abs(finnhub_value - alpha_vantage_value) / abs(alpha_vantage_value) <= tolerance


def _resolve_field(candidates: tuple[str, ...], curr_ic: dict[str, float], prev_ic: dict[str, float] | None,
                    is_first_quarter_of_fiscal_year: bool) -> tuple[float, str] | None:
    """Tries each candidate tag in priority order; returns (isolated_value, tag_used)
    for the FIRST tag present in curr_ic (and, for a non-Q1 quarter, also present in
    prev_ic — decumulation always uses the SAME tag on both readings, never mixes
    two different tags' YTD values). None if no candidate tag satisfies this."""
    for tag in candidates:
        curr_val = curr_ic.get(tag)
        if curr_val is None:
            continue
        if is_first_quarter_of_fiscal_year:
            return curr_val, tag
        prev_val = (prev_ic or {}).get(tag)
        if prev_val is None:
            continue
        return curr_val - prev_val, tag
    return None


def _api_key() -> str | None:
    import os
    return os.environ.get(_API_KEY_ENV)


def fiscal_date_match(date_a: date, date_b: date, tolerance_days: int = FISCAL_DATE_TOLERANCE_DAYS) -> bool:
    """Fail-closed guard: are these two sources' period-end dates close enough
    to be treated as "the same quarter"? Same anti-look-ahead spirit as
    know_date <= as_of — a mismatch here must exclude the field, never
    silently mix data from two different reporting periods."""
    return abs((date_a - date_b).days) <= tolerance_days


def _get_json(path: str, **params: str) -> Any:
    key = _api_key()
    if not key:
        return None
    query = urllib.parse.urlencode({**params, "token": key})
    url = f"{_API_URL}{path}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            import json
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network/HTTP/JSON — never crash the AV-based pipeline
        logger.warning("Finnhub call failed (%s%s): %s", path, params, exc)
        return None


def fetch_isolated_quarters(ticker: str) -> dict[str, dict[str, Any]]:
    """Isolated (decumulated) quarterly revenue/net_income from Finnhub's
    financials-reported, keyed by ISO end_date. Each field is resolved
    independently via `_resolve_field` (multi-tag candidate list, same tag
    required on both the current and prior quarter for decumulation) — one
    field can resolve while the other doesn't for the same quarter (e.g. a
    company reporting revenue cleanly but missing a net-income tag that
    quarter). Each present field also carries which tag matched, e.g.
    ``{"revenue": 1.2e9, "revenue_tag": "us-gaap_Revenues", ...}`` — used by
    ``data_source`` for audit. Empty dict if Finnhub is unavailable (no key,
    call failure, no data) — callers must treat that as "nothing to
    substitute", never as a reason to fail the Alpha-Vantage-based pipeline.
    """
    payload = _get_json("/stock/financials-reported", symbol=ticker, freq="quarterly")
    if not payload or not payload.get("data"):
        return {}

    reports = [r for r in payload["data"] if r.get("form") == "10-Q"]
    reports.sort(key=lambda r: (r["year"], r["quarter"]))

    isolated: dict[str, dict[str, Any]] = {}
    prev: dict[str, Any] | None = None
    for r in reports:
        curr_ic = {item["concept"]: item["value"] for item in r.get("report", {}).get("ic", [])}
        prev_ic = ({item["concept"]: item["value"] for item in prev.get("report", {}).get("ic", [])}
                   if prev is not None else None)
        is_q1 = r["quarter"] == 1 or prev is None or prev["year"] != r["year"]

        row: dict[str, Any] = {}
        rev = _resolve_field(_REVENUE_CONCEPTS, curr_ic, prev_ic, is_q1)
        if rev is not None:
            row["revenue"], row["revenue_tag"] = rev
        ni = _resolve_field(_NET_INCOME_CONCEPTS, curr_ic, prev_ic, is_q1)
        if ni is not None:
            row["net_income"], row["net_income_tag"] = ni

        if row:
            isolated[r["endDate"][:10]] = row
        prev = r

    return isolated


def fetch_quarterly_ebitda(ticker: str) -> dict[str, float]:
    """Single-quarter EBITDA values (already isolated, not YTD — Finnhub's
    stock/metric quarterly series, unlike financials-reported, reports one
    figure per quarter directly) keyed by ISO period date. Values are in
    millions of the reporting currency, like the rest of stock/metric."""
    payload = _get_json("/stock/metric", symbol=ticker, metric="all")
    if not payload:
        return {}
    series = (payload.get("series") or {}).get("quarterly", {}).get("ebitda")
    if not series:
        return {}
    return {row["period"]: float(row["v"]) * 1_000_000 for row in series if row.get("period") and row.get("v") is not None}


def nearest_within_tolerance(
    candidates: dict[str, Any], target: date, tolerance_days: int = FISCAL_DATE_TOLERANCE_DAYS
) -> tuple[str, Any, int] | None:
    """Picks the candidate (keyed by ISO date string) whose date is closest to
    `target`, only if within `tolerance_days`. Returns (date_str, value,
    gap_days) or None if no candidate is close enough — the fail-closed gate."""
    best: tuple[str, Any, int] | None = None
    for date_str, value in candidates.items():
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            continue
        gap = abs((d - target).days)
        if best is None or gap < best[2]:
            best = (date_str, value, gap)
    if best is None or not fiscal_date_match(target, date.fromisoformat(best[0]), tolerance_days):
        return None
    return best


def ttm_ebitda_at(ebitda_by_date: dict[str, float], quarter_end_dates: list[date]) -> tuple[float, list[int]] | None:
    """Sums 4 quarterly EBITDA values, one per date in `quarter_end_dates`
    (most recent first), each matched independently within tolerance. Returns
    (sum, [gap_days...]) or None if ANY of the 4 quarters has no match within
    tolerance — a partial/inconsistent TTM window is never used (fail-closed
    at the window level, not just per-quarter)."""
    if len(quarter_end_dates) != 4:
        return None
    total = 0.0
    gaps = []
    for qd in quarter_end_dates:
        match = nearest_within_tolerance(ebitda_by_date, qd)
        if match is None:
            return None
        _, value, gap = match
        total += value
        gaps.append(gap)
    return total, gaps
