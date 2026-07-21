"""Generalist financial-press RSS collector.

Two feed kinds (see config.PRESS_FEEDS_PER_TICKER / PRESS_FEEDS_GENERAL):
  - PER-TICKER: already scoped to the company (ticker in the query), no
    company-name filtering needed. Fetched once per ticker.
  - GENERAL: broad market wires. Fetched ONCE PER RUN (prime_general_feeds,
    called from run.py) and then filtered PER TICKER by company name/ticker —
    without that filter, hundreds of irrelevant items would reach Groq and burn
    the daily quota. This is the NOISIEST source: a high "other" rejection rate
    at classification is expected, not a bug.
"""

import logging
import re
import urllib.parse
from datetime import date
from typing import Any

from app.qualitative import config
from app.qualitative.feed_status import CollectedItem, record_feed_status

logger = logging.getLogger(__name__)

SOURCE_TYPE = "press"

# Cache of general-feed entries for the current run (primed once by
# prime_general_feeds). Each entry: {"title","summary","published","link"}.
# None means "not primed yet" (distinct from an empty list = primed, no items).
_general_cache: list[dict[str, Any]] | None = None


def prime_general_feeds() -> None:
    """Fetch every general press feed ONCE and cache the entries for this run.

    Called by the orchestrator before the per-ticker loop so a general feed is
    downloaded a single time, not once per ticker. Safe to call repeatedly (it
    re-primes). Never raises — a failing feed is logged and skipped.
    """
    global _general_cache
    try:
        import feedparser
    except ImportError:
        logger.error("feedparser not installed — general press feeds unavailable.")
        _general_cache = []
        return

    entries: list[dict[str, Any]] = []
    for url in config.PRESS_FEEDS_GENERAL:
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:
            logger.warning("General press feed parse failed (%s): %s", url, exc)
            continue
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            logger.warning("General press feed malformed/empty: %s (%s)",
                           url, getattr(parsed, "bozo_exception", "?"))
            continue
        for entry in parsed.entries:
            entries.append({
                "title": (getattr(entry, "title", "") or "").strip(),
                "summary": (getattr(entry, "summary", "") or "").strip(),
                "published": _entry_date(entry),
                "link": getattr(entry, "link", None),
            })
    logger.info("Primed %d general press entrie(s) from %d feed(s).",
                len(entries), len(config.PRESS_FEEDS_GENERAL))
    _general_cache = entries


def _last_scan_date(ticker: str) -> str | None:
    from app.data.supabase_client import execute_with_retry, get_supabase

    try:
        rows = execute_with_retry(
            get_supabase().table("feed_status").select("last_success_at")
            .eq("ticker", ticker).eq("source_type", SOURCE_TYPE).limit(1),
            context=f"press last_scan {ticker}",
        ).data
    except Exception:
        logger.error("Could not read press last_scan for %s.", ticker, exc_info=True)
        return None
    if rows and rows[0].get("last_success_at"):
        return rows[0]["last_success_at"][:10]
    return None


def _entry_date(entry: Any) -> str | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday).isoformat()
            except (ValueError, TypeError):
                continue
    return None


# Trailing corporate-form tokens stripped when deriving a short company name,
# so "Microsoft Corporation" also matches a headline that just says "Microsoft"
# (news rarely uses the full legal name stored in stocks.name).
_CORP_SUFFIX_WORDS = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "cos",
    "ltd", "limited", "llc", "lp", "plc", "nv", "sa", "ag", "se", "ab", "as",
    "holding", "holdings", "group", "the",
}


def _short_name(name: str) -> str:
    """Company name with a trailing corporate-form suffix stripped.

    "Microsoft Corporation" -> "Microsoft"; "Advanced Micro Devices, Inc." ->
    "Advanced Micro Devices"; "NVIDIA Corporation" -> "NVIDIA".
    """
    core = name.split(",")[0].strip()
    tokens = core.split()
    while tokens and tokens[-1].strip(".").lower() in _CORP_SUFFIX_WORDS:
        tokens.pop()
    return " ".join(tokens)


def _mentions(text: str, ticker: str, name: str | None) -> bool:
    """True if the ticker OR the (short) company name appears in text.

    Word-boundary matching (case-insensitive for the name) to keep false
    positives down — a bare substring match would fire on unrelated words.
    """
    if re.search(rf"\b{re.escape(ticker)}\b", text):
        return True
    if name:
        short = _short_name(name)
        if short and len(short) >= 3 and re.search(rf"\b{re.escape(short)}\b", text, re.IGNORECASE):
            return True
    return False


