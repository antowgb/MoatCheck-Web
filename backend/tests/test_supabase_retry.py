"""Regression for a real prod incident: GET /api/portfolio/weights,
/api/correlation, and /api/portfolio/efficient-frontier returned 500 with an
unhandled httpx.RemoteProtocolError (ConnectionTerminated) / [Errno 11]
Resource temporarily unavailable from Supabase — a transient network blip
that app/api/routes.py's own Postgrest calls already retried via
_execute_with_retry, but app/backtest/engine.py::_load_closes (used by all
three endpoints above) called .execute() directly with no retry at all, so
the very first transient error crashed the whole request.

Fixed by moving the retry helper into app/data/supabase_client.py
(execute_with_retry / RETRYABLE_EXC) so both routes.py and engine.py share
it, and adding httpx.RemoteProtocolError to the retryable set (it wasn't
there before — that specific exception type would have gone unhandled even
through the old routes.py-only retry).

Run with pytest if available, else directly:
    python3 tests/test_supabase_retry.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.supabase_client import RETRYABLE_EXC, execute_with_retry


class _FlakyQuery:
    """Raises the given exception the first `fail_times` calls, then
    returns `result` — simulates a transient network blip that recovers."""

    def __init__(self, exc: Exception, fail_times: int, result):
        self._exc = exc
        self._fail_times = fail_times
        self._result = result
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc
        return self._result


def test_remote_protocol_error_is_retryable() -> None:
    """The exact exception type seen in the prod incident must be covered —
    this would have been the silent gap even with the old routes.py-only
    retry, which only listed ReadError/ConnectError/TimeoutException."""
    assert httpx.RemoteProtocolError in RETRYABLE_EXC


def test_execute_with_retry_recovers_from_transient_remote_protocol_error() -> None:
    query = _FlakyQuery(httpx.RemoteProtocolError("ConnectionTerminated"), fail_times=2, result="ok")
    with patch("app.data.supabase_client.time.sleep"):
        result = execute_with_retry(query, context="TEST")
    assert result == "ok"
    assert query.calls == 3


def test_execute_with_retry_reraises_after_exhausting_attempts() -> None:
    query = _FlakyQuery(httpx.RemoteProtocolError("ConnectionTerminated"), fail_times=99, result="ok")
    with patch("app.data.supabase_client.time.sleep"):
        try:
            execute_with_retry(query, context="TEST")
            assert False, "expected RemoteProtocolError to propagate after exhausting retries"
        except httpx.RemoteProtocolError:
            pass
    assert query.calls == 4  # initial attempt + 3 retries, per _RETRY_DELAYS


def test_execute_with_retry_does_not_retry_non_network_errors() -> None:
    query = _FlakyQuery(ValueError("not a network error"), fail_times=1, result="ok")
    try:
        execute_with_retry(query, context="TEST")
        assert False, "expected ValueError to propagate immediately, not be retried"
    except ValueError:
        pass
    assert query.calls == 1


def test_load_closes_uses_the_shared_retry_helper() -> None:
    """app/backtest/engine.py::_load_closes must go through
    execute_with_retry, not call .execute() directly — this is what actually
    failed in production (weights/correlation/efficient-frontier all call
    _load_closes per ticker)."""
    from app.backtest import engine

    with patch.object(engine, "execute_with_retry", wraps=engine.execute_with_retry) as spy, patch.object(
        engine, "get_supabase"
    ) as mock_get_supabase:
        fake_query = _FlakyQuery(
            httpx.RemoteProtocolError("ConnectionTerminated"),
            fail_times=1,
            result=type("R", (), {"data": []})(),
        )
        mock_sb = mock_get_supabase.return_value
        mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.range.return_value = (
            fake_query
        )
        with patch("app.data.supabase_client.time.sleep"):
            engine._load_closes("TEST")

    assert spy.called, "_load_closes must call execute_with_retry (not raw .execute())"


if __name__ == "__main__":
    test_remote_protocol_error_is_retryable()
    test_execute_with_retry_recovers_from_transient_remote_protocol_error()
    test_execute_with_retry_reraises_after_exhausting_attempts()
    test_execute_with_retry_does_not_retry_non_network_errors()
    test_load_closes_uses_the_shared_retry_helper()
    print("OK: Supabase retry helper covers RemoteProtocolError and is used by _load_closes")
