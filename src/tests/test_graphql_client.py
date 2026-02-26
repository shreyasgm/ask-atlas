"""Tests for the GraphQL HTTP client, budget tracker, and circuit breaker.

Covers:
- GraphQLBudgetTracker: sliding-window rate limiting with consume-on-success
- CircuitBreaker: 3-state (closed → open → half-open) failure protection
- AtlasGraphQLClient: async httpx client with retries and error classification
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import src.graphql_client as graphql_client_module
from src.graphql_client import (
    AtlasGraphQLClient,
    BudgetExhaustedError,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    GraphQLBudgetTracker,
    GraphQLError,
    TransientGraphQLError,
    get_shared_budget_tracker,
)

# ---------------------------------------------------------------------------
# GraphQLBudgetTracker
# ---------------------------------------------------------------------------


class TestGraphQLBudgetTracker:
    """Tests for the sliding-window budget tracker."""

    def test_available_when_under_limit(self) -> None:
        """A fresh tracker should report availability."""
        tracker = GraphQLBudgetTracker(max_requests=10, window_seconds=60.0)
        assert tracker.is_available() is True

    def test_remaining_starts_at_max(self) -> None:
        """Initial remaining count equals max_requests."""
        tracker = GraphQLBudgetTracker(max_requests=100, window_seconds=60.0)
        assert tracker.remaining() == 100

    async def test_consume_decrements_remaining(self) -> None:
        """Each consume() call decrements the remaining count."""
        tracker = GraphQLBudgetTracker(max_requests=10, window_seconds=60.0)
        assert await tracker.consume() is True
        assert tracker.remaining() == 9

    async def test_multiple_consumes(self) -> None:
        """Multiple consume() calls decrement sequentially."""
        tracker = GraphQLBudgetTracker(max_requests=5, window_seconds=60.0)
        for _ in range(3):
            await tracker.consume()
        assert tracker.remaining() == 2

    async def test_not_available_when_exhausted(self) -> None:
        """Tracker reports unavailable when budget is fully consumed."""
        tracker = GraphQLBudgetTracker(max_requests=3, window_seconds=60.0)
        for _ in range(3):
            await tracker.consume()
        assert tracker.is_available() is False
        assert tracker.remaining() == 0

    async def test_consume_returns_false_when_exhausted(self) -> None:
        """consume() returns False when budget is fully spent."""
        tracker = GraphQLBudgetTracker(max_requests=2, window_seconds=60.0)
        assert await tracker.consume() is True
        assert await tracker.consume() is True
        assert await tracker.consume() is False

    async def test_sliding_window_expires_old_entries(self) -> None:
        """Old entries fall off the sliding window, freeing budget."""
        tracker = GraphQLBudgetTracker(max_requests=2, window_seconds=0.1)
        await tracker.consume()
        await tracker.consume()
        assert tracker.remaining() == 0

        # Wait for window to expire
        await asyncio.sleep(0.15)

        assert tracker.remaining() == 2
        assert tracker.is_available() is True

    async def test_concurrent_consume_is_safe(self) -> None:
        """Concurrent consume() calls don't exceed the budget."""
        tracker = GraphQLBudgetTracker(max_requests=10, window_seconds=60.0)

        results = await asyncio.gather(*[tracker.consume() for _ in range(15)])

        successful = sum(1 for r in results if r)
        assert successful == 10
        assert tracker.remaining() == 0

    # -- Per-session limits --

    async def test_per_session_limit(self) -> None:
        """Per-session budget is enforced independently of global budget."""
        tracker = GraphQLBudgetTracker(
            max_requests=100,
            window_seconds=60.0,
            max_requests_per_session=3,
        )
        session_id = "user-42"

        for _ in range(3):
            assert await tracker.consume(session_id=session_id) is True

        # Session is exhausted, but global is not
        assert await tracker.consume(session_id=session_id) is False
        assert tracker.is_available() is True

    async def test_different_sessions_have_independent_budgets(self) -> None:
        """Each session gets its own budget allocation."""
        tracker = GraphQLBudgetTracker(
            max_requests=100,
            window_seconds=60.0,
            max_requests_per_session=2,
        )

        assert await tracker.consume(session_id="alice") is True
        assert await tracker.consume(session_id="alice") is True
        assert await tracker.consume(session_id="alice") is False

        # Bob's budget is unaffected
        assert await tracker.consume(session_id="bob") is True
        assert await tracker.consume(session_id="bob") is True

    async def test_session_available_checks_both_limits(self) -> None:
        """is_available with session_id checks both global and session budgets."""
        tracker = GraphQLBudgetTracker(
            max_requests=2,
            window_seconds=60.0,
            max_requests_per_session=5,
        )
        # Exhaust global budget
        await tracker.consume(session_id="a")
        await tracker.consume(session_id="b")

        # Session "c" hasn't used anything, but global is exhausted
        assert tracker.is_available(session_id="c") is False

    async def test_session_sliding_window_expires(self) -> None:
        """Per-session entries also expire from the sliding window."""
        tracker = GraphQLBudgetTracker(
            max_requests=100,
            window_seconds=0.1,
            max_requests_per_session=1,
        )
        await tracker.consume(session_id="x")
        assert await tracker.consume(session_id="x") is False

        await asyncio.sleep(0.15)
        assert await tracker.consume(session_id="x") is True

    async def test_consume_without_session_ignores_per_session(self) -> None:
        """Consuming without a session_id only checks global budget."""
        tracker = GraphQLBudgetTracker(
            max_requests=5,
            window_seconds=60.0,
            max_requests_per_session=1,
        )
        # Multiple consumes without session always succeed (until global limit)
        for _ in range(5):
            assert await tracker.consume() is True
        assert await tracker.consume() is False


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Tests for the 3-state circuit breaker."""

    def test_starts_closed(self) -> None:
        """Initial state is CLOSED — requests flow through."""
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_open() is False

    def test_single_failure_stays_closed(self) -> None:
        """One failure is not enough to trip the circuit."""
        cb = CircuitBreaker(failure_threshold=5)
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_open() is False

    def test_consecutive_failures_trip_to_open(self) -> None:
        """Reaching the failure threshold opens the circuit."""
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_open() is True

    def test_success_resets_failure_count(self) -> None:
        """A success in CLOSED state resets the consecutive failure counter."""
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

        # Need all 5 again to trip
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_recovery_timeout_transitions_to_half_open(self) -> None:
        """After recovery_timeout, is_open() returns False (half-open probe)."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open() is True

        time.sleep(0.15)

        assert cb.is_open() is False
        assert cb.state == CircuitState.HALF_OPEN

    def test_success_in_half_open_closes(self) -> None:
        """A successful probe in HALF_OPEN closes the circuit."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.15)

        # Trigger transition to HALF_OPEN
        assert cb.is_open() is False
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_open() is False

    def test_failure_in_half_open_reopens(self) -> None:
        """A failure in HALF_OPEN immediately re-opens the circuit."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()

        time.sleep(0.15)

        _ = cb.is_open()  # Triggers HALF_OPEN transition
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_open() is True

    def test_multiple_failures_beyond_threshold(self) -> None:
        """Additional failures after tripping keep the circuit open."""
        cb = CircuitBreaker(failure_threshold=2)
        for _ in range(5):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_default_thresholds(self) -> None:
        """Default values match the design spec (5 failures, 30s recovery)."""
        cb = CircuitBreaker()
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 30.0


