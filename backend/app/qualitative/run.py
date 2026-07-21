"""Orchestration for the qualitative layer.

For each active (non-benchmark, non-ETF) ticker, for each ENABLED source
(SOURCE_FLAGS): collect -> dedup -> classify (Groq) -> write to
qualitative_notes. Noise ("other") and low-quality/unparseable classifications
are dropped, never written. Every collector records its own feed_status.

Kept independent from the Alpha Vantage refresh: this can be triggered on its
own (POST /api/qualitative/refresh) or as a parallel step in the daily job.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.data.supabase_client import execute_with_retry, get_supabase
from app.qualitative import config, dedup
from app.qualitative.classify import ClassificationError, GroqDailyQuotaExceeded, classify
from app.qualitative.collectors import edgar, gmail_newsletter, ir_rss, press_rss
from app.qualitative.feed_status import CollectedItem

logger = logging.getLogger(__name__)

# source_type -> collect(ticker, stock) callable.
_COLLECTORS: dict[str, Callable[[str, dict[str, Any]], list[CollectedItem]]] = {
    "edgar": edgar.collect,
    "ir_rss": ir_rss.collect,
    "newsletter": gmail_newsletter.collect,
    "press": press_rss.collect,
}


def _active_stocks() -> list[dict[str, Any]]:
    """Active investable equities, ordered STALEST-FIRST.

    Ordering matters because the Groq daily budget can run out mid-run: the
    tickers scanned least recently (or never) go first, so a partial run still
    makes progress on the most-overdue tickers instead of an arbitrary subset
    (mirrors the Alpha Vantage staleness-ordered maintenance pass). A ticker
    already scanned earlier today is cheap to re-reach anyway — EDGAR only
    fetches filings newer than feed_status.last_success_at and dedup skips
    already-classified items, so no Groq quota is spent re-covering it.
    """
    rows = execute_with_retry(
        get_supabase().table("stocks")
        .select("ticker, name, currency, ir_rss_url, status, asset_type, is_benchmark")
        .eq("is_benchmark", False).neq("asset_type", "etf").eq("status", "active"),
        context="qualitative active stocks",
    ).data

    # Oldest successful scan across any source, per ticker (never-scanned = "").
    try:
        fs = execute_with_retry(
            get_supabase().table("feed_status").select("ticker, last_success_at"),
            context="qualitative feed_status ordering",
        ).data
    except Exception:
        logger.error("Could not load feed_status for staleness ordering — using default order.", exc_info=True)
        fs = []
    latest_scan: dict[str, str] = {}
    for row in fs:
        t = row.get("ticker")
        ts = row.get("last_success_at") or ""
        if t is not None and ts > latest_scan.get(t, ""):
            latest_scan[t] = ts
    rows.sort(key=lambda s: latest_scan.get(s["ticker"], ""))  # "" (never scanned) sorts first
    return rows


def _insert_note(item: CollectedItem, classification: Any, dedup_hash: str) -> bool:
    """Inserts one classified event. Tolerates the unique-index duplicate as a no-op.

    Returns True if a row was written (or already existed), False on a real error.
    """
    from postgrest.exceptions import APIError

    row = {
        "ticker": item.ticker,
        "note": item.raw_text[: config.GROQ_MAX_INPUT_CHARS],
        "source_url": item.url,
        "source_type": item.source_type,
        "category": classification.category,
        "sentiment": classification.sentiment,
        "severity": classification.severity,
        "confidence": classification.confidence,
        "event_date": classification.event_date,
        "dedup_hash": dedup_hash,
        "summary": classification.summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        execute_with_retry(
            get_supabase().table("qualitative_notes").insert(row),
            context=f"insert note {item.ticker}",
        )
        return True
    except APIError as exc:
        if exc.code == "23505":  # duplicate (ticker, dedup_hash) — concurrent/raced insert
            logger.info("%s: duplicate note (dedup_hash already present) — skipped.", item.ticker)
            return True
        logger.error("%s: failed to insert qualitative note: %s", item.ticker, exc)
        return False
    except Exception:
        logger.error("%s: unexpected error inserting qualitative note.", item.ticker, exc_info=True)
        return False


def run_qualitative_refresh() -> dict[str, Any]:
    """Runs the full collect->classify->write pipeline over all active tickers.

    Returns a summary {sources, tickers, collected, classified, written,
    skipped_noise, skipped_failed, skipped_dup, quota_exhausted}. Never raises
    for a single-ticker/single-source failure — those are logged and recorded in
    feed_status. A Groq DAILY-budget hit stops the run CLEANLY (quota_exhausted
    True) and resumes next day (EDGAR's since-last-scan filter + dedup make the
    already-covered tickers cheap to re-reach).
    """
    empty = {"collected": 0, "classified": 0, "written": 0,
             "skipped_noise": 0, "skipped_failed": 0, "skipped_dup": 0}
    enabled = [s for s in config.SOURCE_TYPES if config.SOURCE_FLAGS.get(s)]
    logger.info("Qualitative refresh starting. Enabled sources: %s", enabled or "(none)")
    if not enabled:
        return {"sources": [], "tickers": 0, "quota_exhausted": False, **empty}

    stocks = _active_stocks()
    logger.info("Qualitative refresh over %d active ticker(s) (stalest first).", len(stocks))

    # Gmail is a single-inbox scan, not per-ticker: prime it once if enabled.
    if config.SOURCE_FLAGS.get("newsletter"):
        gmail_newsletter.prime_scan(stocks)
    # General press feeds are shared across tickers: fetch them once per run
    # (per-ticker filtering happens in press_rss.collect against this cache).
    if config.SOURCE_FLAGS.get("press"):
        press_rss.prime_general_feeds()

    totals = dict(empty)
    quota_exhausted = False

    try:
        for stock in stocks:
            ticker = stock["ticker"].upper()
            seen = dedup.existing_hashes(ticker)

            for source in enabled:
                collector = _COLLECTORS[source]
                try:
                    items = collector(ticker, stock)
                except Exception:
                    # Collector-level crash: feed_status is the collector's job, but
                    # guard here too so one source can't abort the whole ticker.
                    logger.error("%s/%s: collector crashed — skipping this source.", ticker, source, exc_info=True)
                    continue

                totals["collected"] += len(items)
                for item in items:
                    h = dedup.compute_hash(item.raw_text)
                    if h in seen:
                        totals["skipped_dup"] += 1
                        continue
                    try:
                        classification = classify(item)
                    except ClassificationError:
                        # Definitive per-item failure (API error after retries, or
                        # oversize) — traceable: counted here + logged by classify().
                        totals["skipped_failed"] += 1
                        continue
                    if classification is None:
                        # Legitimate noise ("other") or a validation-rejected model output.
                        totals["skipped_noise"] += 1
                        continue
                    totals["classified"] += 1
                    if _insert_note(item, classification, h):
                        seen.add(h)  # avoid re-processing an identical item later this run
                        totals["written"] += 1
    except GroqDailyQuotaExceeded as exc:
        # Clean stop: keep everything written so far, resume tomorrow.
        quota_exhausted = True
        logger.warning("Qualitative refresh stopping early — Groq daily quota reached (%s).", exc)

    summary = {"sources": enabled, "tickers": len(stocks), "quota_exhausted": quota_exhausted, **totals}
    logger.info(
        "Qualitative refresh done: %d collected, %d classified, %d written, "
        "%d skipped(noise), %d skipped(failed), %d skipped(dup), quota_exhausted=%s.",
        totals["collected"], totals["classified"], totals["written"],
        totals["skipped_noise"], totals["skipped_failed"], totals["skipped_dup"], quota_exhausted,
    )
    return summary
