"""Comprehensive unit tests for each pipeline node function in src/generate_query.py.

Every test constructs its own AtlasAgentState dict and mocks all external
dependencies so that no LLM, database, or network access is required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from src.error_handling import QueryExecutionError
from src.generate_query import (
    execute_sql_node,
    extract_products_node,
    extract_tool_question,
    format_results_node,
    generate_sql_node,
    get_table_info_node,
    lookup_codes_node,
    max_queries_exceeded_node,
    validate_sql_node,
)
from src.product_and_schema_lookup import (
    ProductCodesMapping,
    ProductDetails,
    ProductSearchResult,
    SchemasAndProductsFound,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_call_message(
    question: str = "What did Brazil export in 2021?",
    tool_call_id: str = "call_abc123",
) -> AIMessage:
    """Create an AIMessage with a single tool_call for query_tool."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": tool_call_id,
                "name": "query_tool",
                "args": {"question": question},
            }
        ],
    )


def _make_multi_tool_call_message(
    questions: list[str] | None = None,
    tool_call_ids: list[str] | None = None,
) -> AIMessage:
    """Create an AIMessage with multiple parallel tool_calls."""
    questions = questions or [
        "What did Brazil export in 2021?",
        "What did Argentina export in 2021?",
    ]
    tool_call_ids = tool_call_ids or [f"call_{i}" for i in range(len(questions))]
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": tc_id,
                "name": "query_tool",
                "args": {"question": q},
            }
            for q, tc_id in zip(questions, tool_call_ids)
        ],
    )


