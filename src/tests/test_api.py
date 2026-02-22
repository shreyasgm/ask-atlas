"""Tests for the FastAPI application — no DB, no LLM.

All tests run against the API contract using TestClient + mocks.
No external services required, so no pytest markers needed.
"""

import json
import threading
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api import _state, app
from src.conversations import InMemoryConversationStore
from src.text_to_sql import AnswerResult, AtlasTextToSQL, StreamData


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> list[dict[str, str]]:
    """Parse raw SSE text into a list of ``{event, data}`` dicts.

    Handles:
    - Multi-line ``data:`` fields (joined with newlines)
    - Empty lines that delimit events
    - Lines with extra whitespace after the colon
    - Events that lack one of the two fields
    """
    events: list[dict[str, str]] = []
    current_event: str | None = None
    current_data_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            current_data_lines.append(line.split(":", 1)[1].strip())
        elif line == "":
            # Empty line = event boundary
            if current_event is not None or current_data_lines:
                entry: dict[str, str] = {}
                if current_event is not None:
                    entry["event"] = current_event
                if current_data_lines:
                    entry["data"] = "\n".join(current_data_lines)
                events.append(entry)
                current_event = None
                current_data_lines = []

    # Trailing event without a final blank line
    if current_event is not None or current_data_lines:
        entry = {}
        if current_event is not None:
            entry["event"] = current_event
        if current_data_lines:
            entry["data"] = "\n".join(current_data_lines)
        events.append(entry)

    return events


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _inject_mock_atlas():
    """Inject a mock AtlasTextToSQL into app state for every test.

    Provides a default mock that returns ``"Mocked answer"`` for non-streaming
    calls and yields two agent_talk StreamData chunks for streaming calls.
    Resets ``_state.atlas_sql`` to ``None`` after each test.
    """
    mock = MagicMock(spec=AtlasTextToSQL)
    mock.aanswer_question = AsyncMock(return_value=AnswerResult(
        answer="Mocked answer",
        queries=[],
        resolved_products=None,
        schemas_used=[],
        total_rows=0,
        total_execution_time_ms=0,
    ))

    async def _fake_stream(question: str, thread_id: str | None = None, **kwargs):
        yield StreamData(
            source="agent", content="streamed ", message_type="agent_talk"
        )
        yield StreamData(
            source="agent", content="answer", message_type="agent_talk"
        )

    mock.aanswer_question_stream = _fake_stream
    _state.atlas_sql = mock
    yield
    _state.atlas_sql = None


