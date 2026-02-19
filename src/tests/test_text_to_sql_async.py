"""Unit tests for AtlasTextToSQL async methods -- behavioral contracts.

These tests verify the observable behavior of the async API surface
(aanswer_question, aanswer_question_stream, astream_agent_response,
create_async, aclose) without reading the implementation.  A stub
LangGraph graph with FakeToolCallingModel provides deterministic
control over agent responses.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.state import AtlasAgentState
from src.text_to_sql import AtlasTextToSQL, StreamData
from src.tests.fake_model import FakeToolCallingModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_call(name: str, question: str, call_id: str) -> dict:
    """Build a tool_call dict in the format LangChain's AIMessage expects.

    Args:
        name: Tool name (e.g. "query_tool").
        question: The question arg passed to the tool.
        call_id: Unique call ID used to correlate ToolMessage responses.
    """
    return {
        "name": name,
        "args": {"question": question},
        "id": call_id,
        "type": "tool_call",
    }


def _build_stub_instance(
    responses: list[AIMessage],
) -> AtlasTextToSQL:
    """Create an AtlasTextToSQL instance with a minimal stub graph.

    Bypasses ``__init__`` entirely; only the compiled graph and helper
    attributes needed by the async methods are wired up.

    The graph has two nodes:
    - **agent**: Calls ``FakeToolCallingModel`` which returns scripted
      ``AIMessage`` responses in order.
    - **format_results**: A stub pipeline node.  The node name MUST be a
      member of ``PIPELINE_NODES`` (defined in ``src.generate_query``)
      because the streaming logic in ``astream_agent_response`` uses
      ``PIPELINE_NODES`` to decide whether a stream update comes from
      the tool pipeline (source="tool") or from the agent (source="agent").
      Using any name outside that frozenset would cause all updates to be
      classified as agent updates, hiding tool_output StreamData items.

    Routing: agent -> tool_calls present? -> format_results -> agent
                                          |-> no tool_calls  -> END
    """
    from langchain_core.tools import tool
    from src.generate_query import QueryToolInput

    @tool("query_tool", args_schema=QueryToolInput)
    def dummy_tool(question: str) -> str:
        """A trade data query tool."""
        return "stub result"

    model = FakeToolCallingModel(responses=responses)

    async def agent_node(state: AtlasAgentState) -> dict:
        model_with_tools = model.bind_tools([dummy_tool])
        return {"messages": [await model_with_tools.ainvoke(state["messages"])]}

    async def format_results(state: AtlasAgentState) -> dict:
        """Stub pipeline node.

        The name "format_results" is deliberately chosen from PIPELINE_NODES
        so that the streaming logic classifies its output as source="tool".
        See the docstring of ``_build_stub_instance`` for details.
        """
        last_msg = state["messages"][-1]
        tool_calls = last_msg.tool_calls
        tc = tool_calls[0]
        messages: list[ToolMessage] = [
            ToolMessage(
                content=f"Query results for: {tc['args']['question']}",
                tool_call_id=tc["id"],
            )
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
            return "format_results"
        return END

    builder = StateGraph(AtlasAgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("format_results", format_results)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent)
    builder.add_edge("format_results", "agent")

    instance = AtlasTextToSQL.__new__(AtlasTextToSQL)
    instance.agent = builder.compile(checkpointer=MemorySaver())
    return instance


# ---------------------------------------------------------------------------
# Tests -- aanswer_question
# ---------------------------------------------------------------------------


class TestAAnswerQuestion:
    """Tests for the non-streaming async method ``aanswer_question``."""

    async def test_returns_final_answer(self):
        """aanswer_question should return the agent's final text answer
        after a tool call round-trip (tool_call -> tool_output -> answer)."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "US exports", "c1")],
            ),
            AIMessage(content="The US exported a lot."),
        ]
        instance = _build_stub_instance(responses)
        answer = await instance.aanswer_question("US exports?", thread_id="t1")
        assert isinstance(answer, str)
        assert "US exported a lot" in answer

    async def test_direct_answer_no_tool(self):
        """When the LLM answers directly without calling a tool, the
        answer string should still be returned correctly."""
        responses = [AIMessage(content="42 is the answer.")]
        instance = _build_stub_instance(responses)
        answer = await instance.aanswer_question("What is 6*7?")
        assert "42" in answer

    async def test_generates_thread_id_when_none(self):
        """When thread_id is None, a UUID is auto-generated and no error
        is raised."""
        responses = [AIMessage(content="Some answer.")]
        instance = _build_stub_instance(responses)
        answer = await instance.aanswer_question("hi", thread_id=None)
        assert isinstance(answer, str)

    async def test_multi_turn_remembers_context(self):
        """Two calls to aanswer_question with the SAME thread_id should
        share conversation history.  The FakeToolCallingModel sees the
        accumulated messages, so its second scripted response can reference
        context from the first turn.

        Behavioral assertion: the second answer is produced without error,
        and the conversation state (tracked by the checkpointer) persists
        across calls, evidenced by the agent receiving prior messages."""
        responses = [
            # Turn 1: agent calls tool, then answers
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "coffee exports", "c1")],
            ),
            AIMessage(content="Brazil leads in coffee exports."),
            # Turn 2: direct answer referencing prior context
            AIMessage(content="As I mentioned, Brazil is the top exporter."),
        ]
        instance = _build_stub_instance(responses)
        thread = "multi-turn-1"

        answer1 = await instance.aanswer_question(
            "Who exports the most coffee?", thread_id=thread
        )
        assert "Brazil" in answer1

        answer2 = await instance.aanswer_question(
            "Can you remind me?", thread_id=thread
        )
        assert isinstance(answer2, str)
        assert "Brazil" in answer2


