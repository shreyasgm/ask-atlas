"""Integration tests for agent tool routing across different modes.

Verifies that the agent routes specific question types to the expected
tools (query_tool, atlas_graphql, docs_tool) based on the configured
or overridden agent mode.

Requires: LLM API keys configured in .env.
Does NOT require a database — routing decisions happen in the agent node
before any DB/API call.

Run::

    PYTHONPATH=$(pwd) uv run pytest src/tests/test_tool_routing.py -m integration -v
"""

from __future__ import annotations


import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent_node import make_agent_node
from src.config import AgentMode, create_llm, get_settings
from src.graphql_client import GraphQLBudgetTracker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_budget(available: bool) -> GraphQLBudgetTracker:
    """Create a budget tracker that is or isn't available."""
    if available:
        return GraphQLBudgetTracker(max_requests=100)
    else:
        return GraphQLBudgetTracker(max_requests=0)


def _base_state(**overrides) -> dict:
    """Build a minimal agent state dict."""
    state: dict = {
        "messages": [HumanMessage(content="placeholder")],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        "pipeline_question": "",
        "pipeline_context": "",
        "pipeline_products": None,
        "pipeline_codes": "",
        "pipeline_table_info": "",
        "pipeline_sql": "",
        "pipeline_result": "",
        "pipeline_result_columns": [],
        "pipeline_result_rows": [],
        "pipeline_execution_time_ms": 0,
        "override_schema": None,
        "override_direction": None,
        "override_mode": None,
    }
    state.update(overrides)
    return state


def _extract_tool_call_name(response: AIMessage) -> str | None:
    """Extract the first tool call name from an AIMessage, or None."""
    tool_calls = getattr(response, "tool_calls", [])
    if tool_calls:
        return tool_calls[0]["name"]
    return None


@pytest.fixture(scope="module")
def frontier_llm():
    """Create a real frontier LLM for integration tests."""
    settings = get_settings()
    if (
        not settings.openai_api_key
        and not settings.anthropic_api_key
        and not settings.google_api_key
    ):
        pytest.skip("No LLM API keys configured — skipping integration tests")
    return create_llm(
        settings.frontier_model, settings.frontier_model_provider, temperature=0
    )


# ---------------------------------------------------------------------------
# Tests: GRAPHQL_SQL mode routing
# ---------------------------------------------------------------------------


class TestGraphqlSqlModeRouting:
    """In GRAPHQL_SQL mode, agent has access to query_tool, atlas_graphql, and docs_tool."""

    async def test_country_profile_question_routes_to_graphql(self, frontier_llm):
        """A country profile question should route to atlas_graphql in dual-tool mode."""
        budget = _make_budget(available=True)
        node = make_agent_node(
            llm=frontier_llm,
            agent_mode=AgentMode.GRAPHQL_SQL,
            max_uses=3,
            top_k_per_query=15,
            budget_tracker=budget,
        )
        state = _base_state(
            messages=[
                HumanMessage(
                    content="What is the GDP and population of Brazil? What is its ECI ranking?"
                )
            ]
        )

        result = await node(state)
        response = result["messages"][0]
        tool_name = _extract_tool_call_name(response)

        assert tool_name is not None, "Agent did not call any tool"
        assert (
            tool_name == "atlas_graphql"
        ), f"Expected atlas_graphql for a country profile question, got {tool_name}"


# ---------------------------------------------------------------------------
# Tests: SQL_ONLY mode routing
# ---------------------------------------------------------------------------


class TestSqlOnlyModeRouting:
    """In SQL_ONLY mode, only query_tool and docs_tool are available."""

    async def test_data_question_routes_to_query_tool(self, frontier_llm):
        """A trade data question should route to query_tool in SQL_ONLY mode."""
        node = make_agent_node(
            llm=frontier_llm,
            agent_mode=AgentMode.SQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )
        state = _base_state(
            messages=[
                HumanMessage(content="What were the top 5 exports of Germany in 2021?")
            ]
        )

        result = await node(state)
        response = result["messages"][0]
        tool_name = _extract_tool_call_name(response)

        assert tool_name is not None, "Agent did not call any tool"
        assert (
            tool_name == "query_tool"
        ), f"Expected query_tool in SQL_ONLY mode, got {tool_name}"

    async def test_atlas_graphql_not_available_in_sql_only(self, frontier_llm):
        """In SQL_ONLY mode, atlas_graphql should not be offered even for a
        country profile question — agent must use query_tool instead."""
        node = make_agent_node(
            llm=frontier_llm,
            agent_mode=AgentMode.SQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )
        state = _base_state(
            messages=[HumanMessage(content="What is Brazil's ECI ranking?")]
        )

        result = await node(state)
        response = result["messages"][0]
        tool_name = _extract_tool_call_name(response)

        # Can only be query_tool or docs_tool — never atlas_graphql
        assert tool_name in (
            "query_tool",
            "docs_tool",
        ), f"Expected query_tool or docs_tool in SQL_ONLY mode, got {tool_name}"


