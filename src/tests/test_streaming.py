"""Tests for src/streaming.py — GraphQL pipeline state extraction, atlas_links,
and streaming event ordering.

These tests were written before streaming.py existed (TDD). All unit tests
import from ``src.streaming``; the integration test hits the live ASGI app.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.state import AtlasAgentState
from src.streaming import (
    AtlasTextToSQL,
    _build_turn_summary,
    _extract_pipeline_state,
)
from src.tests.fake_model import FakeToolCallingModel

# ---------------------------------------------------------------------------
# Helpers (shared with test_text_to_sql_async patterns)
# ---------------------------------------------------------------------------


def _tool_call(name: str, question: str, call_id: str) -> dict:
    return {
        "name": name,
        "args": {"question": question},
        "id": call_id,
        "type": "tool_call",
    }


def _build_graphql_stub_instance(
    responses: list[AIMessage],
    graphql_atlas_links: list[dict] | None = None,
) -> AtlasTextToSQL:
    """Build a stub AtlasTextToSQL that uses atlas_graphql tool + GraphQL pipeline nodes."""
    from langchain_core.tools import tool
    from pydantic import BaseModel, Field

    class AtlasGraphQLInput(BaseModel):
        question: str = Field(description="A question about trade data")
        context: str = Field(default="")

    @tool("atlas_graphql", args_schema=AtlasGraphQLInput)
    def dummy_graphql_tool(question: str, context: str = "") -> str:
        """Queries the Atlas GraphQL API."""
        return "stub graphql result"

    model = FakeToolCallingModel(responses=responses)

    async def agent_node(state: AtlasAgentState) -> dict:
        model_with_tools = model.bind_tools([dummy_graphql_tool])
        return {"messages": [await model_with_tools.ainvoke(state["messages"])]}

    async def format_graphql_results(state: AtlasAgentState) -> dict:
        """Stub GraphQL pipeline terminal node."""
        last_msg = state["messages"][-1]
        tool_calls = last_msg.tool_calls
        tc = tool_calls[0]
        links = graphql_atlas_links or []
        messages = [
            ToolMessage(
                content=f"GraphQL results for: {tc['args']['question']}",
                tool_call_id=tc["id"],
            )
        ]
        return {
            "messages": messages,
            "queries_executed": state.get("queries_executed", 0) + 1,
            "graphql_atlas_links": links,
        }

    def route_after_agent(state: AtlasAgentState) -> str:
        last_msg = state["messages"][-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return "format_graphql_results"
        return END

    builder = StateGraph(AtlasAgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("format_graphql_results", format_graphql_results)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent)
    builder.add_edge("format_graphql_results", "agent")

    instance = AtlasTextToSQL.__new__(AtlasTextToSQL)
    instance.agent = builder.compile(checkpointer=MemorySaver())
    return instance


# ---------------------------------------------------------------------------
# Test Group 1: GraphQL pipeline state extraction
# ---------------------------------------------------------------------------


class TestGraphQLPipelineStateExtraction:
    """_extract_pipeline_state() must handle GraphQL nodes."""

    def test_plan_query_surfaces_query_type_and_rejection(self):
        """plan_query node: result has query_type, is_rejected, rejection_reason."""
        result = _extract_pipeline_state(
            "plan_query",
            {
                "graphql_classification": {
                    "query_type": "reject",
                    "rejection_reason": "Requires SQL aggregation",
                }
            },
        )
        assert result["stage"] == "plan_query"
        assert result["query_type"] == "reject"
        assert result["is_rejected"] is True
        assert result["rejection_reason"] == "Requires SQL aggregation"

    def test_plan_query_non_rejected(self):
        """plan_query node: non-reject query types have is_rejected=False."""
        result = _extract_pipeline_state(
            "plan_query",
            {
                "graphql_classification": {
                    "query_type": "country_profile",
                    "rejection_reason": "",
                }
            },
        )
        assert result["is_rejected"] is False
        assert result["query_type"] == "country_profile"

    def test_plan_query_includes_entities(self):
        """plan_query node: result also includes extracted entities."""
        result = _extract_pipeline_state(
            "plan_query",
            {
                "graphql_classification": {"query_type": "treemap_products"},
                "graphql_entity_extraction": {"country": "Kenya", "year": 2020},
            },
        )
        assert result["stage"] == "plan_query"
        assert result["entities"] == {"country": "Kenya", "year": 2020}

    def test_format_graphql_results_includes_atlas_links(self):
        """format_graphql_results node: result has atlas_links list."""
        atlas_link = {
            "url": "https://atlas.hks.harvard.edu/countries/404",
            "label": "Kenya — Country Profile",
            "link_type": "country_page",
            "resolution_notes": [],
        }
        result = _extract_pipeline_state(
            "format_graphql_results",
            {
                "graphql_atlas_links": [atlas_link],
                "_query_index": 0,
            },
        )
        assert result["stage"] == "format_graphql_results"
        assert "atlas_links" in result
        assert len(result["atlas_links"]) == 1
        assert result["atlas_links"][0]["url"].startswith("https://")
        assert result["atlas_links"][0]["link_type"] in (
            "country_page",
            "explore_page",
            "product_page",
        )

    def test_format_graphql_results_empty_links(self):
        """format_graphql_results node: works when no links present."""
        result = _extract_pipeline_state(
            "format_graphql_results",
            {"graphql_atlas_links": [], "_query_index": 2},
        )
        assert result["atlas_links"] == []
        assert result["query_index"] == 2

    def test_resolve_ids_surfaces_resolved_params(self):
        """resolve_ids node: result has resolved_ids key even when None."""
        result = _extract_pipeline_state(
            "resolve_ids",
            {"graphql_resolved_params": None},
        )
        assert result["stage"] == "resolve_ids"
        assert "resolved_ids" in result

    def test_extract_graphql_question_surfaces_question(self):
        """extract_graphql_question node: result has question key."""
        result = _extract_pipeline_state(
            "extract_graphql_question",
            {"graphql_question": "What is Kenya's ECI?"},
        )
        assert result["stage"] == "extract_graphql_question"
        assert result["question"] == "What is Kenya's ECI?"

    def test_build_and_execute_graphql_surfaces_execution_info(self):
        """build_and_execute_graphql node: result has success, api_target, execution_time_ms."""
        result = _extract_pipeline_state(
            "build_and_execute_graphql",
            {
                "graphql_execution_time_ms": 350,
                "graphql_api_target": "country_pages",
                "graphql_raw_response": {"data": {"country": {}}},
            },
        )
        assert result["stage"] == "build_and_execute_graphql"
        assert "execution_time_ms" in result
        assert "api_target" in result
        assert "success" in result

    def test_retrieve_docs_surfaces_chunk_count_and_titles(self):
        """retrieve_docs node: counts doc_chunk tags and reads titles from state."""
        synthesis = (
            '<doc_chunk source="eci.md" section="Overview">a</doc_chunk>'
            '<doc_chunk source="pci.md" section="Intro">b</doc_chunk>'
            '<doc_chunk source="eci.md" section="Formula">c</doc_chunk>'
        )
        result = _extract_pipeline_state(
            "retrieve_docs",
            {
                "docs_synthesis": synthesis,
                "docs_retrieved_titles": ["ECI Methodology", "PCI Methodology"],
            },
        )
        assert result["stage"] == "retrieve_docs"
        assert result["chunk_count"] == 3
        assert result["doc_titles"] == ["ECI Methodology", "PCI Methodology"]

    def test_retrieve_docs_no_synthesis(self):
        """retrieve_docs node: chunk_count is 0 when no synthesis."""
        result = _extract_pipeline_state(
            "retrieve_docs",
            {"docs_synthesis": ""},
        )
        assert result["chunk_count"] == 0

    def test_retrieve_docs_context_surfaces_chunk_count_and_titles(self):
        """retrieve_docs_context node: counts auto chunks and extracts titles."""
        result = _extract_pipeline_state(
            "retrieve_docs_context",
            {
                "docs_auto_chunks": [
                    {"chunk_id": "c1", "doc_title": "ECI Methodology", "body": "..."},
                    {"chunk_id": "c2", "doc_title": "ECI Methodology", "body": "..."},
                    {"chunk_id": "c3", "doc_title": "PCI Overview", "body": "..."},
                ]
            },
        )
        assert result["stage"] == "retrieve_docs_context"
        assert result["chunk_count"] == 3
        assert result["doc_titles"] == ["ECI Methodology", "PCI Overview"]

    def test_retrieve_docs_context_no_chunks(self):
        """retrieve_docs_context node: chunk_count is 0 when empty."""
        result = _extract_pipeline_state(
            "retrieve_docs_context",
            {"docs_auto_chunks": []},
        )
        assert result["chunk_count"] == 0
        assert result["doc_titles"] == []


# ---------------------------------------------------------------------------
# Test Group 2: _build_turn_summary with atlas_links
# ---------------------------------------------------------------------------


class TestAtlasLinksTurnSummary:
    """_build_turn_summary() must accept and include atlas_links."""

    def test_build_turn_summary_includes_atlas_links(self):
        """When atlas_links is passed, turn summary should include them."""
        links = [
            {
                "url": "https://atlas.hks.harvard.edu/explore",
                "label": "Explore Kenya",
                "link_type": "explore_page",
                "resolution_notes": [],
            }
        ]
        result = _build_turn_summary(
            queries=[], resolved_products=None, atlas_links=links
        )
        assert "atlas_links" in result
        assert len(result["atlas_links"]) == 1

    def test_build_turn_summary_no_atlas_links_by_default(self):
        """Without atlas_links, key should be absent (not an empty list)."""
        result = _build_turn_summary(queries=[], resolved_products=None)
        assert "atlas_links" not in result

    def test_build_turn_summary_empty_links_not_included(self):
        """Passing an empty list should not add atlas_links to summary."""
        result = _build_turn_summary(queries=[], resolved_products=None, atlas_links=[])
        assert "atlas_links" not in result


# ---------------------------------------------------------------------------
# Test Group 3: GraphQL streaming events
# ---------------------------------------------------------------------------


class TestGraphQLStreamingEvents:
    """astream_agent_response() must emit atlas_links via pipeline_state events."""

    async def test_graphql_pipeline_emits_pipeline_state_with_atlas_links(self):
        """format_graphql_results pipeline_state event must carry atlas_links."""
        atlas_link = {
            "url": "https://atlas.hks.harvard.edu/countries/404",
            "label": "Kenya",
            "link_type": "country_page",
            "resolution_notes": [],
        }
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("atlas_graphql", "Kenya profile", "gql1")],
            ),
            AIMessage(content="Kenya's ECI is 0.5."),
        ]
        instance = _build_graphql_stub_instance(
            responses, graphql_atlas_links=[atlas_link]
        )

        import uuid

        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        pipeline_state_events: list[dict] = []
        async for _mode, stream_data in instance.astream_agent_response(
            "What is Kenya's ECI?", config
        ):
            if stream_data.message_type == "pipeline_state" and stream_data.payload:
                pipeline_state_events.append(stream_data.payload)

        format_events = [
            e
            for e in pipeline_state_events
            if e.get("stage") == "format_graphql_results"
        ]
        assert format_events, (
            "Expected at least one format_graphql_results pipeline_state event"
        )
        assert format_events[0].get("atlas_links"), "atlas_links should be non-empty"


# ---------------------------------------------------------------------------
# Test Group 4: Integration — SSE endpoint event ordering
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_api_chat_stream_emits_ordered_pipeline_events():
    """Full SSE streaming path: thread_id first, done last, pipeline events in between.

    Injects a real AtlasTextToSQL instance directly into app state (bypasses
    lifespan, which is not triggered by ASGITransport) and exercises the full
    streaming path against the real LLM + DB.
    """
    import httpx
    from httpx import ASGITransport

    from src.api import _state, app
    from src.streaming import AtlasTextToSQL

    def _parse_sse(raw: str) -> list[dict]:
        events = []
        current: dict = {}
        for line in raw.splitlines():
            if line.startswith("event:"):
                current["event"] = line[len("event:") :].strip()
            elif line.startswith("data:"):
                current["data"] = line[len("data:") :].strip()
            elif line == "" and current:
                events.append(current)
                current = {}
        if current:
            events.append(current)
        return events

    # Bootstrap a real instance without going through the lifespan
    atlas_sql = await AtlasTextToSQL.create_async()
    _state.atlas_sql = atlas_sql
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/chat/stream",
                json={"question": "What were US exports in 2020?"},
                timeout=90.0,
            )
    finally:
        _state.atlas_sql = None
        await atlas_sql.aclose()

    assert response.status_code == 200

    events = _parse_sse(response.text)
    event_types = [e.get("event") for e in events]

    assert event_types[0] == "thread_id", (
        f"First event must be thread_id, got: {event_types[:3]}"
    )
    assert event_types[-1] == "done", (
        f"Last event must be done, got: {event_types[-3:]}"
    )
    assert "node_start" in event_types, "Expected at least one node_start event"
    assert "pipeline_state" in event_types, "Expected at least one pipeline_state event"

    done_event = next(e for e in events if e.get("event") == "done")
    done_data = json.loads(done_event["data"])
    assert done_data.get("total_time_ms", 0) > 0, (
        "done event must include total_time_ms"
    )


# ---------------------------------------------------------------------------
# Test Group 5: Recursion limit configuration
# ---------------------------------------------------------------------------


class TestRecursionLimit:
    """recursion_limit must be set in the config passed to agent.astream()."""

    async def test_aanswer_question_sets_recursion_limit(self):
        """aanswer_question() sets recursion_limit=150 in the config."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("atlas_graphql", "test", "rc1")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_graphql_stub_instance(responses)

        # Spy on agent.astream to capture the config argument
        import unittest.mock as mock

        original_astream = instance.agent.astream
        captured_configs: list[dict] = []

        async def spy_astream(*args, **kwargs):
            config = kwargs.get("config") or (args[2] if len(args) > 2 else None)
            if config:
                captured_configs.append(config)
            async for item in original_astream(*args, **kwargs):
                yield item

        with mock.patch.object(instance.agent, "astream", side_effect=spy_astream):
            await instance.aanswer_question("test question")

        assert captured_configs, "astream should have been called"
        assert captured_configs[0].get("recursion_limit") == 150

    async def test_astream_agent_response_sets_recursion_limit(self):
        """astream_agent_response() sets recursion_limit=150 in the config."""
        import uuid

        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("atlas_graphql", "test", "rc2")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_graphql_stub_instance(responses)

        import unittest.mock as mock

        original_astream = instance.agent.astream
        captured_configs: list[dict] = []

        async def spy_astream(*args, **kwargs):
            config = kwargs.get("config") or (args[2] if len(args) > 2 else None)
            if config:
                captured_configs.append(config)
            async for item in original_astream(*args, **kwargs):
                yield item

        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        with mock.patch.object(instance.agent, "astream", side_effect=spy_astream):
            async for _ in instance.astream_agent_response("test", config):
                pass

        assert captured_configs, "astream should have been called"
        assert captured_configs[0].get("recursion_limit") == 150

    async def test_recursion_limit_does_not_override_explicit(self):
        """If recursion_limit is already set, setdefault() preserves it."""
        import uuid

        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("atlas_graphql", "test", "rc3")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_graphql_stub_instance(responses)

        import unittest.mock as mock

        original_astream = instance.agent.astream
        captured_configs: list[dict] = []

        async def spy_astream(*args, **kwargs):
            config = kwargs.get("config") or (args[2] if len(args) > 2 else None)
            if config:
                captured_configs.append(config)
            async for item in original_astream(*args, **kwargs):
                yield item

        config = {
            "configurable": {"thread_id": str(uuid.uuid4())},
            "recursion_limit": 50,
        }
        with mock.patch.object(instance.agent, "astream", side_effect=spy_astream):
            async for _ in instance.astream_agent_response("test", config):
                pass

        assert captured_configs[0].get("recursion_limit") == 50