# ---------------------------------------------------------------------------
# AtlasGraphQLClient
# ---------------------------------------------------------------------------


def _make_httpx_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
) -> httpx.Response:
    """Build a fake httpx.Response for testing."""
    kwargs: dict = {
        "status_code": status_code,
        "request": httpx.Request("POST", "https://atlas.cid.harvard.edu/api/graphql"),
    }
    if json_data is not None:
        kwargs["json"] = json_data
    else:
        kwargs["text"] = text
    return httpx.Response(**kwargs)


class TestAtlasGraphQLClient:
    """Tests for the async GraphQL HTTP client."""

    @pytest.fixture()
    def client(self) -> AtlasGraphQLClient:
        """Create a client with default settings and no budget/circuit integration."""
        return AtlasGraphQLClient(
            base_url="https://atlas.cid.harvard.edu/api/graphql",
            timeout=5.0,
        )

    @pytest.fixture()
    def budget_tracker(self) -> GraphQLBudgetTracker:
        return GraphQLBudgetTracker(max_requests=10, window_seconds=60.0)

    @pytest.fixture()
    def circuit_breaker(self) -> CircuitBreaker:
        return CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)

    @pytest.fixture()
    def integrated_client(
        self, budget_tracker: GraphQLBudgetTracker, circuit_breaker: CircuitBreaker
    ) -> AtlasGraphQLClient:
        """Client with budget tracker and circuit breaker."""
        return AtlasGraphQLClient(
            base_url="https://atlas.cid.harvard.edu/api/graphql",
            timeout=5.0,
            budget_tracker=budget_tracker,
            circuit_breaker=circuit_breaker,
        )

    async def test_execute_success(self, client: AtlasGraphQLClient) -> None:
        """Successful query returns parsed data."""
        expected = {"data": {"country": {"name": "Brazil"}}}
        mock_response = _make_httpx_response(json_data=expected)

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await client.execute(
                query='{ country(countryCode: "bra") { name } }'
            )

        assert result == {"country": {"name": "Brazil"}}

    async def test_execute_with_variables(self, client: AtlasGraphQLClient) -> None:
        """Variables are passed correctly to the GraphQL request."""
        expected = {"data": {"country": {"name": "Kenya"}}}
        mock_response = _make_httpx_response(json_data=expected)

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_post:
            await client.execute(
                query="query($code: String!) { country(countryCode: $code) { name } }",
                variables={"code": "ken"},
            )

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["variables"] == {"code": "ken"}

    async def test_execute_graphql_error_in_response(
        self, client: AtlasGraphQLClient
    ) -> None:
        """GraphQL errors in the response body raise GraphQLError."""
        error_response = {
            "errors": [{"message": "Field 'foo' not found"}],
            "data": None,
        }
        mock_response = _make_httpx_response(json_data=error_response)

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            with pytest.raises(GraphQLError, match="Field 'foo' not found"):
                await client.execute(query="{ foo }")

    async def test_execute_http_4xx_raises_permanent_error(
        self, client: AtlasGraphQLClient
    ) -> None:
        """4xx HTTP errors raise GraphQLError (permanent, not transient)."""
        mock_response = _make_httpx_response(status_code=400, text="Bad Request")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            with pytest.raises(GraphQLError):
                await client.execute(query="{ invalid }")

    async def test_execute_http_5xx_raises_transient_error(
        self, client: AtlasGraphQLClient
    ) -> None:
        """5xx HTTP errors raise TransientGraphQLError."""
        mock_response = _make_httpx_response(
            status_code=500, text="Internal Server Error"
        )

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            with pytest.raises(TransientGraphQLError):
                await client.execute(query="{ something }")

    async def test_execute_http_429_raises_transient_error(
        self, client: AtlasGraphQLClient
    ) -> None:
        """429 Too Many Requests is classified as transient."""
        mock_response = _make_httpx_response(status_code=429, text="Rate Limited")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            with pytest.raises(TransientGraphQLError):
                await client.execute(query="{ something }")

    async def test_execute_timeout_raises_transient_error(
        self, client: AtlasGraphQLClient
    ) -> None:
        """Network timeout raises TransientGraphQLError."""
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("read timed out"),
        ):
            with pytest.raises(TransientGraphQLError, match="timed out"):
                await client.execute(query="{ something }")

    async def test_execute_connect_error_raises_transient_error(
        self, client: AtlasGraphQLClient
    ) -> None:
        """Connection failures raise TransientGraphQLError."""
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("connection refused"),
        ):
            with pytest.raises(TransientGraphQLError, match="connection refused"):
                await client.execute(query="{ something }")

    async def test_retry_on_transient_error(self, client: AtlasGraphQLClient) -> None:
        """Client retries on transient errors up to max_retries times."""
        success_response = _make_httpx_response(json_data={"data": {"value": 42}})
        # Fail twice, then succeed
        side_effects = [
            httpx.ReadTimeout("timeout 1"),
            httpx.ReadTimeout("timeout 2"),
            success_response,
        ]

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, side_effect=side_effects
        ) as mock_post:
            result = await client.execute(query="{ value }")

        assert result == {"value": 42}
        assert mock_post.call_count == 3

    async def test_retry_exhaustion_raises(self, client: AtlasGraphQLClient) -> None:
        """After max_retries transient failures, the error propagates."""
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("persistent timeout"),
        ) as mock_post:
            with pytest.raises(TransientGraphQLError):
                await client.execute(query="{ value }")

        # Default max_retries is 3 → total 4 attempts (1 initial + 3 retries)
        assert mock_post.call_count == 4

    async def test_no_retry_on_permanent_error(
        self, client: AtlasGraphQLClient
    ) -> None:
        """Permanent errors (4xx, GraphQL errors) are not retried."""
        mock_response = _make_httpx_response(status_code=400, text="Bad Request")

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_post:
            with pytest.raises(GraphQLError):
                await client.execute(query="{ bad }")

        assert mock_post.call_count == 1

    # -- Budget tracker integration --

    async def test_budget_consumed_on_success(
        self,
        integrated_client: AtlasGraphQLClient,
        budget_tracker: GraphQLBudgetTracker,
    ) -> None:
        """Budget is consumed after a successful response."""
        mock_response = _make_httpx_response(json_data={"data": {"x": 1}})
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await integrated_client.execute(query="{ x }")

        assert budget_tracker.remaining() == 9

    async def test_budget_not_consumed_on_error(
        self,
        integrated_client: AtlasGraphQLClient,
        budget_tracker: GraphQLBudgetTracker,
    ) -> None:
        """Budget is NOT consumed when the request fails."""
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("timeout"),
        ):
            with pytest.raises(TransientGraphQLError):
                await integrated_client.execute(query="{ x }")

        assert budget_tracker.remaining() == 10

    async def test_budget_exhausted_raises_before_request(
        self,
        integrated_client: AtlasGraphQLClient,
        budget_tracker: GraphQLBudgetTracker,
    ) -> None:
        """When budget is exhausted, execute raises without making HTTP call."""
        # Exhaust budget
        for _ in range(10):
            await budget_tracker.consume()

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock
        ) as mock_post:
            with pytest.raises(BudgetExhaustedError):
                await integrated_client.execute(query="{ x }")

        mock_post.assert_not_called()

    # -- Circuit breaker integration --

    async def test_circuit_open_raises_without_request(
        self,
        integrated_client: AtlasGraphQLClient,
        circuit_breaker: CircuitBreaker,
    ) -> None:
        """When circuit is open, execute raises without making HTTP call."""
        # Trip the circuit
        for _ in range(3):
            circuit_breaker.record_failure()

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock
        ) as mock_post:
            with pytest.raises(CircuitOpenError):
                await integrated_client.execute(query="{ x }")

        mock_post.assert_not_called()

    async def test_circuit_records_success_on_2xx(
        self,
        integrated_client: AtlasGraphQLClient,
        circuit_breaker: CircuitBreaker,
    ) -> None:
        """Successful requests record success on the circuit breaker."""
        mock_response = _make_httpx_response(json_data={"data": {"x": 1}})
        # Pre-fail to verify success resets
        circuit_breaker.record_failure()
        circuit_breaker.record_failure()

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await integrated_client.execute(query="{ x }")

        assert circuit_breaker._failure_count == 0
        assert circuit_breaker.state == CircuitState.CLOSED

    async def test_circuit_records_failure_on_error(
        self,
        integrated_client: AtlasGraphQLClient,
        circuit_breaker: CircuitBreaker,
    ) -> None:
        """Failed requests record failure on the circuit breaker."""
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            with pytest.raises(TransientGraphQLError):
                await integrated_client.execute(query="{ x }")

        assert circuit_breaker._failure_count > 0

    # -- Custom retry count --

    async def test_custom_max_retries(self) -> None:
        """max_retries parameter is respected."""
        client = AtlasGraphQLClient(
            base_url="https://example.com/graphql",
            max_retries=1,
        )
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("timeout"),
        ) as mock_post:
            with pytest.raises(TransientGraphQLError):
                await client.execute(query="{ x }")

        # 1 initial + 1 retry = 2
        assert mock_post.call_count == 2

    async def test_zero_retries(self) -> None:
        """max_retries=0 means no retries — only the initial attempt."""
        client = AtlasGraphQLClient(
            base_url="https://example.com/graphql",
            max_retries=0,
        )
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("timeout"),
        ) as mock_post:
            with pytest.raises(TransientGraphQLError):
                await client.execute(query="{ x }")

        assert mock_post.call_count == 1

    # -- Response with partial data + errors --

    async def test_partial_data_with_errors_returns_data(
        self, client: AtlasGraphQLClient
    ) -> None:
        """When response has both data and errors, data is returned (GraphQL spec)."""
        response_body = {
            "data": {"country": {"name": "Brazil"}},
            "errors": [{"message": "Deprecated field used"}],
        }
        mock_response = _make_httpx_response(json_data=response_body)

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await client.execute(query="{ country { name } }")

        # Per GraphQL spec, partial data is still returned
        assert result == {"country": {"name": "Brazil"}}

    async def test_errors_only_no_data_raises(self, client: AtlasGraphQLClient) -> None:
        """When response has errors but no data, GraphQLError is raised."""
        response_body = {
            "errors": [{"message": "Cannot query field 'foo'"}],
        }
        mock_response = _make_httpx_response(json_data=response_body)

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            with pytest.raises(GraphQLError, match="Cannot query field"):
                await client.execute(query="{ foo }")


