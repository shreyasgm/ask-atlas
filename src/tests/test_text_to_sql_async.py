"""Unit tests for AtlasTextToSQL async methods -- behavioral contracts.

These tests verify the observable behavior of the async API surface
(aanswer_question, aanswer_question_stream, astream_agent_response,
create_async, aclose) without reading the implementation.  A stub
LangGraph graph with FakeToolCallingModel provides deterministic
control over agent responses.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.state import AtlasAgentState
from src.text_to_sql import AnswerResult, AtlasTextToSQL, StreamData
from src.tests.fake_model import FakeToolCallingModel
from src.product_and_schema_lookup import SchemasAndProductsFound, ProductDetails

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
      member of ``ALL_PIPELINE_NODES`` (defined in ``src.text_to_sql``)
      because the streaming logic in ``astream_agent_response`` uses
      ``ALL_PIPELINE_NODES`` to decide whether a stream update comes from
      the tool pipeline (source="tool") or from the agent (source="agent").
      Using any name outside that frozenset would cause all updates to be
      classified as agent updates, hiding tool_output StreamData items.

    Routing: agent -> tool_calls present? -> format_results -> agent
                                          |-> no tool_calls  -> END
    """
    from langchain_core.tools import tool
    from src.sql_pipeline import QueryToolInput

    @tool("query_tool", args_schema=QueryToolInput)
    def dummy_tool(question: str, context: str = "") -> str:
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
        """aanswer_question should return an AnswerResult with the agent's
        final text answer after a tool call round-trip."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "US exports", "c1")],
            ),
            AIMessage(content="The US exported a lot."),
        ]
        instance = _build_stub_instance(responses)
        result = await instance.aanswer_question("US exports?", thread_id="t1")
        assert isinstance(result, AnswerResult)
        assert "US exported a lot" in result.answer

    async def test_direct_answer_no_tool(self):
        """When the LLM answers directly without calling a tool, the
        AnswerResult.answer should still be returned correctly."""
        responses = [AIMessage(content="42 is the answer.")]
        instance = _build_stub_instance(responses)
        result = await instance.aanswer_question("What is 6*7?")
        assert "42" in result.answer

    async def test_generates_thread_id_when_none(self):
        """When thread_id is None, a UUID is auto-generated and used
        as the checkpointer thread key."""
        import uuid

        responses = [AIMessage(content="Some answer.")]
        instance = _build_stub_instance(responses)
        result = await instance.aanswer_question("hi", thread_id=None)
        assert isinstance(result, AnswerResult)
        assert result.answer  # non-empty answer produced

        # The MemorySaver should have exactly one thread whose key is a valid UUID
        stored_threads = list(instance.agent.checkpointer.storage.keys())
        assert len(stored_threads) == 1
        uuid.UUID(stored_threads[0])  # raises ValueError if not a valid UUID

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

        result1 = await instance.aanswer_question(
            "Who exports the most coffee?", thread_id=thread
        )
        assert "Brazil" in result1.answer

        result2 = await instance.aanswer_question(
            "Can you remind me?", thread_id=thread
        )
        assert isinstance(result2, AnswerResult)
        assert "Brazil" in result2.answer


# ---------------------------------------------------------------------------
# Tests -- aanswer_question pipeline data
# ---------------------------------------------------------------------------


class TestAAnswerQuestionPipelineData:
    """Tests for structured pipeline data returned by ``aanswer_question``."""

    async def test_single_query_returns_answer_result(self):
        """aanswer_question with a tool call should return AnswerResult
        with one query in the queries list."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "US exports", "c1")],
            ),
            AIMessage(content="The US exported goods."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        result = await instance.aanswer_question("US exports?", thread_id="pd-1")
        assert isinstance(result, AnswerResult)
        assert "US exported goods" in result.answer
        assert len(result.queries) == 1

    async def test_query_result_has_correct_fields(self):
        """Each query dict should have sql, columns, rows, row_count,
        execution_time_ms with the values from the pipeline stub."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "trade data", "c1")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        result = await instance.aanswer_question("trade data?", thread_id="pd-2")
        assert len(result.queries) == 1
        q = result.queries[0]
        assert "sql" in q
        assert q["columns"] == ["country", "value"]
        assert q["rows"] == [["USA", 1000], ["CHN", 800]]
        assert q["row_count"] == 2
        assert q["execution_time_ms"] == 42

    async def test_resolved_products_populated(self):
        """resolved_products should contain schemas and products from the
        pipeline when a tool call is made."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "coffee exports", "c1")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        result = await instance.aanswer_question("coffee?", thread_id="pd-3")
        assert result.resolved_products is not None
        assert "hs92" in result.resolved_products["schemas"]
        products = result.resolved_products["products"]
        assert len(products) >= 1
        assert products[0]["name"] == "coffee"

    async def test_direct_answer_has_empty_pipeline_data(self):
        """Direct answer (no tool call) should have empty queries and no
        resolved products."""
        responses = [AIMessage(content="Just a greeting.")]
        instance = _build_pipeline_stub_instance(responses)
        result = await instance.aanswer_question("Hello!", thread_id="pd-4")
        assert result.queries == []
        assert result.resolved_products is None
        assert result.schemas_used == []

    async def test_multi_query_accumulates_results(self):
        """Two tool calls in one turn should produce two query dicts with
        correct aggregate stats."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "Q1", "c1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "Q2", "c2")],
            ),
            AIMessage(content="Both done."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        result = await instance.aanswer_question("both?", thread_id="pd-5")
        assert len(result.queries) == 2
        assert result.total_rows == 4  # 2 rows per query * 2 queries
        assert result.total_execution_time_ms == 84  # 42ms * 2 queries


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
        async for _mode, data in instance.astream_agent_response("trade data?", config):
            sources.add(data.source)

        assert "agent" in sources, "Expected 'agent' source in stream but got: " + str(
            sources
        )
        assert "tool" in sources, "Expected 'tool' source in stream but got: " + str(
            sources
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

        assert "agent" in sources, "Expected 'agent' source in stream but got: " + str(
            sources
        )
        assert "tool" in sources, "Expected 'tool' source in stream but got: " + str(
            sources
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

        assert (
            "agent_talk" in message_types
        ), "Expected 'agent_talk' message_type but got: " + str(message_types)
        assert (
            "tool_output" in message_types
        ), "Expected 'tool_output' message_type but got: " + str(message_types)


# ---------------------------------------------------------------------------
# Tests -- create_async factory
# ---------------------------------------------------------------------------


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
# Pipeline stub helper for node_start / pipeline_state tests
# ---------------------------------------------------------------------------


def _build_pipeline_stub_instance(
    responses: list[AIMessage],
    *,
    validation_error: bool = False,
    max_queries: int = 3,
) -> AtlasTextToSQL:
    """Create an AtlasTextToSQL with a full pipeline stub graph.

    Unlike ``_build_stub_instance`` which has only 2 nodes (agent +
    format_results), this graph has all PIPELINE_SEQUENCE nodes so that
    the streaming logic can emit ``node_start`` / ``pipeline_state``
    events for each pipeline step.

    Args:
        responses: Scripted AIMessage responses for the agent.
        validation_error: If True, validate_sql sets last_error to skip execute_sql.
        max_queries: Max queries before routing to max_queries_exceeded.
    """
    from langchain_core.tools import tool
    from src.sql_pipeline import QueryToolInput

    @tool("query_tool", args_schema=QueryToolInput)
    def dummy_tool(question: str, context: str = "") -> str:
        """A trade data query tool."""
        return "stub result"

    model = FakeToolCallingModel(responses=responses)

    async def agent_node(state: AtlasAgentState) -> dict:
        model_with_tools = model.bind_tools([dummy_tool])
        return {"messages": [await model_with_tools.ainvoke(state["messages"])]}

    async def extract_tool_question(state: AtlasAgentState) -> dict:
        last_msg = state["messages"][-1]
        question = last_msg.tool_calls[0]["args"]["question"]
        return {"pipeline_question": question}

    async def extract_products(state: AtlasAgentState) -> dict:
        return {
            "pipeline_products": SchemasAndProductsFound(
                classification_schemas=["hs92"],
                products=[
                    ProductDetails(
                        name="coffee", classification_schema="hs92", codes=["0901"]
                    )
                ],
                requires_product_lookup=True,
            )
        }

    async def lookup_codes(state: AtlasAgentState) -> dict:
        return {"pipeline_codes": "- coffee (Schema: hs92): 0901"}

    async def get_table_info(state: AtlasAgentState) -> dict:
        return {"pipeline_table_info": "Table: hs92.country_product_year_4"}

    async def generate_sql(state: AtlasAgentState) -> dict:
        return {"pipeline_sql": "SELECT * FROM hs92.country_product_year_4 LIMIT 5"}

    async def validate_sql(state: AtlasAgentState) -> dict:
        if validation_error:
            return {
                "pipeline_sql": state.get("pipeline_sql", ""),
                "pipeline_result": "",
                "last_error": "SQL validation failed: unknown table",
            }
        return {"pipeline_sql": state.get("pipeline_sql", ""), "last_error": ""}

    async def execute_sql(state: AtlasAgentState) -> dict:
        return {
            "pipeline_result": "{'country': 'USA', 'value': 1000}\n{'country': 'CHN', 'value': 800}",
            "pipeline_result_columns": ["country", "value"],
            "pipeline_result_rows": [["USA", 1000], ["CHN", 800]],
            "pipeline_execution_time_ms": 42,
            "last_error": "",
        }

    async def format_results(state: AtlasAgentState) -> dict:
        last_msg = state["messages"][-1]
        tool_calls = last_msg.tool_calls
        tc = tool_calls[0]

        if state.get("last_error"):
            content = f"Error: {state['last_error']}"
        else:
            content = state.get("pipeline_result", "No results.")

        messages = [ToolMessage(content=content, tool_call_id=tc["id"])]
        for extra_tc in tool_calls[1:]:
            messages.append(
                ToolMessage(
                    content="Only one query at a time.",
                    tool_call_id=extra_tc["id"],
                )
            )
        return {
            "messages": messages,
            "queries_executed": state.get("queries_executed", 0) + 1,
        }

    async def max_queries_exceeded(state: AtlasAgentState) -> dict:
        last_msg = state["messages"][-1]
        return {
            "messages": [
                ToolMessage(
                    content="Max queries exceeded.",
                    tool_call_id=tc["id"],
                )
                for tc in last_msg.tool_calls
            ]
        }

    def route_after_agent(state: AtlasAgentState) -> str:
        last_msg = state["messages"][-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            if state.get("queries_executed", 0) >= max_queries:
                return "max_queries_exceeded"
            return "extract_tool_question"
        return END

    def route_after_validation(state: AtlasAgentState) -> str:
        if state.get("last_error"):
            return "format_results"
        return "execute_sql"

    builder = StateGraph(AtlasAgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("extract_tool_question", extract_tool_question)
    builder.add_node("extract_products", extract_products)
    builder.add_node("lookup_codes", lookup_codes)
    builder.add_node("get_table_info", get_table_info)
    builder.add_node("generate_sql", generate_sql)
    builder.add_node("validate_sql", validate_sql)
    builder.add_node("execute_sql", execute_sql)
    builder.add_node("format_results", format_results)
    builder.add_node("max_queries_exceeded", max_queries_exceeded)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent)
    builder.add_edge("extract_tool_question", "extract_products")
    builder.add_edge("extract_products", "lookup_codes")
    builder.add_edge("lookup_codes", "get_table_info")
    builder.add_edge("get_table_info", "generate_sql")
    builder.add_edge("generate_sql", "validate_sql")
    builder.add_conditional_edges("validate_sql", route_after_validation)
    builder.add_edge("execute_sql", "format_results")
    builder.add_edge("format_results", "agent")
    builder.add_edge("max_queries_exceeded", "agent")

    instance = AtlasTextToSQL.__new__(AtlasTextToSQL)
    instance.agent = builder.compile(checkpointer=MemorySaver())
    return instance


# ---------------------------------------------------------------------------
# Tests -- node_start events
# ---------------------------------------------------------------------------


class TestNodeStartEvents:
    """Tests for node_start event emission during pipeline execution."""

    async def test_tool_call_triggers_node_start_events(self):
        """When the agent calls a tool, node_start events should be emitted."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "US exports", "c1")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        config = {"configurable": {"thread_id": "ns-1"}}

        node_starts = []
        async for _mode, data in instance.astream_agent_response("US exports?", config):
            if data.message_type == "node_start":
                node_starts.append(data)

        assert len(node_starts) >= 1
        assert node_starts[0].payload["node"] == "extract_tool_question"

    async def test_node_start_has_required_payload_fields(self):
        """Every node_start event must have node, label, query_index."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "exports", "c1")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        config = {"configurable": {"thread_id": "ns-2"}}

        async for _mode, data in instance.astream_agent_response("exports?", config):
            if data.message_type == "node_start":
                assert "node" in data.payload
                assert "label" in data.payload
                assert "query_index" in data.payload
                assert data.payload["query_index"] >= 1
                assert isinstance(data.payload["label"], str)
                assert len(data.payload["label"]) > 0

    async def test_no_node_start_for_direct_answer(self):
        """When the agent responds without tool calls, no node_start events."""
        responses = [AIMessage(content="Direct answer.")]
        instance = _build_pipeline_stub_instance(responses)
        config = {"configurable": {"thread_id": "ns-3"}}

        node_starts = []
        async for _mode, data in instance.astream_agent_response("hello", config):
            if data.message_type == "node_start":
                node_starts.append(data)

        assert len(node_starts) == 0

    async def test_multiple_queries_increment_query_index(self):
        """Two tool calls in the same turn should have query_index 1 and 2."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "Q1", "c1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "Q2", "c2")],
            ),
            AIMessage(content="Both done."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        config = {"configurable": {"thread_id": "ns-4"}}

        node_starts = []
        async for _mode, data in instance.astream_agent_response("both?", config):
            if data.message_type == "node_start":
                node_starts.append(data)

        q1_indices = {
            ns.payload["query_index"]
            for ns in node_starts
            if ns.payload["query_index"] == 1
        }
        q2_indices = {
            ns.payload["query_index"]
            for ns in node_starts
            if ns.payload["query_index"] == 2
        }
        assert len(q1_indices) == 1, "Expected query_index=1 in first pipeline cycle"
        assert len(q2_indices) == 1, "Expected query_index=2 in second pipeline cycle"


# ---------------------------------------------------------------------------
# Tests -- pipeline_state events
# ---------------------------------------------------------------------------


class TestPipelineStateEvents:
    """Tests for pipeline_state event emission with structured data."""

    async def test_pipeline_state_emitted_for_completed_nodes(self):
        """At least one pipeline_state event should be emitted."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "exports", "c1")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        config = {"configurable": {"thread_id": "ps-1"}}

        pipeline_states = []
        async for _mode, data in instance.astream_agent_response("exports?", config):
            if data.message_type == "pipeline_state":
                pipeline_states.append(data)

        assert len(pipeline_states) >= 1
        for ps in pipeline_states:
            assert "stage" in ps.payload

    async def test_pipeline_state_extract_products_payload(self):
        """pipeline_state for extract_products has schemas and products."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "coffee exports", "c1")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        config = {"configurable": {"thread_id": "ps-2"}}

        extract_products_states = []
        async for _mode, data in instance.astream_agent_response("coffee?", config):
            if (
                data.message_type == "pipeline_state"
                and data.payload.get("stage") == "extract_products"
            ):
                extract_products_states.append(data)

        assert len(extract_products_states) == 1
        payload = extract_products_states[0].payload
        assert "schemas" in payload
        assert "products" in payload

    async def test_pipeline_state_execute_sql_payload(self):
        """pipeline_state for execute_sql has columns, rows, row_count, execution_time_ms."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "trade data", "c1")],
            ),
            AIMessage(content="Done."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        config = {"configurable": {"thread_id": "ps-3"}}

        execute_sql_states = []
        async for _mode, data in instance.astream_agent_response("data?", config):
            if (
                data.message_type == "pipeline_state"
                and data.payload.get("stage") == "execute_sql"
            ):
                execute_sql_states.append(data)

        assert len(execute_sql_states) == 1
        payload = execute_sql_states[0].payload
        assert "columns" in payload
        assert "rows" in payload
        assert "row_count" in payload
        assert "execution_time_ms" in payload
        assert "tables" in payload
        assert isinstance(payload["tables"], list)
        assert "hs92.country_product_year_4" in payload["tables"]

    async def test_no_pipeline_state_for_direct_answer(self):
        """No pipeline_state events when agent answers directly."""
        responses = [AIMessage(content="Just a direct answer.")]
        instance = _build_pipeline_stub_instance(responses)
        config = {"configurable": {"thread_id": "ps-4"}}

        pipeline_states = []
        async for _mode, data in instance.astream_agent_response("hello", config):
            if data.message_type == "pipeline_state":
                pipeline_states.append(data)

        assert len(pipeline_states) == 0


# ---------------------------------------------------------------------------
# Tests -- validation failure routing
# ---------------------------------------------------------------------------


class TestValidationFailureRouting:
    """Tests for correct node_start routing when validation fails."""

    async def test_validation_error_skips_execute_sql_node_start(self):
        """When validate_sql fails, execute_sql should NOT appear in node_starts."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "bad query", "c1")],
            ),
            AIMessage(content="Error reported."),
        ]
        instance = _build_pipeline_stub_instance(responses, validation_error=True)
        config = {"configurable": {"thread_id": "vf-1"}}

        node_start_names = []
        async for _mode, data in instance.astream_agent_response("bad?", config):
            if data.message_type == "node_start":
                node_start_names.append(data.payload["node"])

        assert "execute_sql" not in node_start_names
        assert "format_results" in node_start_names


