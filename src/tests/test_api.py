"""Tests for the FastAPI application — no DB, no LLM.

All tests run against the API contract using TestClient + mocks.
No external services required, so no pytest markers needed.
"""

import json
import threading
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api import _state, app
from src.text_to_sql import AtlasTextToSQL, StreamData


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
    mock.aanswer_question = AsyncMock(return_value="Mocked answer")

    async def _fake_stream(question: str, thread_id: str | None = None):
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
        """Response body must have exactly 'answer' and 'thread_id' keys."""
        data = client.post("/chat", json={"question": "hello"}).json()
        assert set(data.keys()) == {"answer", "thread_id"}


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
        mock.aanswer_question = AsyncMock(return_value="unused")

        async def _mixed_stream(question: str, thread_id: str | None = None):
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

        async def _slow_answer(question, thread_id=None):
            await asyncio.sleep(0.5)
            return "late answer"

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

        async def _answer_by_thread(question, thread_id=None):
            await asyncio.sleep(0.05)
            return answer_map.get(thread_id, "default")

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
