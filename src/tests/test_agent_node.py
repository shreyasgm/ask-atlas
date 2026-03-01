"""Unit tests for src/agent_node.py — mode resolution and tool binding."""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agent_node import make_agent_node, resolve_effective_mode
from src.config import AgentMode
from src.graphql_client import GraphQLBudgetTracker
from src.prompts import build_sql_only_system_prompt

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
    state: dict = {
        "messages": [HumanMessage(content="What did Brazil export in 2021?")],
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


# ---------------------------------------------------------------------------
# Tests: resolve_effective_mode
# ---------------------------------------------------------------------------


class TestResolveEffectiveMode:
    def test_mode_resolution_sql_only_ignores_budget(self):
        """SQL_ONLY always returns SQL_ONLY even when budget is available."""
        budget = _make_budget(available=True)
        result = resolve_effective_mode(AgentMode.SQL_ONLY, budget)
        assert result == AgentMode.SQL_ONLY

    def test_mode_resolution_graphql_sql_ignores_budget(self):
        """GRAPHQL_SQL always returns GRAPHQL_SQL even when budget is exhausted."""
        budget = _make_budget(available=False)
        result = resolve_effective_mode(AgentMode.GRAPHQL_SQL, budget)
        assert result == AgentMode.GRAPHQL_SQL

    def test_mode_resolution_auto_with_available_budget(self):
        """AUTO + budget available → effective mode is GRAPHQL_SQL."""
        budget = _make_budget(available=True)
        result = resolve_effective_mode(AgentMode.AUTO, budget)
        assert result == AgentMode.GRAPHQL_SQL

    def test_mode_resolution_auto_with_exhausted_budget(self):
        """AUTO + budget exhausted → effective mode is SQL_ONLY."""
        budget = _make_budget(available=False)
        result = resolve_effective_mode(AgentMode.AUTO, budget)
        assert result == AgentMode.SQL_ONLY

    def test_mode_resolution_auto_with_none_budget(self):
        """AUTO + None budget → effective mode is SQL_ONLY (no GraphQL available)."""
        result = resolve_effective_mode(AgentMode.AUTO, None)
        assert result == AgentMode.SQL_ONLY

    def test_mode_resolution_graphql_only_ignores_budget(self):
        """GRAPHQL_ONLY always returns GRAPHQL_ONLY even when budget is exhausted."""
        budget = _make_budget(available=False)
        result = resolve_effective_mode(AgentMode.GRAPHQL_ONLY, budget)
        assert result == AgentMode.GRAPHQL_ONLY

    def test_mode_resolution_graphql_only_with_none_budget(self):
        """GRAPHQL_ONLY returns GRAPHQL_ONLY even with no budget tracker."""
        result = resolve_effective_mode(AgentMode.GRAPHQL_ONLY, None)
        assert result == AgentMode.GRAPHQL_ONLY


# ---------------------------------------------------------------------------
# Tests: make_agent_node — SQL-only mode
# ---------------------------------------------------------------------------


class TestAgentNodeSqlOnly:
    async def test_sql_only_agent_prompt_matches_builder(self):
        """REGRESSION: system prompt in SQL-only mode must match build_sql_only_system_prompt."""
        captured_messages = []
        mock_bound = MagicMock()

        async def _capture(messages):
            captured_messages.extend(messages)
            return AIMessage(content="answer")

        mock_bound.ainvoke = _capture

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound

        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.SQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )
        state = _base_state()
        await node(state)

        system_msgs = [m for m in captured_messages if isinstance(m, SystemMessage)]
        assert system_msgs, "No SystemMessage found"
        prompt = system_msgs[0].content

        # Must start with build_sql_only_system_prompt output
        expected_base = build_sql_only_system_prompt(3, 15)
        assert prompt.startswith(expected_base)

        # Must NOT include atlas_graphql (SQL-only mode)
        assert "atlas_graphql" not in prompt

    async def test_sql_only_agent_binds_only_query_tool(self):
        """In SQL-only mode, only query_tool is bound — no atlas_graphql."""
        bound_tools_list = []
        mock_bound = MagicMock()

        async def _capture(messages):
            return AIMessage(content="answer")

        mock_bound.ainvoke = _capture

        mock_llm = MagicMock()

        def _bind_tools(tools, **kwargs):
            bound_tools_list.extend(tools)
            return mock_bound

        mock_llm.bind_tools = _bind_tools

        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.SQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )
        await node(_base_state())

        tool_names = [t.name for t in bound_tools_list]
        assert "query_tool" in tool_names
        assert "docs_tool" in tool_names
        assert "atlas_graphql" not in tool_names

    async def test_budget_status_appears_in_dual_mode_prompt(self):
        """In GRAPHQL_SQL mode with available budget, system prompt contains budget info."""
        captured_messages = []
        mock_bound = MagicMock()

        async def _capture(messages):
            captured_messages.extend(messages)
            return AIMessage(content="answer")

        mock_bound.ainvoke = _capture
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound

        budget = _make_budget(available=True)
        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.GRAPHQL_SQL,
            max_uses=3,
            top_k_per_query=15,
            budget_tracker=budget,
        )
        await node(_base_state())

        system_msgs = [m for m in captured_messages if isinstance(m, SystemMessage)]
        assert system_msgs, "No SystemMessage found"
        prompt = system_msgs[0].content
        assert "Available" in prompt
        assert "calls remaining" in prompt

    async def test_dual_mode_agent_binds_both_tools(self):
        """In GRAPHQL_SQL mode, both query_tool and atlas_graphql are bound."""
        bound_tools_list = []
        mock_bound = MagicMock()

        async def _capture(messages):
            return AIMessage(content="answer")

        mock_bound.ainvoke = _capture

        mock_llm = MagicMock()

        def _bind_tools(tools, **kwargs):
            bound_tools_list.extend(tools)
            return mock_bound

        mock_llm.bind_tools = _bind_tools

        budget = _make_budget(available=True)
        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.GRAPHQL_SQL,
            max_uses=3,
            top_k_per_query=15,
            budget_tracker=budget,
        )
        await node(_base_state())

        tool_names = [t.name for t in bound_tools_list]
        assert "query_tool" in tool_names
        assert "atlas_graphql" in tool_names
        assert "docs_tool" in tool_names


