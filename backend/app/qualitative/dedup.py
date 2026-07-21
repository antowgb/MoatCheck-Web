"""Deduplication for collected qualitative items.

Computes a stable SHA-256 hash of the normalized source text and checks it
against the ``qualitative_notes.dedup_hash`` values already stored for the
ticker, so the same event isn't re-classified (Groq call) or re-inserted.

LIMITATION (assumed, not a bug to fix now): this dedup is APPROXIMATE. It
catches exact/near-exact repeats (same title + lede across scans), but two
sources REWORDING the same event produce different hashes and both survive.
Tightening this (semantic dedup) is deliberately out of scope for V1 of the
qualitative feature — the unique index (ticker, dedup_hash) is the hard
backstop against exact duplicates.
"""

import hashlib
import logging
import re

logger = logging.getLogger(__name__)

# Only the first N chars of the (title + body) feed the hash: enough to
# fingerprint the event's lede, short enough to be stable across minor
# trailing-content churn (feeds often re-emit with an appended disclaimer).
_HASH_PREFIX_CHARS = 200

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, collapse whitespace, strip, keep the first _HASH_PREFIX_CHARS."""
    collapsed = _WHITESPACE_RE.sub(" ", (text or "")).strip().lower()
    return collapsed[:_HASH_PREFIX_CHARS]


def compute_hash(text: str) -> str:
    """SHA-256 hex digest of the normalized text."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def existing_hashes(ticker: str) -> set[str]:
    """All dedup_hash values already stored for this ticker (empty set on error)."""
    from app.data.supabase_client import execute_with_retry, get_supabase

    try:
        rows = execute_with_retry(
            get_supabase().table("qualitative_notes").select("dedup_hash")
            .eq("ticker", ticker).not_.is_("dedup_hash", "null"),
            context=f"dedup hashes {ticker}",
        ).data
    except Exception:
        # On a read failure, return empty rather than a wrong "already seen":
        # the DB unique index still prevents a true duplicate insert, so the
        # worst case is a re-classification, never a double row.
        logger.error("Could not load existing dedup hashes for %s — treating as none.", ticker, exc_info=True)
        return set()
    return {r["dedup_hash"] for r in rows if r.get("dedup_hash")}


def is_duplicate(text: str, seen: set[str]) -> bool:
    """True if this text's hash is already in ``seen``."""
    return compute_hash(text) in seen
