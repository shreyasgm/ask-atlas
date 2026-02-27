"""End-to-end tests for the docs pipeline with full observability.

Verifies that:
1. The agent routes methodology questions to docs_tool (not SQL/GraphQL).
2. All three docs pipeline nodes fire in order.
3. Streaming events (node_start, pipeline_state) carry correct data.
4. The final answer references documentation content.

Requires: LLM API keys configured in .env.
Does NOT require a database — uses MemorySaver checkpointer.

Run::

    PYTHONPATH=$(pwd) uv run pytest src/tests/test_docs_e2e.py -m integration -v
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from src.streaming import DOCS_PIPELINE_SEQUENCE, StreamData
from src.text_to_sql import AtlasTextToSQL

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def atlas_agent():
    """Create a shared AtlasTextToSQL instance (no DB needed for docs tests)."""
    from src.config import get_settings

    settings = get_settings()
    if (
        not settings.openai_api_key
        and not settings.anthropic_api_key
        and not settings.google_api_key
    ):
        pytest.skip("No LLM API keys configured — skipping integration tests")

    # No db_uri → MemorySaver, no SQL execution needed for docs-only questions
    async with await AtlasTextToSQL.create_async() as agent:
        yield agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect_stream_events(
    atlas_agent: AtlasTextToSQL, question: str
) -> tuple[list[StreamData], str, str]:
    """Stream a question through the agent, collecting all events.

    Returns:
        (all_events, final_answer, tool_output_text) tuple.
        agent_talk and tool_output are streamed token-by-token, so we
        accumulate them here.
    """
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    events: list[StreamData] = []
    agent_talk_parts: list[str] = []
    tool_output_parts: list[str] = []

    async for _mode, stream_data in atlas_agent.astream_agent_response(
        question, config
    ):
        events.append(stream_data)
        if stream_data.message_type == "agent_talk" and stream_data.content:
            agent_talk_parts.append(stream_data.content)
        elif stream_data.message_type == "tool_output" and stream_data.content:
            tool_output_parts.append(stream_data.content)

    return events, "".join(agent_talk_parts), "".join(tool_output_parts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDocsPipelineE2E:
    """Full agent → docs pipeline → streaming events tests."""

    async def test_eci_methodology_routes_to_docs_pipeline(self, atlas_agent):
        """A methodology question should trigger docs_tool, not query_tool."""
        events, _answer, _tool_out = await _collect_stream_events(
            atlas_agent,
            "What is the Economic Complexity Index (ECI)? How is it calculated?",
        )

        # --- Verify docs_tool was called ---
        tool_calls = [e for e in events if e.message_type == "tool_call"]
        docs_tool_calls = [e for e in tool_calls if e.tool_call == "docs_tool"]
        assert docs_tool_calls, (
            f"Expected docs_tool to be called, but got tool_calls: "
            f"{[e.tool_call for e in tool_calls]}"
        )

        # --- Verify no SQL/GraphQL tools were called ---
        sql_or_gql_calls = [
            e for e in tool_calls if e.tool_call in ("query_tool", "atlas_graphql")
        ]
        assert not sql_or_gql_calls, (
            f"Expected only docs_tool, but also got: "
            f"{[e.tool_call for e in sql_or_gql_calls]}"
        )

    async def test_docs_pipeline_nodes_fire_in_order(self, atlas_agent):
        """All three docs pipeline nodes should fire in the correct sequence."""
        events, _, _ = await _collect_stream_events(
            atlas_agent,
            "What is Revealed Comparative Advantage (RCA)? How is it defined?",
        )

        # Extract node_start events for docs pipeline nodes
        node_starts = [
            e.payload["node"]
            for e in events
            if e.message_type == "node_start"
            and e.payload
            and e.payload.get("node") in set(DOCS_PIPELINE_SEQUENCE)
        ]

        assert node_starts == list(DOCS_PIPELINE_SEQUENCE), (
            f"Expected docs pipeline sequence {DOCS_PIPELINE_SEQUENCE}, "
            f"got node_starts: {node_starts}"
        )

    async def test_pipeline_state_events_carry_correct_data(self, atlas_agent):
        """pipeline_state events should contain structured data for each docs node."""
        events, _, _ = await _collect_stream_events(
            atlas_agent,
            "What is the Product Complexity Index (PCI)?",
        )

        # Collect pipeline_state events for docs nodes
        pipeline_states = {
            e.payload["stage"]: e.payload
            for e in events
            if e.message_type == "pipeline_state"
            and e.payload
            and e.payload.get("stage") in set(DOCS_PIPELINE_SEQUENCE)
        }

        # extract_docs_question should report the question
        assert "extract_docs_question" in pipeline_states, (
            f"Missing pipeline_state for extract_docs_question. "
            f"Got stages: {list(pipeline_states.keys())}"
        )
        extract_state = pipeline_states["extract_docs_question"]
        assert (
            "pci" in extract_state.get("question", "").lower()
            or "product complexity" in extract_state.get("question", "").lower()
        ), f"Expected PCI-related question, got: {extract_state.get('question')}"

        # select_and_synthesize should report selected files
        assert "select_and_synthesize" in pipeline_states
        select_state = pipeline_states["select_and_synthesize"]
        assert select_state.get("selected_files"), "No docs were selected"
        assert select_state.get("has_synthesis") is True, "Synthesis not produced"

        # At least metrics_glossary.md should be among selected files
        selected = select_state["selected_files"]
        assert (
            "metrics_glossary.md" in selected
        ), f"Expected metrics_glossary.md in selected files, got: {selected}"

    async def test_final_answer_references_documentation(self, atlas_agent):
        """The agent's final answer should contain ECI methodology details from docs."""
        _events, answer, _tool_out = await _collect_stream_events(
            atlas_agent,
            "What is ECI and how is it calculated?",
        )

        assert answer, "Agent returned an empty answer"
        answer_lower = answer.lower()
        assert (
            "eci" in answer_lower or "economic complexity" in answer_lower
        ), f"Answer does not mention ECI: {answer[:300]}"

    async def test_tool_output_contains_synthesis(self, atlas_agent):
        """The tool_output event for docs_tool should carry the synthesized content."""
        _events, _answer, tool_output = await _collect_stream_events(
            atlas_agent,
            "How is the Complexity Outlook Index calculated?",
        )

        assert tool_output, "No tool_output content found"
        # The tool output should have substantive content (not just "No docs found")
        assert (
            len(tool_output) > 50
        ), f"Tool output seems too short to be a real synthesis: {tool_output[:200]}"