@pytest.fixture()
def client() -> TestClient:
    """Synchronous TestClient bound to the FastAPI ``app``."""
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Tests for the health-check endpoint."""

    def test_returns_200(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200

    def test_returns_status_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.json() == {"status": "ok"}

    def test_health_independent_of_atlas_state(self, client: TestClient) -> None:
        """Health endpoint should return 200 even when atlas_sql is None."""
        _state.atlas_sql = None
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /threads
# ---------------------------------------------------------------------------


class TestThreadCreation:
    """Tests for the thread creation endpoint."""

    def test_returns_200(self, client: TestClient) -> None:
        response = client.post("/threads")
        assert response.status_code == 200

    def test_returns_thread_id(self, client: TestClient) -> None:
        data = client.post("/threads").json()
        assert "thread_id" in data
        assert len(data["thread_id"]) > 0

    def test_thread_id_is_valid_uuid(self, client: TestClient) -> None:
        data = client.post("/threads").json()
        # Should not raise ValueError if it is a valid UUID
        parsed = uuid.UUID(data["thread_id"])
        assert str(parsed) == data["thread_id"]

    def test_two_calls_return_unique_ids(self, client: TestClient) -> None:
        r1 = client.post("/threads").json()
        r2 = client.post("/threads").json()
        assert r1["thread_id"] != r2["thread_id"]

    def test_many_calls_all_unique(self, client: TestClient) -> None:
        ids = {client.post("/threads").json()["thread_id"] for _ in range(20)}
        assert len(ids) == 20


# ---------------------------------------------------------------------------
# POST /chat  (non-streaming)
# ---------------------------------------------------------------------------


class TestChat:
    """Tests for the synchronous chat endpoint."""

    def test_returns_200(self, client: TestClient) -> None:
        response = client.post("/chat", json={"question": "Top US exports?"})
        assert response.status_code == 200

    def test_returns_mocked_answer(self, client: TestClient) -> None:
        data = client.post("/chat", json={"question": "Top US exports?"}).json()
        assert data["answer"] == "Mocked answer"

    def test_response_includes_thread_id(self, client: TestClient) -> None:
        data = client.post("/chat", json={"question": "hello"}).json()
        assert "thread_id" in data
        assert len(data["thread_id"]) > 0

    def test_echoes_provided_thread_id(self, client: TestClient) -> None:
        response = client.post(
            "/chat", json={"question": "hi", "thread_id": "my-thread-42"}
        )
        assert response.status_code == 200
        assert response.json()["thread_id"] == "my-thread-42"

    def test_generates_thread_id_when_omitted(self, client: TestClient) -> None:
        data = client.post("/chat", json={"question": "hello"}).json()
        assert len(data["thread_id"]) > 0

    def test_forwards_question_to_atlas(self, client: TestClient) -> None:
        """The question from the request body is forwarded to aanswer_question."""
        client.post("/chat", json={"question": "Brazil exports?"})
        _state.atlas_sql.aanswer_question.assert_awaited_once()
        call_args = _state.atlas_sql.aanswer_question.call_args
        assert call_args.args[0] == "Brazil exports?"

    def test_forwards_thread_id_to_atlas(self, client: TestClient) -> None:
        """The thread_id should be passed through to the backend."""
        client.post(
            "/chat", json={"question": "test", "thread_id": "tid-123"}
        )
        call_kwargs = _state.atlas_sql.aanswer_question.call_args
        # thread_id may be positional or keyword
        assert "tid-123" in (
            list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        )

    def test_missing_question_returns_422(self, client: TestClient) -> None:
        response = client.post("/chat", json={})
        assert response.status_code == 422

    def test_empty_body_returns_422(self, client: TestClient) -> None:
        response = client.post("/chat", content=b"", headers={"content-type": "application/json"})
        assert response.status_code == 422

    def test_response_matches_chat_response_schema(self, client: TestClient) -> None:
        """Response body must have the expected ChatResponse keys."""
        data = client.post("/chat", json={"question": "hello"}).json()
        expected_keys = {
            "answer", "thread_id", "queries", "resolved_products",
            "schemas_used", "total_rows", "total_execution_time_ms",
        }
        assert set(data.keys()) == expected_keys


# ---------------------------------------------------------------------------
# POST /chat — pipeline data
# ---------------------------------------------------------------------------


class TestChatPipelineData:
    """Tests for structured pipeline data in /chat response."""

    @pytest.fixture(autouse=True)
    def _inject_pipeline_mock(self):
        """Override default mock with one that returns pipeline data."""
        mock = MagicMock(spec=AtlasTextToSQL)
        mock.aanswer_question = AsyncMock(return_value=AnswerResult(
            answer="Brazil exports coffee.",
            queries=[{
                "sql": "SELECT * FROM hs92.country_product_year_4",
                "columns": ["country", "value"],
                "rows": [["BRA", 5000]],
                "row_count": 1,
                "execution_time_ms": 55,
                "tables": ["hs92.country_product_year_4"],
                "schema_name": "hs92",
            }],
            resolved_products={
                "schemas": ["hs92"],
                "products": [{"name": "coffee", "codes": ["0901"], "schema": "hs92"}],
            },
            schemas_used=["hs92"],
            total_rows=1,
            total_execution_time_ms=55,
        ))

        async def _fake_stream(question, thread_id=None, **kwargs):
            yield StreamData(source="agent", content="ok", message_type="agent_talk")

        mock.aanswer_question_stream = _fake_stream
        _state.atlas_sql = mock
        yield
        _state.atlas_sql = None

    def test_chat_includes_queries_when_present(self, client: TestClient) -> None:
        """Response should include queries list with correct shape."""
        data = client.post("/chat", json={"question": "Brazil coffee?"}).json()
        assert data["queries"] is not None
        assert len(data["queries"]) == 1
        q = data["queries"][0]
        assert q["sql"] == "SELECT * FROM hs92.country_product_year_4"
        assert q["columns"] == ["country", "value"]
        assert q["rows"] == [["BRA", 5000]]
        assert q["row_count"] == 1
        assert q["execution_time_ms"] == 55

    def test_chat_response_includes_aggregate_stats(self, client: TestClient) -> None:
        """Response should include total_rows and total_execution_time_ms."""
        data = client.post("/chat", json={"question": "Brazil coffee?"}).json()
        assert data["total_rows"] == 1
        assert data["total_execution_time_ms"] == 55

    def test_chat_backward_compatible_answer_and_thread_id(self, client: TestClient) -> None:
        """answer and thread_id must still be present and correct."""
        data = client.post("/chat", json={"question": "test"}).json()
        assert data["answer"] == "Brazil exports coffee."
        assert "thread_id" in data
        assert len(data["thread_id"]) > 0


class TestChatPipelineDataEmpty:
    """Tests for /chat when no pipeline queries were executed."""

    def test_chat_null_pipeline_fields_for_conversational(self, client: TestClient) -> None:
        """When no queries run, pipeline fields should be null."""
        data = client.post("/chat", json={"question": "hello"}).json()
        assert data["queries"] is None
        assert data["total_rows"] is None
        assert data["total_execution_time_ms"] is None


# ---------------------------------------------------------------------------
# POST /chat/stream  (SSE)
# ---------------------------------------------------------------------------


class TestChatStream:
    """Tests for the SSE streaming chat endpoint."""

    def test_returns_200(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "Exports?"})
        assert response.status_code == 200

    def test_content_type_is_event_stream(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "Exports?"})
        assert "text/event-stream" in response.headers.get("content-type", "")

    def test_first_event_is_thread_id(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "Exports?"})
        events = _parse_sse(response.text)
        assert len(events) > 0
        assert events[0]["event"] == "thread_id"
        thread_data = json.loads(events[0]["data"])
        assert "thread_id" in thread_data

    def test_last_event_is_done(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "Exports?"})
        events = _parse_sse(response.text)
        assert events[-1]["event"] == "done"

    def test_done_event_contains_thread_id(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "Exports?"})
        events = _parse_sse(response.text)
        done_data = json.loads(events[-1]["data"])
        assert "thread_id" in done_data

    def test_middle_events_are_agent_talk(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "Exports?"})
        events = _parse_sse(response.text)
        middle = events[1:-1]
        assert len(middle) >= 1
        for ev in middle:
            assert ev["event"] == "agent_talk"

    def test_middle_events_have_valid_json(self, client: TestClient) -> None:
        """Every SSE event's data field should be parseable JSON."""
        response = client.post("/chat/stream", json={"question": "Exports?"})
        events = _parse_sse(response.text)
        for ev in events:
            data = json.loads(ev["data"])
            assert isinstance(data, dict)

    def test_middle_events_contain_required_fields(self, client: TestClient) -> None:
        """Middle (content) events should include source, content, message_type."""
        response = client.post("/chat/stream", json={"question": "Exports?"})
        events = _parse_sse(response.text)
        middle = events[1:-1]
        for ev in middle:
            data = json.loads(ev["data"])
            assert "source" in data
            assert "content" in data
            assert "message_type" in data

    def test_streamed_content_matches_mock(self, client: TestClient) -> None:
        """Concatenated content from middle events should match mock output."""
        response = client.post("/chat/stream", json={"question": "Exports?"})
        events = _parse_sse(response.text)
        middle = events[1:-1]
        combined = "".join(json.loads(e["data"])["content"] for e in middle)
        assert combined == "streamed answer"

    def test_echoes_provided_thread_id(self, client: TestClient) -> None:
        """Provided thread_id appears in the first and last SSE events."""
        response = client.post(
            "/chat/stream",
            json={"question": "hi", "thread_id": "sse-thread-1"},
        )
        events = _parse_sse(response.text)

        first_data = json.loads(events[0]["data"])
        assert first_data["thread_id"] == "sse-thread-1"

        last_data = json.loads(events[-1]["data"])
        assert last_data["thread_id"] == "sse-thread-1"

    def test_missing_question_returns_422(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /chat/stream — mixed message types (agent_talk + tool_output)
# ---------------------------------------------------------------------------


class TestChatStreamMixedTypes:
    """Tests that SSE streaming handles all StreamData message types."""

    @pytest.fixture(autouse=True)
    def _inject_mixed_stream(self):
        """Override the default mock with a stream that yields varied types."""
        mock = MagicMock(spec=AtlasTextToSQL)
        mock.aanswer_question = AsyncMock(return_value=AnswerResult(
            answer="unused", queries=[], resolved_products=None,
            schemas_used=[], total_rows=0, total_execution_time_ms=0,
        ))

        async def _mixed_stream(question: str, thread_id: str | None = None, **kwargs):
            yield StreamData(
                source="agent",
                content="Let me look that up.",
                message_type="agent_talk",
            )
            yield StreamData(
                source="tool",
                content="SELECT * FROM products LIMIT 5",
                message_type="tool_call",
                name="sql_query",
                tool_call="sql_query",
            )
            yield StreamData(
                source="tool",
                content='[{"id":1,"name":"Coffee"}]',
                message_type="tool_output",
                name="sql_query",
            )
            yield StreamData(
                source="agent",
                content="Here are the results.",
                message_type="agent_talk",
            )

        mock.aanswer_question_stream = _mixed_stream
        _state.atlas_sql = mock
        yield
        _state.atlas_sql = None

    def test_all_message_types_present(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "products?"})
        events = _parse_sse(response.text)
        middle = events[1:-1]
        event_types = {e["event"] for e in middle}
        assert "agent_talk" in event_types
        assert "tool_call" in event_types
        assert "tool_output" in event_types

    def test_tool_call_event_content(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "products?"})
        events = _parse_sse(response.text)
        tool_calls = [
            e for e in events if e.get("event") == "tool_call"
        ]
        assert len(tool_calls) == 1
        data = json.loads(tool_calls[0]["data"])
        assert "SELECT" in data["content"]
        assert data["source"] == "tool"

    def test_tool_output_event_content(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "products?"})
        events = _parse_sse(response.text)
        tool_outputs = [
            e for e in events if e.get("event") == "tool_output"
        ]
        assert len(tool_outputs) == 1
        data = json.loads(tool_outputs[0]["data"])
        assert "Coffee" in data["content"]
        assert data["source"] == "tool"

    def test_event_order_preserved(self, client: TestClient) -> None:
        """Events should arrive in the same order the mock yields them."""
        response = client.post("/chat/stream", json={"question": "products?"})
        events = _parse_sse(response.text)
        types = [e["event"] for e in events]
        # thread_id, agent_talk, tool_call, tool_output, agent_talk, done
        assert types[0] == "thread_id"
        assert types[1] == "agent_talk"
        assert types[2] == "tool_call"
        assert types[3] == "tool_output"
        assert types[4] == "agent_talk"
        assert types[-1] == "done"

    def test_four_middle_events(self, client: TestClient) -> None:
        """The mixed mock yields exactly 4 StreamData chunks."""
        response = client.post("/chat/stream", json={"question": "products?"})
        events = _parse_sse(response.text)
        middle = events[1:-1]
        assert len(middle) == 4


