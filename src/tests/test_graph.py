"""Unit tests for src/graph.py — graph wiring and routing.

Uses FakeToolCallingModel and build_atlas_graph with mocked GraphQL dependencies.
All tests are unit tests — no database or external LLM required.
"""

import pytest

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from src.config import AgentMode
from src.graph import build_atlas_graph
from src.graphql_client import GraphQLBudgetTracker
from src.tests.fake_model import FakeToolCallingModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_call(name: str, question: str, call_id: str) -> dict:
    return {
        "name": name,
        "args": {"question": question},
        "id": call_id,
        "type": "tool_call",
    }


def _make_mock_db():
    mock_db = MagicMock()
    mock_db.get_table_info.return_value = "-- table info --"
    return mock_db


def _make_mock_engine():
    return MagicMock()


def _build_graph(
    fake_model: FakeToolCallingModel,
    *,
    agent_mode: AgentMode = AgentMode.SQL_ONLY,
    max_uses: int = 3,
    budget_tracker: GraphQLBudgetTracker | None = None,
) -> object:
    """Build a real build_atlas_graph with mocked SQL/GraphQL dependencies."""
    mock_db = _make_mock_db()
    mock_engine = _make_mock_engine()

    with (patch("src.sql_pipeline.ProductAndSchemaLookup") as mock_lookup_cls,):
        mock_lookup = MagicMock()
        mock_lookup_cls.return_value = mock_lookup

        graph = build_atlas_graph(
            llm=fake_model,
            lightweight_llm=fake_model,
            db=mock_db,
            engine=mock_engine,
            table_descriptions={},
            example_queries=[],
            top_k_per_query=15,
            max_uses=max_uses,
            checkpointer=MemorySaver(),
            agent_mode=agent_mode,
            budget_tracker=budget_tracker,
        )
    return graph


# ---------------------------------------------------------------------------
# Tests: unknown tool name routes to END
# ---------------------------------------------------------------------------


class TestUnknownToolRouting:
    async def test_unknown_tool_name_routes_to_end(self):
        """When the agent emits a tool call with an unrecognised name,
        the graph terminates (routes to END) with no ToolMessage."""
        model = FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("nonexistent_tool", "some question", "call-x")
                    ],
                ),
            ]
        )
        graph = _build_graph(model)
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="test")]},
            config={"configurable": {"thread_id": "unknown-tool-test"}},
        )
        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert tool_msgs == []


# ---------------------------------------------------------------------------
# Tests: RetryPolicy configured on GraphQL LLM nodes
# ---------------------------------------------------------------------------


class TestRetryPolicyConfiguration:
    """Verify LangGraph RetryPolicy is attached to GraphQL nodes that make LLM calls."""

    def _build_real_graph(self):
        """Build graph so RetryPolicy config is preserved."""
        mock_db = _make_mock_db()
        mock_engine = _make_mock_engine()
        fake_model = FakeToolCallingModel(responses=[AIMessage(content="done")])

        with patch("src.sql_pipeline.ProductAndSchemaLookup"):
            return build_atlas_graph(
                llm=fake_model,
                lightweight_llm=fake_model,
                db=mock_db,
                engine=mock_engine,
                table_descriptions={},
                example_queries=[],
                top_k_per_query=15,
                max_uses=3,
                checkpointer=MemorySaver(),
                agent_mode=AgentMode.SQL_ONLY,
            )

    def test_plan_query_has_retry_policy(self):
        """plan_query makes an LLM call and should have RetryPolicy."""
        graph = self._build_real_graph()
        node = graph.nodes["plan_query"]
        assert node.retry_policy is not None, "plan_query should have RetryPolicy"
        policy = node.retry_policy[0]
        assert policy.max_attempts == 3

    def test_resolve_ids_has_retry_policy(self):
        """resolve_ids contains an LLM disambiguation call and should have RetryPolicy."""
        graph = self._build_real_graph()
        node = graph.nodes["resolve_ids"]
        assert node.retry_policy is not None, "resolve_ids should have RetryPolicy"

    def test_build_and_execute_has_no_retry_policy(self):
        """build_and_execute_graphql handles retries internally via the GraphQL client."""
        graph = self._build_real_graph()
        node = graph.nodes["build_and_execute_graphql"]
        assert node.retry_policy is None, (
            "build_and_execute_graphql should NOT have RetryPolicy "
            "(GraphQL client handles HTTP retries)"
        )