# ---------------------------------------------------------------------------
# Test Group 6: GraphRecursionError handling in SSE endpoint
# ---------------------------------------------------------------------------


class TestGraphRecursionErrorSSE:
    """When GraphRecursionError is raised, the SSE endpoint must emit an error event."""

    async def test_graph_recursion_error_yields_error_event(self):
        """GraphRecursionError should produce an error SSE event, not a crash."""
        from unittest.mock import AsyncMock

        import httpx
        from httpx import ASGITransport
        from langgraph.errors import GraphRecursionError

        from src.api import _state, app
        from src.conversations import InMemoryConversationStore

        def _parse_sse(raw: str) -> list[dict]:
            events = []
            current: dict = {}
            for line in raw.splitlines():
                if line.startswith("event:"):
                    current["event"] = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    current["data"] = line[len("data:") :].strip()
                elif line == "" and current:
                    events.append(current)
                    current = {}
            if current:
                events.append(current)
            return events

        # Build a stub AtlasTextToSQL whose aanswer_question_stream raises
        from src.streaming import StreamData

        async def _failing_stream(*args, **kwargs):
            # Yield a real StreamData so the loop body can process it
            yield StreamData(
                source="agent",
                content="partial",
                message_type="agent_talk",
            )
            raise GraphRecursionError("Recursion limit of 25 reached")

        stub = AtlasTextToSQL.__new__(AtlasTextToSQL)
        stub.aanswer_question_stream = _failing_stream
        # Provide a stub agent with aget_state/aupdate_state for post-stream cleanup
        mock_state = AsyncMock()
        mock_state.values = {"messages": [], "token_usage": [], "step_timing": []}
        stub.agent = AsyncMock()
        stub.agent.aget_state = AsyncMock(return_value=mock_state)
        stub.agent.aupdate_state = AsyncMock()

        _state.atlas_sql = stub
        _state.conversation_store = InMemoryConversationStore()
        try:
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/chat/stream",
                    json={"question": "complex multi-tool question"},
                    headers={"X-Session-Id": "test-session"},
                    timeout=10.0,
                )
        finally:
            _state.atlas_sql = None
            _state.conversation_store = None

        assert response.status_code == 200

        events = _parse_sse(response.text)
        event_types = [e.get("event") for e in events]

        assert "thread_id" in event_types, "Should still emit thread_id"
        assert "error" in event_types, "Should emit an error event"

        error_event = next(e for e in events if e.get("event") == "error")
        error_data = json.loads(error_event["data"])
        assert "message" in error_data
        assert "too many" in error_data["message"].lower()
