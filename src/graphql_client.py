"""GraphQL HTTP client with budget tracking and circuit breaker.

Provides three main components:

- ``GraphQLBudgetTracker``: Sliding-window rate limiter with consume-on-success
  semantics. Prevents the application from exceeding the Atlas GraphQL API
  rate limit (~120 req/min) by maintaining a configurable budget window.

- ``CircuitBreaker``: Three-state (CLOSED → OPEN → HALF_OPEN) pattern that
  fast-fails requests when the upstream API is unhealthy, preventing cascading
  failures and wasted budget.

- ``AtlasGraphQLClient``: Async HTTP client (httpx) for the Atlas GraphQL API
  with automatic retries on transient errors, error classification, and
  optional integration with the budget tracker and circuit breaker.
"""

import asyncio
import enum
import logging
import time
from collections import deque
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GraphQLError(Exception):
    """Permanent GraphQL error (bad query, validation failure, 4xx HTTP)."""

    def __init__(self, message: str, errors: list[dict] | None = None) -> None:
        self.errors = errors or []
        super().__init__(message)


class TransientGraphQLError(GraphQLError):
    """Transient error that may succeed on retry (5xx, 429, timeout, network)."""


class BudgetExhaustedError(GraphQLError):
    """Raised when the GraphQL API budget is exhausted."""

    def __init__(self) -> None:
        super().__init__("GraphQL API budget exhausted")