# ---------------------------------------------------------------------------
# POST /chat/stream — enhanced events (node_start, pipeline_state, done stats)
# ---------------------------------------------------------------------------


class TestChatStreamEnhancedEvents:
    """Tests for new SSE event types: node_start, pipeline_state, enhanced done."""

    @pytest.fixture(autouse=True)
    def _inject_pipeline_stream(self):
        """Mock stream that yields pipeline events alongside existing types."""
        mock = MagicMock(spec=AtlasTextToSQL)
        mock.aanswer_question = AsyncMock(return_value=AnswerResult(
            answer="unused", queries=[], resolved_products=None,
            schemas_used=[], total_rows=0, total_execution_time_ms=0,
        ))

        async def _pipeline_stream(question: str, thread_id: str | None = None, **kwargs):
            yield StreamData(
                source="agent",
                content="",
                message_type="tool_call",
                tool_call="query_tool",
            )
            yield StreamData(
                source="pipeline",
                content="",
                message_type="node_start",
                payload={"node": "extract_tool_question", "label": "Extracting question", "query_index": 1},
            )
            yield StreamData(
                source="pipeline",
                content="",
                message_type="pipeline_state",
                payload={"stage": "extract_tool_question", "question": "test?"},
            )
            yield StreamData(
                source="pipeline",
                content="",
                message_type="node_start",
                payload={"node": "execute_sql", "label": "Executing query", "query_index": 1},
            )
            yield StreamData(
                source="pipeline",
                content="",
                message_type="pipeline_state",
                payload={
                    "stage": "execute_sql",
                    "columns": ["country", "value"],
                    "rows": [["USA", 1000], ["CHN", 800]],
                    "row_count": 2,
                    "execution_time_ms": 150,
                    "sql": "SELECT * FROM t",
                    "tables": ["t"],
                },
            )
            yield StreamData(
                source="tool",
                content="{'country': 'USA'}",
                message_type="tool_output",
                name="query_tool",
            )
            yield StreamData(
                source="agent",
                content="Here are the results.",
                message_type="agent_talk",
            )

        mock.aanswer_question_stream = _pipeline_stream
        _state.atlas_sql = mock
        yield
        _state.atlas_sql = None

    def test_node_start_in_sse_stream(self, client: TestClient) -> None:
        """node_start events should appear in the SSE stream."""
        response = client.post("/chat/stream", json={"question": "test?"})
        events = _parse_sse(response.text)
        node_starts = [e for e in events if e.get("event") == "node_start"]
        assert len(node_starts) >= 1
        data = json.loads(node_starts[0]["data"])
        assert "node" in data

    def test_pipeline_state_in_sse_stream(self, client: TestClient) -> None:
        """pipeline_state events should appear in the SSE stream."""
        response = client.post("/chat/stream", json={"question": "test?"})
        events = _parse_sse(response.text)
        pipeline_states = [e for e in events if e.get("event") == "pipeline_state"]
        assert len(pipeline_states) >= 1
        data = json.loads(pipeline_states[0]["data"])
        assert "stage" in data

    def test_done_event_has_aggregate_stats(self, client: TestClient) -> None:
        """done event should include total_queries, total_rows, etc."""
        response = client.post("/chat/stream", json={"question": "test?"})
        events = _parse_sse(response.text)
        done_data = json.loads(events[-1]["data"])
        assert done_data["total_queries"] == 1
        assert done_data["total_rows"] == 2
        assert done_data["total_execution_time_ms"] == 150
        assert "total_time_ms" in done_data
        assert done_data["total_time_ms"] >= 0

    def test_done_with_no_pipeline_has_zero_stats(self, client: TestClient) -> None:
        """When no pipeline runs, done stats should all be 0."""
        # Override with simple stream (no pipeline events)
        mock = MagicMock(spec=AtlasTextToSQL)

        async def _simple_stream(question: str, thread_id: str | None = None, **kwargs):
            yield StreamData(
                source="agent", content="Direct answer.", message_type="agent_talk"
            )

        mock.aanswer_question_stream = _simple_stream
        _state.atlas_sql = mock

        response = client.post("/chat/stream", json={"question": "hi"})
        events = _parse_sse(response.text)
        done_data = json.loads(events[-1]["data"])
        assert done_data["total_queries"] == 0
        assert done_data["total_rows"] == 0
        assert done_data["total_execution_time_ms"] == 0

    def test_pipeline_state_payload_not_wrapped(self, client: TestClient) -> None:
        """pipeline_state SSE data should NOT have source/message_type wrapper."""
        response = client.post("/chat/stream", json={"question": "test?"})
        events = _parse_sse(response.text)
        pipeline_states = [e for e in events if e.get("event") == "pipeline_state"]
        assert len(pipeline_states) >= 1
        data = json.loads(pipeline_states[0]["data"])
        assert "source" not in data
        assert "message_type" not in data
        assert "stage" in data

    def test_node_start_payload_not_wrapped(self, client: TestClient) -> None:
        """node_start SSE data should NOT have source/message_type wrapper."""
        response = client.post("/chat/stream", json={"question": "test?"})
        events = _parse_sse(response.text)
        node_starts = [e for e in events if e.get("event") == "node_start"]
        assert len(node_starts) >= 1
        data = json.loads(node_starts[0]["data"])
        assert "source" not in data
        assert "message_type" not in data
        assert "node" in data

    def test_backward_compatible_event_format(self, client: TestClient) -> None:
        """Existing event types (agent_talk, tool_output) still use the wrapper."""
        response = client.post("/chat/stream", json={"question": "test?"})
        events = _parse_sse(response.text)
        agent_talks = [e for e in events if e.get("event") == "agent_talk"]
        assert len(agent_talks) >= 1
        data = json.loads(agent_talks[0]["data"])
        assert "source" in data
        assert "content" in data
        assert "message_type" in data


