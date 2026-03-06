"""Tests for the SQL sub-agent (sql_subagent.py).

Tests the sub-agent's internal routing, tool nodes, wrapper, and end-to-end flows
using mocked LLMs and databases.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.sql_subagent import (
    MAX_ITERATIONS,
    RESULT_DISPLAY_ROWS,
    RESULT_TRUNCATION_THRESHOLD,
    _build_initial_message,
    _explore_schema_sync,
    _extract_table_name,
    _format_result_rows,
    build_sql_subagent,
    execute_sql_tool_node,
    lookup_products_node,
    reasoning_node,
    route_after_reasoning,
    sql_query_agent_node,
)
from src.tests.fake_model import FakeToolCallingModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_call(name: str, args: dict, call_id: str = "tc-1") -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _base_subagent_state(**overrides) -> dict:
    state = {
        "question": "What did Brazil export in 2022?",
        "context": "",
        "products": None,
        "codes": "",
        "table_info": "CREATE TABLE hs12.country_year (iso3_code TEXT, year INT, export_value NUMERIC);",
        "override_direction": None,
        "override_mode": None,
        "messages": [],
        "sql": "",
        "result": "",
        "result_columns": [],
        "result_rows": [],
        "execution_time_ms": 0,
        "last_error": "",
        "iteration_count": 0,
        "attempt_history": [],
        "_top_k": 15,
    }
    state.update(overrides)
    return state


def _base_parent_state(**overrides) -> dict:
    state = {
        "messages": [
            HumanMessage(content="What did Brazil export?"),
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call("query_tool", {"question": "What did Brazil export?"})
                ],
            ),
        ],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        "pipeline_question": "What did Brazil export?",
        "pipeline_context": "",
        "pipeline_products": None,
        "pipeline_codes": "",
        "pipeline_table_info": "DDL here",
        "pipeline_sql": "",
        "pipeline_result": "",
        "pipeline_result_columns": [],
        "pipeline_result_rows": [],
        "pipeline_execution_time_ms": 0,
        "turn_summaries": [],
        "token_usage": [],
        "step_timing": [],
        "pipeline_sql_history": [],
        "override_schema": None,
        "override_direction": None,
        "override_mode": None,
        "override_agent_mode": None,
        "graphql_question": "",
        "graphql_context": "",
        "graphql_classification": None,
        "graphql_entity_extraction": None,
        "graphql_resolved_params": None,
        "graphql_query": None,
        "graphql_api_target": None,
        "graphql_raw_response": None,
        "graphql_execution_time_ms": 0,
        "graphql_atlas_links": [],
        "sql_call_history": [],
        "graphql_call_history": [],
        "docs_question": "",
        "docs_context": "",
        "docs_selected_files": [],
        "docs_synthesis": "",
    }
    state.update(overrides)
    return state


def _mock_engine_with_results(columns, rows):
    """Create a mock sync engine that returns the given results."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_result = MagicMock()
    mock_result.returns_rows = True
    mock_result.keys.return_value = columns
    mock_result.fetchall.return_value = rows
    mock_conn.execute.return_value = mock_result
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_engine.connect.return_value = mock_conn
    return mock_engine


