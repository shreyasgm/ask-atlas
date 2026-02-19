"""Graph structure and conditional routing tests.

Verifies the agent graph's conditional routing (agent -> END, agent -> pipeline,
agent -> max_queries_exceeded) and state transitions using a simplified test graph
that replaces the full pipeline nodes with stubs while preserving routing logic.

All tests are unit tests -- no database or external LLM required.
"""

import pytest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.generate_query import QueryToolInput, max_queries_exceeded_node
from src.state import AtlasAgentState
from src.tests.fake_model import FakeToolCallingModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_call(name: str, question: str, call_id: str) -> dict:
    """Build a ``tool_calls`` entry dict."""
    return {
        "name": name,
        "args": {"question": question},
        "id": call_id,
        "type": "tool_call",
    }


def build_test_graph(
    fake_model: FakeToolCallingModel,
    max_uses: int = 3,
    *,
    pipeline_error: str | None = None,
):
    """Build a graph with real routing logic but stub pipeline nodes.

    Args:
        fake_model: Scripted model that returns pre-defined AIMessage responses.
        max_uses: Maximum number of pipeline executions before routing to
            max_queries_exceeded.
        pipeline_error: If provided, the pipeline stub will set ``last_error``
            to this string and leave ``pipeline_result`` empty, simulating a
            SQL execution failure.
    """

    @tool("query_tool", args_schema=QueryToolInput)
    def dummy_tool(question: str) -> str:
        """A trade data query tool."""
        return "stub result"

    def agent_node(state: AtlasAgentState) -> dict:
        model_with_tools = fake_model.bind_tools([dummy_tool])
        return {"messages": [model_with_tools.invoke(state["messages"])]}

    def pipeline_stub(state: AtlasAgentState) -> dict:
        """Simulate the full pipeline in one step.

        Extracts the tool_call from the last AI message, fabricates a result
        (or an error), wraps it in a ToolMessage, and increments
        ``queries_executed``.
        """
        last_msg = state["messages"][-1]
        tc = last_msg.tool_calls[0]
        question = tc["args"]["question"]

        if pipeline_error:
            # Simulate execute_sql setting last_error
            content = f"Error executing query: {pipeline_error}"
        else:
            content = f"Query results for: {question}"

        return {
            "messages": [ToolMessage(content=content, tool_call_id=tc["id"])],
            "queries_executed": state.get("queries_executed", 0) + 1,
        }

    def route_after_agent(state: AtlasAgentState) -> str:
        """Mirror the routing logic from create_sql_agent."""
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

    return builder.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGraphRouting:
    """Verify conditional routing edges in the agent graph."""

    def test_agent_routes_to_end_without_tool_call(self):
        """Agent produces AIMessage without tool_calls -> graph ends.

        When the LLM responds with a plain text answer (no tool_calls), the
        route_after_agent function should direct the graph to END and no
        ToolMessage should appear in the output.
        """
        model = FakeToolCallingModel(
            responses=[AIMessage(content="I can answer directly: 42.")]
        )
        graph = build_test_graph(model)
        config = {"configurable": {"thread_id": "end-no-tool"}}

        result = graph.invoke(
            {"messages": [HumanMessage(content="What is 6 times 7?")]},
            config=config,
        )

        msgs = result["messages"]
        # Should be exactly HumanMessage + AIMessage
        assert len(msgs) == 2
        assert isinstance(msgs[0], HumanMessage)
        assert isinstance(msgs[1], AIMessage)
        assert msgs[1].content == "I can answer directly: 42."
        # No tool messages at all
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 0

    def test_agent_routes_to_pipeline_on_tool_call(self):
        """Agent produces AIMessage with tool_calls -> pipeline -> ToolMessage -> agent -> final answer.

        The scripted model first emits a tool_call, which routes through the
        pipeline stub to produce a ToolMessage. The agent is then re-invoked
        and produces a final plain-text answer.
        """
        model = FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("query_tool", "US machinery exports", "call-1")
                    ],
                ),
                AIMessage(content="The US exported a lot of machinery."),
            ]
        )
        graph = build_test_graph(model)
        config = {"configurable": {"thread_id": "pipeline-route"}}

        result = graph.invoke(
            {"messages": [HumanMessage(content="US machinery exports?")]},
            config=config,
        )

        msgs = result["messages"]
        # Human -> AI(tool_call) -> ToolMessage -> AI(answer) = 4 messages
        assert len(msgs) == 4
        assert isinstance(msgs[0], HumanMessage)
        assert isinstance(msgs[1], AIMessage)
        assert msgs[1].tool_calls  # has tool_calls
        assert isinstance(msgs[2], ToolMessage)
        assert "Query results for: US machinery exports" in msgs[2].content
        assert isinstance(msgs[3], AIMessage)
        assert msgs[3].content == "The US exported a lot of machinery."

    def test_max_queries_enforced(self):
        """After N queries (queries_executed >= max_uses), tool_call routes to max_queries_exceeded.

        With max_uses=2, the first two tool_calls go through the pipeline stub.
        The third tool_call should be caught by max_queries_exceeded and return
        an error ToolMessage instead of executing the pipeline.
        """
        model = FakeToolCallingModel(
            responses=[
                # Round 1: tool call
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("query_tool", "question 1", "call-1")
                    ],
                ),
                # Round 2: another tool call
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("query_tool", "question 2", "call-2")
                    ],
                ),
                # Round 3: another tool call (should be blocked by max_queries)
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("query_tool", "question 3", "call-3")
                    ],
                ),
                # Round 3 continued: agent sees the error and gives final answer
                AIMessage(content="I have exceeded the query limit."),
            ]
        )
        graph = build_test_graph(model, max_uses=2)
        config = {"configurable": {"thread_id": "max-queries"}}

        result = graph.invoke(
            {"messages": [HumanMessage(content="Complex multi-step question")]},
            config=config,
        )

        msgs = result["messages"]
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]

        # Should have 3 ToolMessages: 2 from pipeline, 1 from max_queries_exceeded
        assert len(tool_msgs) == 3

        # First two tool messages are pipeline results
        assert "Query results for: question 1" in tool_msgs[0].content
        assert "Query results for: question 2" in tool_msgs[1].content

        # Third tool message is the max_queries_exceeded error
        assert "Maximum number of queries exceeded" in tool_msgs[2].content

        # queries_executed should be 2 (only pipeline stubs increment it)
        assert result.get("queries_executed", 0) == 2

        # Final message is the agent's concluding answer
        assert isinstance(msgs[-1], AIMessage)
        assert "exceeded" in msgs[-1].content.lower()

    def test_full_round_trip_state(self):
        """After a complete pipeline pass, state fields are populated correctly.

        Verifies that messages contain the expected ToolMessage and that
        queries_executed is incremented by the pipeline stub.
        """
        model = FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("query_tool", "Brazil coffee exports", "call-rt")
                    ],
                ),
                AIMessage(content="Brazil exports lots of coffee."),
            ]
        )
        graph = build_test_graph(model)
        config = {"configurable": {"thread_id": "round-trip"}}

        result = graph.invoke(
            {"messages": [HumanMessage(content="Brazil coffee exports?")]},
            config=config,
        )

        # queries_executed should be 1 after one pipeline pass
        assert result.get("queries_executed", 0) == 1

        # Messages should have the full trajectory
        msgs = result["messages"]
        assert len(msgs) == 4

        # The ToolMessage should carry the pipeline result
        tool_msg = msgs[2]
        assert isinstance(tool_msg, ToolMessage)
        assert tool_msg.tool_call_id == "call-rt"
        assert "Query results for: Brazil coffee exports" in tool_msg.content

        # The final AI message should be the answer
        assert isinstance(msgs[3], AIMessage)
        assert msgs[3].content == "Brazil exports lots of coffee."

    def test_error_in_pipeline_propagates(self):
        """If execute_sql sets last_error, format_results creates a ToolMessage with the error content.

        Uses the pipeline_error parameter of build_test_graph to simulate
        a SQL execution failure. The resulting ToolMessage should contain the
        error message, and the agent should still get a chance to respond.
        """
        error_text = "relation hs92.country_product_year_4 does not exist"
        model = FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("query_tool", "bad query", "call-err")
                    ],
                ),
                AIMessage(
                    content="Sorry, the query failed. Let me try again differently."
                ),
            ]
        )
        graph = build_test_graph(model, pipeline_error=error_text)
        config = {"configurable": {"thread_id": "error-propagation"}}

        result = graph.invoke(
            {"messages": [HumanMessage(content="Run a bad query")]},
            config=config,
        )

        msgs = result["messages"]
        # Human -> AI(tool_call) -> ToolMessage(error) -> AI(answer) = 4
        assert len(msgs) == 4

        tool_msg = msgs[2]
        assert isinstance(tool_msg, ToolMessage)
        assert "Error executing query" in tool_msg.content
        assert error_text in tool_msg.content

        # Agent still gets to respond after the error
        assert isinstance(msgs[3], AIMessage)
        assert "failed" in msgs[3].content.lower()