# ---------------------------------------------------------------------------
# Tests -- backward compatibility of new streaming
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Ensure existing event types still work alongside new ones."""

    async def test_existing_event_types_still_present(self):
        """agent_talk and tool_output should still appear in the stream."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "exports", "c1")],
            ),
            AIMessage(content="Here are results."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        config = {"configurable": {"thread_id": "bc-1"}}

        message_types = set()
        async for _mode, data in instance.astream_agent_response("exports?", config):
            message_types.add(data.message_type)

        assert (
            "agent_talk" in message_types
        ), "Expected 'agent_talk' in stream but got: " + str(message_types)
        assert (
            "tool_output" in message_types
        ), "Expected 'tool_output' in stream but got: " + str(message_types)


# ---------------------------------------------------------------------------
# Tests -- no duplicate agent_talk events
# ---------------------------------------------------------------------------


class TestNoDuplicateAgentTalk:
    """Verify that agent_talk content is NOT emitted twice.

    LangGraph's dual stream modes ('messages' + 'updates') both produce
    agent_talk events for the same content. The streaming logic must
    deduplicate so the frontend doesn't see doubled text.
    """

    async def test_direct_answer_not_doubled(self):
        """A simple direct answer should yield agent_talk content exactly once."""
        responses = [AIMessage(content="Hello world!")]
        instance = _build_stub_instance(responses)
        config = {"configurable": {"thread_id": "dedup-1"}}

        agent_talks = []
        async for _mode, data in instance.astream_agent_response("hi", config):
            if data.message_type == "agent_talk":
                agent_talks.append(data)

        combined = "".join(d.content for d in agent_talks)
        # Content must appear exactly once, not doubled
        assert (
            combined.count("Hello world!") == 1
        ), f"Expected 'Hello world!' exactly once but got: {combined!r}"

    async def test_post_tool_answer_not_doubled(self):
        """After a tool call round-trip, the final answer should not be doubled."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "exports", "c1")],
            ),
            AIMessage(content="Here are the results."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        config = {"configurable": {"thread_id": "dedup-2"}}

        agent_talks = []
        async for _mode, data in instance.astream_agent_response("exports?", config):
            if data.message_type == "agent_talk":
                agent_talks.append(data)

        combined = "".join(d.content for d in agent_talks)
        assert (
            combined.count("Here are the results.") == 1
        ), f"Expected answer exactly once but got: {combined!r}"

    async def test_aanswer_question_stream_not_doubled(self):
        """High-level streaming API should also not duplicate agent_talk."""
        responses = [AIMessage(content="No duplication please.")]
        instance = _build_stub_instance(responses)

        agent_talks = []
        async for data in instance.aanswer_question_stream("test", thread_id="dedup-3"):
            if data.message_type == "agent_talk":
                agent_talks.append(data)

        combined = "".join(d.content for d in agent_talks)
        assert (
            combined.count("No duplication please.") == 1
        ), f"Expected content exactly once but got: {combined!r}"


# ---------------------------------------------------------------------------
# Tests -- turn summary persistence
# ---------------------------------------------------------------------------


class TestTurnSummaryPersistence:
    """Tests for persisting turn_summaries to the LangGraph checkpoint."""

    async def test_aanswer_question_persists_turn_summary(self):
        """After aanswer_question with a tool call, checkpoint state should
        have turn_summaries with 1 entry containing query data."""
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "US exports", "c1")],
            ),
            AIMessage(content="The US exported goods."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        thread_id = "ts-1"
        await instance.aanswer_question("US exports?", thread_id=thread_id)

        # Retrieve the checkpoint state
        config = {"configurable": {"thread_id": thread_id}}
        state = await instance.agent.aget_state(config)
        summaries = state.values.get("turn_summaries", [])

        assert len(summaries) == 1
        summary = summaries[0]
        assert "queries" in summary
        assert "entities" in summary
        assert "total_rows" in summary
        assert "total_execution_time_ms" in summary
        assert len(summary["queries"]) >= 1
        assert summary["total_rows"] > 0

    async def test_direct_answer_persists_empty_summary(self):
        """Direct answer (no tool call) should persist a summary with empty queries."""
        responses = [AIMessage(content="Just a greeting.")]
        instance = _build_pipeline_stub_instance(responses)
        thread_id = "ts-2"
        await instance.aanswer_question("Hello!", thread_id=thread_id)

        config = {"configurable": {"thread_id": thread_id}}
        state = await instance.agent.aget_state(config)
        summaries = state.values.get("turn_summaries", [])

        assert len(summaries) == 1
        summary = summaries[0]
        assert summary["queries"] == []
        assert summary["total_rows"] == 0
        assert summary["total_execution_time_ms"] == 0

    async def test_multi_turn_accumulates_summaries(self):
        """Two turns on the same thread should produce 2 summaries in state."""
        responses = [
            # Turn 1: tool call
            AIMessage(
                content="",
                tool_calls=[_tool_call("query_tool", "coffee exports", "c1")],
            ),
            AIMessage(content="Brazil leads in coffee."),
            # Turn 2: direct answer
            AIMessage(content="As I said, Brazil."),
        ]
        instance = _build_pipeline_stub_instance(responses)
        thread_id = "ts-3"

        await instance.aanswer_question("Coffee exports?", thread_id=thread_id)
        await instance.aanswer_question("Remind me?", thread_id=thread_id)

        config = {"configurable": {"thread_id": thread_id}}
        state = await instance.agent.aget_state(config)
        summaries = state.values.get("turn_summaries", [])

        assert len(summaries) == 2
        # First turn had a query
        assert len(summaries[0]["queries"]) >= 1
        # Second turn was direct
        assert summaries[1]["queries"] == []


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