def _base_state(**overrides) -> dict:
    """Return a minimal AtlasAgentState dict with sensible defaults.

    Callers can override any key via keyword arguments.
    """
    state: dict = {
        "messages": [_make_tool_call_message()],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        "pipeline_question": "",
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
# 1. extract_tool_question
# ---------------------------------------------------------------------------


class TestExtractToolQuestion:
    """Tests for extract_tool_question node."""

    async def test_extracts_question_from_tool_call(self):
        msg = _make_tool_call_message(question="Top exporters of cotton?")
        state = _base_state(messages=[msg])

        result = await extract_tool_question(state)

        assert result == {"pipeline_question": "Top exporters of cotton?"}

    async def test_uses_last_message(self):
        """When multiple messages exist, the node reads the *last* one."""
        earlier = AIMessage(content="Hello")
        tool_msg = _make_tool_call_message(question="Second question")
        state = _base_state(messages=[earlier, tool_msg])

        result = await extract_tool_question(state)

        assert result["pipeline_question"] == "Second question"

    async def test_preserves_unicode_question(self):
        msg = _make_tool_call_message(question="Exportaciones de cafe en 2021?")
        state = _base_state(messages=[msg])

        result = await extract_tool_question(state)

        assert result["pipeline_question"] == "Exportaciones de cafe en 2021?"

    async def test_empty_question_string(self):
        msg = _make_tool_call_message(question="")
        state = _base_state(messages=[msg])

        result = await extract_tool_question(state)

        assert result == {"pipeline_question": ""}

    async def test_extracts_first_question_from_parallel_tool_calls(self):
        """When the LLM emits multiple parallel tool_calls, only the first question is used."""
        msg = _make_multi_tool_call_message(
            questions=["First question?", "Second question?"],
            tool_call_ids=["call_a", "call_b"],
        )
        state = _base_state(messages=[msg])

        result = await extract_tool_question(state)

        assert result == {"pipeline_question": "First question?"}


# ---------------------------------------------------------------------------
# 2. extract_products_node
# ---------------------------------------------------------------------------


class TestExtractProductsNode:
    """Tests for extract_products_node node."""

    async def test_returns_schemas_and_products(self):
        canned = SchemasAndProductsFound(
            classification_schemas=["hs92"],
            products=[
                ProductDetails(
                    name="cotton",
                    classification_schema="hs92",
                    codes=["5201", "5202"],
                )
            ],
            requires_product_lookup=True,
        )

        mock_llm = MagicMock()
        mock_engine = MagicMock()

        with patch("src.generate_query.ProductAndSchemaLookup") as MockLookup:
            mock_instance = MagicMock()
            mock_instance.aextract_schemas_and_product_mentions_direct = AsyncMock(
                return_value=canned
            )
            MockLookup.return_value = mock_instance

            state = _base_state(pipeline_question="US exports of cotton?")
            result = await extract_products_node(
                state, llm=mock_llm, engine=mock_engine
            )

        assert result == {"pipeline_products": canned}
        MockLookup.assert_called_once_with(llm=mock_llm, connection=mock_engine)
        mock_instance.aextract_schemas_and_product_mentions_direct.assert_awaited_once_with(
            "US exports of cotton?"
        )

    async def test_no_products_found(self):
        canned = SchemasAndProductsFound(
            classification_schemas=["hs92"],
            products=[],
            requires_product_lookup=False,
        )

        mock_llm = MagicMock()
        mock_engine = MagicMock()

        with patch("src.generate_query.ProductAndSchemaLookup") as MockLookup:
            mock_instance = MagicMock()
            mock_instance.aextract_schemas_and_product_mentions_direct = AsyncMock(
                return_value=canned
            )
            MockLookup.return_value = mock_instance

            state = _base_state(pipeline_question="What is Brazil's ECI?")
            result = await extract_products_node(
                state, llm=mock_llm, engine=mock_engine
            )

        assert result["pipeline_products"].products == []
        assert result["pipeline_products"].classification_schemas == ["hs92"]

    async def test_multiple_schemas(self):
        canned = SchemasAndProductsFound(
            classification_schemas=["hs92", "services_bilateral"],
            products=[],
            requires_product_lookup=False,
        )

        mock_llm = MagicMock()
        mock_engine = MagicMock()

        with patch("src.generate_query.ProductAndSchemaLookup") as MockLookup:
            mock_instance = MagicMock()
            mock_instance.aextract_schemas_and_product_mentions_direct = AsyncMock(
                return_value=canned
            )
            MockLookup.return_value = mock_instance

            state = _base_state(
                pipeline_question="Goods and services trade between US and China?"
            )
            result = await extract_products_node(
                state, llm=mock_llm, engine=mock_engine
            )

        assert result["pipeline_products"].classification_schemas == [
            "hs92",
            "services_bilateral",
        ]


# ---------------------------------------------------------------------------
# 3. lookup_codes_node
# ---------------------------------------------------------------------------


class TestLookupCodesNode:
    """Tests for lookup_codes_node node."""

    async def test_returns_formatted_codes(self):
        products_found = SchemasAndProductsFound(
            classification_schemas=["hs92"],
            products=[
                ProductDetails(
                    name="wheat", classification_schema="hs92", codes=["1001"]
                )
            ],
            requires_product_lookup=True,
        )
        candidates = [
            ProductSearchResult(
                name="wheat",
                classification_schema="hs92",
                llm_suggestions=[{"product_code": "1001", "product_name": "Wheat"}],
                db_suggestions=[],
            )
        ]
        final_codes = ProductCodesMapping(
            mappings=[
                ProductDetails(
                    name="wheat", classification_schema="hs92", codes=["1001"]
                )
            ]
        )

        mock_llm = MagicMock()
        mock_engine = MagicMock()

        with patch("src.generate_query.ProductAndSchemaLookup") as MockLookup:
            mock_instance = MagicMock()
            mock_instance.get_candidate_codes.return_value = candidates
            mock_instance.aselect_final_codes_direct = AsyncMock(
                return_value=final_codes
            )
            MockLookup.return_value = mock_instance

            state = _base_state(
                pipeline_question="US wheat exports?",
                pipeline_products=products_found,
            )
            result = await lookup_codes_node(state, llm=mock_llm, engine=mock_engine)

        assert "pipeline_codes" in result
        assert "wheat" in result["pipeline_codes"]
        assert "1001" in result["pipeline_codes"]

    async def test_no_products_returns_empty_codes(self):
        """When pipeline_products has no products, codes should be empty."""
        products_found = SchemasAndProductsFound(
            classification_schemas=["hs92"],
            products=[],
            requires_product_lookup=False,
        )

        mock_llm = MagicMock()
        mock_engine = MagicMock()

        state = _base_state(
            pipeline_question="Brazil ECI?",
            pipeline_products=products_found,
        )
        result = await lookup_codes_node(state, llm=mock_llm, engine=mock_engine)

        assert result == {"pipeline_codes": ""}

    async def test_none_products_returns_empty_codes(self):
        """When pipeline_products is None, codes should be empty."""
        mock_llm = MagicMock()
        mock_engine = MagicMock()

        state = _base_state(pipeline_question="Hello", pipeline_products=None)
        result = await lookup_codes_node(state, llm=mock_llm, engine=mock_engine)

        assert result == {"pipeline_codes": ""}

    async def test_multiple_products(self):
        products_found = SchemasAndProductsFound(
            classification_schemas=["hs92"],
            products=[
                ProductDetails(
                    name="cotton", classification_schema="hs92", codes=["5201"]
                ),
                ProductDetails(
                    name="wheat", classification_schema="hs92", codes=["1001"]
                ),
            ],
            requires_product_lookup=True,
        )
        candidates = [
            ProductSearchResult(
                name="cotton",
                classification_schema="hs92",
                llm_suggestions=[{"product_code": "5201", "product_name": "Cotton"}],
                db_suggestions=[],
            ),
            ProductSearchResult(
                name="wheat",
                classification_schema="hs92",
                llm_suggestions=[{"product_code": "1001", "product_name": "Wheat"}],
                db_suggestions=[],
            ),
        ]
        final_codes = ProductCodesMapping(
            mappings=[
                ProductDetails(
                    name="cotton", classification_schema="hs92", codes=["5201"]
                ),
                ProductDetails(
                    name="wheat", classification_schema="hs92", codes=["1001"]
                ),
            ]
        )

        mock_llm = MagicMock()
        mock_engine = MagicMock()

        with patch("src.generate_query.ProductAndSchemaLookup") as MockLookup:
            mock_instance = MagicMock()
            mock_instance.get_candidate_codes.return_value = candidates
            mock_instance.aselect_final_codes_direct = AsyncMock(
                return_value=final_codes
            )
            MockLookup.return_value = mock_instance

            state = _base_state(
                pipeline_question="US cotton and wheat exports?",
                pipeline_products=products_found,
            )
            result = await lookup_codes_node(state, llm=mock_llm, engine=mock_engine)

        assert "cotton" in result["pipeline_codes"]
        assert "wheat" in result["pipeline_codes"]


# ---------------------------------------------------------------------------
# 4. get_table_info_node
# ---------------------------------------------------------------------------


class TestGetTableInfoNode:
    """Tests for get_table_info_node node."""

    async def test_returns_table_info_string(self):
        products_found = SchemasAndProductsFound(
            classification_schemas=["hs92"],
            products=[],
            requires_product_lookup=False,
        )
        mock_db = MagicMock()
        mock_table_desc = {
            "hs92": [{"table_name": "country_year", "context_str": "Year-level data"}]
        }

        with patch("src.generate_query.get_table_info_for_schemas") as mock_get:
            mock_get.return_value = (
                "Table: hs92.country_year\nDescription: Year-level data\n"
            )

            state = _base_state(pipeline_products=products_found)
            result = await get_table_info_node(
                state, db=mock_db, table_descriptions=mock_table_desc
            )

        assert "pipeline_table_info" in result
        assert "country_year" in result["pipeline_table_info"]
        mock_get.assert_called_once_with(
            db=mock_db,
            table_descriptions=mock_table_desc,
            classification_schemas=["hs92"],
        )

    async def test_no_products_passes_empty_schemas(self):
        """When pipeline_products is None, schemas list should be empty."""
        mock_db = MagicMock()
        mock_table_desc = {}

        with patch("src.generate_query.get_table_info_for_schemas") as mock_get:
            mock_get.return_value = ""

            state = _base_state(pipeline_products=None)
            result = await get_table_info_node(
                state, db=mock_db, table_descriptions=mock_table_desc
            )

        assert result == {"pipeline_table_info": ""}
        mock_get.assert_called_once_with(
            db=mock_db,
            table_descriptions=mock_table_desc,
            classification_schemas=[],
        )

    async def test_multiple_schemas(self):
        products_found = SchemasAndProductsFound(
            classification_schemas=["hs92", "services_bilateral"],
            products=[],
            requires_product_lookup=False,
        )
        mock_db = MagicMock()
        mock_table_desc = {}

        with patch("src.generate_query.get_table_info_for_schemas") as mock_get:
            mock_get.return_value = "table info for both schemas"

            state = _base_state(pipeline_products=products_found)
            result = await get_table_info_node(
                state, db=mock_db, table_descriptions=mock_table_desc
            )

        mock_get.assert_called_once_with(
            db=mock_db,
            table_descriptions=mock_table_desc,
            classification_schemas=["hs92", "services_bilateral"],
        )
        assert result["pipeline_table_info"] == "table info for both schemas"


# ---------------------------------------------------------------------------
# 5. generate_sql_node
# ---------------------------------------------------------------------------


class TestGenerateSqlNode:
    """Tests for generate_sql_node node."""

    async def test_generates_sql_query(self):
        mock_llm = MagicMock()

        with patch("src.generate_query.create_query_generation_chain") as mock_create:
            mock_chain = MagicMock()
            mock_chain.ainvoke = AsyncMock(
                return_value="SELECT * FROM hs92.country_year LIMIT 5"
            )
            mock_create.return_value = mock_chain

            state = _base_state(
                pipeline_question="Brazil exports?",
                pipeline_codes="",
                pipeline_table_info="Table: hs92.country_year",
            )
            result = await generate_sql_node(
                state, llm=mock_llm, example_queries=[], max_results=15
            )

        assert result == {"pipeline_sql": "SELECT * FROM hs92.country_year LIMIT 5"}
        mock_create.assert_called_once_with(
            llm=mock_llm,
            codes=None,
            top_k=15,
            table_info="Table: hs92.country_year",
            example_queries=[],
            direction_constraint=None,
            mode_constraint=None,
        )
        mock_chain.ainvoke.assert_awaited_once_with({"question": "Brazil exports?"})

    async def test_passes_codes_when_present(self):
        mock_llm = MagicMock()
        codes_str = "\n- wheat (Schema: hs92): 1001\n"

        with patch("src.generate_query.create_query_generation_chain") as mock_create:
            mock_chain = MagicMock()
            mock_chain.ainvoke = AsyncMock(
                return_value="SELECT * FROM hs92.country_product_year_4 WHERE product_code = '1001'"
            )
            mock_create.return_value = mock_chain

            state = _base_state(
                pipeline_question="US wheat exports?",
                pipeline_codes=codes_str,
                pipeline_table_info="some table info",
            )
            result = await generate_sql_node(
                state, llm=mock_llm, example_queries=[], max_results=10
            )

        # When codes is a non-empty string, it should be passed as-is (not None)
        mock_create.assert_called_once_with(
            llm=mock_llm,
            codes=codes_str,
            top_k=10,
            table_info="some table info",
            example_queries=[],
            direction_constraint=None,
            mode_constraint=None,
        )
        assert "pipeline_sql" in result

    async def test_empty_codes_passed_as_none(self):
        """An empty-string codes value should be converted to None."""
        mock_llm = MagicMock()

        with patch("src.generate_query.create_query_generation_chain") as mock_create:
            mock_chain = MagicMock()
            mock_chain.ainvoke = AsyncMock(return_value="SELECT 1")
            mock_create.return_value = mock_chain

            state = _base_state(
                pipeline_question="ECI of Brazil?",
                pipeline_codes="",
                pipeline_table_info="",
            )
            await generate_sql_node(
                state, llm=mock_llm, example_queries=[], max_results=15
            )

        # The node does `codes = state.get("pipeline_codes") or None`
        mock_create.assert_called_once_with(
            llm=mock_llm,
            codes=None,
            top_k=15,
            table_info="",
            example_queries=[],
            direction_constraint=None,
            mode_constraint=None,
        )

    async def test_example_queries_forwarded(self):
        mock_llm = MagicMock()
        examples = [{"question": "Top exporters?", "query": "SELECT country FROM ..."}]

        with patch("src.generate_query.create_query_generation_chain") as mock_create:
            mock_chain = MagicMock()
            mock_chain.ainvoke = AsyncMock(return_value="SELECT 1")
            mock_create.return_value = mock_chain

            state = _base_state(pipeline_question="?", pipeline_codes="")
            await generate_sql_node(
                state, llm=mock_llm, example_queries=examples, max_results=15
            )

        _, kwargs = mock_create.call_args
        assert kwargs["example_queries"] == examples


# ---------------------------------------------------------------------------
# 6. execute_sql_node
# ---------------------------------------------------------------------------


class TestExecuteSqlNode:
    """Tests for execute_sql_node node."""

    @staticmethod
    def _mock_engine(rows, columns, returns_rows=True):
        """Build a mock SQLAlchemy engine with a canned result set."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.returns_rows = returns_rows
        mock_result.keys.return_value = columns
        mock_result.fetchall.return_value = rows
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        return mock_engine

    async def test_successful_query_with_rows(self):
        engine = self._mock_engine(
            rows=[("USA", 1000), ("CHN", 800)],
            columns=["country", "value"],
        )
        state = _base_state(
            pipeline_sql="SELECT country, value FROM hs92.country_year LIMIT 2"
        )

        with patch(
            "src.generate_query.execute_with_retry",
            side_effect=lambda fn, *a, **kw: fn(),
        ):
            result = await execute_sql_node(state, async_engine=engine)

        assert result["last_error"] == ""
        assert "USA" in result["pipeline_result"]
        assert "CHN" in result["pipeline_result"]
        assert "1000" in result["pipeline_result"]

    async def test_query_returns_no_rows(self):
        engine = self._mock_engine(rows=[], columns=["country", "value"])
        state = _base_state(pipeline_sql="SELECT * FROM hs92.country_year WHERE 1=0")

        with patch(
            "src.generate_query.execute_with_retry",
            side_effect=lambda fn, *a, **kw: fn(),
        ):
            result = await execute_sql_node(state, async_engine=engine)

        assert result["pipeline_result"] == "SQL query returned no results."
        assert result["last_error"] == ""

    async def test_non_returning_statement(self):
        """A statement that does not return rows (e.g., DDL) should return empty."""
        engine = self._mock_engine(rows=[], columns=[], returns_rows=False)
        state = _base_state(pipeline_sql="CREATE TABLE tmp (id int)")

        with patch(
            "src.generate_query.execute_with_retry",
            side_effect=lambda fn, *a, **kw: fn(),
        ):
            result = await execute_sql_node(state, async_engine=engine)

        assert result["pipeline_result"] == "SQL query returned no results."
        assert result["last_error"] == ""

    async def test_query_execution_error(self):
        """QueryExecutionError should be caught and stored in last_error."""
        mock_engine = MagicMock()
        state = _base_state(pipeline_sql="SELECT bad syntax")

        with patch(
            "src.generate_query.execute_with_retry",
            side_effect=QueryExecutionError("syntax error at position 7"),
        ):
            result = await execute_sql_node(state, async_engine=mock_engine)

        assert result["pipeline_result"] == ""
        assert "syntax error" in result["last_error"]

    async def test_unexpected_exception(self):
        """Generic exceptions should also be caught."""
        mock_engine = MagicMock()
        state = _base_state(pipeline_sql="SELECT 1")

        with patch(
            "src.generate_query.execute_with_retry",
            side_effect=RuntimeError("connection lost"),
        ):
            result = await execute_sql_node(state, async_engine=mock_engine)

        assert result["pipeline_result"] == ""
        assert "connection lost" in result["last_error"]

    async def test_result_format_is_dict_per_row(self):
        """Each row should be formatted as a dict string."""
        engine = self._mock_engine(
            rows=[("BRA", 500)],
            columns=["iso3_code", "export_value"],
        )
        state = _base_state(pipeline_sql="SELECT iso3_code, export_value FROM t")

        with patch(
            "src.generate_query.execute_with_retry",
            side_effect=lambda fn, *a, **kw: fn(),
        ):
            result = await execute_sql_node(state, async_engine=engine)

        # The formatting is str(dict(zip(columns, row)))
        assert "'iso3_code': 'BRA'" in result["pipeline_result"]
        assert "'export_value': 500" in result["pipeline_result"]


# ---------------------------------------------------------------------------
# 6b. execute_sql_node — structured data
# ---------------------------------------------------------------------------


class TestExecuteSqlNodeStructuredData:
    """Tests for structured columns/rows/timing returned by execute_sql_node."""

    @staticmethod
    def _mock_engine(rows, columns, returns_rows=True):
        """Build a mock SQLAlchemy engine with a canned result set."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.returns_rows = returns_rows
        mock_result.keys.return_value = columns
        mock_result.fetchall.return_value = rows
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        return mock_engine

    async def test_successful_query_returns_columns_and_rows(self):
        engine = self._mock_engine(
            rows=[("USA", 1000), ("CHN", 800)],
            columns=["country", "value"],
        )
        state = _base_state(pipeline_sql="SELECT country, value FROM t LIMIT 2")

        with patch(
            "src.generate_query.execute_with_retry",
            side_effect=lambda fn, *a, **kw: fn(),
        ):
            result = await execute_sql_node(state, async_engine=engine)

        assert result["pipeline_result_columns"] == ["country", "value"]
        assert result["pipeline_result_rows"] == [["USA", 1000], ["CHN", 800]]
        assert isinstance(result["pipeline_execution_time_ms"], int)
        assert result["pipeline_execution_time_ms"] >= 0
        # Existing pipeline_result string still populated
        assert "USA" in result["pipeline_result"]

    async def test_empty_result_preserves_column_names(self):
        engine = self._mock_engine(rows=[], columns=["country", "value"])
        state = _base_state(pipeline_sql="SELECT * FROM t WHERE 1=0")

        with patch(
            "src.generate_query.execute_with_retry",
            side_effect=lambda fn, *a, **kw: fn(),
        ):
            result = await execute_sql_node(state, async_engine=engine)

        assert result["pipeline_result_columns"] == ["country", "value"]
        assert result["pipeline_result_rows"] == []
        assert result["pipeline_execution_time_ms"] >= 0

    async def test_error_returns_empty_structured_fields(self):
        mock_engine = MagicMock()
        state = _base_state(pipeline_sql="SELECT bad")

        with patch(
            "src.generate_query.execute_with_retry",
            side_effect=QueryExecutionError("syntax error"),
        ):
            result = await execute_sql_node(state, async_engine=mock_engine)

        assert result["pipeline_result_columns"] == []
        assert result["pipeline_result_rows"] == []
        assert result["pipeline_execution_time_ms"] == 0
        assert result["last_error"] != ""

    async def test_no_returns_rows_query(self):
        engine = self._mock_engine(rows=[], columns=[], returns_rows=False)
        state = _base_state(pipeline_sql="INSERT INTO t VALUES (1)")

        with patch(
            "src.generate_query.execute_with_retry",
            side_effect=lambda fn, *a, **kw: fn(),
        ):
            result = await execute_sql_node(state, async_engine=engine)

        assert result["pipeline_result_columns"] == []
        assert result["pipeline_result_rows"] == []


# ---------------------------------------------------------------------------
# 7. format_results_node
# ---------------------------------------------------------------------------


class TestFormatResultsNode:
    """Tests for format_results_node node."""

    async def test_success_path(self):
        """When pipeline_result is populated, create ToolMessage with that content."""
        msg = _make_tool_call_message(tool_call_id="call_xyz")
        state = _base_state(
            messages=[msg],
            pipeline_result="{'country': 'USA', 'value': 1000}",
            last_error="",
            queries_executed=0,
        )

        result = await format_results_node(state)

        assert len(result["messages"]) == 1
        tool_msg = result["messages"][0]
        assert isinstance(tool_msg, ToolMessage)
        assert tool_msg.tool_call_id == "call_xyz"
        assert "USA" in tool_msg.content
        assert result["queries_executed"] == 1

    async def test_error_path(self):
        """When last_error is set, ToolMessage content should include the error."""
        msg = _make_tool_call_message(tool_call_id="call_err")
        state = _base_state(
            messages=[msg],
            pipeline_result="",
            last_error="relation does not exist",
            queries_executed=1,
        )

        result = await format_results_node(state)

        tool_msg = result["messages"][0]
        assert isinstance(tool_msg, ToolMessage)
        assert tool_msg.tool_call_id == "call_err"
        assert "Error executing query" in tool_msg.content
        assert "relation does not exist" in tool_msg.content
        assert result["queries_executed"] == 2

    async def test_no_result_and_no_error_key_present(self):
        """When pipeline_result is empty string and last_error is empty, content is empty string.

        The node uses state.get("pipeline_result", default) -- when the key
        exists (even as ""), get() returns the value, not the default.
        """
        msg = _make_tool_call_message(tool_call_id="call_empty")
        state = _base_state(
            messages=[msg],
            pipeline_result="",
            last_error="",
            queries_executed=0,
        )

        result = await format_results_node(state)

        tool_msg = result["messages"][0]
        assert tool_msg.content == ""
        assert tool_msg.tool_call_id == "call_empty"

    async def test_no_result_key_missing_returns_default(self):
        """When pipeline_result key is absent entirely, the default message is used."""
        msg = _make_tool_call_message(tool_call_id="call_missing")
        state = {
            "messages": [msg],
            "last_error": "",
            "queries_executed": 0,
        }

        result = await format_results_node(state)

        tool_msg = result["messages"][0]
        assert tool_msg.content == "SQL query returned no results."
        assert tool_msg.tool_call_id == "call_missing"

    async def test_increments_queries_executed(self):
        msg = _make_tool_call_message()
        state = _base_state(
            messages=[msg],
            pipeline_result="some data",
            last_error="",
            queries_executed=2,
        )

        result = await format_results_node(state)

        assert result["queries_executed"] == 3

    async def test_queries_executed_defaults_to_zero(self):
        """If queries_executed is missing from state, treat as 0."""
        msg = _make_tool_call_message()
        state = {
            "messages": [msg],
            "pipeline_result": "data",
            "last_error": "",
        }

        result = await format_results_node(state)

        assert result["queries_executed"] == 1

    async def test_tool_call_id_matches_incoming_message(self):
        """The ToolMessage must reference the same tool_call_id from the AIMessage."""
        custom_id = "call_custom_id_99"
        msg = _make_tool_call_message(tool_call_id=custom_id)
        state = _base_state(
            messages=[msg],
            pipeline_result="result data",
            last_error="",
        )

        result = await format_results_node(state)

        assert result["messages"][0].tool_call_id == custom_id

    async def test_multiple_tool_calls_all_get_tool_messages(self):
        """N tool_calls should produce N ToolMessages."""
        msg = _make_multi_tool_call_message(
            questions=["Q1?", "Q2?", "Q3?"],
            tool_call_ids=["call_x", "call_y", "call_z"],
        )
        state = _base_state(
            messages=[msg],
            pipeline_result="data for Q1",
            last_error="",
            queries_executed=0,
        )

        result = await format_results_node(state)

        assert len(result["messages"]) == 3
        assert result["messages"][0].tool_call_id == "call_x"
        assert "data for Q1" in result["messages"][0].content
        assert result["messages"][1].tool_call_id == "call_y"
        assert "one query" in result["messages"][1].content.lower()
        assert result["messages"][2].tool_call_id == "call_z"
        assert "one query" in result["messages"][2].content.lower()
        assert result["queries_executed"] == 1

    async def test_multiple_tool_calls_with_error(self):
        """Error path: all tool_calls get ToolMessages; first carries the error."""
        msg = _make_multi_tool_call_message(
            questions=["Q1?", "Q2?"],
            tool_call_ids=["call_e1", "call_e2"],
        )
        state = _base_state(
            messages=[msg],
            pipeline_result="",
            last_error="relation does not exist",
            queries_executed=1,
        )

        result = await format_results_node(state)

        assert len(result["messages"]) == 2
        assert "Error executing query" in result["messages"][0].content
        assert result["messages"][0].tool_call_id == "call_e1"
        assert result["messages"][1].tool_call_id == "call_e2"
        assert "one query" in result["messages"][1].content.lower()


# ---------------------------------------------------------------------------
# 8. max_queries_exceeded_node
# ---------------------------------------------------------------------------


class TestMaxQueriesExceededNode:
    """Tests for max_queries_exceeded_node node."""

    async def test_returns_error_tool_message(self):
        msg = _make_tool_call_message(tool_call_id="call_limit")
        state = _base_state(messages=[msg], queries_executed=3)

        result = await max_queries_exceeded_node(state)

        assert len(result["messages"]) == 1
        tool_msg = result["messages"][0]
        assert isinstance(tool_msg, ToolMessage)
        assert tool_msg.tool_call_id == "call_limit"
        assert "Maximum number of queries exceeded" in tool_msg.content

    async def test_tool_call_id_matches(self):
        custom_id = "call_over_limit_42"
        msg = _make_tool_call_message(tool_call_id=custom_id)
        state = _base_state(messages=[msg])

        result = await max_queries_exceeded_node(state)

        assert result["messages"][0].tool_call_id == custom_id

    async def test_does_not_increment_queries_executed(self):
        """max_queries_exceeded_node should not return queries_executed."""
        msg = _make_tool_call_message()
        state = _base_state(messages=[msg], queries_executed=5)

        result = await max_queries_exceeded_node(state)

        assert "queries_executed" not in result

    async def test_multiple_tool_calls_all_get_error_messages(self):
        """All parallel tool_calls receive the exceeded-limit error ToolMessage."""
        msg = _make_multi_tool_call_message(
            questions=["Q1?", "Q2?"],
            tool_call_ids=["call_m1", "call_m2"],
        )
        state = _base_state(messages=[msg], queries_executed=5)

        result = await max_queries_exceeded_node(state)

        assert len(result["messages"]) == 2
        for tm in result["messages"]:
            assert isinstance(tm, ToolMessage)
            assert "Maximum number of queries exceeded" in tm.content
        assert result["messages"][0].tool_call_id == "call_m1"
        assert result["messages"][1].tool_call_id == "call_m2"


# ---------------------------------------------------------------------------
# 9. validate_sql_node
# ---------------------------------------------------------------------------


class TestValidateSqlNode:
    """Tests for validate_sql_node node."""

    TABLE_DESCRIPTIONS = {
        "hs92": [
            {"table_name": "country_year", "context_str": "Year-level data"},
            {
                "table_name": "country_product_year_4",
                "context_str": "4-digit product data",
            },
        ],
        "classification": [
            {
                "table_name": "location_country",
                "context_str": "Country-level data with names, ISO codes, and hierarchical information.",
            },
            {
                "table_name": "product_hs92",
                "context_str": "HS92 product classification data.",
            },
            {
                "table_name": "product_hs12",
                "context_str": "HS12 product classification data.",
            },
        ],
    }

    TABLE_INFO_DDL = (
        "Table: hs92.country_year\nDescription: Year-level data\n"
        "CREATE TABLE hs92.country_year (\n  country_id integer\n);\n\n"
        "Table: hs92.country_product_year_4\nDescription: 4-digit product data\n"
        "CREATE TABLE hs92.country_product_year_4 (\n  product_id integer\n);\n"
    )

    async def test_valid_sql_passes_through(self):
        state = _base_state(
            pipeline_sql="SELECT country_id FROM hs92.country_year",
            pipeline_table_info=self.TABLE_INFO_DDL,
            pipeline_products=SchemasAndProductsFound(
                classification_schemas=["hs92"],
                products=[],
                requires_product_lookup=False,
            ),
        )

        result = await validate_sql_node(
            state, table_descriptions=self.TABLE_DESCRIPTIONS
        )

        assert result["last_error"] == ""
        assert result["pipeline_sql"] == "SELECT country_id FROM hs92.country_year"

    async def test_syntax_error_sets_last_error(self):
        state = _base_state(
            pipeline_sql="SELEC country FORM table",
            pipeline_table_info=self.TABLE_INFO_DDL,
            pipeline_products=SchemasAndProductsFound(
                classification_schemas=["hs92"],
                products=[],
                requires_product_lookup=False,
            ),
        )

        result = await validate_sql_node(
            state, table_descriptions=self.TABLE_DESCRIPTIONS
        )

        assert result["last_error"] != ""
        assert "SQL validation failed" in result["last_error"]

    async def test_unknown_table_sets_last_error(self):
        state = _base_state(
            pipeline_sql="SELECT * FROM nonexistent.bad_table",
            pipeline_table_info=self.TABLE_INFO_DDL,
            pipeline_products=SchemasAndProductsFound(
                classification_schemas=["hs92"],
                products=[],
                requires_product_lookup=False,
            ),
        )

        result = await validate_sql_node(
            state, table_descriptions=self.TABLE_DESCRIPTIONS
        )

        assert result["last_error"] != ""
        assert "nonexistent.bad_table" in result["last_error"].lower()

    async def test_warnings_dont_block(self):
        """SELECT * produces a warning but should still pass validation."""
        state = _base_state(
            pipeline_sql="SELECT * FROM hs92.country_year",
            pipeline_table_info=self.TABLE_INFO_DDL,
            pipeline_products=SchemasAndProductsFound(
                classification_schemas=["hs92"],
                products=[],
                requires_product_lookup=False,
            ),
        )

        result = await validate_sql_node(
            state, table_descriptions=self.TABLE_DESCRIPTIONS
        )

        assert result["last_error"] == ""

    async def test_no_pipeline_products_still_uses_ddl_tables(self):
        """When pipeline_products is None, valid tables from DDL should still work."""
        state = _base_state(
            pipeline_sql="SELECT country_id FROM hs92.country_year",
            pipeline_table_info=self.TABLE_INFO_DDL,
            pipeline_products=None,
        )

        result = await validate_sql_node(
            state, table_descriptions=self.TABLE_DESCRIPTIONS
        )

        assert result["last_error"] == ""

    async def test_classification_join_tables_are_valid(self):
        """SQL using classification.location_country and classification.product_hs92 JOINs must pass."""
        sql = (
            "SELECT cy.country_id, lc.name_en, ph.name_short_en "
            "FROM hs92.country_product_year_4 cy "
            "JOIN classification.location_country lc ON cy.country_id = lc.country_id "
            "JOIN classification.product_hs92 ph ON cy.product_id = ph.product_id"
        )
        state = _base_state(
            pipeline_sql=sql,
            pipeline_table_info=self.TABLE_INFO_DDL,
            pipeline_products=SchemasAndProductsFound(
                classification_schemas=["hs92"],
                products=[],
                requires_product_lookup=False,
            ),
        )

        result = await validate_sql_node(
            state, table_descriptions=self.TABLE_DESCRIPTIONS
        )

        assert (
            result["last_error"] == ""
        ), f"Expected no error, got: {result['last_error']}"

    async def test_classification_location_country_always_valid(self):
        """classification.location_country should be valid even without product tables."""
        sql = (
            "SELECT cy.country_id, lc.name_en "
            "FROM hs92.country_year cy "
            "JOIN classification.location_country lc ON cy.country_id = lc.country_id"
        )
        state = _base_state(
            pipeline_sql=sql,
            pipeline_table_info=self.TABLE_INFO_DDL,
            pipeline_products=SchemasAndProductsFound(
                classification_schemas=["hs92"],
                products=[],
                requires_product_lookup=False,
            ),
        )

        result = await validate_sql_node(
            state, table_descriptions=self.TABLE_DESCRIPTIONS
        )

        assert result["last_error"] == ""