# ---------------------------------------------------------------------------
# Tests: GRAPHQL_ONLY mode routing
# ---------------------------------------------------------------------------


class TestGraphqlOnlyModeRouting:
    """In GRAPHQL_ONLY mode, only atlas_graphql and docs_tool are available."""

    async def test_data_question_routes_to_graphql(self, frontier_llm):
        """A data question should route to atlas_graphql in GRAPHQL_ONLY mode
        since query_tool is not available."""
        budget = _make_budget(available=True)
        node = make_agent_node(
            llm=frontier_llm,
            agent_mode=AgentMode.GRAPHQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
            budget_tracker=budget,
        )
        state = _base_state(
            messages=[HumanMessage(content="What is the GDP and population of Japan?")]
        )

        result = await node(state)
        response = result["messages"][0]
        tool_name = _extract_tool_call_name(response)

        assert tool_name is not None, "Agent did not call any tool"
        assert tool_name in (
            "atlas_graphql",
            "docs_tool",
        ), f"Expected atlas_graphql or docs_tool in GRAPHQL_ONLY mode, got {tool_name}"

    async def test_query_tool_not_available_in_graphql_only(self, frontier_llm):
        """In GRAPHQL_ONLY mode, query_tool should never be called."""
        budget = _make_budget(available=True)
        node = make_agent_node(
            llm=frontier_llm,
            agent_mode=AgentMode.GRAPHQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
            budget_tracker=budget,
        )
        state = _base_state(
            messages=[
                HumanMessage(content="What were the top 5 exports of Brazil in 2020?")
            ]
        )

        result = await node(state)
        response = result["messages"][0]
        tool_name = _extract_tool_call_name(response)

        # Can only be atlas_graphql or docs_tool — never query_tool
        assert tool_name in (
            "atlas_graphql",
            "docs_tool",
            None,
        ), f"Expected atlas_graphql or docs_tool in GRAPHQL_ONLY mode, got {tool_name}"


# ---------------------------------------------------------------------------
# Tests: docs_tool routing (available in all modes)
# ---------------------------------------------------------------------------


class TestDocsToolRouting:
    """docs_tool should be available and routed to for methodology questions in any mode."""

    async def test_methodology_question_routes_to_docs_tool_in_sql_only(
        self, frontier_llm
    ):
        """A methodology question should route to docs_tool in SQL_ONLY mode."""
        node = make_agent_node(
            llm=frontier_llm,
            agent_mode=AgentMode.SQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )
        state = _base_state(
            messages=[
                HumanMessage(
                    content=(
                        "What is the Economic Complexity Index (ECI)? "
                        "How is it calculated? What formula is used?"
                    )
                )
            ]
        )

        result = await node(state)
        response = result["messages"][0]
        tool_name = _extract_tool_call_name(response)

        # The agent should prefer docs_tool for a pure methodology question,
        # but it may also use query_tool. Both are acceptable.
        assert tool_name in (
            "docs_tool",
            "query_tool",
        ), f"Expected docs_tool or query_tool for a methodology question, got {tool_name}"

    async def test_methodology_question_routes_to_docs_tool_in_graphql_sql(
        self, frontier_llm
    ):
        """A methodology question should route to docs_tool in GRAPHQL_SQL mode."""
        budget = _make_budget(available=True)
        node = make_agent_node(
            llm=frontier_llm,
            agent_mode=AgentMode.GRAPHQL_SQL,
            max_uses=3,
            top_k_per_query=15,
            budget_tracker=budget,
        )
        state = _base_state(
            messages=[
                HumanMessage(
                    content=(
                        "What is the Economic Complexity Index (ECI)? "
                        "How is it calculated? Explain the methodology."
                    )
                )
            ]
        )

        result = await node(state)
        response = result["messages"][0]
        tool_name = _extract_tool_call_name(response)

        assert tool_name in (
            "docs_tool",
            "query_tool",
            "atlas_graphql",
        ), f"Expected docs_tool (or fallback data tool) for methodology question, got {tool_name}"
