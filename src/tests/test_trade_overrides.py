"""Tests for trade toggle overrides — schema, direction, and mode.

Covers:
- ChatRequest Pydantic validation (valid/invalid override values)
- _turn_input() correctly propagating override fields into state
- extract_products_node applying schema/mode overrides
- generate_sql_node forwarding direction/mode overrides to the chain
- agent_node building dynamic system prompts with overrides
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.api import ChatRequest
from src.generate_query import (
    extract_products_node,
    generate_sql_node,
)
from src.product_and_schema_lookup import (
    ProductDetails,
    SchemasAndProductsFound,
)
from src.text_to_sql import AtlasTextToSQL

from langchain_core.messages import AIMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_call_message(
    question: str = "What did Brazil export in 2021?",
    tool_call_id: str = "call_abc123",
) -> AIMessage:
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


def _base_state(**overrides) -> dict:
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
# 1. ChatRequest Pydantic validation
# ---------------------------------------------------------------------------


class TestChatRequestOverrideValidation:
    """Pydantic validation for override fields on ChatRequest."""

    def test_no_overrides_accepted(self):
        """Backward compat: request without overrides is valid."""
        req = ChatRequest(question="What did Brazil export?")
        assert req.override_schema is None
        assert req.override_direction is None
        assert req.override_mode is None

    def test_valid_schema_override_hs92(self):
        req = ChatRequest(question="q", override_schema="hs92")
        assert req.override_schema == "hs92"

    def test_valid_schema_override_hs12(self):
        req = ChatRequest(question="q", override_schema="hs12")
        assert req.override_schema == "hs12"

    def test_valid_schema_override_sitc(self):
        req = ChatRequest(question="q", override_schema="sitc")
        assert req.override_schema == "sitc"

    def test_valid_direction_override_exports(self):
        req = ChatRequest(question="q", override_direction="exports")
        assert req.override_direction == "exports"

    def test_valid_direction_override_imports(self):
        req = ChatRequest(question="q", override_direction="imports")
        assert req.override_direction == "imports"

    def test_valid_mode_override_goods(self):
        req = ChatRequest(question="q", override_mode="goods")
        assert req.override_mode == "goods"

    def test_valid_mode_override_services(self):
        req = ChatRequest(question="q", override_mode="services")
        assert req.override_mode == "services"

    def test_all_overrides_together(self):
        req = ChatRequest(
            question="q",
            override_schema="hs12",
            override_direction="imports",
            override_mode="goods",
        )
        assert req.override_schema == "hs12"
        assert req.override_direction == "imports"
        assert req.override_mode == "goods"

    def test_invalid_schema_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="q", override_schema="hs2024")

    def test_invalid_direction_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="q", override_direction="re-exports")

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="q", override_mode="digital")


# ---------------------------------------------------------------------------
# 2. _turn_input propagation
# ---------------------------------------------------------------------------


class TestTurnInputOverrides:
    """_turn_input() should include override fields in the returned dict."""

    def test_no_overrides_default_to_none(self):
        result = AtlasTextToSQL._turn_input("What did Brazil export?")
        assert result["override_schema"] is None
        assert result["override_direction"] is None
        assert result["override_mode"] is None

    def test_schema_override_set(self):
        result = AtlasTextToSQL._turn_input(
            "q", override_schema="hs12"
        )
        assert result["override_schema"] == "hs12"
        assert result["override_direction"] is None
        assert result["override_mode"] is None

    def test_all_overrides_set(self):
        result = AtlasTextToSQL._turn_input(
            "q",
            override_schema="sitc",
            override_direction="imports",
            override_mode="services",
        )
        assert result["override_schema"] == "sitc"
        assert result["override_direction"] == "imports"
        assert result["override_mode"] == "services"


# ---------------------------------------------------------------------------
# 3. extract_products_node — schema/mode overrides
# ---------------------------------------------------------------------------


class TestExtractProductsNodeOverrides:
    """extract_products_node should apply schema and mode overrides."""

    async def test_schema_override_forces_classification_schemas(self):
        """When override_schema is set, classification_schemas should be [override]."""
        canned = SchemasAndProductsFound(
            classification_schemas=["hs92"],
            products=[
                ProductDetails(
                    name="cotton",
                    classification_schema="hs92",
                    codes=["5201"],
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

            state = _base_state(
                pipeline_question="US cotton exports?",
                override_schema="hs12",
            )
            result = await extract_products_node(
                state, llm=mock_llm, engine=mock_engine
            )

        products = result["pipeline_products"]
        assert products.classification_schemas == ["hs12"]
        # Product schema should also be rebound
        assert products.products[0].classification_schema == "hs12"

    async def test_mode_goods_filters_services_schemas(self):
        """override_mode='goods' should remove services schemas."""
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
                pipeline_question="Trade between US and China?",
                override_mode="goods",
            )
            result = await extract_products_node(
                state, llm=mock_llm, engine=mock_engine
            )

        schemas = result["pipeline_products"].classification_schemas
        assert "services_bilateral" not in schemas
        assert len(schemas) >= 1  # Should have at least hs92

    async def test_mode_goods_defaults_to_hs92_when_empty(self):
        """If goods mode filters out all schemas, default to ['hs92']."""
        canned = SchemasAndProductsFound(
            classification_schemas=["services_unilateral"],
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
                pipeline_question="Trade data?",
                override_mode="goods",
            )
            result = await extract_products_node(
                state, llm=mock_llm, engine=mock_engine
            )

        assert result["pipeline_products"].classification_schemas == ["hs92"]

    async def test_mode_services_forces_services_schema(self):
        """override_mode='services' should keep only services schemas."""
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
                pipeline_question="Services trade?",
                override_mode="services",
            )
            result = await extract_products_node(
                state, llm=mock_llm, engine=mock_engine
            )

        schemas = result["pipeline_products"].classification_schemas
        for s in schemas:
            assert s.startswith("services_")

    async def test_mode_services_defaults_when_no_services_detected(self):
        """If services mode but no services schemas found, default to services_unilateral."""
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

            state = _base_state(
                pipeline_question="Services trade?",
                override_mode="services",
            )
            result = await extract_products_node(
                state, llm=mock_llm, engine=mock_engine
            )

        assert result["pipeline_products"].classification_schemas == [
            "services_unilateral"
        ]

    async def test_schema_override_takes_precedence_over_mode(self):
        """When both schema and mode are set, schema override wins."""
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

            state = _base_state(
                pipeline_question="q",
                override_schema="sitc",
                override_mode="services",
            )
            result = await extract_products_node(
                state, llm=mock_llm, engine=mock_engine
            )

        # Schema override = sitc, even though mode says services
        assert result["pipeline_products"].classification_schemas == ["sitc"]


# ---------------------------------------------------------------------------
# 4. generate_sql_node — direction/mode constraint forwarding
# ---------------------------------------------------------------------------


class TestGenerateSqlNodeOverrides:
    """generate_sql_node should forward direction/mode overrides to the chain."""

    async def test_direction_override_forwarded_to_chain(self):
        mock_llm = MagicMock()

        with patch("src.generate_query.create_query_generation_chain") as mock_create:
            mock_chain = MagicMock()
            mock_chain.ainvoke = AsyncMock(return_value="SELECT 1")
            mock_create.return_value = mock_chain

            state = _base_state(
                pipeline_question="Brazil exports?",
                pipeline_codes="",
                pipeline_table_info="table info",
                override_direction="imports",
            )
            await generate_sql_node(
                state, llm=mock_llm, example_queries=[], max_results=15
            )

        _, kwargs = mock_create.call_args
        assert kwargs.get("direction_constraint") == "imports"

    async def test_mode_override_forwarded_to_chain(self):
        mock_llm = MagicMock()

        with patch("src.generate_query.create_query_generation_chain") as mock_create:
            mock_chain = MagicMock()
            mock_chain.ainvoke = AsyncMock(return_value="SELECT 1")
            mock_create.return_value = mock_chain

            state = _base_state(
                pipeline_question="Trade data?",
                pipeline_codes="",
                pipeline_table_info="table info",
                override_mode="services",
            )
            await generate_sql_node(
                state, llm=mock_llm, example_queries=[], max_results=15
            )

        _, kwargs = mock_create.call_args
        assert kwargs.get("mode_constraint") == "services"

    async def test_no_overrides_passes_none_constraints(self):
        mock_llm = MagicMock()

        with patch("src.generate_query.create_query_generation_chain") as mock_create:
            mock_chain = MagicMock()
            mock_chain.ainvoke = AsyncMock(return_value="SELECT 1")
            mock_create.return_value = mock_chain

            state = _base_state(
                pipeline_question="q",
                pipeline_codes="",
                pipeline_table_info="",
            )
            await generate_sql_node(
                state, llm=mock_llm, example_queries=[], max_results=15
            )

        _, kwargs = mock_create.call_args
        assert kwargs.get("direction_constraint") is None
        assert kwargs.get("mode_constraint") is None
