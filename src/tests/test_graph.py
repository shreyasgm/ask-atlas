"""Unit tests for src/graph.py — graph wiring and routing.

Uses FakeToolCallingModel and build_atlas_graph with mocked GraphQL dependencies.
All tests are unit tests — no database or external LLM required.
"""

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.config import AgentMode
from src.graph import build_atlas_graph
from src.graphql_client import GraphQLBudgetTracker
from src.sql_pipeline import max_queries_exceeded_node
from src.state import AtlasAgentState
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

    with (
        patch("src.sql_pipeline.ProductAndSchemaLookup") as mock_lookup_cls,
        patch("src.graphql_pipeline.classify_query") as _,
    ):
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
# Tests: max queries enforced
# ---------------------------------------------------------------------------


class TestMaxQueriesInGraph:
    async def test_max_queries_enforced_in_new_graph(self):
        """After max_uses queries, further tool calls route to max_queries_exceeded_node."""
        model = FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[_tool_call("query_tool", "question 1", "call-1")],
                ),
                AIMessage(
                    content="",
                    tool_calls=[_tool_call("query_tool", "question 2", "call-2")],
                ),
                AIMessage(content="I have exceeded the query limit."),
            ]
        )

        # Build a simple test graph that mirrors build_atlas_graph routing
        # but uses a stub pipeline to avoid real SQL execution
        from src.sql_pipeline import QueryToolInput
        from langchain_core.tools import tool

        @tool("query_tool", args_schema=QueryToolInput)
        def dummy_query_tool(question: str, context: str = "") -> str:
            """A trade data query tool."""
            return "stub result"

        async def agent_node(state: AtlasAgentState) -> dict:
            model_with_tools = model.bind_tools([dummy_query_tool])
            return {"messages": [await model_with_tools.ainvoke(state["messages"])]}

        async def pipeline_stub(state: AtlasAgentState) -> dict:
            last_msg = state["messages"][-1]
            tc = last_msg.tool_calls[0]
            content = f"Query results for: {tc['args']['question']}"
            messages = [ToolMessage(content=content, tool_call_id=tc["id"])]
            for extra_tc in last_msg.tool_calls[1:]:
                messages.append(
                    ToolMessage(content="One at a time.", tool_call_id=extra_tc["id"])
                )
            return {
                "messages": messages,
                "queries_executed": state.get("queries_executed", 0) + 1,
            }

        max_uses = 1

        def route_after_agent(state: AtlasAgentState) -> str:
            last_msg = state["messages"][-1]
            if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                if state.get("queries_executed", 0) >= max_uses:
                    return "max_queries_exceeded"
                return "pipeline_stub"
            return END

        builder = StateGraph(AtlasAgentState)
        builder.add_node("agent", agent_node)
        builder.add_node("pipeline_stub", pipeline_stub)
        builder.add_node("max_queries_exceeded", max_queries_exceeded_node)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", route_after_agent)
        builder.add_edge("pipeline_stub", "agent")
        builder.add_edge("max_queries_exceeded", "agent")
        graph = builder.compile(checkpointer=MemorySaver())

        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="Complex question")]},
            config={"configurable": {"thread_id": "max-queries-test"}},
        )

        msgs = result["messages"]
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 2
        assert "Maximum number of queries exceeded" in tool_msgs[1].content
        assert result.get("queries_executed", 0) == 1


# ---------------------------------------------------------------------------
# Tests: AUTO mode budget degradation
# ---------------------------------------------------------------------------


class TestAutoModeBudget:
    def test_auto_mode_budget_exhausted_behaves_like_sql_only(self):
        """In AUTO mode with exhausted budget, agent gets only query_tool."""
        from src.agent_node import make_agent_node, resolve_effective_mode

        exhausted_budget = GraphQLBudgetTracker(max_requests=0)
        effective = resolve_effective_mode(AgentMode.AUTO, exhausted_budget)
        assert effective == AgentMode.SQL_ONLY

        # Verify tool binding
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

        import asyncio
        from langchain_core.messages import HumanMessage

        node = make_agent_node(
            llm=mock_llm,
            agent_mode=AgentMode.AUTO,
            max_uses=3,
            top_k_per_query=15,
            budget_tracker=exhausted_budget,
        )
        state = {
            "messages": [HumanMessage(content="test")],
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
        asyncio.get_event_loop().run_until_complete(node(state))

        tool_names = [t.name for t in bound_tools_list]
        assert "query_tool" in tool_names
        assert "atlas_graphql" not in tool_names


# ---------------------------------------------------------------------------
# Tests: RetryPolicy configured on GraphQL LLM nodes
# ---------------------------------------------------------------------------


class TestRetryPolicyConfiguration:
    """Verify LangGraph RetryPolicy is attached to GraphQL nodes that make LLM calls."""

    def _build_real_graph(self):
        """Build graph without patching classify_query so RetryPolicy config is preserved."""
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

    def test_classify_query_has_retry_policy(self):
        """classify_query makes an LLM call and should have RetryPolicy."""
        graph = self._build_real_graph()
        node = graph.nodes["classify_query"]
        assert node.retry_policy is not None, "classify_query should have RetryPolicy"
        policy = node.retry_policy[0]
        assert policy.max_attempts == 3

    def test_extract_entities_has_retry_policy(self):
        """extract_entities makes an LLM call and should have RetryPolicy."""
        graph = self._build_real_graph()
        node = graph.nodes["extract_entities"]
        assert node.retry_policy is not None, "extract_entities should have RetryPolicy"
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