def _mock_engine_fail_then_succeed(error_msg, columns, rows):
    """Mock engine: first execute() raises, second returns results."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception(error_msg)
        result = MagicMock()
        result.returns_rows = True
        result.keys.return_value = columns
        result.fetchall.return_value = rows
        return result

    mock_conn.execute.side_effect = _side_effect
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_engine.connect.return_value = mock_conn
    return mock_engine


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------


class TestRouteAfterReasoning:
    def test_no_tool_calls_routes_to_end(self):
        state = _base_subagent_state(
            messages=[AIMessage(content="Done, query succeeded.")]
        )
        assert route_after_reasoning(state) == "__end__"

    def test_execute_sql_tool_call_routes_correctly(self):
        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[_tool_call("execute_sql", {"sql": "SELECT 1"})],
                )
            ]
        )
        assert route_after_reasoning(state) == "execute_sql"

    def test_explore_schema_tool_call_routes_correctly(self):
        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("explore_schema", {"query": "List tables in hs92"})
                    ],
                )
            ]
        )
        assert route_after_reasoning(state) == "explore_schema"

    def test_lookup_products_tool_call_routes_correctly(self):
        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("lookup_products", {"instruction": "Try SITC"})
                    ],
                )
            ]
        )
        assert route_after_reasoning(state) == "lookup_products"


# ---------------------------------------------------------------------------
# Reasoning node tests
# ---------------------------------------------------------------------------


class TestReasoningNodeParallelToolCalls:
    async def test_bind_tools_disables_parallel_tool_calls(self):
        """reasoning_node passes parallel_tool_calls=False to bind_tools."""
        model = FakeToolCallingModel(responses=[AIMessage(content="thinking...")])
        captured_kwargs: list[dict] = []
        original_bind = FakeToolCallingModel.bind_tools

        def spy_bind(self_inner, tools, **kwargs):
            captured_kwargs.append(kwargs)
            return original_bind(self_inner, tools, **kwargs)

        state = _base_subagent_state(
            iteration_count=0,
            messages=[HumanMessage(content="Write SQL")],
        )
        with patch.object(FakeToolCallingModel, "bind_tools", spy_bind):
            await reasoning_node(state, llm=model)
        assert len(captured_kwargs) == 1
        assert captured_kwargs[0].get("parallel_tool_calls") is False


class TestReasoningNode:
    async def test_max_iterations_enforced(self):
        state = _base_subagent_state(iteration_count=MAX_ITERATIONS)
        result = await reasoning_node(state, llm=FakeToolCallingModel(responses=[]))
        # Should return a message about reaching max attempts, not call LLM
        assert len(result["messages"]) == 1
        assert "maximum attempts" in result["messages"][0].content.lower()

    async def test_increments_iteration_count(self):
        model = FakeToolCallingModel(
            responses=[AIMessage(content="I'll write SQL now.")]
        )
        state = _base_subagent_state(
            iteration_count=0,
            messages=[HumanMessage(content="Write SQL")],
        )
        result = await reasoning_node(state, llm=model)
        assert result["iteration_count"] == 1


# ---------------------------------------------------------------------------
# execute_sql tool node tests
# ---------------------------------------------------------------------------


class TestExecuteSqlToolNode:
    async def test_validation_error_returns_error_message(self):
        """Invalid SQL → returns validation error, no DB execution."""
        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[_tool_call("execute_sql", {"sql": "DROP TABLE foo"})],
                )
            ]
        )
        # Create a mock engine that should NOT be called
        mock_engine = MagicMock()
        result = await execute_sql_tool_node(state, async_engine=mock_engine)

        assert len(result["messages"]) == 1
        msg = result["messages"][0]
        assert isinstance(msg, ToolMessage)
        assert (
            "validation" in msg.content.lower()
            or "write operations" in msg.content.lower()
        )
        assert result["last_error"]  # Should have error set
        mock_engine.connect.assert_not_called()

    async def test_execution_error_returns_error_message(self):
        """Valid SQL but DB throws error → returns execution error."""
        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "execute_sql",
                            {"sql": "SELECT * FROM nonexistent_table"},
                        )
                    ],
                )
            ]
        )
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("relation does not exist")
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn

        result = await execute_sql_tool_node(state, async_engine=mock_engine)

        msg = result["messages"][0]
        assert isinstance(msg, ToolMessage)
        assert "error" in msg.content.lower()
        assert result["last_error"]

    async def test_happy_path_returns_results(self):
        """Valid SQL, successful execution → returns formatted results."""
        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "execute_sql",
                            {
                                "sql": "SELECT iso3_code, export_value FROM hs12.country_year WHERE year = 2022 LIMIT 2"
                            },
                        )
                    ],
                )
            ]
        )
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.keys.return_value = ["iso3_code", "export_value"]
        mock_result.fetchall.return_value = [("BRA", 100000), ("USA", 200000)]
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn

        result = await execute_sql_tool_node(state, async_engine=mock_engine)

        msg = result["messages"][0]
        assert isinstance(msg, ToolMessage)
        assert "Success" in msg.content
        assert "2 rows" in msg.content
        assert result["sql"]
        assert result["result_columns"] == ["iso3_code", "export_value"]
        assert len(result["result_rows"]) == 2
        assert result["last_error"] == ""

    async def test_zero_rows_hint(self):
        """0-row result includes diagnostic hint."""
        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "execute_sql",
                            {
                                "sql": "SELECT iso3_code FROM hs12.country_year WHERE iso3_code = 'ZZZ'"
                            },
                        )
                    ],
                )
            ]
        )
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.keys.return_value = ["iso3_code"]
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn

        result = await execute_sql_tool_node(state, async_engine=mock_engine)

        msg = result["messages"][0]
        assert "0 rows" in msg.content
        assert "hint" in msg.content.lower() or "product codes" in msg.content.lower()

    async def test_result_truncation(self):
        """Results exceeding RESULT_TRUNCATION_THRESHOLD are truncated."""
        # Build >50 rows
        many_rows = [(f"C{i:03d}", i * 1000) for i in range(60)]

        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "execute_sql",
                            {
                                "sql": "SELECT iso3_code, export_value FROM hs12.country_year"
                            },
                        )
                    ],
                )
            ]
        )
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.keys.return_value = ["iso3_code", "export_value"]
        mock_result.fetchall.return_value = many_rows
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn

        result = await execute_sql_tool_node(state, async_engine=mock_engine)

        msg = result["messages"][0]
        assert "60 rows" in msg.content
        assert f"showing first {RESULT_DISPLAY_ROWS}" in msg.content.lower()
        # Full rows should still be stored in state
        assert len(result["result_rows"]) == 60

    async def test_exactly_threshold_rows_not_truncated(self):
        """Boundary: exactly RESULT_TRUNCATION_THRESHOLD rows should NOT be truncated."""
        exact_rows = [
            (f"C{i:03d}", i * 1000) for i in range(RESULT_TRUNCATION_THRESHOLD)
        ]

        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "execute_sql",
                            {"sql": "SELECT a, b FROM t"},
                        )
                    ],
                )
            ]
        )
        mock_engine = _mock_engine_with_results(["a", "b"], exact_rows)
        result = await execute_sql_tool_node(state, async_engine=mock_engine)

        msg = result["messages"][0]
        assert f"{RESULT_TRUNCATION_THRESHOLD} rows returned" in msg.content
        # Must NOT be truncated — threshold is exclusive (> not >=)
        assert "showing first" not in msg.content.lower()
        assert len(result["result_rows"]) == RESULT_TRUNCATION_THRESHOLD


# ---------------------------------------------------------------------------
# Initial context message tests
# ---------------------------------------------------------------------------


class TestBuildInitialMessage:
    def test_basic_message(self):
        msg = _build_initial_message(
            question="What did Brazil export?",
            context="",
            codes="- coffee (hs12): 0901",
            table_info="CREATE TABLE ...",
            override_direction=None,
            override_mode=None,
            example_queries=[],
        )
        assert "What did Brazil export?" in msg.content
        assert "coffee" in msg.content
        assert "CREATE TABLE" in msg.content

    def test_includes_overrides(self):
        msg = _build_initial_message(
            question="Q",
            context="",
            codes="",
            table_info="DDL",
            override_direction="exports",
            override_mode="goods",
            example_queries=[],
        )
        assert "exports" in msg.content
        assert "goods" in msg.content

    def test_includes_examples(self):
        msg = _build_initial_message(
            question="Q",
            context="",
            codes="",
            table_info="DDL",
            override_direction=None,
            override_mode=None,
            example_queries=[{"question": "Example Q", "query": "SELECT 1"}],
        )
        assert "Example Q" in msg.content
        assert "SELECT 1" in msg.content


# ---------------------------------------------------------------------------
# Wrapper node tests
# ---------------------------------------------------------------------------


class TestSqlQueryAgentNode:
    async def test_wrapper_maps_state_correctly(self):
        """Sub-agent result → correct parent state fields."""
        mock_subagent = AsyncMock()
        mock_subagent.ainvoke.return_value = {
            "sql": "SELECT 1",
            "result": "1",
            "result_columns": ["one"],
            "result_rows": [[1]],
            "execution_time_ms": 42,
            "last_error": "",
            "attempt_history": [
                {"sql": "SELECT 1", "stage": "executed", "errors": None}
            ],
            "messages": [],
        }

        state = _base_parent_state()
        result = await sql_query_agent_node(
            state,
            subagent=mock_subagent,
            top_k=15,
            example_queries=[],
        )

        assert result["pipeline_sql"] == "SELECT 1"
        assert result["pipeline_result"] == "1"
        assert result["pipeline_result_columns"] == ["one"]
        assert result["pipeline_result_rows"] == [[1]]
        assert result["pipeline_execution_time_ms"] == 42
        assert result["last_error"] == ""
        assert result["retry_count"] == 0
        assert len(result["pipeline_sql_history"]) == 1
        assert result["step_timing"]  # Should have timing record

    async def test_wrapper_propagates_error(self):
        """Sub-agent error → last_error set in parent state."""
        mock_subagent = AsyncMock()
        mock_subagent.ainvoke.return_value = {
            "sql": "SELECT bad",
            "result": "",
            "result_columns": [],
            "result_rows": [],
            "execution_time_ms": 0,
            "last_error": "column 'bad' does not exist",
            "attempt_history": [],
            "messages": [],
        }

        state = _base_parent_state()
        result = await sql_query_agent_node(
            state,
            subagent=mock_subagent,
            top_k=15,
            example_queries=[],
        )

        assert result["last_error"] == "column 'bad' does not exist"
        assert result["pipeline_result"] == ""


# ---------------------------------------------------------------------------
# Format result rows helper
# ---------------------------------------------------------------------------


class TestFormatResultRows:
    def test_formats_with_header(self):
        result = _format_result_rows(["a", "b"], [[1, 2], [3, 4]])
        assert "a | b" in result
        assert "1 | 2" in result
        assert "3 | 4" in result

    def test_empty_rows(self):
        assert _format_result_rows(["a"], []) == ""


# ---------------------------------------------------------------------------
# Schema exploration tests
# ---------------------------------------------------------------------------


class TestExtractTableName:
    """Tests _extract_table_name regex — the security boundary for explore_schema."""

    def test_matches_known_schema_dot_table(self):
        assert _extract_table_name("show hs92.country_year") == "hs92.country_year"
        assert (
            _extract_table_name("from sitc.country_product_year_4")
            == "sitc.country_product_year_4"
        )
        assert (
            _extract_table_name("services_bilateral.country_country_year")
            == "services_bilateral.country_country_year"
        )
        assert (
            _extract_table_name("columns in classification.location_country")
            == "classification.location_country"
        )

    def test_rejects_unknown_schemas(self):
        """Only allow known Atlas schemas — reject arbitrary schema.table patterns."""
        assert _extract_table_name("public.users") is None
        assert _extract_table_name("pg_catalog.pg_tables") is None
        assert _extract_table_name("information_schema.tables") is None

    def test_returns_none_for_no_table_reference(self):
        assert _extract_table_name("what schemas are available?") is None
        assert _extract_table_name("show me everything") is None


class TestExploreSchemaSync:
    """Tests _explore_schema_sync dispatch logic and output formatting."""

    def test_schema_listing_includes_all_schemas(self):
        result = _explore_schema_sync(
            "what schemas are available?", MagicMock(), MagicMock()
        )
        for schema in [
            "hs92",
            "hs12",
            "sitc",
            "services_unilateral",
            "services_bilateral",
        ]:
            assert schema in result, f"Missing schema: {schema}"

    def test_table_listing_queries_db_and_formats(self):
        db = MagicMock()
        engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(
            return_value=iter([("country_year",), ("country_product_year_4",)])
        )
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        engine.connect.return_value = mock_conn

        result = _explore_schema_sync("list tables in hs92", db, engine)
        assert "hs92.country_year" in result
        assert "hs92.country_product_year_4" in result

    def test_sample_rows_includes_header_and_data(self):
        db = MagicMock()
        engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.keys.return_value = ["iso3_code", "year"]
        mock_result.fetchall.return_value = [("BRA", 2022), ("USA", 2021)]
        mock_conn.execute.return_value = mock_result
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        engine.connect.return_value = mock_conn

        result = _explore_schema_sync("sample rows from hs92.country_year", db, engine)
        assert "iso3_code" in result
        assert "BRA" in result
        assert "2 rows" in result

    def test_ddl_lookup_delegates_to_db(self):
        db = MagicMock()
        db.get_table_info.return_value = (
            "CREATE TABLE hs92.country_year (iso3_code TEXT, year INT);"
        )
        engine = MagicMock()

        result = _explore_schema_sync("show columns in hs92.country_year", db, engine)
        assert "CREATE TABLE" in result
        db.get_table_info.assert_called_once_with(table_names=["hs92.country_year"])

    def test_unknown_query_returns_help_text(self):
        result = _explore_schema_sync(
            "tell me about the meaning of life", MagicMock(), MagicMock()
        )
        assert "Available schemas" in result
        assert "hs92" in result


# ---------------------------------------------------------------------------
# lookup_products node tests
# ---------------------------------------------------------------------------


class TestLookupProductsNode:
    async def test_returns_updated_state_fields(self):
        """lookup_products must return updated products, codes, and table_info."""
        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("lookup_products", {"instruction": "Try SITC"})
                    ],
                )
            ]
        )

        mock_products = MagicMock()
        mock_products.products = [MagicMock()]  # non-empty
        mock_products.classification_schemas = ["sitc"]
        mock_products.requires_group_tables = False

        mock_lookup = AsyncMock()
        mock_lookup.aextract_schemas_and_product_mentions_direct.return_value = (
            mock_products
        )
        mock_lookup.aget_candidate_codes.return_value = {"coffee": [("0901", "Coffee")]}
        mock_lookup.aselect_final_codes_direct.return_value = {"coffee": "0901"}

        with (
            patch("src.sql_subagent.ProductAndSchemaLookup", return_value=mock_lookup),
            patch(
                "src.sql_subagent.format_product_codes_for_prompt",
                return_value="- coffee (sitc): 0901",
            ),
            patch(
                "src.sql_subagent.get_table_info_for_schemas",
                return_value="CREATE TABLE sitc.country_product_year_4 (...);",
            ),
        ):
            result = await lookup_products_node(
                state,
                lightweight_llm=MagicMock(),
                engine=MagicMock(),
                db=MagicMock(),
                table_descriptions={},
            )

        # Contract: must return updated state fields
        assert result["products"] is mock_products
        assert result["codes"] == "- coffee (sitc): 0901"
        assert "sitc" in result["table_info"]
        # Must return a ToolMessage for the sub-agent conversation
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], ToolMessage)
        assert "sitc" in result["messages"][0].content

    async def test_error_returns_toolmessage_without_state_update(self):
        """When extraction fails, return error ToolMessage but don't update state fields."""
        state = _base_subagent_state(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("lookup_products", {"instruction": "Try SITC"})
                    ],
                )
            ]
        )

        with patch(
            "src.sql_subagent.ProductAndSchemaLookup",
            side_effect=Exception("extraction failed"),
        ):
            result = await lookup_products_node(
                state,
                lightweight_llm=MagicMock(),
                engine=MagicMock(),
                db=MagicMock(),
                table_descriptions={},
            )

        # Error ToolMessage returned
        assert isinstance(result["messages"][0], ToolMessage)
        assert "error" in result["messages"][0].content.lower()
        # State fields should NOT be present (no partial update)
        assert "products" not in result
        assert "codes" not in result