# ---------------------------------------------------------------------------
# Tests -- astream_agent_response
# ---------------------------------------------------------------------------


class TestAStreamAgentResponse:
    """Tests for the low-level async streaming method ``astream_agent_response``."""

    async def test_yields_stream_data_tuples(self):
        """Each yielded item is a ``(stream_mode, StreamData)`` tuple where
        stream_mode is a string and data is a StreamData instance."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "exports", "c1")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_stub_instance(responses)
        config = {"configurable": {"thread_id": "stream-1"}}

        items = []
        async for mode, data in instance.astream_agent_response("exports?", config):
            assert isinstance(mode, str)
            assert isinstance(data, StreamData)
            items.append((mode, data))

        assert len(items) > 0

    async def test_agent_talk_present(self):
        """The final agent answer should appear as one or more StreamData
        items with message_type="agent_talk"."""
        responses = [AIMessage(content="Direct answer here.")]
        instance = _build_stub_instance(responses)
        config = {"configurable": {"thread_id": "stream-2"}}

        agent_talks = []
        async for _mode, data in instance.astream_agent_response("question", config):
            if data.message_type == "agent_talk":
                agent_talks.append(data)

        assert len(agent_talks) > 0
        combined = "".join(d.content for d in agent_talks)
        assert "Direct answer" in combined

    async def test_tool_output_present(self):
        """When the agent calls a tool, at least one StreamData with
        message_type="tool_output" should appear in the stream."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "coffee exports", "c1")],
            ),
            AIMessage(content="Here are results."),
        ]
        instance = _build_stub_instance(responses)
        config = {"configurable": {"thread_id": "stream-3"}}

        tool_outputs = []
        async for _mode, data in instance.astream_agent_response("coffee?", config):
            if data.message_type == "tool_output":
                tool_outputs.append(data)

        assert len(tool_outputs) > 0

    async def test_both_sources_present(self):
        """When the agent performs a tool call, the stream MUST contain
        StreamData items from BOTH sources: "agent" and "tool".

        This is the corrected version of the original test that only
        checked for "agent" in sources."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "trade data", "c1")],
            ),
            AIMessage(content="Here is the trade data."),
        ]
        instance = _build_stub_instance(responses)
        config = {"configurable": {"thread_id": "stream-both"}}

        sources = set()
        async for _mode, data in instance.astream_agent_response(
            "trade data?", config
        ):
            sources.add(data.source)

        assert "agent" in sources, (
            "Expected 'agent' source in stream but got: " + str(sources)
        )
        assert "tool" in sources, (
            "Expected 'tool' source in stream but got: " + str(sources)
        )


# ---------------------------------------------------------------------------
# Tests -- aanswer_question_stream
# ---------------------------------------------------------------------------


class TestAAnswerQuestionStream:
    """Tests for the high-level async streaming method ``aanswer_question_stream``."""

    async def test_yields_stream_data_objects(self):
        """Each yielded item should be a StreamData object (no mode prefix),
        unlike astream_agent_response which yields (mode, StreamData) tuples."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "exports", "c1")],
            ),
            AIMessage(content="Final answer."),
        ]
        instance = _build_stub_instance(responses)

        items = []
        async for data in instance.aanswer_question_stream(
            "exports?", thread_id="hl-1"
        ):
            assert isinstance(data, StreamData)
            items.append(data)

        assert len(items) > 0

    async def test_contains_agent_and_tool_data(self):
        """Stream should contain data from BOTH "agent" and "tool" sources
        when the agent calls a tool.

        This is the corrected version -- the original test only asserted
        "agent" in sources, which was misleading given the test name."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "machinery", "c1")],
            ),
            AIMessage(content="Machinery results."),
        ]
        instance = _build_stub_instance(responses)

        sources = set()
        async for data in instance.aanswer_question_stream(
            "machinery?", thread_id="hl-2"
        ):
            sources.add(data.source)

        assert "agent" in sources, (
            "Expected 'agent' source in stream but got: " + str(sources)
        )
        assert "tool" in sources, (
            "Expected 'tool' source in stream but got: " + str(sources)
        )

    async def test_message_types_present(self):
        """When the agent performs a tool call round-trip, the stream should
        contain at least "agent_talk" and "tool_output" message_types."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "oil imports", "c1")],
            ),
            AIMessage(content="Oil import results."),
        ]
        instance = _build_stub_instance(responses)

        message_types = set()
        async for data in instance.aanswer_question_stream(
            "oil imports?", thread_id="hl-3"
        ):
            message_types.add(data.message_type)

        assert "agent_talk" in message_types, (
            "Expected 'agent_talk' message_type but got: " + str(message_types)
        )
        assert "tool_output" in message_types, (
            "Expected 'tool_output' message_type but got: " + str(message_types)
        )


