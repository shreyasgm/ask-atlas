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

    def test_classify_query_surfaces_query_type_and_rejection(self):
        """classify_query node: result has query_type, is_rejected, rejection_reason."""
        result = _extract_pipeline_state(
            "classify_query",
            {
                "graphql_classification": {
                    "query_type": "reject",
                    "rejection_reason": "Requires SQL aggregation",
                }
            },
        )
        assert result["stage"] == "classify_query"
        assert result["query_type"] == "reject"
        assert result["is_rejected"] is True
        assert result["rejection_reason"] == "Requires SQL aggregation"

    def test_classify_query_non_rejected(self):
        """classify_query node: non-reject query types have is_rejected=False."""
        result = _extract_pipeline_state(
            "classify_query",
            {
                "graphql_classification": {
                    "query_type": "country_profile",
                    "rejection_reason": "",
                }
            },
        )
        assert result["is_rejected"] is False
        assert result["query_type"] == "country_profile"

    def test_classify_query_handles_none_classification(self):
        """classify_query node: gracefully handles None classification."""
        result = _extract_pipeline_state(
            "classify_query",
            {"graphql_classification": None},
        )
        assert result["stage"] == "classify_query"
        assert "query_type" in result

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

    def test_extract_entities_surfaces_entities(self):
        """extract_entities node: result has entities key."""
        result = _extract_pipeline_state(
            "extract_entities",
            {"graphql_entity_extraction": {"country": "Kenya", "year": 2020}},
        )
        assert result["stage"] == "extract_entities"
        assert "entities" in result

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
        assert (
            format_events
        ), "Expected at least one format_graphql_results pipeline_state event"
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

    assert (
        event_types[0] == "thread_id"
    ), f"First event must be thread_id, got: {event_types[:3]}"
    assert (
        event_types[-1] == "done"
    ), f"Last event must be done, got: {event_types[-3:]}"
    assert "node_start" in event_types, "Expected at least one node_start event"
    assert "pipeline_state" in event_types, "Expected at least one pipeline_state event"

    done_event = next(e for e in events if e.get("event") == "done")
    done_data = json.loads(done_event["data"])
    assert (
        done_data.get("total_time_ms", 0) > 0
    ), "done event must include total_time_ms"