# ---------------------------------------------------------------------------
# Full sub-agent flow tests (end-to-end subgraph)
# ---------------------------------------------------------------------------


class TestFullSubagentFlow:
    """End-to-end tests that build and invoke the actual subgraph.

    These test the core value proposition: the sub-agent writes SQL,
    executes it via tools, and self-corrects on errors.
    """

    def _build_subagent(self, fake_model, mock_engine):
        subagent, _ = build_sql_subagent(
            llm=fake_model,
            lightweight_llm=fake_model,
            db=MagicMock(),
            engine=MagicMock(),
            table_descriptions={},
            async_engine=mock_engine,
        )
        return subagent

    def _subagent_input(self):
        return {
            "question": "What did Brazil export in 2022?",
            "context": "",
            "products": None,
            "codes": "",
            "table_info": "CREATE TABLE hs12.country_year (iso3_code TEXT, year INT, export_value NUMERIC);",
            "override_direction": None,
            "override_mode": None,
            "messages": [
                HumanMessage(
                    content=(
                        "Answer this question by writing a SQL query:\n\n"
                        "What did Brazil export in 2022?\n\n"
                        "Table schemas (DDL):\n"
                        "CREATE TABLE hs12.country_year (iso3_code TEXT, year INT, export_value NUMERIC);\n\n"
                        "Write a SQL query to answer the question, then call execute_sql to run it."
                    )
                )
            ],
            "sql": "",
            "result": "",
            "result_columns": [],
            "result_rows": [],
            "execution_time_ms": 0,
            "last_error": "",
            "iteration_count": 0,
            "attempt_history": [],
            "_top_k": 15,
        }

    async def test_happy_path_executes_and_stops(self):
        """Agent writes SQL → execute_sql succeeds → agent stops. No extra iterations."""
        fake_model = FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "execute_sql",
                            {
                                "sql": "SELECT iso3_code, export_value FROM hs12.country_year WHERE iso3_code = 'BRA' AND year = 2022"
                            },
                        )
                    ],
                ),
                AIMessage(content="Query succeeded."),
            ]
        )
        mock_engine = _mock_engine_with_results(
            ["iso3_code", "export_value"],
            [("BRA", 280000000000)],
        )

        subagent = self._build_subagent(fake_model, mock_engine)
        result = await subagent.ainvoke(
            self._subagent_input(), config={"recursion_limit": 25}
        )

        # Verify successful execution
        assert "BRA" in result["sql"] or "iso3_code" in result["sql"]
        assert result["result_columns"] == ["iso3_code", "export_value"]
        assert len(result["result_rows"]) == 1
        assert result["last_error"] == ""
        # Two reasoning calls: write SQL, then stop
        assert result["iteration_count"] == 2
        # One successful attempt
        assert len(result["attempt_history"]) == 1
        assert result["attempt_history"][0]["stage"] == "executed"

    async def test_recovery_corrects_after_execution_error(self):
        """First SQL fails (wrong column) → agent corrects → second SQL succeeds.

        This is the core value proposition of the agentic sub-agent vs. blind retry.
        """
        fake_model = FakeToolCallingModel(
            responses=[
                # Attempt 1: bad column name
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "execute_sql",
                            {
                                "sql": "SELECT export_value_usd FROM hs12.country_year WHERE iso3_code = 'BRA'"
                            },
                            "tc-1",
                        )
                    ],
                ),
                # Attempt 2: corrected
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "execute_sql",
                            {
                                "sql": "SELECT export_value FROM hs12.country_year WHERE iso3_code = 'BRA' AND year = 2022"
                            },
                            "tc-2",
                        )
                    ],
                ),
                # Stop
                AIMessage(content="Results look correct."),
            ]
        )
        mock_engine = _mock_engine_fail_then_succeed(
            error_msg="column 'export_value_usd' does not exist",
            columns=["export_value"],
            rows=[(280000000000,)],
        )

        subagent = self._build_subagent(fake_model, mock_engine)
        result = await subagent.ainvoke(
            self._subagent_input(), config={"recursion_limit": 25}
        )

        # Final state has CORRECTED SQL, not the bad one
        assert "export_value_usd" not in result["sql"]
        assert result["sql"] != ""
        assert result["result_columns"] == ["export_value"]
        assert len(result["result_rows"]) == 1
        assert result["last_error"] == ""
        # Three reasoning calls: attempt 1, correction, stop
        assert result["iteration_count"] == 3
        # Two attempts in history: one error, one success
        assert len(result["attempt_history"]) == 2
        assert result["attempt_history"][0]["stage"] == "execution_error"
        assert result["attempt_history"][1]["stage"] == "executed"

    async def test_max_iterations_terminates_gracefully(self):
        """Agent that always retries is stopped by MAX_ITERATIONS safety limit."""
        # Each response must be a unique AIMessage with unique tool_call_id,
        # otherwise add_messages deduplicates by message ID.
        fake_model = FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call(
                            "execute_sql",
                            {"sql": "SELECT bad_col FROM hs12.country_year"},
                            f"tc-{i}",
                        )
                    ],
                )
                for i in range(MAX_ITERATIONS + 2)
            ]
        )
        # Engine always errors
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("column 'bad_col' does not exist")
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn

        subagent = self._build_subagent(fake_model, mock_engine)
        result = await subagent.ainvoke(
            self._subagent_input(), config={"recursion_limit": 50}
        )

        # Must terminate, not loop forever
        assert result["iteration_count"] == MAX_ITERATIONS
        assert result["last_error"] != ""