def _cap_newest(items: list[CollectedItem], ticker: str, label: str) -> list[CollectedItem]:
    """Truncates to PRESS_MAX_ITEMS_PER_TICKER_PER_RUN, keeping the newest first.

    Called AFTER date-gating and (for general feeds) company-name filtering —
    never before: cutting pre-filter would drop relevant items in favor of
    noise that was about to be discarded anyway. Missing published_date sorts
    last (an undated item is less certainly "newest").
    """
    cap = config.PRESS_MAX_ITEMS_PER_TICKER_PER_RUN
    if len(items) <= cap:
        return items
    ranked = sorted(items, key=lambda i: i.published_date or "", reverse=True)
    kept = ranked[:cap]
    logger.info(
        "%s: %s press items capped %d -> %d (PRESS_MAX_ITEMS_PER_TICKER_PER_RUN=%d) — newest kept.",
        ticker, label, len(items), len(kept), cap,
    )
    return kept


def collect(ticker: str, stock: dict[str, Any]) -> list[CollectedItem]:
    ticker = ticker.upper()
    try:
        import feedparser
    except ImportError:
        logger.error("feedparser not installed — cannot parse press RSS for %s.", ticker)
        record_feed_status(ticker, SOURCE_TYPE, "failed", last_error="feedparser not installed")
        return []

    last_scan = _last_scan_date(ticker)
    name = stock.get("name")
    per_ticker_items: list[CollectedItem] = []
    general_items: list[CollectedItem] = []
    seen_links: set[str] = set()
    any_success = False
    last_error: str | None = None

    def _consider(bucket: list[CollectedItem], title: str, summary: str, published: str | None,
                  link: str | None, *, needs_filter: bool) -> None:
        """Date-gate, (optionally) company-name-filter, dedup-by-link, and append to `bucket`."""
        if last_scan is not None and published is not None and published <= last_scan:
            return
        text = f"{title}. {summary}".strip()
        if not text or text == ".":
            return
        # PER-TICKER feeds are already scoped; GENERAL feeds MUST be filtered.
        if needs_filter and not _mentions(text, ticker, name):
            return
        if link and link in seen_links:
            return
        if link:
            seen_links.add(link)
        bucket.append(
            CollectedItem(ticker=ticker, raw_text=text, published_date=published,
                          source_type=SOURCE_TYPE, url=link)
        )

    # 1. Per-ticker feeds (scoped, no name filter needed) — fetched per ticker.
    for template in config.PRESS_FEEDS_PER_TICKER.values():
        url = template.format(ticker=urllib.parse.quote(ticker))
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:
            last_error = str(exc)
            logger.warning("%s: per-ticker press feed parse failed (%s): %s", ticker, url, exc)
            continue
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            last_error = str(getattr(parsed, "bozo_exception", "malformed feed"))
            logger.warning("%s: per-ticker press feed malformed/empty (%s).", ticker, url)
            continue
        any_success = True
        for entry in parsed.entries:
            _consider(per_ticker_items, (getattr(entry, "title", "") or "").strip(),
                      (getattr(entry, "summary", "") or "").strip(),
                      _entry_date(entry), getattr(entry, "link", None), needs_filter=False)

    # 2. General feeds (primed once per run) — filtered by company name/ticker.
    if config.PRESS_FEEDS_GENERAL:
        if _general_cache is None:
            # Not primed (e.g. collector run in isolation): prime now so behavior
            # stays correct, at the cost of one fetch here. run.py primes upfront.
            prime_general_feeds()
        general = _general_cache or []
        if general:  # at least one general feed was reachable and yielded entries
            any_success = True
        for e in general:
            _consider(general_items, e["title"], e["summary"], e["published"], e["link"], needs_filter=True)

    # Cap AFTER date-gating + name filtering, per feed kind, so a noisy general
    # feed doesn't starve the (already-scoped) per-ticker feed's quota or vice
    # versa. See config.PRESS_MAX_ITEMS_PER_TICKER_PER_RUN.
    per_ticker_items = _cap_newest(per_ticker_items, ticker, "per-ticker")
    general_items = _cap_newest(general_items, ticker, "general")
    items = per_ticker_items + general_items

    if any_success:
        logger.info("%s: press RSS collected %d new item(s) (since %s).", ticker, len(items), last_scan or "beginning")
        record_feed_status(ticker, SOURCE_TYPE, "ok")
    else:
        record_feed_status(ticker, SOURCE_TYPE, "failed", last_error=last_error or "all press feeds failed")
    return items
