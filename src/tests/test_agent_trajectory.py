"""Agent trajectory tests using FakeToolCallingModel — no LLM, no DB."""

import pytest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END

from src.generate_query import QueryToolInput
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


def _make_tool_message(content: str, tool_call_id: str) -> ToolMessage:
    """Build a ToolMessage for pipeline simulation."""
    return ToolMessage(content=content, tool_call_id=tool_call_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def dummy_query_tool():
    """A no-op query_tool that always returns canned results."""

    @tool("query_tool", args_schema=QueryToolInput)
    def _query_tool(question: str) -> str:
        """A trade data query tool."""
        return "Results: USA exported $500B in machinery in 2020"

    return _query_tool


@pytest.fixture()
def make_agent(dummy_query_tool):
    """Factory: takes a list of AIMessage responses, returns (agent, memory).

    Builds a simplified graph that mirrors the production structure:
    agent → (tool_calls?) → pipeline_stub → agent  or  agent → END

    The pipeline_stub node extracts the tool_call_id, invokes the dummy tool,
    and wraps the result in a ToolMessage — equivalent to the full pipeline's
    format_results_node but without the intermediate steps.
    """

    def _make(responses: list[AIMessage]):
        model = FakeToolCallingModel(responses=responses)
        memory = MemorySaver()

        async def agent_node(state: AtlasAgentState) -> dict:
            model_with_tools = model.bind_tools([dummy_query_tool])
            return {"messages": [await model_with_tools.ainvoke(state["messages"])]}

        async def pipeline_stub(state: AtlasAgentState) -> dict:
            """Simulate the full pipeline: extract question, run tool, return ToolMessage.

            Mirrors production: responds to ALL tool_calls so that OpenAI
            doesn't reject the message history when parallel calls are made.
            """
            last_msg = state["messages"][-1]
            tool_calls = last_msg.tool_calls
            tc = tool_calls[0]
            result = dummy_query_tool.invoke(tc["args"])
            messages: list[ToolMessage] = [
                ToolMessage(content=result, tool_call_id=tc["id"])
            ]
            for extra_tc in tool_calls[1:]:
                messages.append(
                    ToolMessage(
                        content="Only one query can be executed at a time.",
                        tool_call_id=extra_tc["id"],
                    )
                )
            return {
                "messages": messages,
                "queries_executed": state.get("queries_executed", 0) + 1,
            }

        def route_after_agent(state: AtlasAgentState) -> str:
            last_msg = state["messages"][-1]
            if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                return "pipeline_stub"
            return END

        builder = StateGraph(AtlasAgentState)
        builder.add_node("agent", agent_node)
        builder.add_node("pipeline_stub", pipeline_stub)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", route_after_agent)
        builder.add_edge("pipeline_stub", "agent")

        agent = builder.compile(checkpointer=memory)
        return agent, memory

    return _make


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestAgentToolCalling:
    """Verify that the agent invokes (or skips) the tool based on model output."""

    async def test_agent_calls_tool_when_instructed(self, make_agent):
        """Model emitting tool_calls → ToolMessage appears with dummy output."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "US machinery exports", "c1")],
            ),
            AIMessage(content="The US exported $500B in machinery."),
        ]
        agent, _ = make_agent(responses)
        config = {"configurable": {"thread_id": "tool-call-1"}}

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="US machinery exports")]},
            config=config,
        )

        msgs = result["messages"]
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert "500B" in tool_msgs[0].content

    async def test_agent_terminates_without_tool_calls(self, make_agent):
        """Model without tool_calls → only HumanMessage + AIMessage, no ToolMessage."""
        responses = [
            AIMessage(content="I can answer that directly: 42."),
        ]
        agent, _ = make_agent(responses)
        config = {"configurable": {"thread_id": "no-tool-1"}}

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="What is the answer?")]},
            config=config,
        )

        msgs = result["messages"]
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 0
        # Should have at least HumanMessage + AIMessage
        assert len(msgs) == 2
        assert isinstance(msgs[0], HumanMessage)
        assert isinstance(msgs[1], AIMessage)


class TestAgentMessageSequence:
    """Verify the exact shape of the message trajectory."""

    async def test_full_trajectory_shape(self, make_agent):
        """Tool-call trajectory: Human → AI(tool_call) → Tool → AI(answer)."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "exports?", "c2")],
            ),
            AIMessage(content="Here are the results."),
        ]
        agent, _ = make_agent(responses)
        config = {"configurable": {"thread_id": "shape-1"}}

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="Tell me about exports")]},
            config=config,
        )

        msgs = result["messages"]
        assert len(msgs) == 4
        assert isinstance(msgs[0], HumanMessage)
        assert isinstance(msgs[1], AIMessage)
        assert msgs[1].tool_calls  # has tool_calls
        assert isinstance(msgs[2], ToolMessage)
        assert "500B" in msgs[2].content
        assert isinstance(msgs[3], AIMessage)
        assert msgs[3].content == "Here are the results."


class TestAgentPersistence:
    """Verify multi-turn memory within and across threads."""

    async def test_multi_turn_persistence(self, make_agent):
        """Two invocations on the same thread accumulate messages."""
        responses = [
            # Turn 1: direct answer (no tool call)
            AIMessage(content="Sure, I know about trade."),
            # Turn 2: tool call + answer
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "machinery?", "c3")],
            ),
            AIMessage(content="Here you go."),
        ]
        agent, _ = make_agent(responses)
        config = {"configurable": {"thread_id": "multi-turn-1"}}

        # Turn 1
        await agent.ainvoke(
            {"messages": [HumanMessage(content="Hi")]},
            config=config,
        )

        # Turn 2
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="Now query machinery")]},
            config=config,
        )

        msgs = result["messages"]
        # Turn 1: Human + AI = 2, Turn 2: Human + AI(tool_call) + Tool + AI = 4
        # Total accumulated = 6
        assert len(msgs) == 6
        assert isinstance(msgs[0], HumanMessage)
        assert isinstance(msgs[1], AIMessage)
        assert isinstance(msgs[2], HumanMessage)
        assert isinstance(msgs[3], AIMessage)
        assert isinstance(msgs[4], ToolMessage)
        assert isinstance(msgs[5], AIMessage)

    async def test_different_threads_are_independent(self, make_agent):
        """Thread A and Thread B don't share state."""
        responses = [
            AIMessage(content="Answer for thread A."),
            AIMessage(content="Answer for thread B."),
        ]
        agent, _ = make_agent(responses)

        result_a = await agent.ainvoke(
            {"messages": [HumanMessage(content="Question A")]},
            config={"configurable": {"thread_id": "thread-A"}},
        )
        result_b = await agent.ainvoke(
            {"messages": [HumanMessage(content="Question B")]},
            config={"configurable": {"thread_id": "thread-B"}},
        )

        # Each thread should have exactly 2 messages (Human + AI)
        assert len(result_a["messages"]) == 2
        assert len(result_b["messages"]) == 2
        # Content should be independent
        assert result_a["messages"][1].content == "Answer for thread A."
        assert result_b["messages"][1].content == "Answer for thread B."