# ---------------------------------------------------------------------------
# Permanent errors must NOT trip circuit breaker
# ---------------------------------------------------------------------------


class TestPermanentErrorCircuitBreakerInteraction:
    """Verify that permanent GraphQL errors don't affect circuit breaker health."""

    async def test_permanent_error_does_not_trip_circuit_breaker(self) -> None:
        """Permanent GraphQLErrors must not count toward circuit breaker failures.

        A permanent error (bad query, validation failure) means the API is
        healthy — it responded correctly. Only transient errors (network, 5xx)
        should trip the circuit.
        """
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        client = AtlasGraphQLClient(
            base_url="https://atlas.cid.harvard.edu/api/graphql",
            timeout=5.0,
            max_retries=0,
            circuit_breaker=cb,
        )

        # Response that triggers a permanent GraphQLError (errors + data: null)
        error_response = _make_httpx_response(
            json_data={
                "errors": [{"message": "Field 'x' not found"}],
                "data": None,
            }
        )

        for _ in range(10):
            with patch.object(
                httpx.AsyncClient,
                "post",
                new_callable=AsyncMock,
                return_value=error_response,
            ):
                with pytest.raises(GraphQLError, match="Field 'x' not found"):
                    await client.execute(query="{ x }")

        # Circuit breaker should remain healthy
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    async def test_transient_errors_still_trip_circuit_breaker(self) -> None:
        """Regression guard: transient errors must still trip the circuit breaker.

        After failure_threshold consecutive transient errors, the circuit
        breaker should transition to OPEN.
        """
        threshold = 3
        cb = CircuitBreaker(failure_threshold=threshold, recovery_timeout=30.0)
        client = AtlasGraphQLClient(
            base_url="https://atlas.cid.harvard.edu/api/graphql",
            timeout=5.0,
            max_retries=0,
            circuit_breaker=cb,
        )

        for _ in range(threshold):
            with patch.object(
                httpx.AsyncClient,
                "post",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("connection refused"),
            ):
                with pytest.raises(TransientGraphQLError):
                    await client.execute(query="{ x }")

        assert cb.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# Process-global budget tracker singleton