# ---------------------------------------------------------------------------
# Service unavailable (503)
# ---------------------------------------------------------------------------


class TestServiceUnavailable:
    """When _state.atlas_sql is None, chat endpoints should return 503."""

    @pytest.fixture(autouse=True)
    def _clear_atlas(self):
        """Ensure atlas_sql is None for all tests in this class."""
        _state.atlas_sql = None
        yield
        _state.atlas_sql = None

    def test_chat_returns_503(self, client: TestClient) -> None:
        response = client.post("/chat", json={"question": "hi"})
        assert response.status_code == 503

    def test_chat_503_detail_message(self, client: TestClient) -> None:
        response = client.post("/chat", json={"question": "hi"})
        detail = response.json()["detail"]
        assert "not ready" in detail.lower()

    def test_chat_stream_returns_503(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "hi"})
        assert response.status_code == 503

    def test_chat_stream_503_detail_message(self, client: TestClient) -> None:
        response = client.post("/chat/stream", json={"question": "hi"})
        detail = response.json()["detail"]
        assert "not ready" in detail.lower()


# ---------------------------------------------------------------------------
# Timeout middleware
# ---------------------------------------------------------------------------


class TestTimeoutMiddleware:
    """Verify the timeout middleware returns 504 when a request exceeds the timeout.

    Strategy: patch ``REQUEST_TIMEOUT_SECONDS`` to a tiny value so we don't
    have to wait 120 seconds, then make the mock sleep past it.
    """

    def test_504_response_on_timeout(self, client: TestClient) -> None:
        """A slow handler returns 504 with the correct detail message."""
        import asyncio
        from unittest.mock import patch

        async def _slow_answer(question, thread_id=None, **kwargs):
            await asyncio.sleep(0.5)
            return AnswerResult(
                answer="late answer",
                queries=[],
                resolved_products=None,
                schemas_used=[],
                total_rows=0,
                total_execution_time_ms=0,
            )

        _state.atlas_sql.aanswer_question = _slow_answer

        with patch("src.api.REQUEST_TIMEOUT_SECONDS", 0.05):
            response = client.post("/chat", json={"question": "slow"})
            assert response.status_code == 504
            assert "timed out" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Concurrent requests
