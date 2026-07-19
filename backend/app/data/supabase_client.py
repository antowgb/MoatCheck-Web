"""Shared Supabase client (lazy singleton) + retry helper for Postgrest queries."""

import logging
import os
import time
from functools import lru_cache
from typing import Any

import httpx
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be set (see .env.example)."
        )
    return create_client(url, key)


# Free-tier hosting (both Render and Supabase) occasionally drops an
# in-flight HTTP/2 connection (RemoteProtocolError: ConnectionTerminated) or
# hits a transient socket error ([Errno 11] Resource temporarily unavailable,
# surfaced as ReadError/ConnectError) — none of these produce an HTTP
# response, so postgrest-py's own retry (which only covers 503/520 status
# codes) never sees them. Retried here instead, at the point every Postgrest
# query goes through.
RETRYABLE_EXC = (httpx.ReadError, httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)
_RETRY_DELAYS = (0.5, 1, 2)  # seconds, between attempts (max 3 retries)


def execute_with_retry(query: Any, context: str = "") -> Any:
    """Runs a Postgrest query, retrying on transient network errors only.

    Shared by app/api/routes.py (direct sb.table(...) calls) and
    app/backtest/engine.py::_load_closes (paginated price_history reads) —
    every Postgrest GET in the app should go through this, not call
    .execute() directly, or a transient blip surfaces as an unhandled 500
    instead of a smoothed-over retry.
    """
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0,) + _RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return query.execute()
        except RETRYABLE_EXC as exc:
            last_exc = exc
            logger.warning(
                "Postgrest transient network error%s (attempt %d/%d): %s",
                f" [{context}]" if context else "", attempt + 1, len(_RETRY_DELAYS) + 1, exc,
            )
    assert last_exc is not None
    raise last_exc