# ---------------------------------------------------------------------------
# Tests: make_agent_node — GRAPHQL_ONLY mode
# ---------------------------------------------------------------------------


class TestAgentNodeGraphqlOnly:
    async def test_graphql_only_binds_only_graphql_and_docs(self):
        """In GRAPHQL_ONLY mode, only atlas_graphql and docs_tool are bound — no query_tool."""
        bound_tools_list = []
        mock_bound = MagicMock()

        async def _capture(messages):
            return AIMessage(content="answer")

        mock_bound.ainvoke = _capture

        mock_llm = MagicMock()

        def _bind_tools(tools, **kwargs):
            bound_tools_list.extend(tools)
            return mock_bound

        mock_llm.bind_tools = _bind_tools

        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.GRAPHQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )
        await node(_base_state())

        tool_names = [t.name for t in bound_tools_list]
        assert "atlas_graphql" in tool_names
        assert "docs_tool" in tool_names
        assert "query_tool" not in tool_names

    async def test_graphql_only_prompt_has_override_prefix(self):
        """GRAPHQL_ONLY mode should prepend the GRAPHQL_ONLY_OVERRIDE."""
        captured_messages = []
        mock_bound = MagicMock()

        async def _capture(messages):
            captured_messages.extend(messages)
            return AIMessage(content="answer")

        mock_bound.ainvoke = _capture
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound

        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.GRAPHQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )
        await node(_base_state())

        system_msgs = [m for m in captured_messages if isinstance(m, SystemMessage)]
        assert system_msgs
        prompt = system_msgs[0].content
        # Should start with the GraphQL-only override
        assert "SQL Tool Disabled" in prompt
        # Should contain docs_tool guidance
        assert "docs_tool" in prompt
        # Should contain atlas_graphql tool reference
        assert "atlas_graphql" in prompt

    async def test_graphql_only_via_per_request_override(self):
        """Per-request override_agent_mode='graphql_only' binds only graphql + docs."""
        bound_tools_list = []
        mock_bound = MagicMock()

        async def _capture(messages):
            return AIMessage(content="answer")

        mock_bound.ainvoke = _capture

        mock_llm = MagicMock()

        def _bind_tools(tools, **kwargs):
            bound_tools_list.extend(tools)
            return mock_bound

        mock_llm.bind_tools = _bind_tools

        # Build node with SQL_ONLY as the default, but override per-request
        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.SQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )
        state = _base_state(override_agent_mode="graphql_only")
        await node(state)

        tool_names = [t.name for t in bound_tools_list]
        assert "atlas_graphql" in tool_names
        assert "docs_tool" in tool_names
        assert "query_tool" not in tool_names
