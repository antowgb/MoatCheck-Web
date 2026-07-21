"""Gmail newsletter collector (read-only OAuth).

Reads emails from a curated list of sender addresses (NEWSLETTER_SENDERS_FILE,
NOT hardcoded — the operator fills it in), extracts the plain-text body, and
emits an item for each ticker mentioned explicitly (by ticker symbol or
company name) in that body.

Scope: ``gmail.readonly`` ONLY — this collector never sends, drafts, labels,
or deletes anything. See GMAIL_SETUP.md for the OAuth setup.

Because Gmail isn't per-ticker (one inbox, many tickers), ``collect`` here is
best driven once per run over all active tickers: pass the full stock list via
``collect_all``. The per-ticker ``collect`` entrypoint (used by the generic
orchestrator loop) delegates to a cached full scan so the OAuth/list calls
happen once, not once per ticker.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.qualitative import config
from app.qualitative.feed_status import CollectedItem, record_feed_status

logger = logging.getLogger(__name__)

SOURCE_TYPE = "newsletter"

# Gmail OAuth scope — read-only, minimal. NEVER add a write/send scope here.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Cached per process so a full inbox scan runs once per refresh, not per ticker.
_scan_cache: dict[str, list[CollectedItem]] | None = None


def _load_senders() -> list[str]:
    """Curated newsletter sender addresses. [] if the file is absent/empty."""
    path = config.NEWSLETTER_SENDERS_FILE
    if not path.exists():
        logger.info("Newsletter senders file %s absent — no newsletters followed.", path)
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Newsletter senders file unreadable (%s).", exc)
        return []
    senders = data.get("senders") if isinstance(data, dict) else data
    return [s for s in (senders or []) if isinstance(s, str) and s.strip()]


def _gmail_service() -> Any | None:
    """Builds an authenticated read-only Gmail service, or None if unavailable.

    Uses an OAuth token cached at GMAIL_API_CREDENTIALS_PATH's sibling
    ``gmail_token.json``; if there's no valid token, returns None (the operator
    must run the one-time consent flow, see GMAIL_SETUP.md) rather than
    attempting an interactive flow in a headless refresh job.
    """
    import os

    creds_path = os.environ.get("GMAIL_API_CREDENTIALS_PATH")
    if not creds_path:
        logger.info("GMAIL_API_CREDENTIALS_PATH not set — Gmail newsletter collector inactive.")
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        logger.error("google-api-python-client / google-auth not installed — Gmail collector unavailable.")
        return None

    from pathlib import Path

    token_path = Path(creds_path).with_name("gmail_token.json")
    if not token_path.exists():
        logger.error(
            "No Gmail OAuth token at %s — run the one-time consent flow (see GMAIL_SETUP.md).", token_path
        )
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        if not creds.valid:
            logger.error("Gmail OAuth token invalid and not refreshable — re-run consent flow.")
            return None
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception:
        logger.error("Failed to build Gmail service.", exc_info=True)
        return None


def _extract_plain_text(payload: dict[str, Any]) -> str:
    """Best-effort plain-text body from a Gmail message payload (recursive)."""
    import base64

    def decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
        except Exception:
            return ""

    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime == "text/plain" and body.get("data"):
        return decode(body["data"])
    parts = payload.get("parts") or []
    for part in parts:
        text = _extract_plain_text(part)
        if text:
            return text
    # Fall back to any body data (e.g. text/html) if no text/plain part found.
    if body.get("data"):
        return decode(body["data"])
    return ""


def _mentions_ticker(text: str, ticker: str, name: str | None) -> bool:
    """True if the ticker symbol (word-boundary) or company name appears in text."""
    import re

    if re.search(rf"\b{re.escape(ticker)}\b", text):
        return True
    if name:
        # Match the first significant word of the company name (drop suffixes like Inc./Corp.).
        core = name.split(",")[0].strip()
        if core and len(core) >= 3 and core.lower() in text.lower():
            return True
    return False


def _run_full_scan(stocks: list[dict[str, Any]]) -> dict[str, list[CollectedItem]]:
    """Scans the inbox once and buckets matching items per ticker."""
    result: dict[str, list[CollectedItem]] = {s["ticker"].upper(): [] for s in stocks}
    senders = _load_senders()
    if not senders:
        return result
    service = _gmail_service()
    if service is None:
        # Record 'failed' for every ticker so the /admin monitor surfaces it.
        for s in stocks:
            record_feed_status(s["ticker"].upper(), SOURCE_TYPE, "failed",
                               last_error="Gmail service unavailable (see logs / GMAIL_SETUP.md)")
        return result

    query = " OR ".join(f"from:{s}" for s in senders)
    try:
        listed = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
        message_ids = [m["id"] for m in listed.get("messages", [])]
    except Exception:
        logger.error("Gmail message list failed.", exc_info=True)
        for s in stocks:
            record_feed_status(s["ticker"].upper(), SOURCE_TYPE, "failed", last_error="Gmail list call failed")
        return result

    for msg_id in message_ids:
        try:
            msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        except Exception:
            logger.warning("Gmail message %s fetch failed — skipped.", msg_id, exc_info=True)
            continue
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "")
        body = _extract_plain_text(msg.get("payload", {}))
        text = f"{subject}. {body}".strip()
        if not text:
            continue
        # internalDate is epoch ms.
        published = None
        if msg.get("internalDate"):
            published = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc).date().isoformat()
        url = f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"

        for s in stocks:
            tkr = s["ticker"].upper()
            if _mentions_ticker(text, tkr, s.get("name")):
                result[tkr].append(
                    CollectedItem(ticker=tkr, raw_text=text, published_date=published,
                                  source_type=SOURCE_TYPE, url=url)
                )

    for s in stocks:
        tkr = s["ticker"].upper()
        record_feed_status(tkr, SOURCE_TYPE, "ok")
    return result


def prime_scan(stocks: list[dict[str, Any]]) -> None:
    """Runs (or re-runs) the single full-inbox scan and caches it for this process."""
    global _scan_cache
    _scan_cache = _run_full_scan(stocks)


def collect(ticker: str, stock: dict[str, Any]) -> list[CollectedItem]:
    """Per-ticker entrypoint. Relies on a primed full scan (prime_scan).

    If the scan wasn't primed (e.g. this collector was invoked in isolation),
    it primes it for just this ticker so behavior stays correct, at the cost of
    one inbox scan per ticker — the orchestrator always calls prime_scan first.
    """
    global _scan_cache
    ticker = ticker.upper()
    if _scan_cache is None:
        prime_scan([stock])
    return (_scan_cache or {}).get(ticker, [])