# ---------------------------------------------------------------------------


class TestGetSharedBudgetTracker:
    """Tests for the get_shared_budget_tracker() factory function."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self) -> None:
        """Reset the module-level singleton before and after each test."""
        graphql_client_module._shared_budget_tracker = None
        yield  # type: ignore[misc]
        graphql_client_module._shared_budget_tracker = None

    def test_get_shared_budget_tracker_returns_singleton(self) -> None:
        """Two calls return the exact same object; mutations are shared."""
        tracker_a = get_shared_budget_tracker()
        tracker_b = get_shared_budget_tracker()

        assert tracker_a is tracker_b

    async def test_shared_budget_tracker_shares_state(self) -> None:
        """Consuming from one reference is visible through the other."""
        tracker_a = get_shared_budget_tracker(max_requests=5, window_seconds=60.0)
        tracker_b = get_shared_budget_tracker()

        assert tracker_a.remaining() == 5

        await tracker_a.consume()
        assert tracker_b.remaining() == 4


# ---------------------------------------------------------------------------
# Integration tests — real Atlas GraphQL API
# ---------------------------------------------------------------------------

ATLAS_GRAPHQL_URL = "https://atlas.hks.harvard.edu/api/graphql"


@pytest.mark.integration
class TestAtlasGraphQLClientIntegration:
    """Integration tests that hit the real Atlas GraphQL API.

    These verify that the client handles real HTTP responses, error codes,
    and response shapes correctly — things mocked tests cannot catch.
    """

    @pytest.fixture()
    def client(self) -> AtlasGraphQLClient:
        return AtlasGraphQLClient(
            base_url=ATLAS_GRAPHQL_URL,
            timeout=15.0,
            max_retries=1,
        )

    async def test_simple_metadata_query(self, client: AtlasGraphQLClient) -> None:
        """A minimal no-args query returns expected metadata fields."""
        data = await client.execute(query="{ metadata { serverName ingestionDate } }")
        assert "metadata" in data
        assert "serverName" in data["metadata"]
        assert "ingestionDate" in data["metadata"]

    async def test_country_product_year_query(self, client: AtlasGraphQLClient) -> None:
        """A standard trade data query returns rows with expected schema."""
        query = """
        {
            countryProductYear(
                countryId: 404
                productLevel: 2
                yearMin: 2022
                yearMax: 2022
            ) {
                countryId productId year exportValue
            }
        }
        """
        data = await client.execute(query=query)
        rows = data["countryProductYear"]
        assert len(rows) > 0

        row = rows[0]
        # API returns countryId as a string like "country-404"
        assert "404" in str(row["countryId"])
        assert row["year"] == 2022
        assert "exportValue" in row
        assert "productId" in row

    async def test_invalid_field_returns_graphql_error(
        self, client: AtlasGraphQLClient
    ) -> None:
        """Querying a non-existent field raises a permanent GraphQLError."""
        with pytest.raises(GraphQLError):
            await client.execute(query="{ metadata { nonExistentField } }")

    async def test_timeout_with_very_short_limit(self) -> None:
        """An extremely short timeout triggers a TransientGraphQLError."""
        short_client = AtlasGraphQLClient(
            base_url=ATLAS_GRAPHQL_URL,
            timeout=0.001,
            max_retries=0,
        )
        with pytest.raises(TransientGraphQLError):
            await short_client.execute(query="{ metadata { serverName } }")

    async def test_budget_tracker_integration_with_real_api(self) -> None:
        """Budget is consumed only on successful real API calls."""
        tracker = GraphQLBudgetTracker(max_requests=5, window_seconds=60.0)
        client = AtlasGraphQLClient(
            base_url=ATLAS_GRAPHQL_URL,
            timeout=15.0,
            max_retries=0,
            budget_tracker=tracker,
        )

        await client.execute(query="{ metadata { serverName } }")
        assert tracker.remaining() == 4

        # A failing query should NOT consume budget
        with pytest.raises(GraphQLError):
            await client.execute(query="{ metadata { bogusField } }")
        assert tracker.remaining() == 4

    async def test_circuit_breaker_closes_after_real_success(self) -> None:
        """A real successful response resets the circuit breaker."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # not yet tripped

        client = AtlasGraphQLClient(
            base_url=ATLAS_GRAPHQL_URL,
            timeout=15.0,
            max_retries=0,
            circuit_breaker=cb,
        )
        await client.execute(query="{ metadata { serverName } }")
        assert cb._failure_count == 0
        assert cb.state == CircuitState.CLOSED