class CircuitOpenError(GraphQLError):
    """Raised when the circuit breaker is open (API unhealthy)."""

    def __init__(self) -> None:
        super().__init__("Circuit breaker is open — GraphQL API is unhealthy")


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class CircuitState(enum.Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Three-state circuit breaker for API health protection.

    States:
        CLOSED (normal): Requests flow through. Consecutive failures are tracked.
        OPEN (tripped): All requests fast-fail for ``recovery_timeout`` seconds.
        HALF_OPEN (probe): One request allowed through. Success → CLOSED,
            failure → OPEN again.

    Args:
        failure_threshold: Consecutive failures required to trip (default 5).
        recovery_timeout: Seconds before transitioning from OPEN to HALF_OPEN
            (default 30.0).
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False, repr=False)
    _failure_count: int = field(default=0, init=False, repr=False)
    _opened_at: float = field(default=0.0, init=False, repr=False)

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state

    def is_open(self) -> bool:
        """Return True if requests should be blocked.

        Also handles the OPEN → HALF_OPEN transition when the recovery
        timeout has elapsed.
        """
        if self._state == CircuitState.CLOSED:
            return False

        if self._state == CircuitState.HALF_OPEN:
            return False

        # OPEN — check if recovery timeout has elapsed
        elapsed = time.monotonic() - self._opened_at
        if elapsed >= self.recovery_timeout:
            self._state = CircuitState.HALF_OPEN
            logger.info(
                "Circuit breaker transitioning to HALF_OPEN after %.1fs", elapsed
            )
            return False

        return True

    def record_success(self) -> None:
        """Record a successful API call.

        In CLOSED state: resets the failure counter.
        In HALF_OPEN state: closes the circuit (recovery confirmed).
        """
        if self._state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker closing — probe succeeded")
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed API call.

        In CLOSED state: increments counter; trips to OPEN at threshold.
        In HALF_OPEN state: immediately re-opens the circuit.
        """
        if self._state == CircuitState.HALF_OPEN:
            logger.warning("Circuit breaker re-opening — probe failed")
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            self._failure_count = self.failure_threshold
            return

        self._failure_count += 1
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit breaker tripped after %d consecutive failures",
                self._failure_count,
            )


# ---------------------------------------------------------------------------
# GraphQLBudgetTracker
# ---------------------------------------------------------------------------


@dataclass
class GraphQLBudgetTracker:
    """Sliding-window rate limiter for GraphQL API calls.

    Tracks successful API calls within a rolling time window. Budget tokens
    are consumed AFTER a successful HTTP response, not before — preventing
    API outages from burning through the budget.

    Args:
        max_requests: Maximum requests allowed in the window (default 100).
        window_seconds: Sliding window duration in seconds (default 60.0).
        max_requests_per_session: Optional per-session limit within the same
            window. When None, only the global limit applies.
    """

    max_requests: int = 100
    window_seconds: float = 60.0
    max_requests_per_session: int | None = None

    _timestamps: deque[float] = field(default_factory=deque, init=False, repr=False)
    _session_timestamps: dict[str, deque[float]] = field(
        default_factory=dict, init=False, repr=False
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def _prune_window(self, dq: deque[float]) -> None:
        """Remove entries older than the sliding window."""
        cutoff = time.monotonic() - self.window_seconds
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def remaining(self, session_id: str | None = None) -> int:
        """Return the number of requests remaining in the current window.

        Args:
            session_id: If provided, returns the minimum of global and
                session-level remaining budget.
        """
        self._prune_window(self._timestamps)
        global_remaining = self.max_requests - len(self._timestamps)

        if session_id is None or self.max_requests_per_session is None:
            return max(0, global_remaining)

        session_dq = self._session_timestamps.get(session_id, deque())
        self._prune_window(session_dq)
        session_remaining = self.max_requests_per_session - len(session_dq)

        return max(0, min(global_remaining, session_remaining))

    def is_available(self, session_id: str | None = None) -> bool:
        """Check if budget exists (pre-flight, no consumption).

        Args:
            session_id: If provided, checks both global and session budgets.
        """
        return self.remaining(session_id=session_id) > 0

    async def consume(self, session_id: str | None = None) -> bool:
        """Atomically check-and-record a successful API call.

        Returns True if the call was recorded, False if budget is exhausted.

        Args:
            session_id: If provided, enforces per-session limits too.
        """
        async with self._lock:
            now = time.monotonic()

            # Prune global window
            self._prune_window(self._timestamps)
            if len(self._timestamps) >= self.max_requests:
                return False

            # Prune and check per-session window
            if session_id is not None and self.max_requests_per_session is not None:
                if session_id not in self._session_timestamps:
                    self._session_timestamps[session_id] = deque()
                session_dq = self._session_timestamps[session_id]
                self._prune_window(session_dq)
                if len(session_dq) >= self.max_requests_per_session:
                    return False
                session_dq.append(now)

            self._timestamps.append(now)
            return True


# ---------------------------------------------------------------------------
# Process-global budget tracker singleton
# ---------------------------------------------------------------------------

_shared_budget_tracker: GraphQLBudgetTracker | None = None


def get_shared_budget_tracker(
    max_requests: int = 100,
    window_seconds: float = 60.0,
) -> GraphQLBudgetTracker:
    """Return the process-global budget tracker singleton.

    Creates the instance on first call; returns the same instance thereafter.
    Tests should create their own GraphQLBudgetTracker instances directly.
    """
    global _shared_budget_tracker
    if _shared_budget_tracker is None:
        _shared_budget_tracker = GraphQLBudgetTracker(
            max_requests=max_requests,
            window_seconds=window_seconds,
        )
    return _shared_budget_tracker


# ---------------------------------------------------------------------------
# AtlasGraphQLClient
# ---------------------------------------------------------------------------

# HTTP status codes classified as transient (eligible for retry)
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


@dataclass
class AtlasGraphQLClient:
    """Async HTTP client for the Atlas GraphQL API.

    Features:
        - Automatic retries on transient errors (5xx, 429, timeouts, network)
        - Error classification: transient vs. permanent
        - Optional budget tracker integration (consume-on-success)
        - Optional circuit breaker integration (fast-fail when API is down)

    Args:
        base_url: GraphQL endpoint URL.
        timeout: Request timeout in seconds (default 10.0).
        max_retries: Number of retry attempts for transient errors (default 3).
        backoff_base: Base seconds for exponential backoff (default 1.0).
        budget_tracker: Optional budget tracker for rate limiting.
        circuit_breaker: Optional circuit breaker for health checking.
    """

    base_url: str
    timeout: float = 10.0
    max_retries: int = 3
    backoff_base: float = 1.0
    budget_tracker: GraphQLBudgetTracker | None = None
    circuit_breaker: CircuitBreaker | None = None

    async def execute(
        self,
        query: str,
        variables: dict | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Execute a GraphQL query against the Atlas API.

        Args:
            query: GraphQL query string.
            variables: Optional query variables.
            session_id: Optional session ID for per-session budget tracking.

        Returns:
            The ``data`` field from the GraphQL response.

        Raises:
            BudgetExhaustedError: Budget is exhausted (no HTTP call made).
            CircuitOpenError: Circuit breaker is open (no HTTP call made).
            GraphQLError: Permanent error (4xx, GraphQL validation error).
            TransientGraphQLError: Transient error after all retries exhausted.
        """
        # Pre-flight checks
        if self.circuit_breaker is not None and self.circuit_breaker.is_open():
            raise CircuitOpenError()

        if self.budget_tracker is not None and not self.budget_tracker.is_available(
            session_id=session_id
        ):
            raise BudgetExhaustedError()

        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables

        last_error: Exception | None = None
        total_attempts = 1 + self.max_retries

        for attempt in range(total_attempts):
            try:
                data = await self._send_request(payload)

                # Success — record on tracker/breaker
                if self.circuit_breaker is not None:
                    self.circuit_breaker.record_success()
                if self.budget_tracker is not None:
                    await self.budget_tracker.consume(session_id=session_id)

                return data

            except TransientGraphQLError as exc:
                last_error = exc
                if self.circuit_breaker is not None:
                    self.circuit_breaker.record_failure()

                if attempt < total_attempts - 1:
                    delay = self.backoff_base * (2**attempt)
                    logger.warning(
                        "Transient error (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        total_attempts,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("All %d attempts failed: %s", total_attempts, exc)

            except GraphQLError:
                # Permanent errors (bad query, validation) — API is healthy,
                # don't count toward circuit breaker failures.
                raise

        # All retries exhausted
        raise last_error  # type: ignore[misc]

    async def _send_request(self, payload: dict) -> dict:
        """Send a single HTTP request and parse the response.

        Raises:
            TransientGraphQLError: For transient HTTP/network errors.
            GraphQLError: For permanent errors (4xx, GraphQL errors).
        """
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout)
            ) as http_client:
                response = await http_client.post(
                    self.base_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.TimeoutException as exc:
            raise TransientGraphQLError(f"Request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise TransientGraphQLError(str(exc)) from exc
        except httpx.RequestError as exc:
            raise TransientGraphQLError(f"Network error: {exc}") from exc

        # Classify HTTP status
        if response.status_code in _TRANSIENT_STATUS_CODES:
            raise TransientGraphQLError(
                f"HTTP {response.status_code}: {response.text[:200]}"
            )
        if response.status_code >= 400:
            raise GraphQLError(f"HTTP {response.status_code}: {response.text[:200]}")

        # Parse JSON response
        try:
            body = response.json()
        except ValueError as exc:
            raise GraphQLError(f"Invalid JSON response: {exc}") from exc

        errors = body.get("errors")
        data = body.get("data")

        # GraphQL spec: if data is present (even with errors), return it
        if data is not None:
            if errors:
                logger.warning("GraphQL response contained partial errors: %s", errors)
            return data

        # No data — treat errors as the response
        if errors:
            messages = "; ".join(e.get("message", str(e)) for e in errors)
            raise GraphQLError(messages, errors=errors)

        raise GraphQLError("Empty GraphQL response: no data and no errors")