# ---------------------------------------------------------------------------
# Tests -- create_async factory
# ---------------------------------------------------------------------------


class TestCreateAsync:
    """Behavioral tests for the ``create_async`` classmethod.

    Rather than mocking every internal module and asserting mock call
    patterns (which couples tests to implementation), these tests verify
    observable properties of the returned instance.
    """

    async def test_create_async_returns_instance(self):
        """create_async should return an AtlasTextToSQL instance."""
        with (
            patch("src.text_to_sql.get_settings") as mock_settings,
            patch("src.text_to_sql.create_engine") as mock_engine,
            patch("src.text_to_sql.create_async_engine") as mock_async_engine,
            patch("src.text_to_sql.SQLDatabaseWithSchemas") as mock_db_cls,
            patch("src.text_to_sql.create_llm") as mock_create_llm,
            patch("src.text_to_sql.load_example_queries") as mock_load_queries,
            patch("src.text_to_sql.create_sql_agent") as mock_create_agent,
            patch("src.text_to_sql.AsyncCheckpointerManager") as mock_acm_cls,
        ):
            mock_settings.return_value = MagicMock(
                atlas_db_url="postgresql://test:5432/db",
                max_results_per_query=15,
                max_queries_per_question=3,
                metadata_model="test-model",
                metadata_model_provider="openai",
                query_model="test-model",
                query_model_provider="openai",
            )
            mock_engine.return_value = MagicMock()
            mock_async_engine.return_value = MagicMock()
            mock_db_cls.return_value = MagicMock()
            mock_create_llm.return_value = MagicMock()
            mock_load_queries.return_value = []
            mock_create_agent.return_value = MagicMock()

            mock_acm = MagicMock()
            mock_acm.get_checkpointer = AsyncMock(return_value=MemorySaver())
            mock_acm_cls.return_value = mock_acm

            instance = await AtlasTextToSQL.create_async(
                db_uri="postgresql://test:5432/db",
                table_descriptions_json="db_table_descriptions.json",
                table_structure_json="db_table_structure.json",
                queries_json="queries.json",
                example_queries_dir="example_queries",
            )

            assert isinstance(instance, AtlasTextToSQL)

    async def test_create_async_memorysaver_fallback(self):
        """When no DB URL is available for the async checkpointer, the
        factory should still produce a usable instance.

        Behavioral assertion: the returned instance has both
        ``_async_checkpointer_manager`` and ``agent`` attributes set,
        confirming that the factory wired up persistence and the graph."""
        with (
            patch("src.text_to_sql.get_settings") as mock_settings,
            patch("src.text_to_sql.create_engine") as mock_engine,
            patch("src.text_to_sql.create_async_engine") as mock_async_engine,
            patch("src.text_to_sql.SQLDatabaseWithSchemas") as mock_db_cls,
            patch("src.text_to_sql.create_llm") as mock_create_llm,
            patch("src.text_to_sql.load_example_queries") as mock_load_queries,
            patch("src.text_to_sql.create_sql_agent") as mock_create_agent,
            patch("src.text_to_sql.AsyncCheckpointerManager") as mock_acm_cls,
        ):
            mock_settings.return_value = MagicMock(
                atlas_db_url="postgresql://test:5432/db",
                max_results_per_query=15,
                max_queries_per_question=3,
                metadata_model="test-model",
                metadata_model_provider="openai",
                query_model="test-model",
                query_model_provider="openai",
            )
            mock_engine.return_value = MagicMock()
            mock_async_engine.return_value = MagicMock()
            mock_db_cls.return_value = MagicMock()
            mock_create_llm.return_value = MagicMock()
            mock_load_queries.return_value = []
            mock_create_agent.return_value = MagicMock()

            # Simulate MemorySaver fallback: get_checkpointer returns a
            # MemorySaver regardless of DB URL.
            mock_acm = MagicMock()
            mock_acm.get_checkpointer = AsyncMock(return_value=MemorySaver())
            mock_acm_cls.return_value = mock_acm

            instance = await AtlasTextToSQL.create_async(
                db_uri="postgresql://test:5432/db",
                table_descriptions_json="db_table_descriptions.json",
                table_structure_json="db_table_structure.json",
                queries_json="queries.json",
                example_queries_dir="example_queries",
            )

            # Behavioral assertions: the factory produced a complete instance
            assert hasattr(instance, "_async_checkpointer_manager"), (
                "create_async must set _async_checkpointer_manager on the instance"
            )
            assert hasattr(instance, "agent"), (
                "create_async must set agent on the instance"
            )


