"""Unit tests for Groq response parsing (app/qualitative/classify.py).

The key contract, mirroring the rest of the pipeline's "never a made-up
default" rule: a malformed / invalid Groq response must be SKIPPED cleanly
(return None), never crash and never fabricate a classification.

Run with pytest, or directly:
    python3 tests/test_qualitative_classify.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.qualitative import config
from app.qualitative.classify import (
    Classification,
    GroqDailyQuotaExceeded,
    _DailyQuota,
    estimate_tokens,
    parse_response,
)


def _valid_payload(**overrides):
    base = {
        "category": "dated_contract",
        "sentiment": "positive",
        "severity": "medium",
        "confidence": "high",
        "event_date": "2026-03-01",
        "summary": "Acme signs a $2 billion contract.",
    }
    base.update(overrides)
    return json.dumps(base)


def test_malformed_json_is_skipped_not_crashing():
    # The important regression: garbage in, None out, no exception.
    assert parse_response("this is not json at all {") is None
    assert parse_response("") is None
    assert parse_response("[1, 2, 3]") is None  # valid JSON, but not an object


def test_category_other_is_dropped():
    assert parse_response(_valid_payload(category="other")) is None


def test_unknown_category_is_dropped():
    assert parse_response(_valid_payload(category="not_a_category")) is None


def test_invalid_enums_are_dropped():
    assert parse_response(_valid_payload(sentiment="great")) is None
    assert parse_response(_valid_payload(severity="huge")) is None
    assert parse_response(_valid_payload(confidence="sure")) is None


def test_empty_summary_is_dropped():
    assert parse_response(_valid_payload(summary="   ")) is None


def test_valid_payload_parses():
    result = parse_response(_valid_payload())
    assert isinstance(result, Classification)
    assert result.category == "dated_contract"
    assert result.sentiment == "positive"
    assert result.event_date == "2026-03-01"


def test_invalid_event_date_becomes_none_but_rest_survives():
    # A bad date must not fabricate one, but shouldn't drop the whole event
    # (a validly-classified event with an unparseable date is still useful).
    result = parse_response(_valid_payload(event_date="March 1st"))
    assert isinstance(result, Classification)
    assert result.event_date is None
    result2 = parse_response(_valid_payload(event_date=None))
    assert isinstance(result2, Classification)
    assert result2.event_date is None


def test_daily_quota_hard_stops_on_requests():
    config.GROQ_PROGRESS_FILE = Path(tempfile.mktemp())
    q = _DailyQuota()
    q._state["requests_used"] = config.GROQ_MAX_REQUESTS_PER_DAY
    try:
        q.check_can_spend(10)
        assert False, "should have raised GroqDailyQuotaExceeded on RPD"
    except GroqDailyQuotaExceeded:
        pass


def test_daily_quota_hard_stops_on_tokens():
    config.GROQ_PROGRESS_FILE = Path(tempfile.mktemp())
    q = _DailyQuota()
    q._state["tokens_used"] = config.GROQ_MAX_TOKENS_PER_DAY
    try:
        q.check_can_spend(10_000)
        assert False, "should have raised GroqDailyQuotaExceeded on TPD"
    except GroqDailyQuotaExceeded:
        pass


def test_estimate_tokens_bounds_and_grows_with_length():
    small = estimate_tokens("hello")
    big = estimate_tokens("x" * 8000)
    assert big > small
    # A max-length input must stay well under the per-minute token ceiling so
    # single requests are always sendable.
    max_input = estimate_tokens("x" * config.GROQ_MAX_INPUT_CHARS)
    assert max_input < config.GROQ_MAX_TOKENS_PER_MINUTE


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all classify tests passed")