class TestMultiplePipelineRounds:
    """Verify correct state accumulation across multiple pipeline invocations."""

    def test_queries_executed_increments_per_round(self):
        """Each pipeline pass increments queries_executed by 1."""
        model = FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("query_tool", "round 1", "call-r1")
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("query_tool", "round 2", "call-r2")
                    ],
                ),
                AIMessage(content="Done with both queries."),
            ]
        )
        graph = build_test_graph(model, max_uses=5)
        config = {"configurable": {"thread_id": "multi-round"}}

        result = graph.invoke(
            {"messages": [HumanMessage(content="Multi-step question")]},
            config=config,
        )

        assert result.get("queries_executed", 0) == 2

        # Should have 6 messages:
        # Human, AI(tc), Tool, AI(tc), Tool, AI(answer)
        msgs = result["messages"]
        assert len(msgs) == 6
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 2
        assert "round 1" in tool_msgs[0].content
        assert "round 2" in tool_msgs[1].content

    def test_max_uses_one_blocks_immediately(self):
        """With max_uses=0, the very first tool_call is blocked."""
        model = FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("query_tool", "any question", "call-blocked")
                    ],
                ),
                AIMessage(content="Query limit was reached before I could query."),
            ]
        )
        graph = build_test_graph(model, max_uses=0)
        config = {"configurable": {"thread_id": "block-immediate"}}

        result = graph.invoke(
            {"messages": [HumanMessage(content="Try to query")]},
            config=config,
        )

        msgs = result["messages"]
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert "Maximum number of queries exceeded" in tool_msgs[0].content

        # queries_executed should still be 0 since pipeline never ran
        assert result.get("queries_executed", 0) == 0
