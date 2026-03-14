"""Unit tests for src/agent_node.py — mode resolution and tool binding."""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

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


# ---------------------------------------------------------------------------
# Tests: auto-injected docs in user message
# ---------------------------------------------------------------------------


class TestDocsAutoInjection:
    """Verify that docs_auto_chunks are injected into the user message sent to the LLM."""

    async def test_auto_chunks_injected_into_last_human_message(self):
        """When docs_auto_chunks is populated, the last HumanMessage sent to
        the LLM should contain the documentation_context XML and framing text."""
        captured_messages = []
        mock_bound = MagicMock()

        async def _capture(messages):
            captured_messages.extend(messages)
            return AIMessage(content="ECI measures complexity")

        mock_bound.ainvoke = _capture
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound

        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.SQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )

        chunks = [
            {
                "chunk_id": "abc123",
                "doc_filename": "eci.md",
                "doc_title": "ECI Methodology",
                "section_title": "Overview",
                "body": "The ECI measures productive knowledge.",
            },
            {
                "chunk_id": "def456",
                "doc_filename": "rca.md",
                "doc_title": "RCA Definition",
                "section_title": "Formula",
                "body": "RCA is calculated as...",
            },
        ]
        state = _base_state(
            messages=[HumanMessage(content="What is ECI?")],
            docs_auto_chunks=chunks,
        )
        await node(state)

        # Find the HumanMessage sent to the LLM
        human_msgs = [m for m in captured_messages if isinstance(m, HumanMessage)]
        assert human_msgs, "No HumanMessage found in LLM call"
        content = human_msgs[-1].content

        # Question should come first (primacy)
        assert content.startswith("What is ECI?")
        # Framing text should be present
        assert "Auto-retrieved documentation" in content
        assert "Call docs_tool only if you need" in content
        # Documentation XML should be present
        assert "<documentation_context>" in content
        assert 'source="eci.md"' in content
        assert 'source="rca.md"' in content
        assert "The ECI measures productive knowledge." in content

    async def test_no_injection_when_auto_chunks_empty(self):
        """When docs_auto_chunks is empty, the user message should be unmodified."""
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

        state = _base_state(
            messages=[HumanMessage(content="What did Brazil export?")],
            docs_auto_chunks=[],
        )
        await node(state)

        human_msgs = [m for m in captured_messages if isinstance(m, HumanMessage)]
        assert human_msgs
        content = human_msgs[-1].content
        assert content == "What did Brazil export?"
        assert "<documentation_context>" not in content

    async def test_no_injection_when_auto_chunks_missing(self):
        """When docs_auto_chunks is not in state at all, no injection occurs."""
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

        # No docs_auto_chunks key at all
        state = _base_state(
            messages=[HumanMessage(content="What did Brazil export?")],
        )
        await node(state)

        human_msgs = [m for m in captured_messages if isinstance(m, HumanMessage)]
        content = human_msgs[-1].content
        assert content == "What did Brazil export?"

    async def test_injection_targets_last_human_message_in_multi_turn(self):
        """In multi-turn conversations, only the last HumanMessage gets docs injected."""
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

        chunks = [
            {
                "chunk_id": "abc",
                "doc_filename": "eci.md",
                "doc_title": "ECI",
                "section_title": "Intro",
                "body": "ECI content",
            }
        ]
        state = _base_state(
            messages=[
                HumanMessage(content="First question"),
                AIMessage(content="First answer"),
                HumanMessage(content="Follow-up question"),
            ],
            docs_auto_chunks=chunks,
        )
        await node(state)

        human_msgs = [m for m in captured_messages if isinstance(m, HumanMessage)]
        assert len(human_msgs) == 2
        # First message should be unmodified
        assert human_msgs[0].content == "First question"
        # Last message should have docs injected
        assert "<documentation_context>" in human_msgs[1].content
        assert human_msgs[1].content.startswith("Follow-up question")


# ---------------------------------------------------------------------------
# Tests: orphan tool call repair
# ---------------------------------------------------------------------------


class TestOrphanToolCallRepair:
    """Verify that agent_node self-heals orphan tool_calls from cancelled requests."""

    async def test_orphan_tool_call_repaired_before_llm_call(self):
        """When messages contain an AIMessage with tool_calls but no matching
        ToolMessages, agent_node should inject stubs so the LLM call succeeds."""
        captured_messages = []
        mock_bound = MagicMock()

        async def _capture(messages):
            captured_messages.extend(messages)
            return AIMessage(content="recovered answer")

        mock_bound.ainvoke = _capture
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound

        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.SQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )

        # Simulate orphan: AIMessage has tool_calls but no ToolMessage follows
        orphan_ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_ORPHAN_123",
                    "name": "query_tool",
                    "args": {"question": "test"},
                }
            ],
        )
        state = _base_state(
            messages=[
                HumanMessage(content="What did Brazil export?"),
                orphan_ai,
            ]
        )

        result = await node(state)

        # The repaired stub should appear in messages sent to LLM
        tool_msgs = [m for m in captured_messages if isinstance(m, ToolMessage)]
        assert any(m.tool_call_id == "call_ORPHAN_123" for m in tool_msgs)

        # The stub should also be in the returned state update so it persists
        returned_msgs = result["messages"]
        stubs = [m for m in returned_msgs if isinstance(m, ToolMessage)]
        assert any(m.tool_call_id == "call_ORPHAN_123" for m in stubs)

    async def test_no_repair_when_tool_calls_are_matched(self):
        """When all tool_calls have matching ToolMessages, no stubs are added."""
        captured_messages = []
        mock_bound = MagicMock()

        async def _capture(messages):
            captured_messages.extend(messages)
            return AIMessage(content="normal answer")

        mock_bound.ainvoke = _capture
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound

        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.SQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )

        matched_ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_MATCHED",
                    "name": "query_tool",
                    "args": {"question": "test"},
                }
            ],
        )
        matched_tool = ToolMessage(content="result data", tool_call_id="call_MATCHED")

        state = _base_state(
            messages=[
                HumanMessage(content="What did Brazil export?"),
                matched_ai,
                matched_tool,
            ]
        )

        result = await node(state)

        # Only the LLM response should be returned — no stubs
        returned_msgs = result["messages"]
        assert len(returned_msgs) == 1
        assert isinstance(returned_msgs[0], AIMessage)

    async def test_multiple_orphan_tool_calls_all_repaired(self):
        """When an AIMessage has multiple orphan tool_calls, all get stubs."""
        captured_messages = []
        mock_bound = MagicMock()

        async def _capture(messages):
            captured_messages.extend(messages)
            return AIMessage(content="recovered")

        mock_bound.ainvoke = _capture
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound

        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.SQL_ONLY,
            max_uses=3,
            top_k_per_query=15,
        )

        orphan_ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_A", "name": "query_tool", "args": {"question": "q1"}},
                {
                    "id": "call_B",
                    "name": "atlas_graphql",
                    "args": {"question": "q2"},
                },
            ],
        )
        state = _base_state(messages=[HumanMessage(content="test"), orphan_ai])

        result = await node(state)

        returned_stubs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        stub_ids = {m.tool_call_id for m in returned_stubs}
        assert stub_ids == {"call_A", "call_B"}
