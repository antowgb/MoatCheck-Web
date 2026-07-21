"""Groq-based event classification.

Sends a collected item's text to Groq in JSON mode (structured output — no
regex parsing of free text) and returns a validated classification, or None
when the item is noise ("other") or the call/parse fails.

Error handling mirrors the rest of the pipeline's "never a made-up default"
rule: a malformed/unparseable Groq response is logged and SKIPPED (returns
None), never coerced into a fabricated classification. The model is explicitly
instructed never to invent a date or fact absent from the source text, and to
return confidence='low' when uncertain.
"""

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.qualitative import config
from app.qualitative.feed_status import CollectedItem

logger = logging.getLogger(__name__)


class GroqDailyQuotaExceeded(Exception):
    """Raised when Groq's daily request/token budget is (about to be) exhausted.

    Propagated out of classify() so the orchestrator stops the whole run
    CLEANLY (like Alpha Vantage's QuotaExceeded), rather than hammering 429s
    item after item. NOT retried — a daily limit doesn't clear within a run.
    """


class ClassificationError(Exception):
    """A single item could not be classified (network/API failure after bounded
    retries, or a text too large to ever fit the budget).

    Distinct from a plain ``None`` return (which means legitimate noise —
    category 'other' or a model output that failed validation): this signals a
    FAILED item so the orchestrator counts and logs it rather than silently
    conflating it with rejected noise.
    """


@dataclass
class Classification:
    category: str
    sentiment: str
    severity: str
    confidence: str
    event_date: str | None  # ISO YYYY-MM-DD extracted from the text, or None
    summary: str


_SYSTEM_PROMPT = f"""You are a financial-event classifier for a long-term stock-picking tool.
You receive the text of a news item / regulatory filing about ONE company and must
classify it into exactly one category, or reject it as noise.

Categories (choose exactly one):
- "dated_contract": a MATERIAL commercial contract with an explicit date (signed/announced deal, order, partnership with a stated date).
- "m_and_a": a merger, acquisition, divestiture, or takeover (of/by the company).
- "regulatory_admission": litigation, regulatory sanction, investigation, fine, or a regulatory/legal admission.
- "guidance": an official figure PROJECTED by the company itself for a future period beyond one year (e.g. multi-year revenue or margin targets). Does NOT include next-quarter-only guidance or analyst estimates.
- "backlog": a booked/signed order backlog or contracted-but-not-yet-recognized revenue figure, stated as a concrete number. Distinct from "guidance": this is revenue ALREADY CONTRACTUALLY COMMITTED, not a projection.
- "governance_risk": board composition, multi-class voting-rights structure, CEO succession/tenure, related-party transactions, or other governance red flags.
- "activist_pressure": an activist investor publicly pushing for board seats, a strategic review, a sale, or similar pressure on management/the board.
- "customer_concentration": the company discloses material revenue dependence on one customer, a small number of customers, or a small number of partners/suppliers.
- "other": anything else, or too vague/generic to fit the above. Use this liberally to reject noise.

Also extract:
- "sentiment": one of "positive", "negative", "neutral" (impact for a long-term shareholder).
- "severity": one of "low", "medium", "high" (materiality of the event).
- "confidence": one of "high", "medium", "low" — YOUR certainty in this classification.
- "event_date": the date the event OCCURRED or was announced, extracted ONLY from the text,
  in strict YYYY-MM-DD format. If the text gives no explicit date, return null. NEVER invent a date.
- "summary": a 1-2 sentence factual summary IN ENGLISH, drawn only from the text.

Hard rules:
- NEVER invent a date, number, or fact not present in the provided text.
- If you are unsure of the category or the facts, set "confidence": "low".
- If the item doesn't clearly match one of the 8 real categories, use "other".

Respond with ONLY a JSON object with exactly these keys:
category, sentiment, severity, confidence, event_date, summary."""


def estimate_tokens(text: str) -> int:
    """Rough token estimate for a request (input + framing + output allowance).

    Deliberately conservative — used only to pace the minute limiter and to
    refuse a request we already know would bust TPM. The authoritative count
    comes from the response usage once the call returns.
    """
    input_tokens = math.ceil(len(text) / config.GROQ_CHARS_PER_TOKEN)
    return input_tokens + config.GROQ_PROMPT_OVERHEAD_TOKENS + config.GROQ_MAX_OUTPUT_TOKENS


