"""Investor-relations RSS/Atom collector.

Reads ``stocks.ir_rss_url`` (curated manually via /admin). If NULL, the ticker
is skipped cleanly (logged, feed_status='stale', not an error). Otherwise the
feed is parsed with feedparser and entries published after the last successful
scan are emitted.
"""

import logging
from datetime import date
from typing import Any

from app.qualitative import config
from app.qualitative.feed_status import CollectedItem, record_feed_status

logger = logging.getLogger(__name__)

SOURCE_TYPE = "ir_rss"


def _last_scan_date(ticker: str) -> str | None:
    from app.data.supabase_client import execute_with_retry, get_supabase

    try:
        rows = execute_with_retry(
            get_supabase().table("feed_status").select("last_success_at")
            .eq("ticker", ticker).eq("source_type", SOURCE_TYPE).limit(1),
            context=f"ir_rss last_scan {ticker}",
        ).data
    except Exception:
        logger.error("Could not read ir_rss last_scan for %s.", ticker, exc_info=True)
        return None
    if rows and rows[0].get("last_success_at"):
        return rows[0]["last_success_at"][:10]
    return None


def _entry_date(entry: Any) -> str | None:
    """ISO YYYY-MM-DD from a feedparser entry's parsed date, or None."""
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None) or (entry.get(attr) if hasattr(entry, "get") else None)
        if parsed:
            try:
                return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday).isoformat()
            except (ValueError, TypeError):
                continue
    return None


def collect(ticker: str, stock: dict[str, Any]) -> list[CollectedItem]:
    """Collects recent entries from the ticker's IR feed. [] if no feed configured."""
    ticker = ticker.upper()
    feed_url = stock.get("ir_rss_url")
    if not feed_url:
        logger.info("%s: no ir_rss_url configured — IR RSS skipped.", ticker)
        record_feed_status(ticker, SOURCE_TYPE, "stale", last_error="no ir_rss_url configured")
        return []

    # Imported lazily so a missing optional dep doesn't break module import
    # for the other (enabled) collectors.
    try:
        import feedparser
    except ImportError:
        logger.error("feedparser not installed — cannot parse IR RSS for %s.", ticker)
        record_feed_status(ticker, SOURCE_TYPE, "failed", feed_url=feed_url, last_error="feedparser not installed")
        return []

    try:
        parsed = feedparser.parse(feed_url)
    except Exception as exc:
        logger.error("%s: IR RSS parse failed for %s: %s", ticker, feed_url, exc, exc_info=True)
        record_feed_status(ticker, SOURCE_TYPE, "failed", feed_url=feed_url, last_error=str(exc))
        return []

    # feedparser sets .bozo on malformed feeds but often still yields entries;
    # a hard failure (no entries + bozo) is recorded as failed.
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        err = str(getattr(parsed, "bozo_exception", "malformed feed"))
        logger.error("%s: IR RSS malformed and empty (%s).", ticker, err)
        record_feed_status(ticker, SOURCE_TYPE, "failed", feed_url=feed_url, last_error=err)
        return []

    last_scan = _last_scan_date(ticker)
    items: list[CollectedItem] = []
    for entry in parsed.entries:
        published = _entry_date(entry)
        if last_scan is not None and published is not None and published <= last_scan:
            continue
        title = (getattr(entry, "title", "") or "").strip()
        summary = (getattr(entry, "summary", "") or "").strip()
        link = getattr(entry, "link", None)
        raw_text = f"{title}. {summary}".strip()
        if not raw_text or raw_text == ".":
            continue
        items.append(
            CollectedItem(
                ticker=ticker,
                raw_text=raw_text,
                published_date=published,
                source_type=SOURCE_TYPE,
                url=link,
            )
        )

    logger.info("%s: IR RSS collected %d new entry(ies) (since %s).",
                ticker, len(items), last_scan or "beginning")
    record_feed_status(ticker, SOURCE_TYPE, "ok", feed_url=feed_url)
    return items
