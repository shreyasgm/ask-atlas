"""Integration tests for error handling / retry logic.

Tests real tenacity retry behavior (no mocks of the retry decorator itself).
No external dependencies required.
"""

import pytest
from unittest.mock import MagicMock
from sqlalchemy.exc import OperationalError
from tenacity import RetryError, wait_none

from src.error_handling import execute_with_retry, QueryExecutionError

# Read max attempts from the actual retry config so tests stay in sync
MAX_ATTEMPTS = execute_with_retry.retry.stop.max_attempt_number

# Store original wait strategy so we can restore it after tests
_original_wait = execute_with_retry.retry.wait


@pytest.fixture(autouse=True)
def _no_backoff_wait():
    """Replace exponential backoff with no-wait for fast unit tests."""
    execute_with_retry.retry.wait = wait_none()
    yield
    execute_with_retry.retry.wait = _original_wait


class TestRetryBehavior:
    """Verify real tenacity retry semantics with controlled callables."""

    def test_succeeds_on_first_try(self):
        """Happy path: function succeeds immediately, returns correct value."""
        fn = MagicMock(return_value="ok")
        result = execute_with_retry(fn, "arg1", key="val")
        assert result == "ok"
        fn.assert_called_once_with("arg1", key="val")

    def test_retries_on_operational_error(self):
        """Transient OperationalError is retried; succeeds on final attempt."""
        fn = MagicMock(
            side_effect=[OperationalError("conn lost", params=None, orig=Exception())]
            * (MAX_ATTEMPTS - 1)
            + ["recovered"]
        )
        result = execute_with_retry(fn, "q")
        assert result == "recovered"
        assert fn.call_count == MAX_ATTEMPTS

    def test_wraps_non_retryable_error(self):
        """Non-retryable errors are wrapped in QueryExecutionError."""
        fn = MagicMock(side_effect=ValueError("bad input"))
        with pytest.raises(QueryExecutionError) as exc_info:
            execute_with_retry(fn, "q")
        assert exc_info.value.original_error is not None
        assert isinstance(exc_info.value.original_error, ValueError)

    def test_exhausts_retries(self):
        """Persistent OperationalError raises RetryError after all attempts exhausted."""
        fn = MagicMock(
            side_effect=OperationalError("down", params=None, orig=Exception())
        )
        with pytest.raises(RetryError):
            execute_with_retry(fn, "q")
        assert fn.call_count == MAX_ATTEMPTS
