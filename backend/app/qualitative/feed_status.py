"""Shared helpers: normalized collector record + feed_status writes.

Every collector returns a list of ``CollectedItem`` and, after each run,
records its status in ``feed_status`` via ``record_feed_status`` — no silent
failures (same principle as the rest of the pipeline).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.data.supabase_client import execute_with_retry, get_supabase

logger = logging.getLogger(__name__)

FeedState = Literal["ok", "failed", "stale"]


@dataclass
class CollectedItem:
    """Normalized output of every collector, before dedup/classification.

    ``published_date`` is the source's own publication date (ISO YYYY-MM-DD or
    None if the source doesn't expose one) — NOT the scan date, and NOT yet the
    event_date (which Groq extracts from the text during classification).
    """

    ticker: str
    raw_text: str
    published_date: str | None
    source_type: str
    url: str | None


def record_feed_status(
    ticker: str,
    source_type: str,
    status: FeedState,
    feed_url: str | None = None,
    last_error: str | None = None,
) -> None:
    """Upserts the (ticker, source_type) row in feed_status.

    On ``ok`` also stamps last_success_at. Never raises: a status-write failure
    must not take down the collection run (it's logged instead).
    """
    now = datetime.now(timezone.utc).isoformat()
    row: dict[str, Any] = {
        "ticker": ticker,
        "source_type": source_type,
        "status": status,
        "feed_url": feed_url,
        "last_error": last_error,
        "updated_at": now,
    }
    if status == "ok":
        row["last_success_at"] = now
    try:
        execute_with_retry(
            get_supabase().table("feed_status").upsert(row, on_conflict="ticker,source_type"),
            context=f"feed_status {ticker}/{source_type}",
        )
    except Exception:
        logger.error(
            "feed_status upsert failed for %s/%s (status=%s) — logged, not fatal.",
            ticker, source_type, status, exc_info=True,
        )
