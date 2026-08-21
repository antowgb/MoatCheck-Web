#!/usr/bin/env python3
"""Exports the current Supabase state to static JSON files, uploaded to a
public Supabase Storage bucket — this is what the mobile app now reads
instead of talking to a live backend server (see docs/mobile-local-first.md
if that gets written; for now, see the "Éliminer le backend Render" plan).

Runs after run_refresh.py/run_qualitative.py in the same GitHub Actions
workflow, with the service_role key already active (use_service_role_key()
already called by the caller — see __main__ below for standalone use).

Mirrors the read shapes from app/api/routes.py (list_stocks, qualitative
tally) so the mobile client can keep the exact same TypeScript interfaces
it already had for the Render API (see mobile/lib/api.ts).
"""

import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.supabase_client import execute_with_retry, get_supabase  # noqa: E402
from app.qualitative import config as qual_config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("export_json")

BUCKET = "public-data"
QUALITATIVE_EXPORT_WINDOW_DAYS = 90  # same as the feed's own tally/timeline window


def _empty_tally() -> dict[str, Any]:
    return {"positive": 0, "negative": 0, "neutral": 0, "window_days": qual_config.TALLY_WINDOW_DAYS}


def _qualitative_tally_by_ticker(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """Port of app/api/routes.py::_qualitative_tally_by_ticker (same query,
    same windowing/exclusion rules) — kept in sync manually since routes.py
    isn't imported here (avoids dragging in the whole FastAPI app)."""
    if not tickers:
        return {}
    cutoff = (date.today() - timedelta(days=qual_config.TALLY_WINDOW_DAYS)).isoformat()
    sb = get_supabase()
    rows = execute_with_retry(
        sb.table("qualitative_notes")
        .select("ticker, sentiment, confidence, event_date, created_at")
        .in_("ticker", tickers)
        .neq("confidence", qual_config.TALLY_EXCLUDE_CONFIDENCE),
        context=f"tally {len(tickers)} tickers",
    ).data

    tally: dict[str, dict[str, Any]] = {}
    for row in rows:
        eff = row.get("event_date") or (row.get("created_at") or "")[:10]
        if not eff or eff < cutoff:
            continue
        sentiment = row.get("sentiment")
        if sentiment not in qual_config.SENTIMENTS:
            continue
        t = row["ticker"]
        bucket = tally.setdefault(t, _empty_tally())
        bucket[sentiment] += 1
    return tally


def _latest_scores_by_ticker(tickers: list[str]) -> dict[str, dict[str, Any]]:
    if not tickers:
        return {}
    sb = get_supabase()
    rows = execute_with_retry(
        sb.table("scores").select("*").in_("ticker", tickers).order("computed_at", desc=True)
    ).data
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest.setdefault(row["ticker"], row)
    return latest


def _paginate(table: str, select: str = "*") -> list[dict[str, Any]]:
    sb = get_supabase()
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = execute_with_retry(
            sb.table(table).select(select).order("ticker").range(offset, offset + 999),
            context=table,
        ).data
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return rows


def build_stocks_json() -> list[dict[str, Any]]:
    """All stocks (incl. benchmarks/ETFs — the mobile client itself filters
    them out for the dashboard/screener, same as list_stocks() used to, but
    keeps them for /stocks/benchmarks-equivalent lookups), each with the
    latest score and qualitative tally attached."""
    sb = get_supabase()
    stocks = execute_with_retry(sb.table("stocks").select("*").order("ticker")).data
    tickers = [s["ticker"] for s in stocks]
    scores = _latest_scores_by_ticker(tickers)
    tally = _qualitative_tally_by_ticker(tickers)
    for s in stocks:
        score = scores.get(s["ticker"])
        s["composite_score"] = score["composite_score"] if score else None
        s["computed_at"] = score["computed_at"] if score else None
        s["qualitative_tally"] = tally.get(s["ticker"], _empty_tally())
    return stocks


def build_prices_json() -> dict[str, list[dict[str, Any]]]:
    rows = _paginate("price_history", "ticker, date, close, volume")
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(
            {"date": r["date"], "close": r["close"], "volume": r.get("volume")}
        )
    return by_ticker


def build_fundamentals_json() -> dict[str, list[dict[str, Any]]]:
    rows = _paginate("fundamentals", "*")
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    return by_ticker


def build_qualitative_json() -> dict[str, Any]:
    cutoff = (date.today() - timedelta(days=QUALITATIVE_EXPORT_WINDOW_DAYS)).isoformat()
    sb = get_supabase()
    events = execute_with_retry(
        sb.table("qualitative_notes").select("*").gte("event_date", cutoff).order("event_date", desc=True)
    ).data
    return {"events": events, "window_days": QUALITATIVE_EXPORT_WINDOW_DAYS}


def build_feed_status_json() -> dict[str, Any]:
    sb = get_supabase()
    rows = execute_with_retry(sb.table("feed_status").select("*")).data
    return {"feeds": rows}


def _upload(sb, name: str, payload: Any) -> None:
    data = json.dumps(payload, default=str).encode("utf-8")
    storage = sb.storage.from_(BUCKET)
    try:
        storage.update(name, data, {"content-type": "application/json", "cache-control": "300"})
    except Exception:
        storage.upload(name, data, {"content-type": "application/json", "cache-control": "300"})
    logger.info("Uploaded %s (%d bytes).", name, len(data))


def _ensure_bucket_exists(sb) -> None:
    """Creates the public-data bucket if it doesn't exist yet — the Storage
    API never auto-creates a bucket on upload, so a fresh Supabase project
    (or one where the bucket was never made via the dashboard) would 400
    with "Bucket not found" on every export run otherwise."""
    existing = {b.name for b in sb.storage.list_buckets()}
    if BUCKET in existing:
        return
    logger.info("Bucket '%s' not found — creating it as public.", BUCKET)
    sb.storage.create_bucket(BUCKET, options={"public": True})


def main() -> int:
    sb = get_supabase()
    _ensure_bucket_exists(sb)
    exports = {
        "stocks.json": build_stocks_json(),
        "prices.json": build_prices_json(),
        "fundamentals.json": build_fundamentals_json(),
        "qualitative.json": build_qualitative_json(),
        "feed_status.json": build_feed_status_json(),
    }
    for name, payload in exports.items():
        _upload(sb, name, payload)
    logger.info("Export complete: %d file(s) written to bucket '%s'.", len(exports), BUCKET)
    return 0


if __name__ == "__main__":
    from scripts._service_role import use_service_role_key

    use_service_role_key()
    sys.exit(main())