# ---------------------------------------------------------------------------


class TestConcurrentRequests:
    """Verify that simultaneous requests do not interfere with each other."""

    def test_two_concurrent_chat_requests(self, client: TestClient) -> None:
        """Two threads hitting /chat at the same time get independent results.

        Uses the shared ``client`` (no lifespan trigger) from threads.
        """
        import asyncio

        answer_map = {
            "thread-A": "Answer for A",
            "thread-B": "Answer for B",
        }

        async def _answer_by_thread(question, thread_id=None, **kwargs):
            await asyncio.sleep(0.05)
            return AnswerResult(
                answer=answer_map.get(thread_id, "default"),
                queries=[],
                resolved_products=None,
                schemas_used=[],
                total_rows=0,
                total_execution_time_ms=0,
            )

        _state.atlas_sql.aanswer_question = _answer_by_thread

        results: dict[str, dict] = {}

        def _post(tid: str) -> None:
            resp = client.post("/chat", json={"question": "q", "thread_id": tid})
            results[tid] = resp.json()

        t1 = threading.Thread(target=_post, args=("thread-A",))
        t2 = threading.Thread(target=_post, args=("thread-B",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert results["thread-A"]["answer"] == "Answer for A"
        assert results["thread-A"]["thread_id"] == "thread-A"
        assert results["thread-B"]["answer"] == "Answer for B"
        assert results["thread-B"]["thread_id"] == "thread-B"

    def test_concurrent_stream_requests(self, client: TestClient) -> None:
        """Two concurrent SSE requests produce independent event streams."""
        results: dict[str, str] = {}

        def _stream(tid: str) -> None:
            resp = client.post(
                "/chat/stream",
                json={"question": "q", "thread_id": tid},
            )
            events = _parse_sse(resp.text)
            first_data = json.loads(events[0]["data"])
            results[tid] = first_data["thread_id"]

        t1 = threading.Thread(target=_stream, args=("stream-A",))
        t2 = threading.Thread(target=_stream, args=("stream-B",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert results["stream-A"] == "stream-A"
        assert results["stream-B"] == "stream-B"


# ---------------------------------------------------------------------------
# Integration-level: full TestClient -> endpoint -> mock round-trip
# ---------------------------------------------------------------------------


class TestEndToEndFlow:
    """More thorough round-trip tests through the TestClient."""

    def test_chat_then_stream_same_thread(self, client: TestClient) -> None:
        """A thread_id obtained from /chat can be reused in /chat/stream."""
        r1 = client.post("/chat", json={"question": "first"})
        tid = r1.json()["thread_id"]

        r2 = client.post(
            "/chat/stream", json={"question": "follow-up", "thread_id": tid}
        )
        events = _parse_sse(r2.text)
        first_data = json.loads(events[0]["data"])
        assert first_data["thread_id"] == tid

    def test_threads_endpoint_id_works_in_chat(self, client: TestClient) -> None:
        """A thread_id from /threads can be used in /chat."""
        tid = client.post("/threads").json()["thread_id"]
        r = client.post("/chat", json={"question": "hi", "thread_id": tid})
        assert r.status_code == 200
        assert r.json()["thread_id"] == tid

    def test_stream_event_data_all_valid_json(self, client: TestClient) -> None:
        """Every single SSE event carries parseable JSON in its data field."""
        response = client.post("/chat/stream", json={"question": "check json"})
        events = _parse_sse(response.text)
        assert len(events) >= 3  # at minimum: thread_id, one chunk, done
        for i, ev in enumerate(events):
            assert "data" in ev, f"Event {i} missing data field"
            parsed = json.loads(ev["data"])
            assert isinstance(parsed, dict), f"Event {i} data is not a dict"

    def test_chat_response_content_type_json(self, client: TestClient) -> None:
        """Non-streaming /chat should return application/json."""
        response = client.post("/chat", json={"question": "hello"})
        assert "application/json" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# POST /chat/stream — trade toggle overrides
# ---------------------------------------------------------------------------


class TestChatStreamWithOverrides:
    """Tests that trade toggle overrides are forwarded and validated."""

    @pytest.fixture(autouse=True)
    def _inject_override_capturing_mock(self):
        """Mock that captures kwargs passed to aanswer_question_stream."""
        mock = MagicMock(spec=AtlasTextToSQL)
        mock.aanswer_question = AsyncMock(return_value="Mocked answer")
        self.captured_kwargs: dict = {}

        parent = self

        async def _capturing_stream(question: str, thread_id=None, **kwargs):
            parent.captured_kwargs = kwargs
            yield StreamData(
                source="agent", content="ok", message_type="agent_talk"
            )

        mock.aanswer_question_stream = _capturing_stream
        _state.atlas_sql = mock
        yield
        _state.atlas_sql = None

    def test_overrides_forwarded_to_stream(self, client: TestClient) -> None:
        """Override kwargs should reach aanswer_question_stream."""
        client.post(
            "/chat/stream",
            json={
                "question": "Brazil exports?",
                "override_schema": "hs12",
                "override_direction": "imports",
                "override_mode": "goods",
            },
        )
        assert self.captured_kwargs.get("override_schema") == "hs12"
        assert self.captured_kwargs.get("override_direction") == "imports"
        assert self.captured_kwargs.get("override_mode") == "goods"

    def test_no_overrides_sends_none(self, client: TestClient) -> None:
        """When no overrides are sent, kwargs should have None values."""
        client.post(
            "/chat/stream",
            json={"question": "Brazil exports?"},
        )
        assert self.captured_kwargs.get("override_schema") is None
        assert self.captured_kwargs.get("override_direction") is None
        assert self.captured_kwargs.get("override_mode") is None

    def test_invalid_schema_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/chat/stream",
            json={"question": "q", "override_schema": "invalid"},
        )
        assert response.status_code == 422

    def test_invalid_direction_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/chat/stream",
            json={"question": "q", "override_direction": "re-exports"},
        )
        assert response.status_code == 422

    def test_invalid_mode_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/chat/stream",
            json={"question": "q", "override_mode": "digital"},
        )
        assert response.status_code == 422


class TestChatNonStreamWithOverrides:
    """Tests that trade toggle overrides are forwarded via /chat endpoint."""

    @pytest.fixture(autouse=True)
    def _inject_override_capturing_mock(self):
        """Mock that captures kwargs passed to aanswer_question."""
        mock = MagicMock(spec=AtlasTextToSQL)
        self.captured_kwargs: dict = {}

        parent = self

        async def _capturing_answer(question: str, thread_id=None, **kwargs):
            parent.captured_kwargs = kwargs
            return AnswerResult(
                answer="Mocked answer",
                queries=[],
                resolved_products=None,
                schemas_used=[],
                total_rows=0,
                total_execution_time_ms=0,
            )

        mock.aanswer_question = _capturing_answer

        async def _fake_stream(question: str, thread_id=None, **kwargs):
            yield StreamData(
                source="agent", content="ok", message_type="agent_talk"
            )

        mock.aanswer_question_stream = _fake_stream
        _state.atlas_sql = mock
        yield
        _state.atlas_sql = None

    def test_overrides_forwarded_to_answer(self, client: TestClient) -> None:
        client.post(
            "/chat",
            json={
                "question": "Brazil exports?",
                "override_schema": "sitc",
                "override_direction": "exports",
                "override_mode": "services",
            },
        )
        assert self.captured_kwargs.get("override_schema") == "sitc"
        assert self.captured_kwargs.get("override_direction") == "exports"
        assert self.captured_kwargs.get("override_mode") == "services"


# ---------------------------------------------------------------------------
# GET /threads — list conversations
# ---------------------------------------------------------------------------


class TestListThreads:
    """Tests for listing conversations by session."""

    @pytest.fixture(autouse=True)
    def _setup_store(self):
        """Inject a fresh InMemoryConversationStore into app state."""
        _state.conversation_store = InMemoryConversationStore()
        yield
        _state.conversation_store = None

    def test_missing_session_id_returns_400(self, client: TestClient) -> None:
        response = client.get("/threads")
        assert response.status_code == 400

    def test_empty_session_returns_empty_list(self, client: TestClient) -> None:
        response = client.get("/threads", headers={"X-Session-Id": "s1"})
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_conversations_for_session(self, client: TestClient) -> None:
        import asyncio

        store = _state.conversation_store
        asyncio.get_event_loop().run_until_complete(
            store.create("t1", "s1", "First chat")
        )
        asyncio.get_event_loop().run_until_complete(
            store.create("t2", "s1", "Second chat")
        )
        response = client.get("/threads", headers={"X-Session-Id": "s1"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        ids = {c["thread_id"] for c in data}
        assert ids == {"t1", "t2"}

    def test_does_not_leak_other_sessions(self, client: TestClient) -> None:
        import asyncio

        store = _state.conversation_store
        asyncio.get_event_loop().run_until_complete(
            store.create("t1", "s1", "Mine")
        )
        asyncio.get_event_loop().run_until_complete(
            store.create("t2", "s2", "Theirs")
        )
        response = client.get("/threads", headers={"X-Session-Id": "s1"})
        data = response.json()
        assert len(data) == 1
        assert data[0]["thread_id"] == "t1"

    def test_response_shape(self, client: TestClient) -> None:
        import asyncio

        store = _state.conversation_store
        asyncio.get_event_loop().run_until_complete(
            store.create("t1", "s1", "Chat")
        )
        response = client.get("/threads", headers={"X-Session-Id": "s1"})
        item = response.json()[0]
        assert "thread_id" in item
        assert "title" in item
        assert "created_at" in item
        assert "updated_at" in item


# ---------------------------------------------------------------------------
# GET /threads/{thread_id}/messages — retrieve message history
# ---------------------------------------------------------------------------


class TestGetThreadMessages:
    """Tests for retrieving message history from LangGraph state."""

    @pytest.fixture(autouse=True)
    def _setup_store(self):
        _state.conversation_store = InMemoryConversationStore()
        yield
        _state.conversation_store = None

    def test_no_checkpoint_returns_404(self, client: TestClient) -> None:
        """A thread with no checkpoints should return 404."""
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=MagicMock(values={}))
        _state.atlas_sql.agent = mock_agent
        response = client.get("/threads/nonexistent/messages")
        assert response.status_code == 404

    def test_returns_messages(self, client: TestClient) -> None:
        """Should return human and AI messages from state."""
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        mock_state = MagicMock()
        mock_state.values = {
            "messages": [
                HumanMessage(content="Hi"),
                AIMessage(content="Hello!"),
                ToolMessage(content="sql output", tool_call_id="tc1"),
                AIMessage(content="Here are results."),
            ]
        }
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=mock_state)
        _state.atlas_sql.agent = mock_agent

        response = client.get("/threads/t1/messages")
        assert response.status_code == 200
        data = response.json()
        assert len(data["messages"]) == 3  # Human, AI, AI — ToolMessage filtered out

    def test_filters_tool_messages(self, client: TestClient) -> None:
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        mock_state = MagicMock()
        mock_state.values = {
            "messages": [
                HumanMessage(content="query"),
                ToolMessage(content="raw sql", tool_call_id="tc1"),
                AIMessage(content="result"),
            ]
        }
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=mock_state)
        _state.atlas_sql.agent = mock_agent

        response = client.get("/threads/t1/messages")
        data = response.json()
        roles = [m["role"] for m in data["messages"]]
        assert "tool" not in roles

    def test_filters_empty_ai_messages(self, client: TestClient) -> None:
        from langchain_core.messages import AIMessage, HumanMessage

        mock_state = MagicMock()
        mock_state.values = {
            "messages": [
                HumanMessage(content="Hi"),
                AIMessage(content=""),  # Empty — should be filtered
                AIMessage(content="Real response"),
            ]
        }
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=mock_state)
        _state.atlas_sql.agent = mock_agent

        response = client.get("/threads/t1/messages")
        data = response.json()
        assert len(data["messages"]) == 2  # Human + non-empty AI

    def test_message_shape(self, client: TestClient) -> None:
        from langchain_core.messages import AIMessage, HumanMessage

        mock_state = MagicMock()
        mock_state.values = {
            "messages": [
                HumanMessage(content="Hi"),
                AIMessage(content="Hello!"),
            ]
        }
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=mock_state)
        _state.atlas_sql.agent = mock_agent

        response = client.get("/threads/t1/messages")
        data = response.json()
        assert data["messages"][0]["role"] == "human"
        assert data["messages"][0]["content"] == "Hi"
        assert data["messages"][1]["role"] == "ai"
        assert data["messages"][1]["content"] == "Hello!"

    def test_response_includes_overrides(self, client: TestClient) -> None:
        """Response should include overrides alongside messages."""
        from langchain_core.messages import AIMessage, HumanMessage

        mock_state = MagicMock()
        mock_state.values = {
            "messages": [
                HumanMessage(content="Hi"),
                AIMessage(content="Hello!"),
            ],
            "override_schema": "hs12",
            "override_direction": "exports",
            "override_mode": "goods",
        }
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=mock_state)
        _state.atlas_sql.agent = mock_agent

        response = client.get("/threads/t1/messages")
        data = response.json()
        assert "overrides" in data
        assert data["overrides"]["override_schema"] == "hs12"
        assert data["overrides"]["override_direction"] == "exports"
        assert data["overrides"]["override_mode"] == "goods"

    def test_response_null_overrides_when_none_set(self, client: TestClient) -> None:
        """Overrides should be null when not present in state."""
        from langchain_core.messages import AIMessage, HumanMessage

        mock_state = MagicMock()
        mock_state.values = {
            "messages": [
                HumanMessage(content="Hi"),
                AIMessage(content="Hello!"),
            ]
        }
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=mock_state)
        _state.atlas_sql.agent = mock_agent

        response = client.get("/threads/t1/messages")
        data = response.json()
        assert data["overrides"]["override_schema"] is None
        assert data["overrides"]["override_direction"] is None
        assert data["overrides"]["override_mode"] is None

    def test_response_has_messages_overrides_and_turn_summaries_keys(self, client: TestClient) -> None:
        """Response should have 'messages', 'overrides', and 'turn_summaries' keys."""
        from langchain_core.messages import AIMessage, HumanMessage

        mock_state = MagicMock()
        mock_state.values = {
            "messages": [
                HumanMessage(content="Hi"),
                AIMessage(content="Hello!"),
            ]
        }
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=mock_state)
        _state.atlas_sql.agent = mock_agent

        response = client.get("/threads/t1/messages")
        data = response.json()
        assert set(data.keys()) == {"messages", "overrides", "turn_summaries"}

    def test_response_includes_turn_summaries_when_present(self, client: TestClient) -> None:
        """State with turn_summaries should include them in response."""
        from langchain_core.messages import AIMessage, HumanMessage

        mock_state = MagicMock()
        mock_state.values = {
            "messages": [
                HumanMessage(content="Hi"),
                AIMessage(content="Hello!"),
            ],
            "turn_summaries": [
                {
                    "entities": {
                        "schemas": ["hs92"],
                        "products": [{"name": "coffee", "codes": ["0901"], "schema": "hs92"}],
                    },
                    "queries": [
                        {
                            "sql": "SELECT * FROM t",
                            "columns": ["country", "value"],
                            "rows": [["USA", 1000]],
                            "row_count": 1,
                            "execution_time_ms": 42,
                            "tables": ["hs92.country_product_year_4"],
                            "schema_name": "hs92",
                        }
                    ],
                    "total_rows": 1,
                    "total_execution_time_ms": 42,
                }
            ],
        }
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=mock_state)
        _state.atlas_sql.agent = mock_agent

        response = client.get("/threads/t1/messages")
        data = response.json()
        assert len(data["turn_summaries"]) == 1
        ts = data["turn_summaries"][0]
        assert ts["entities"]["schemas"] == ["hs92"]
        assert len(ts["queries"]) == 1
        assert ts["queries"][0]["sql"] == "SELECT * FROM t"
        assert ts["total_rows"] == 1
        assert ts["total_execution_time_ms"] == 42

    def test_response_empty_turn_summaries_when_absent(self, client: TestClient) -> None:
        """Old checkpoints (no turn_summaries key) should return empty list."""
        from langchain_core.messages import AIMessage, HumanMessage

        mock_state = MagicMock()
        mock_state.values = {
            "messages": [
                HumanMessage(content="Hi"),
                AIMessage(content="Hello!"),
            ]
        }
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=mock_state)
        _state.atlas_sql.agent = mock_agent

        response = client.get("/threads/t1/messages")
        data = response.json()
        assert data["turn_summaries"] == []