class _DailyQuota:
    """Persistent per-day request/token counter for Groq (RPD + TPD).

    Same shape/rationale as fetch.py's Alpha Vantage tracker: JSON file keyed by
    date, reset on a new day, so several runs in one day share one budget and a
    run stops cleanly at the ceiling. Never raises on I/O errors (logs instead).
    """

    def __init__(self) -> None:
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        today = date.today().isoformat()
        path = config.GROQ_PROGRESS_FILE
        if path.exists():
            try:
                state = json.loads(path.read_text())
                if state.get("date") == today:
                    return state
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("groq_progress unreadable (%s) — reset.", exc)
            logger.info("New day (%s): Groq quota reset to 0.", today)
        return {"date": today, "requests_used": 0, "tokens_used": 0}

    def _save(self) -> None:
        try:
            config.GROQ_PROGRESS_FILE.write_text(json.dumps(self._state, indent=2))
        except OSError as exc:
            logger.warning("Could not write groq_progress (%s).", exc)

    @property
    def requests_used(self) -> int:
        return self._state["requests_used"]

    @property
    def tokens_used(self) -> int:
        return self._state["tokens_used"]

    def check_can_spend(self, est_tokens: int) -> None:
        """Raise GroqDailyQuotaExceeded if this request would breach RPD/TPD."""
        req_ceiling = int(config.GROQ_MAX_REQUESTS_PER_DAY * config.GROQ_QUOTA_SAFETY_FRACTION)
        tok_ceiling = int(config.GROQ_MAX_TOKENS_PER_DAY * config.GROQ_QUOTA_SAFETY_FRACTION)
        if self._state["requests_used"] + 1 > req_ceiling:
            raise GroqDailyQuotaExceeded(
                f"daily request budget reached ({self._state['requests_used']}/{req_ceiling})"
            )
        if self._state["tokens_used"] + est_tokens > tok_ceiling:
            raise GroqDailyQuotaExceeded(
                f"daily token budget reached ({self._state['tokens_used']}+{est_tokens} > {tok_ceiling})"
            )

    def record(self, tokens: int) -> None:
        self._state["requests_used"] += 1
        self._state["tokens_used"] += max(tokens, 0)
        self._save()

    def force_daily_exhausted(self) -> None:
        """Mark the daily budgets as spent (used when Groq itself reports a daily 429)."""
        self._state["requests_used"] = config.GROQ_MAX_REQUESTS_PER_DAY
        self._state["tokens_used"] = config.GROQ_MAX_TOKENS_PER_DAY
        self._save()


