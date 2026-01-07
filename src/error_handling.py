# src/error_handling.py
"""Error handling utilities with retry logic for database operations."""

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from sqlalchemy.exc import OperationalError, TimeoutError
import logging

logger = logging.getLogger(__name__)


class QueryExecutionError(Exception):
    """Custom exception for query execution failures."""

    def __init__(
        self, message: str, sql: str = None, original_error: Exception = None
    ):
        self.sql = sql
        self.original_error = original_error
        super().__init__(message)


def _log_retry(retry_state):
    """Log retry attempts with context."""
    logger.warning(
        f"Query failed (attempt {retry_state.attempt_number}), "
        f"retrying in {retry_state.next_action.sleep:.1f} seconds... "
        f"Error: {retry_state.outcome.exception()}"
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((OperationalError, TimeoutError)),
    before_sleep=_log_retry,
)
def execute_with_retry(execute_fn, *args, **kwargs):
    """
    Execute a function with automatic retry on transient failures.

    Uses exponential backoff with the following behavior:
    - Retries up to 3 times
    - Wait time increases exponentially: 2s, 4s, 8s (capped at 10s)
    - Only retries on OperationalError and TimeoutError

    Args:
        execute_fn: The function to execute
        *args: Positional arguments to pass to execute_fn
        **kwargs: Keyword arguments to pass to execute_fn

    Returns:
        The result of execute_fn

    Raises:
        QueryExecutionError: If all retries fail
    """
    try:
        return execute_fn(*args, **kwargs)
    except (OperationalError, TimeoutError):
        # Re-raise to trigger retry logic
        raise
    except Exception as e:
        logger.error(f"Query execution failed with non-retryable error: {e}")
        raise QueryExecutionError(
            f"Failed to execute query: {str(e)}", original_error=e
        )