# ---------------------------------------------------------------------------
# DELETE /threads/{thread_id} — delete conversation
# ---------------------------------------------------------------------------


class TestDeleteThread:
    """Tests for deleting a conversation and its checkpoints."""

    @pytest.fixture(autouse=True)
    def _setup_store(self):
        _state.conversation_store = InMemoryConversationStore()
        yield
        _state.conversation_store = None

    def test_delete_returns_204(self, client: TestClient) -> None:
        response = client.delete("/threads/any-id")
        assert response.status_code == 204

    def test_delete_nonexistent_returns_204(self, client: TestClient) -> None:
        """Idempotent — deleting nonexistent thread is fine."""
        response = client.delete("/threads/no-such-thread")
        assert response.status_code == 204

    def test_delete_removes_from_store(self, client: TestClient) -> None:
        import asyncio

        store = _state.conversation_store
        asyncio.get_event_loop().run_until_complete(
            store.create("t1", "s1", "Doomed")
        )
        response = client.delete("/threads/t1")
        assert response.status_code == 204
        row = asyncio.get_event_loop().run_until_complete(store.get("t1"))
        assert row is None


# ---------------------------------------------------------------------------
# Lazy conversation creation in chat endpoints
# ---------------------------------------------------------------------------


class TestLazyConversationCreation:
    """Tests that /chat and /chat/stream create conversations lazily."""

    @pytest.fixture(autouse=True)
    def _setup_store(self):
        _state.conversation_store = InMemoryConversationStore()
        yield
        _state.conversation_store = None

    def test_chat_creates_conversation_with_session_header(
        self, client: TestClient
    ) -> None:
        import asyncio

        response = client.post(
            "/chat",
            json={"question": "Top exports of Brazil?"},
            headers={"X-Session-Id": "session-1"},
        )
        assert response.status_code == 200
        thread_id = response.json()["thread_id"]

        store = _state.conversation_store
        row = asyncio.get_event_loop().run_until_complete(store.get(thread_id))
        assert row is not None
        assert row.session_id == "session-1"

    def test_chat_no_session_header_skips_creation(
        self, client: TestClient
    ) -> None:
        """Without X-Session-Id, no conversation row is created."""
        import asyncio

        response = client.post("/chat", json={"question": "Hello"})
        assert response.status_code == 200
        thread_id = response.json()["thread_id"]

        store = _state.conversation_store
        row = asyncio.get_event_loop().run_until_complete(store.get(thread_id))
        assert row is None

    def test_stream_creates_conversation_with_session_header(
        self, client: TestClient
    ) -> None:
        import asyncio

        response = client.post(
            "/chat/stream",
            json={"question": "Exports data?"},
            headers={"X-Session-Id": "session-2"},
        )
        assert response.status_code == 200
        events = _parse_sse(response.text)
        thread_id = json.loads(events[0]["data"])["thread_id"]

        store = _state.conversation_store
        row = asyncio.get_event_loop().run_until_complete(store.get(thread_id))
        assert row is not None
        assert row.session_id == "session-2"

    def test_chat_derives_title_from_question(
        self, client: TestClient
    ) -> None:
        import asyncio

        response = client.post(
            "/chat",
            json={"question": "What are the main exports of Germany?"},
            headers={"X-Session-Id": "s1"},
        )
        thread_id = response.json()["thread_id"]

        store = _state.conversation_store
        row = asyncio.get_event_loop().run_until_complete(store.get(thread_id))
        assert row is not None
        assert "Germany" in row.title

    def test_second_message_updates_timestamp(
        self, client: TestClient
    ) -> None:
        import asyncio

        # First message creates the conversation
        r1 = client.post(
            "/chat",
            json={"question": "First question"},
            headers={"X-Session-Id": "s1"},
        )
        thread_id = r1.json()["thread_id"]

        store = _state.conversation_store
        row1 = asyncio.get_event_loop().run_until_complete(store.get(thread_id))
        old_updated = row1.updated_at

        # Second message with same thread_id updates the timestamp
        client.post(
            "/chat",
            json={"question": "Follow up", "thread_id": thread_id},
            headers={"X-Session-Id": "s1"},
        )
        row2 = asyncio.get_event_loop().run_until_complete(store.get(thread_id))
        assert row2.updated_at >= old_updated