class _MinuteLimiter:
    """Sliding-60s window enforcing BOTH RPM and TPM.

    Sleeps just enough so that adding the next request keeps the last 60s under
    both the request count and the token budget (the token budget is the real
    binding constraint for our request size). This is what prevents the RPM
    throttle from silently blowing TPM.
    """

    def __init__(self) -> None:
        # (timestamp, tokens) for each call in the trailing window.
        self._events: deque[tuple[float, int]] = deque()

    def _evict(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= 60.0:
            self._events.popleft()

    def wait(self, est_tokens: int) -> None:
        req_ceiling = int(config.GROQ_MAX_REQUESTS_PER_MINUTE * config.GROQ_QUOTA_SAFETY_FRACTION)
        tok_ceiling = int(config.GROQ_MAX_TOKENS_PER_MINUTE * config.GROQ_QUOTA_SAFETY_FRACTION)
        # Loop: sleep until the oldest event ages out enough to fit this call.
        while True:
            now = time.monotonic()
            self._evict(now)
            req_count = len(self._events)
            tok_count = sum(t for _, t in self._events)
            over_req = req_count + 1 > req_ceiling
            over_tok = tok_count + est_tokens > tok_ceiling and self._events
            if not over_req and not over_tok:
                self._events.append((now, est_tokens))
                return
            # Sleep until the oldest event leaves the window (+ small margin).
            sleep_for = 60.0 - (now - self._events[0][0]) + 0.05
            logger.info(
                "Groq minute limiter: %d req / %d tok in window, waiting %.1fs (RPM+TPM pacing).",
                req_count, tok_count, sleep_for,
            )
            time.sleep(max(sleep_for, 0.05))


_daily_quota: _DailyQuota | None = None
_minute_limiter = _MinuteLimiter()
_client: Any | None = None


def _get_daily_quota() -> _DailyQuota:
    global _daily_quota
    if _daily_quota is None:
        _daily_quota = _DailyQuota()
    return _daily_quota


def _is_daily_rate_limit(exc: Exception) -> bool:
    """Heuristic: does this 429 look like a DAILY (per-day) limit, not per-minute?"""
    msg = str(getattr(exc, "message", "") or exc).lower()
    return "per day" in msg or "rpd" in msg or "tpd" in msg or "daily" in msg


def _remaining_headers_signal_daily_exhaustion(headers: Any) -> bool:
    """True if the response's rate-limit headers show the daily budget at ~0.

    Lets us stop BEFORE the next request 429s. Header names per Groq docs
    (x-ratelimit-remaining-requests / -tokens); best-effort, tolerant of
    missing/renamed headers.
    """
    if not headers:
        return False
    try:
        get = headers.get
    except AttributeError:
        return False
    for name in ("x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens"):
        val = get(name)
        if val is not None:
            try:
                if int(float(val)) <= 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _get_client() -> Any | None:
    """Lazy Groq client. None if the SDK isn't installed or no API key is set."""
    global _client
    if _client is not None:
        return _client
    import os

    if not os.environ.get("GROQ_API_KEY"):
        logger.error("GROQ_API_KEY not set — classification unavailable.")
        return None
    try:
        from groq import Groq
    except ImportError:
        logger.error("groq SDK not installed — classification unavailable.")
        return None
    _client = Groq()
    return _client


def _valid_iso_date(value: Any) -> str | None:
    """Returns value if it's a valid YYYY-MM-DD date, else None (no fabrication)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        date.fromisoformat(value)
        return value
    except ValueError:
        return None


def parse_response(content: str) -> Classification | None:
    """Parses + validates a Groq JSON response.

    Returns None (skip) on: unparseable JSON, missing/invalid enum fields, or
    category == "other" (noise). Never fabricates a default value. Exposed
    separately from the network call so it can be unit-tested against a
    simulated malformed response.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Groq response is not valid JSON — skipping item. (%s)", exc)
        return None
    if not isinstance(data, dict):
        logger.warning("Groq response JSON is not an object — skipping item.")
        return None

    category = data.get("category")
    if category == config.CATEGORY_OTHER:
        # Noise: intentionally not persisted.
        return None
    if category not in config.CATEGORIES:
        logger.warning("Groq returned unknown category %r — skipping item.", category)
        return None

    sentiment = data.get("sentiment")
    severity = data.get("severity")
    confidence = data.get("confidence")
    if sentiment not in config.SENTIMENTS or severity not in config.SEVERITIES or confidence not in config.CONFIDENCES:
        logger.warning(
            "Groq returned invalid enum(s) (sentiment=%r severity=%r confidence=%r) — skipping item.",
            sentiment, severity, confidence,
        )
        return None

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        logger.warning("Groq returned empty/invalid summary — skipping item.")
        return None

    return Classification(
        category=category,
        sentiment=sentiment,
        severity=severity,
        confidence=confidence,
        event_date=_valid_iso_date(data.get("event_date")),
        summary=summary.strip(),
    )


def classify(item: CollectedItem) -> Classification | None:
    """Classifies one collected item via Groq.

    Returns a Classification, or None when the item is noise/empty or the call
    fails after bounded retries (logged, never fabricated). Raises
    ``GroqDailyQuotaExceeded`` when the daily budget is (about to be) hit, so
    the orchestrator can stop the whole run cleanly instead of 429-hammering.
    """
    client = _get_client()
    if client is None:
        return None

    raw = item.raw_text or ""
    if not raw.strip():
        logger.info("%s: empty item text — nothing to classify.", item.ticker)
        return None

    # Brute char truncation — logged, per the "no silent partial treatment"
    # rule. (A keyword-aware extractor would be better; deliberately kept simple
    # for V1, the lede of a filing/headline is what matters for categorization.)
    text = raw
    if len(raw) > config.GROQ_MAX_INPUT_CHARS:
        text = raw[: config.GROQ_MAX_INPUT_CHARS]
        logger.info(
            "%s: source text truncated %d -> %d chars before Groq — classified on a PARTIAL text.",
            item.ticker, len(raw), config.GROQ_MAX_INPUT_CHARS,
        )

    user_prompt = (
        f"Company ticker: {item.ticker}. Source type: {item.source_type}. "
        f"Publication date (context only, not necessarily the event date): {item.published_date or 'unknown'}.\n\n"
        f"Text to classify:\n{text}"
    )

    est_tokens = estimate_tokens(user_prompt + _SYSTEM_PROMPT)
    # Pre-flight token sanity: a single request that can't fit the per-minute
    # token budget will never succeed — skip it rather than 429-looping.
    if est_tokens > int(config.GROQ_MAX_TOKENS_PER_MINUTE * config.GROQ_QUOTA_SAFETY_FRACTION):
        logger.error(
            "%s: estimated %d tokens exceeds per-minute budget — skipping item (won't fit).",
            item.ticker, est_tokens,
        )
        raise ClassificationError(f"item too large ({est_tokens} est. tokens) to fit TPM budget")

    quota = _get_daily_quota()

    last_exc: Exception | None = None
    for attempt in range(config.GROQ_MAX_RETRIES):
        # Hard daily-budget gate BEFORE spending anything (raises to stop the run).
        quota.check_can_spend(est_tokens)
        # Pace against the 60s RPM+TPM window.
        _minute_limiter.wait(est_tokens)
        try:
            kwargs = dict(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=config.GROQ_MAX_OUTPUT_TOKENS,
                response_format={"type": "json_object"},
            )
            # Prefer the raw-response API so we can read rate-limit headers and
            # stop before the next 429. Fall back to a plain call if the SDK
            # version doesn't expose it (headers just become unavailable).
            headers = None
            try:
                raw_resp = client.chat.completions.with_raw_response.create(**kwargs)
                resp = raw_resp.parse()
                headers = getattr(raw_resp, "headers", None)
            except AttributeError:
                resp = client.chat.completions.create(**kwargs)

            # Record ACTUAL tokens from usage when available, else the estimate.
            used = getattr(getattr(resp, "usage", None), "total_tokens", None) or est_tokens
            quota.record(used)
            content = resp.choices[0].message.content
            result = parse_response(content)

            # Proactively stop before the next call 429s, if headers say the
            # daily budget just hit zero. (We keep THIS item's result; the next
            # call is what check_can_spend will refuse.)
            if _remaining_headers_signal_daily_exhaustion(headers):
                quota.force_daily_exhausted()
                logger.warning("%s: Groq rate-limit headers report daily budget exhausted.", item.ticker)
            return result
        except GroqDailyQuotaExceeded:
            raise
        except Exception as exc:
            last_exc = exc
            status = getattr(exc, "status_code", None)
            if status == 429 and _is_daily_rate_limit(exc):
                # Daily limit surfaced as a 429: mark exhausted and hard-stop.
                quota.force_daily_exhausted()
                raise GroqDailyQuotaExceeded(f"Groq daily 429: {exc}") from exc
            backoff = config.GROQ_RETRY_BACKOFF_SECONDS[min(attempt, len(config.GROQ_RETRY_BACKOFF_SECONDS) - 1)]
            logger.warning(
                "%s: Groq classify attempt %d/%d failed (status=%s, %s) — retrying in %.0fs.",
                item.ticker, attempt + 1, config.GROQ_MAX_RETRIES, status, exc, backoff,
            )
            time.sleep(backoff)

    logger.error("%s: Groq classification failed after %d attempts (%s) — item marked failed.",
                 item.ticker, config.GROQ_MAX_RETRIES, last_exc)
    raise ClassificationError(f"failed after {config.GROQ_MAX_RETRIES} attempts: {last_exc}")