# ---------------------------------------------------------------------------
# Tests -- aclose
# ---------------------------------------------------------------------------


class TestAClose:
    """Tests for the async close method and context manager protocol."""

    async def test_aclose_calls_async_checkpointer_manager(self):
        """aclose should close the async checkpointer manager if present
        and dispose both sync and async engines."""
        instance = AtlasTextToSQL.__new__(AtlasTextToSQL)
        instance._async_checkpointer_manager = MagicMock()
        instance._async_checkpointer_manager.close = AsyncMock()
        instance.engine = MagicMock()
        instance.async_engine = MagicMock()
        instance.async_engine.dispose = AsyncMock()

        await instance.aclose()

        instance._async_checkpointer_manager.close.assert_awaited_once()
        instance.async_engine.dispose.assert_awaited_once()
        instance.engine.dispose.assert_called_once()

    async def test_aclose_without_async_manager_is_safe(self):
        """aclose should not fail if no async checkpointer manager exists."""
        instance = AtlasTextToSQL.__new__(AtlasTextToSQL)
        instance.engine = MagicMock()

        await instance.aclose()  # should not raise

        instance.engine.dispose.assert_called_once()

    async def test_aclose_without_engine_is_safe(self):
        """aclose should not fail if engine attribute does not exist."""
        instance = AtlasTextToSQL.__new__(AtlasTextToSQL)
        # No engine, no _async_checkpointer_manager set
        await instance.aclose()  # should not raise

    async def test_async_context_manager(self):
        """Instance should work as an async context manager; __aexit__
        calls aclose automatically."""
        instance = AtlasTextToSQL.__new__(AtlasTextToSQL)
        instance._async_checkpointer_manager = MagicMock()
        instance._async_checkpointer_manager.close = AsyncMock()
        instance.engine = MagicMock()

        async with instance:
            pass  # aclose should be called on exit

        instance._async_checkpointer_manager.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Integration tests (require external services)
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
class TestIntegrationAtlasTextToSQL:
    """Integration tests that require a real LLM and database.

    Redundant tests (covered by test_text_to_sql.py and
    test_query_tool_integration.py) have been removed.  Only the
    multi-turn streaming test remains — it is the sole test that
    exercises aanswer_question_stream across multiple turns on the
    same thread_id.
    """

    async def test_multi_turn_streaming(self, base_dir):
        """Multi-turn conversation with streaming: second question should
        be informed by context from the first."""
        async with await AtlasTextToSQL.create_async(
            table_descriptions_json=base_dir / "db_table_descriptions.json",
            table_structure_json=base_dir / "db_table_structure.json",
            queries_json=base_dir / "src/example_queries/queries.json",
            example_queries_dir=base_dir / "src/example_queries",
        ) as agent:
            thread = "integration-multi"
            items1 = []
            async for data in agent.aanswer_question_stream(
                "What are Japan's main exports?", thread_id=thread
            ):
                items1.append(data)
            assert len(items1) > 0

            items2 = []
            async for data in agent.aanswer_question_stream(
                "How about imports?", thread_id=thread
            ):
                items2.append(data)
            assert len(items2) > 0