# ---------------------------------------------------------------------------
# Integration test: real LLM + real DB
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestSubagentIntegration:
    """Integration test: build and invoke the SQL sub-agent with real LLM and DB.

    Requires: ATLAS_DB_URL env var (live Atlas database), LLM API keys.

    Run with::

        PYTHONPATH=$(pwd) uv run pytest \\
            src/tests/test_sql_subagent.py::TestSubagentIntegration -v -m integration
    """

    async def test_subagent_generates_and_executes_correct_sql(self):
        """The sub-agent should write valid SQL, execute it, and return rows.

        This is the core integration test for the agentic SQL sub-agent.
        It verifies:
        1. The frontier LLM generates syntactically correct SQL from the prompt
        2. The SQL executes against the real Atlas database
        3. Results are non-empty and structurally correct
        4. The sub-agent stops after getting results (no over-iteration)
        5. attempt_history records the execution
        """
        from pathlib import Path

        from sqlalchemy import create_engine
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import make_url

        from src.config import create_llm, get_settings
        from src.sql_multiple_schemas import SQLDatabaseWithSchemas
        from src.sql_pipeline import get_table_info_for_schemas, load_example_queries

        settings = get_settings()
        if not settings.atlas_db_url:
            pytest.skip("ATLAS_DB_URL not configured")

        # --- Set up real LLM + DB ---
        frontier_llm = create_llm(
            settings.frontier_model,
            settings.frontier_model_provider,
            temperature=0,
        )
        lightweight_llm = create_llm(
            settings.lightweight_model,
            settings.lightweight_model_provider,
            temperature=0,
        )

        engine = create_engine(
            settings.atlas_db_url,
            execution_options={"postgresql_readonly": True},
            connect_args={
                "connect_timeout": 10,
                "options": "-c statement_timeout=30000",
            },
        )
        async_url = make_url(settings.atlas_db_url).set(drivername="postgresql+psycopg")
        async_engine = create_async_engine(
            async_url,
            execution_options={"postgresql_readonly": True},
            connect_args={
                "connect_timeout": 10,
                "options": "-c statement_timeout=30000",
            },
        )

        db = SQLDatabaseWithSchemas(engine=engine)

        base_dir = Path(__file__).resolve().parents[2]
        table_descriptions_path = (
            base_dir / "src" / "schema" / "db_table_descriptions.json"
        )
        import json

        with open(table_descriptions_path) as f:
            table_descriptions = json.load(f)

        example_queries = load_example_queries(
            base_dir / "src" / "example_queries" / "queries.json",
            base_dir / "src" / "example_queries",
        )

        # --- Build sub-agent ---
        subagent, top_k = build_sql_subagent(
            llm=frontier_llm,
            lightweight_llm=lightweight_llm,
            db=db,
            engine=engine,
            table_descriptions=table_descriptions,
            async_engine=async_engine,
        )

        # --- Prepare input (simulating what sql_query_agent_node does) ---
        # Use a question with known answer: Brazil is a major exporter
        question = "What were the top 5 products exported by Brazil in 2020?"
        codes = ""  # No pre-extracted codes — let the sub-agent work with DDL alone
        table_info = get_table_info_for_schemas(
            db=db,
            table_descriptions=table_descriptions,
            classification_schemas=["hs12"],
            requires_group_tables=False,
        )

        initial_msg = _build_initial_message(
            question=question,
            context="",
            codes=codes,
            table_info=table_info,
            override_direction=None,
            override_mode=None,
            example_queries=example_queries,
        )

        sub_input = {
            "question": question,
            "context": "",
            "products": None,
            "codes": codes,
            "table_info": table_info,
            "override_direction": None,
            "override_mode": None,
            "messages": [initial_msg],
            "sql": "",
            "result": "",
            "result_columns": [],
            "result_rows": [],
            "execution_time_ms": 0,
            "last_error": "",
            "iteration_count": 0,
            "attempt_history": [],
            "_top_k": top_k,
        }

        # --- Invoke ---
        result = await subagent.ainvoke(sub_input, config={"recursion_limit": 25})

        # --- Assertions ---

        # 1. SQL was generated and stored
        assert result["sql"], "Sub-agent should have generated SQL"

        # 2. No error in final state
        assert (
            result["last_error"] == ""
        ), f"Sub-agent ended with error: {result['last_error']}"

        # 3. Got actual results with columns and rows
        assert len(result["result_columns"]) > 0, "Should have result columns"
        assert len(result["result_rows"]) > 0, (
            f"Should have returned rows for Brazil's top exports; "
            f"SQL was: {result['sql']}"
        )

        # 4. Sub-agent didn't over-iterate (most queries should succeed in 1-2 attempts)
        assert result["iteration_count"] <= 3, (
            f"Sub-agent took {result['iteration_count']} iterations — "
            f"expected ≤ 3 for a straightforward query"
        )

        # 5. attempt_history has at least one successful execution
        assert any(
            a["stage"] == "executed" for a in result["attempt_history"]
        ), f"No successful execution in attempt_history: {result['attempt_history']}"

        # 6. Execution time is plausible (> 0ms)
        assert result["execution_time_ms"] > 0

        # Cleanup
        engine.dispose()
        await async_engine.dispose()