# ---------------------------------------------------------------------------
# Tests: SQL-only mode regression (integration)
# ---------------------------------------------------------------------------

_GRAPHQL_STAGES = frozenset(
    {
        "plan_query",
        "resolve_ids",
        "build_and_execute_graphql",
        "format_graphql_results",
    }
)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSQLOnlyModeRegression:
    """Regression gate: SQL-only mode must never touch the GraphQL pipeline.

    Requires a live Atlas DB and LLM API keys.  Run with::

        PYTHONPATH=$(pwd) uv run pytest \\
            src/tests/test_graph.py::TestSQLOnlyModeRegression -v -m integration
    """

    async def test_sql_only_never_calls_graphql_tool(self):
        """End-to-end SQL-only invariant: only query_tool called, only SQL nodes traversed.

        Failure modes caught by each assertion:
        1. query_tool called at least once
           → agent produced no tool calls at all (routing failure / empty response)
        2. atlas_graphql NEVER called
           → mode override ignored; agent had both tools and routed to GraphQL
        3. sql_query_agent stage present in pipeline_states
           → SQL pipeline started but silently stopped before execution
        4. No GraphQL stages in pipeline_states
           → agent called query_tool but graph routing sent output to GraphQL node
        5. SQL returned at least 1 row
           → query executed but returned empty (wrong schema / broken SQL generation)
        6. Final answer is non-trivial (> 80 chars, not an error message)
           → agent received tool output but produced a refusal or generic error
        """
        from src.streaming import AtlasTextToSQL

        question = "What were the top 5 products exported by Brazil in 2020?"

        tool_calls: list[str] = []
        pipeline_states: list[dict] = []
        answer_chunks: list[str] = []

        async with await AtlasTextToSQL.create_async() as atlas:
            async for stream_data in atlas.aanswer_question_stream(
                question,
                agent_mode="sql_only",
            ):
                if stream_data.message_type == "tool_call":
                    tool_calls.append(stream_data.tool_call or "")
                elif stream_data.message_type == "pipeline_state":
                    pipeline_states.append(stream_data.payload or {})
                elif stream_data.message_type == "agent_talk":
                    answer_chunks.append(stream_data.content or "")

        final_answer = "".join(answer_chunks)
        stages = {p.get("stage") for p in pipeline_states}

        # 1. query_tool was called at least once
        assert any(t == "query_tool" for t in tool_calls), (
            "Expected at least one query_tool call in SQL-only mode; "
            f"got tool_calls={tool_calls}"
        )

        # 2. atlas_graphql was NEVER called
        assert not any(t == "atlas_graphql" for t in tool_calls), (
            "atlas_graphql was called in SQL-only mode — mode override was ignored; "
            f"tool_calls={tool_calls}"
        )

        # 3. sql_query_agent stage present
        assert "sql_query_agent" in stages, (
            "SQL pipeline did not reach sql_query_agent stage; "
            f"observed stages={stages}"
        )

        # 4. No GraphQL stages in pipeline_states
        graphql_stages_seen = stages & _GRAPHQL_STAGES
        assert (
            not graphql_stages_seen
        ), f"GraphQL pipeline stages appeared in SQL-only mode: {graphql_stages_seen}"

        # 5. SQL returned at least 1 row
        execute_sql_states = [
            p for p in pipeline_states if p.get("stage") == "sql_query_agent"
        ]
        row_counts = [p.get("row_count", 0) for p in execute_sql_states]
        assert any(
            rc > 0 for rc in row_counts
        ), f"SQL query returned no rows; row_counts={row_counts}"

        # 6. Final answer is non-trivial
        assert len(final_answer) > 80, (
            f"Final answer is too short ({len(final_answer)} chars); "
            f"likely an error or refusal: {final_answer!r}"
        )
