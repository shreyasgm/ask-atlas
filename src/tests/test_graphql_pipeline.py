"""Unit tests for the GraphQL pipeline nodes in src/graphql_pipeline.py.

Every test constructs its own AtlasAgentState dict and mocks all external
dependencies so that no LLM, database, or network access is required.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.cache import CatalogCache
from src.graphql_client import BudgetExhaustedError, GraphQLError
from src.graphql_pipeline import (
    GraphQLEntityExtraction,
    GraphQLQueryClassification,
    build_graphql_query,
    build_and_execute_graphql,
    classify_query,
    extract_entities,
    extract_graphql_question,
    format_graphql_results,
    format_ids_for_api,
    resolve_ids,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GRAPHQL_STATE_DEFAULTS: dict = {
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
}


def _make_graphql_tool_call_message(
    question: str = "What did Kenya export in 2024?",
    tool_call_id: str = "call_gql_001",
) -> AIMessage:
    """Create an AIMessage with a single tool_call for graphql_tool."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": tool_call_id,
                "name": "graphql_tool",
                "args": {"question": question},
            }
        ],
    )


def _make_multi_graphql_tool_call_message(
    questions: list[str] | None = None,
    tool_call_ids: list[str] | None = None,
) -> AIMessage:
    """Create an AIMessage with multiple parallel tool_calls."""
    questions = questions or [
        "What did Kenya export in 2024?",
        "What is Brazil's ECI?",
    ]
    tool_call_ids = tool_call_ids or [f"call_gql_{i}" for i in range(len(questions))]
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": tc_id,
                "name": "graphql_tool",
                "args": {"question": q},
            }
            for q, tc_id in zip(questions, tool_call_ids)
        ],
    )


def _base_graphql_state(**overrides) -> dict:
    """Return a minimal AtlasAgentState dict with graphql defaults."""
    state: dict = {
        "messages": [_make_graphql_tool_call_message()],
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
        **GRAPHQL_STATE_DEFAULTS,
    }
    state.update(overrides)
    return state


def _make_country_cache() -> CatalogCache:
    """Create and populate a test country catalog cache."""
    cache = CatalogCache("test_country", ttl=3600)
    cache.add_index(
        "iso3",
        key_fn=lambda e: (e.get("iso3Code") or "").upper() or None,
        normalize_query=lambda q: q.strip().upper(),
    )
    cache.add_index(
        "name",
        key_fn=lambda e: (e.get("nameShortEn") or "").strip().lower() or None,
        normalize_query=lambda q: q.strip().lower(),
    )
    cache.add_index(
        "id",
        key_fn=lambda e: str(e["countryId"]) if "countryId" in e else None,
    )
    cache.populate(
        [
            {"countryId": 404, "iso3Code": "KEN", "nameShortEn": "Kenya"},
            {"countryId": 76, "iso3Code": "BRA", "nameShortEn": "Brazil"},
            {"countryId": 356, "iso3Code": "IND", "nameShortEn": "India"},
        ]
    )
    return cache


def _make_product_cache() -> CatalogCache:
    """Create and populate a test product catalog cache."""
    cache = CatalogCache("test_product", ttl=3600)
    cache.add_index(
        "code",
        key_fn=lambda e: (e.get("code") or "").strip() or None,
        normalize_query=lambda q: q.strip(),
    )
    cache.add_index(
        "name",
        key_fn=lambda e: (e.get("nameShortEn") or "").strip().lower() or None,
        normalize_query=lambda q: q.strip().lower(),
    )
    cache.add_index(
        "id",
        key_fn=lambda e: str(e["productId"]) if "productId" in e else None,
    )
    cache.populate(
        [
            {"productId": 726, "code": "0901", "nameShortEn": "Coffee"},
            {"productId": 897, "code": "2709", "nameShortEn": "Petroleum oils, crude"},
            {"productId": 112, "code": "5201", "nameShortEn": "Cotton"},
        ]
    )
    return cache


def _make_services_cache() -> CatalogCache:
    """Create and populate a test services catalog cache."""
    cache = CatalogCache("test_services", ttl=3600)
    cache.add_index(
        "name",
        key_fn=lambda e: (e.get("nameShortEn") or "").strip().lower() or None,
        normalize_query=lambda q: q.strip().lower(),
    )
    cache.add_index(
        "id",
        key_fn=lambda e: str(e["productId"]) if "productId" in e else None,
    )
    cache.populate(
        [
            {"productId": 1, "nameShortEn": "Transport"},
            {"productId": 2, "nameShortEn": "Insurance"},
        ]
    )
    return cache


def _explore_extraction(**overrides) -> dict:
    """Build a standard entity extraction dict for Explore API tests."""
    base = {
        "reasoning": "...",
        "country_name": "Kenya",
        "country_code_guess": "KEN",
        "partner_name": None,
        "partner_code_guess": None,
        "product_name": None,
        "product_code_guess": None,
        "product_level": None,
        "product_class": None,
        "year": 2024,
        "year_min": None,
        "year_max": None,
        "group_name": None,
        "group_type": None,
        "lookback_years": None,
    }
    base.update(overrides)
    return base


def _explore_classification(**overrides) -> dict:
    """Build a standard classification dict for Explore API tests."""
    base = {
        "query_type": "treemap_products",
        "api_target": "explore",
        "reasoning": "...",
        "rejection_reason": None,
    }
    base.update(overrides)
    return base


def _rejection_classification(**overrides) -> dict:
    """Build a rejection classification dict."""
    base = {
        "query_type": "reject",
        "rejection_reason": "Not relevant",
        "reasoning": "...",
        "api_target": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. extract_graphql_question
# ---------------------------------------------------------------------------


class TestExtractGraphQLQuestion:
    """Tests for extract_graphql_question node."""

    async def test_extracts_question_from_tool_call(self):
        msg = _make_graphql_tool_call_message(question="What does Kenya export?")
        state = _base_graphql_state(messages=[msg])

        result = await extract_graphql_question(state)

        assert result["graphql_question"] == "What does Kenya export?"

    async def test_resets_all_graphql_state_fields(self):
        """Prevents cross-turn leakage by resetting all graphql_* fields."""
        state = _base_graphql_state(
            graphql_classification={"query_type": "old"},
            graphql_entity_extraction={"country_name": "old"},
            graphql_resolved_params={"country_id": 999},
            graphql_query="old query",
            graphql_api_target="old_target",
            graphql_raw_response={"old": "data"},
            graphql_execution_time_ms=999,
            graphql_atlas_links=[{"url": "old"}],
        )

        result = await extract_graphql_question(state)

        assert result["graphql_classification"] is None
        assert result["graphql_entity_extraction"] is None
        assert result["graphql_resolved_params"] is None
        assert result["graphql_query"] is None
        assert result["graphql_api_target"] is None
        assert result["graphql_raw_response"] is None
        assert result["graphql_execution_time_ms"] == 0
        assert result["graphql_atlas_links"] == []

    async def test_handles_parallel_tool_calls(self):
        """Uses first tool_call; logs warning for extras."""
        msg = _make_multi_graphql_tool_call_message(
            questions=["First question?", "Second question?"],
        )
        state = _base_graphql_state(messages=[msg])

        result = await extract_graphql_question(state)

        assert result["graphql_question"] == "First question?"


# ---------------------------------------------------------------------------
# 2. GraphQLQueryClassification schema
# ---------------------------------------------------------------------------


class TestGraphQLQueryClassification:
    """Tests for the GraphQLQueryClassification Pydantic schema."""

    def test_schema_has_required_fields(self):
        fields = set(GraphQLQueryClassification.model_fields.keys())
        assert {"api_target", "query_type", "reasoning", "rejection_reason"} == fields

    def test_all_query_types_present(self):
        """The query_type literal must include all 22 valid types + reject."""
        import typing

        field_info = GraphQLQueryClassification.model_fields["query_type"]
        args = typing.get_args(field_info.annotation)
        expected = {
            "country_profile",
            "country_profile_exports",
            "country_profile_complexity",
            "country_lookback",
            "new_products",
            "treemap_products",
            "treemap_partners",
            "treemap_bilateral",
            "overtime_products",
            "overtime_partners",
            "marketshare",
            "product_space",
            "feasibility",
            "feasibility_table",
            "growth_opportunities",
            "product_table",
            "country_year",
            "product_info",
            "explore_bilateral",
            "explore_group",
            "global_datum",
            "explore_data_availability",
            "reject",
        }
        assert set(args) == expected


# ---------------------------------------------------------------------------
# 3. classify_query
# ---------------------------------------------------------------------------


class TestClassifyQuery:
    """Tests for classify_query node."""

    async def test_writes_classification_and_api_target_to_state(self):
        """LLM structured output is model_dump'd into graphql_classification
        and api_target is extracted to its own state key."""
        classification = GraphQLQueryClassification(
            reasoning="Country profile question",
            query_type="country_profile",
            rejection_reason=None,
            api_target="country_pages",
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=classification
        )

        state = _base_graphql_state(graphql_question="Tell me about Kenya")

        result = await classify_query(state, lightweight_model=mock_llm)

        assert result["graphql_classification"]["query_type"] == "country_profile"
        assert (
            result["graphql_classification"]["reasoning"] == "Country profile question"
        )
        assert result["graphql_api_target"] == "country_pages"
        # Verify with_structured_output was called with function_calling method
        mock_llm.with_structured_output.assert_called_once_with(
            GraphQLQueryClassification, method="function_calling"
        )

    async def test_llm_error_propagates_for_retry(self):
        """LLM errors propagate (not caught) so LangGraph RetryPolicy can retry."""
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            side_effect=Exception("LLM error")
        )

        state = _base_graphql_state(graphql_question="some question")

        with pytest.raises(Exception, match="LLM error"):
            await classify_query(state, lightweight_model=mock_llm)


# ---------------------------------------------------------------------------
# 4. extract_entities
# ---------------------------------------------------------------------------


class TestExtractEntities:
    """Tests for extract_entities node."""

    async def test_extracts_entities_and_writes_to_state(self):
        """LLM structured output is model_dump'd into graphql_entity_extraction."""
        extraction = GraphQLEntityExtraction(
            reasoning="Kenya exports coffee",
            country_name="Kenya",
            country_code_guess="KEN",
            partner_name=None,
            partner_code_guess=None,
            product_name="Coffee",
            product_code_guess="0901",
            product_level="fourDigit",
            product_class="HS92",
            year=2024,
            year_min=None,
            year_max=None,
            group_name=None,
            group_type=None,
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=extraction
        )

        state = _base_graphql_state(
            graphql_question="What coffee did Kenya export in 2024?",
            graphql_classification=_explore_classification(),
        )

        result = await extract_entities(state, lightweight_model=mock_llm)

        ext = result["graphql_entity_extraction"]
        assert ext["country_name"] == "Kenya"
        assert ext["country_code_guess"] == "KEN"
        assert ext["product_code_guess"] == "0901"
        assert ext["year"] == 2024
        # Verify function_calling method is used (avoids ParsedChatCompletion warnings)
        mock_llm.with_structured_output.assert_called_once_with(
            GraphQLEntityExtraction, method="function_calling"
        )

    async def test_skips_when_rejected(self):
        mock_llm = MagicMock()

        state = _base_graphql_state(
            graphql_question="Not a trade question",
            graphql_classification=_rejection_classification(),
        )

        result = await extract_entities(state, lightweight_model=mock_llm)

        assert result["graphql_entity_extraction"] is None
        mock_llm.with_structured_output.assert_not_called()

    async def test_llm_error_propagates_for_retry(self):
        """LLM errors propagate (not caught) so LangGraph RetryPolicy can retry."""
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            side_effect=Exception("LLM failure")
        )

        state = _base_graphql_state(
            graphql_question="some question",
            graphql_classification=_explore_classification(),
        )

        with pytest.raises(Exception, match="LLM failure"):
            await extract_entities(state, lightweight_model=mock_llm)


# ---------------------------------------------------------------------------
# 5. resolve_ids
# ---------------------------------------------------------------------------


class TestResolveIds:
    """Tests for resolve_ids node."""

    async def test_resolves_country_by_iso3_code_lookup(self):
        """ISO3 code "KEN" → countryId 404 via exact index lookup."""
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()
        mock_llm = MagicMock()

        state = _base_graphql_state(
            graphql_question="What did Kenya export?",
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(),
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        assert result["graphql_resolved_params"]["country_id"] == 404
        assert result["graphql_resolved_params"]["country_name"] == "Kenya"

    async def test_falls_back_to_name_search_when_code_misses(self):
        """Wrong code "XXX" misses index → name search finds "Kenya" → 404."""
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()
        mock_llm = MagicMock()

        state = _base_graphql_state(
            graphql_question="What did Kenya export?",
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(country_code_guess="XXX"),
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        assert result["graphql_resolved_params"]["country_id"] == 404

    async def test_resolves_product_by_hs_code(self):
        """HS code "0901" → productId 726 via exact index lookup."""
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()
        mock_llm = MagicMock()

        state = _base_graphql_state(
            graphql_question="Kenya coffee exports?",
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(
                product_name="Coffee",
                product_code_guess="0901",
                product_level="fourDigit",
                product_class="HS92",
            ),
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        assert result["graphql_resolved_params"]["product_id"] == 726

    async def test_product_falls_back_to_name_search(self):
        """Wrong code "9999" misses → name search "Coffee" → productId 726."""
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()
        mock_llm = MagicMock()

        state = _base_graphql_state(
            graphql_question="Kenya coffee exports?",
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(
                product_name="Coffee",
                product_code_guess="9999",
                product_level="fourDigit",
                product_class="HS92",
            ),
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        assert result["graphql_resolved_params"]["product_id"] == 726

    async def test_entity_not_in_cache_omitted_from_params(self):
        """When both code and name miss, the entity is not in resolved params."""
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()
        mock_llm = MagicMock()

        state = _base_graphql_state(
            graphql_question="Narnia tea exports?",
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(
                country_name="Narnia",
                country_code_guess="NAR",
                product_name="Unicorn horns",
                product_code_guess="9999",
            ),
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        params = result["graphql_resolved_params"]
        assert "country_id" not in params
        assert "product_id" not in params
        # Scalar fields still pass through
        assert params["year"] == 2024

    async def test_country_pages_api_transforms_ids_to_prefixed_strings(self):
        """country_pages target converts country_id → "location-404"."""
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()
        mock_llm = MagicMock()

        state = _base_graphql_state(
            graphql_question="Tell me about Kenya",
            graphql_classification=_explore_classification(
                query_type="country_profile",
                api_target="country_pages",
            ),
            graphql_entity_extraction=_explore_extraction(),
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        params = result["graphql_resolved_params"]
        assert params["location"] == "location-404"
        # Original key should be removed
        assert "country_id" not in params

    async def test_skips_when_rejected(self):
        mock_llm = MagicMock()
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()

        state = _base_graphql_state(
            graphql_question="Not trade",
            graphql_classification=_rejection_classification(),
            graphql_entity_extraction=None,
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        assert result["graphql_resolved_params"] is None

    async def test_skips_when_extraction_is_none(self):
        """If entity extraction failed (returned None), resolve_ids does nothing."""
        mock_llm = MagicMock()
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()

        state = _base_graphql_state(
            graphql_question="something",
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=None,
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        assert result["graphql_resolved_params"] is None


# ---------------------------------------------------------------------------
# 6. build_and_execute_graphql
# ---------------------------------------------------------------------------


class TestBuildAndExecuteGraphQL:
    """Tests for build_and_execute_graphql node."""

    async def test_executes_query_and_returns_response(self):
        response_data = {"countryProductYear": [{"exportValue": 1000}]}
        mock_client = MagicMock()
        mock_client.execute = AsyncMock(return_value=response_data)

        state = _base_graphql_state(
            graphql_classification=_explore_classification(),
            graphql_resolved_params={
                "country_id": 404,
                "country_name": "Kenya",
                "product_level": 4,
                "product_class": "HS92",
                "year": 2024,
            },
        )

        result = await build_and_execute_graphql(state, graphql_client=mock_client)

        assert result["graphql_raw_response"] == response_data
        assert result["last_error"] == ""
        assert "graphql_execution_time_ms" in result
        assert isinstance(result["graphql_query"], str)
        mock_client.execute.assert_awaited_once()

    async def test_catches_graphql_error_without_raising(self):
        mock_client = MagicMock()
        mock_client.execute = AsyncMock(side_effect=GraphQLError("Bad query syntax"))

        state = _base_graphql_state(
            graphql_classification=_explore_classification(),
            graphql_resolved_params={
                "country_id": 404,
                "country_name": "Kenya",
                "product_level": 4,
                "product_class": "HS92",
                "year": 2024,
            },
        )

        # Should not raise
        result = await build_and_execute_graphql(state, graphql_client=mock_client)

        assert isinstance(result["graphql_raw_response"], dict)
        assert "error" in result["graphql_raw_response"]
        assert "Bad query syntax" in result["last_error"]

    async def test_catches_budget_exhausted_with_specific_message(self):
        mock_client = MagicMock()
        mock_client.execute = AsyncMock(side_effect=BudgetExhaustedError())

        state = _base_graphql_state(
            graphql_classification=_explore_classification(),
            graphql_resolved_params={
                "country_id": 404,
                "country_name": "Kenya",
                "product_level": 4,
                "product_class": "HS92",
                "year": 2024,
            },
        )

        result = await build_and_execute_graphql(state, graphql_client=mock_client)

        assert isinstance(result["graphql_raw_response"], dict)
        assert result["graphql_raw_response"]["error"] == "budget_exhausted"
        assert "budget" in result["last_error"].lower()

    async def test_skips_for_rejected_queries(self):
        mock_client = MagicMock()

        state = _base_graphql_state(
            graphql_classification=_rejection_classification(),
            graphql_resolved_params=None,
        )

        result = await build_and_execute_graphql(state, graphql_client=mock_client)

        mock_client.execute.assert_not_called()
        assert result["graphql_raw_response"] is None
        assert result["last_error"] == ""

    async def test_handles_query_build_failure(self):
        """If build_graphql_query raises ValueError, error is captured in state."""
        mock_client = MagicMock()

        state = _base_graphql_state(
            # Use a query_type not in _QUERY_BUILDERS but not "reject"
            graphql_classification=_explore_classification(
                query_type="treemap_products",
            ),
            # Missing required fields will cause the builder to create a
            # query with None values, but we can test the ValueError path
            # by providing a classification with a valid-looking but
            # non-existent query_type. We need to bypass the Literal
            # validation, so let's test it differently: set resolved_params
            # to something that causes the builder to error.
            graphql_resolved_params={"country_id": 404, "year": 2024},
        )

        # This should succeed (treemap_products exists), so let's test the
        # actual ValueError path by mocking build_graphql_query
        from unittest.mock import patch

        with patch(
            "src.graphql_pipeline.build_graphql_query",
            side_effect=ValueError("Missing required param"),
        ):
            result = await build_and_execute_graphql(state, graphql_client=mock_client)

        assert isinstance(result["graphql_raw_response"], dict)
        assert result["graphql_raw_response"]["error"] == "build_failed"
        assert "Missing required param" in result["last_error"]
        mock_client.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 7. format_graphql_results
# ---------------------------------------------------------------------------


class TestFormatGraphQLResults:
    """Tests for format_graphql_results node."""

    async def test_success_formats_data_as_json_and_preserves_links(self):
        atlas_links = [{"url": "https://atlas.example.com", "label": "link"}]
        state = _base_graphql_state(
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(),
            graphql_raw_response={"countryProductYear": [{"exportValue": 1000}]},
            graphql_atlas_links=atlas_links,
            last_error="",
        )

        result = await format_graphql_results(state)

        messages = result["messages"]
        assert len(messages) == 1
        assert isinstance(messages[0], ToolMessage)
        # Content should contain the JSON-serialized response data
        assert "countryProductYear" in messages[0].content
        assert "1000" in messages[0].content
        # Atlas links preserved on success
        assert result["graphql_atlas_links"] == atlas_links
        assert result["queries_executed"] == 1

    async def test_error_returns_error_message_and_discards_links(self):
        state = _base_graphql_state(
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(),
            graphql_raw_response=None,
            last_error="GraphQL query failed: Bad request",
            graphql_atlas_links=[{"url": "https://atlas.example.com", "label": "link"}],
        )

        result = await format_graphql_results(state)

        assert "Bad request" in result["messages"][0].content
        assert result["graphql_atlas_links"] == []

    async def test_rejection_includes_reason_in_message(self):
        state = _base_graphql_state(
            graphql_classification=_rejection_classification(
                rejection_reason="This question is about philosophy"
            ),
            graphql_raw_response=None,
            last_error="",
        )

        result = await format_graphql_results(state)

        content = result["messages"][0].content
        assert "philosophy" in content.lower()

    async def test_handles_parallel_tool_calls_with_stubs(self):
        msg = _make_multi_graphql_tool_call_message(
            questions=["Q1", "Q2"],
            tool_call_ids=["call_gql_0", "call_gql_1"],
        )
        state = _base_graphql_state(
            messages=[msg],
            graphql_classification=_explore_classification(),
            graphql_raw_response={"data": "ok"},
            last_error="",
        )

        result = await format_graphql_results(state)

        messages = result["messages"]
        assert len(messages) == 2
        # Second message is a stub explaining only one query runs
        assert messages[0].tool_call_id == "call_gql_0"
        assert messages[1].tool_call_id == "call_gql_1"
        assert (
            "one query" in messages[1].content.lower()
            or "only" in messages[1].content.lower()
        )

    async def test_increments_queries_executed_from_nonzero(self):
        state = _base_graphql_state(
            queries_executed=3,
            graphql_classification=_explore_classification(),
            graphql_raw_response={"data": "ok"},
            last_error="",
        )

        result = await format_graphql_results(state)

        assert result["queries_executed"] == 4


# ---------------------------------------------------------------------------
# 8. build_graphql_query
# ---------------------------------------------------------------------------


class TestBuildGraphQLQuery:
    """Tests for build_graphql_query helper."""

    def test_treemap_products_maps_to_country_product_year(self):
        query, variables = build_graphql_query(
            "treemap_products",
            {
                "country_id": 404,
                "product_level": 4,
                "product_class": "HS92",
                "year": 2024,
            },
        )
        assert "countryProductYear" in query
        assert variables["countryId"] == 404
        assert variables["productLevel"] == 4
        assert variables["productClass"] == "HS92"

    def test_unknown_query_type_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown"):
            build_graphql_query("nonexistent_type", {})

    def test_country_profile_requests_key_analytical_fields(self):
        query, variables = build_graphql_query(
            "country_profile",
            {"location": "location-404"},
        )
        assert "countryProfile" in query
        assert "latestGdpPerCapita" in query
        assert "latestEci" in query
        assert "growthProjection" in query
        assert variables["location"] == "location-404"

    def test_country_profile_exports_reuses_country_profile_builder(self):
        """country_profile_exports uses the same countryProfile query."""
        query, variables = build_graphql_query(
            "country_profile_exports",
            {"location": "location-404"},
        )
        assert "countryProfile" in query
        assert "latestEci" in query
        assert variables["location"] == "location-404"

    def test_country_profile_complexity_reuses_country_profile_builder(self):
        """country_profile_complexity uses the same countryProfile query."""
        query, variables = build_graphql_query(
            "country_profile_complexity",
            {"location": "location-404"},
        )
        assert "countryProfile" in query
        assert "growthProjection" in query
        assert variables["location"] == "location-404"

    def test_single_year_sets_yearmin_equal_yearmax(self):
        """When params has `year` (not a range), yearMin == yearMax."""
        _, variables = build_graphql_query(
            "country_year",
            {"country_id": 404, "year": 2020},
        )
        assert variables["yearMin"] == 2020
        assert variables["yearMax"] == 2020

    def test_year_range_uses_year_min_and_year_max(self):
        """When params has year_min/year_max, they map to yearMin/yearMax."""
        _, variables = build_graphql_query(
            "overtime_products",
            {
                "country_id": 404,
                "product_level": 4,
                "product_class": "HS92",
                "year_min": 1995,
                "year_max": 2024,
            },
        )
        assert variables["yearMin"] == 1995
        assert variables["yearMax"] == 2024

    def test_bilateral_query_includes_partner(self):
        query, variables = build_graphql_query(
            "treemap_bilateral",
            {
                "country_id": 404,
                "partner_id": 76,
                "product_level": 4,
                "product_class": "HS92",
                "year": 2024,
            },
        )
        assert "countryCountryProductYear" in query
        assert variables["partnerCountryId"] == 76


# ---------------------------------------------------------------------------
# 9. format_ids_for_api
# ---------------------------------------------------------------------------


class TestFormatIdsForApi:
    """Tests for format_ids_for_api helper."""

    def test_explore_api_passes_through_integer_ids(self):
        result = format_ids_for_api(
            {"country_id": 404, "product_id": 726, "year": 2024},
            "explore",
        )
        assert result["country_id"] == 404
        assert result["product_id"] == 726
        assert result["year"] == 2024

    def test_country_pages_transforms_and_removes_original_keys(self):
        result = format_ids_for_api(
            {"country_id": 404, "product_id": 726, "year": 2024},
            "country_pages",
        )
        assert result["location"] == "location-404"
        assert result["product"] == "product-HS-726"
        assert result["year"] == 2024
        # Original keys must be removed to avoid sending both formats
        assert "country_id" not in result
        assert "product_id" not in result

    def test_country_pages_transforms_partner_id(self):
        result = format_ids_for_api(
            {"country_id": 404, "partner_id": 76},
            "country_pages",
        )
        assert result["partner"] == "location-76"
        assert "partner_id" not in result

    def test_does_not_mutate_input_dict(self):
        original = {"country_id": 404, "year": 2024}
        format_ids_for_api(original, "country_pages")
        # Original dict should be unchanged
        assert "country_id" in original
        assert "location" not in original


# ---------------------------------------------------------------------------
# Integration tests — real LLM + real Atlas GraphQL API
# ---------------------------------------------------------------------------

ATLAS_EXPLORE_URL = "https://atlas.hks.harvard.edu/api/graphql"
ATLAS_COUNTRY_PAGES_URL = "https://atlas.hks.harvard.edu/api/countries/graphql"


@pytest.mark.integration
class TestClassifyAndExtractIntegration:
    """Run classify_query and extract_entities with a real LLM.

    Verifies that the Pydantic schemas, prompts, and LLM interaction
    produce reasonable structured output for known trade questions.
    """

    @pytest.fixture()
    def lightweight_model(self):
        from src.config import get_prompt_model

        return get_prompt_model("graphql_classification")

    async def test_kenya_exports_classified_and_extracted(self, lightweight_model):
        """'What did Kenya export in 2024?' → sensible classification + entities."""
        state = _base_graphql_state(graphql_question="What did Kenya export in 2024?")

        cls_result = await classify_query(state, lightweight_model=lightweight_model)

        classification = cls_result["graphql_classification"]
        assert classification["query_type"] != "reject", (
            f"Expected a trade-related classification, got reject: "
            f"{classification.get('rejection_reason')}"
        )
        assert classification["query_type"] in {
            "treemap_products",
            "country_profile",
            "country_year",
            "overtime_products",
        }
        assert classification["api_target"] in {"explore", "country_pages"}

        # Now run extract_entities with the real classification
        state.update(cls_result)
        ext_result = await extract_entities(state, lightweight_model=lightweight_model)

        extraction = ext_result["graphql_entity_extraction"]
        assert extraction is not None, "Entity extraction returned None"
        assert extraction["country_name"] is not None
        assert "kenya" in extraction["country_name"].lower()
        assert extraction["country_code_guess"] == "KEN"

    async def test_bilateral_trade_question(self, lightweight_model):
        """'What does the US export to China?' → bilateral classification + both countries."""
        state = _base_graphql_state(
            graphql_question="What does the United States export to China?"
        )

        cls_result = await classify_query(state, lightweight_model=lightweight_model)

        classification = cls_result["graphql_classification"]
        assert classification["query_type"] != "reject"
        assert classification["query_type"] in {
            "treemap_bilateral",
            "explore_bilateral",
            "treemap_products",
        }

        state.update(cls_result)
        ext_result = await extract_entities(state, lightweight_model=lightweight_model)

        extraction = ext_result["graphql_entity_extraction"]
        assert extraction is not None
        # Should identify both countries
        assert extraction["country_name"] is not None
        assert extraction["partner_name"] is not None

    async def test_reject_non_trade_question(self, lightweight_model):
        """A clearly non-trade question should be rejected."""
        state = _base_graphql_state(graphql_question="What is the meaning of life?")

        cls_result = await classify_query(state, lightweight_model=lightweight_model)

        classification = cls_result["graphql_classification"]
        assert classification["query_type"] == "reject"
        assert classification["rejection_reason"] is not None


@pytest.mark.integration
class TestResolveIdsIntegration:
    """Run resolve_ids with real CatalogCache data and a real LLM.

    Tests that entity resolution correctly maps names/codes to
    internal Atlas IDs using real catalog data.
    """

    @pytest.fixture()
    def lightweight_model(self):
        from src.config import get_prompt_model

        return get_prompt_model("id_resolution_selection")

    async def test_resolves_kenya_by_code_and_name(self, lightweight_model):
        """Kenya (KEN) resolves to countryId 404 via cache lookup."""
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()

        state = _base_graphql_state(
            graphql_question="What did Kenya export in 2024?",
            graphql_classification=_explore_classification(
                query_type="treemap_products",
                api_target="explore",
            ),
            graphql_entity_extraction=_explore_extraction(
                country_name="Kenya",
                country_code_guess="KEN",
                year=2024,
            ),
        )

        result = await resolve_ids(
            state,
            lightweight_model=lightweight_model,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        params = result["graphql_resolved_params"]
        assert params is not None
        assert params["country_id"] == 404
        assert params["year"] == 2024

    async def test_end_to_end_classify_extract_resolve(self, lightweight_model):
        """Full LLM pipeline: classify → extract → resolve for a known question."""
        from src.config import get_prompt_model

        classification_model = get_prompt_model("graphql_classification")
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()

        state = _base_graphql_state(graphql_question="What did Kenya export in 2024?")

        # Step 1: classify
        cls_result = await classify_query(state, lightweight_model=classification_model)
        state.update(cls_result)
        assert state["graphql_classification"]["query_type"] != "reject"

        # Step 2: extract entities
        ext_result = await extract_entities(
            state, lightweight_model=classification_model
        )
        state.update(ext_result)
        assert state["graphql_entity_extraction"] is not None

        # Step 3: resolve IDs
        res_result = await resolve_ids(
            state,
            lightweight_model=lightweight_model,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        params = res_result["graphql_resolved_params"]
        assert params is not None
        # Kenya should be resolved regardless of LLM extraction format
        country_key = "country_id" if "country_id" in params else "location"
        assert country_key in params
        if country_key == "country_id":
            assert params["country_id"] == 404
        else:
            assert params["location"] == "location-404"


@pytest.mark.integration
class TestBuildAndExecuteIntegration:
    """Execute real GraphQL queries against the Atlas API.

    Validates that the query templates produce syntactically valid
    GraphQL that the Atlas API accepts and returns meaningful data.
    """

    @pytest.fixture()
    def explore_client(self):
        from src.graphql_client import AtlasGraphQLClient

        return AtlasGraphQLClient(
            base_url=ATLAS_EXPLORE_URL,
            timeout=15.0,
            max_retries=1,
        )

    @pytest.fixture()
    def country_pages_client(self):
        from src.graphql_client import AtlasGraphQLClient

        return AtlasGraphQLClient(
            base_url=ATLAS_COUNTRY_PAGES_URL,
            timeout=15.0,
            max_retries=1,
        )

    async def test_country_profile_against_real_api(self, country_pages_client):
        """country_profile query for Kenya returns GDP, ECI, and growth data."""
        state = _base_graphql_state(
            graphql_classification={
                "query_type": "country_profile",
                "api_target": "country_pages",
                "reasoning": "...",
                "rejection_reason": None,
            },
            graphql_resolved_params={"location": "location-404"},
        )

        result = await build_and_execute_graphql(
            state, graphql_client=country_pages_client
        )

        assert result["last_error"] == "", f"API error: {result['last_error']}"
        assert result["graphql_raw_response"] is not None

        profile = result["graphql_raw_response"]["countryProfile"]
        assert profile["location"]["id"] is not None
        assert profile["latestGdpPerCapita"] is not None
        assert profile["latestEci"] is not None
        assert profile["growthProjection"] is not None

    async def test_treemap_products_against_real_api(self, explore_client):
        """countryProductYear for Kenya returns product-level trade data."""
        state = _base_graphql_state(
            graphql_classification=_explore_classification(
                query_type="treemap_products",
                api_target="explore",
            ),
            graphql_resolved_params={
                "country_id": 404,
                "product_level": 4,
                "product_class": "HS92",
                "year": 2022,
            },
        )

        result = await build_and_execute_graphql(state, graphql_client=explore_client)

        assert result["last_error"] == "", f"API error: {result['last_error']}"
        response = result["graphql_raw_response"]
        assert response is not None

        rows = response["countryProductYear"]
        assert len(rows) > 0, "Expected product-level trade data for Kenya"
        row = rows[0]
        assert "exportValue" in row
        assert "productId" in row

    async def test_country_year_against_real_api(self, explore_client):
        """countryYear for Kenya returns GDP, ECI, and trade totals."""
        state = _base_graphql_state(
            graphql_classification=_explore_classification(
                query_type="country_year",
                api_target="explore",
            ),
            graphql_resolved_params={
                "country_id": 404,
                "year": 2022,
            },
        )

        result = await build_and_execute_graphql(state, graphql_client=explore_client)

        assert result["last_error"] == "", f"API error: {result['last_error']}"
        response = result["graphql_raw_response"]
        assert response is not None

        rows = response["countryYear"]
        assert len(rows) > 0
        row = rows[0]
        assert row["year"] == 2022
        assert "exportValue" in row
        assert "eci" in row

    async def test_data_availability_against_real_api(self, explore_client):
        """dataAvailability query returns year coverage info."""
        state = _base_graphql_state(
            graphql_classification=_explore_classification(
                query_type="explore_data_availability",
                api_target="explore",
            ),
            graphql_resolved_params={},
        )

        result = await build_and_execute_graphql(state, graphql_client=explore_client)

        assert result["last_error"] == "", f"API error: {result['last_error']}"
        response = result["graphql_raw_response"]
        assert response is not None
        assert "dataAvailability" in response

    async def test_global_datum_against_real_api(self, country_pages_client):
        """globalDatum query returns global-level aggregate data."""
        state = _base_graphql_state(
            graphql_classification={
                "query_type": "global_datum",
                "api_target": "country_pages",
                "reasoning": "...",
                "rejection_reason": None,
            },
            graphql_resolved_params={},
        )

        result = await build_and_execute_graphql(
            state, graphql_client=country_pages_client
        )

        assert result["last_error"] == "", f"API error: {result['last_error']}"
        response = result["graphql_raw_response"]
        assert response is not None
        assert "globalDatum" in response

    async def test_country_lookback_with_year_range_real_api(
        self, country_pages_client
    ):
        """countryLookback with yearRange: FiveYears for Kenya returns growth dynamics."""
        state = _base_graphql_state(
            graphql_classification={
                "query_type": "country_lookback",
                "api_target": "country_pages",
                "reasoning": "...",
                "rejection_reason": None,
            },
            graphql_resolved_params={
                "location": "location-404",
                "lookback_years": 5,
            },
        )

        result = await build_and_execute_graphql(
            state, graphql_client=country_pages_client
        )

        assert result["last_error"] == "", f"API error: {result['last_error']}"
        response = result["graphql_raw_response"]
        assert response is not None
        lookback = response["countryLookback"]
        assert lookback["id"] is not None
        assert "eciRankChange" in lookback
        assert "exportValueConstGrowthCagr" in lookback


# ---------------------------------------------------------------------------
# 10. Schema validation tests (Fixes 2.1, 2.2, 2.3, 2.4)
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Tests for Pydantic schema field constraints."""

    def test_api_target_rejects_invalid_string(self):
        """api_target must be 'explore', 'country_pages', or None — not arbitrary strings."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GraphQLQueryClassification(
                reasoning="x",
                query_type="reject",
                api_target="invalid",
            )

    def test_api_target_accepts_valid_values(self):
        """api_target accepts 'explore', 'country_pages', and None."""
        for target in ("explore", "country_pages", None):
            c = GraphQLQueryClassification(
                reasoning="x",
                query_type="reject",
                api_target=target,
            )
            assert c.api_target == target

    def test_product_level_defaults_to_four_digit(self):
        """product_level defaults to 'fourDigit' when not specified."""
        extraction = GraphQLEntityExtraction(reasoning="x")
        assert extraction.product_level == "fourDigit"

    def test_lookback_years_accepts_valid_values(self):
        """lookback_years accepts 3, 5, 10, 15."""
        extraction = GraphQLEntityExtraction(reasoning="x", lookback_years=5)
        assert extraction.lookback_years == 5

    def test_lookback_years_rejects_invalid_value(self):
        """lookback_years=7 is not a valid choice."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GraphQLEntityExtraction(reasoning="x", lookback_years=7)


# ---------------------------------------------------------------------------
# 11. resolve_ids additional tests (Fixes 2.7, 2.8, 2.9, 2.10)
# ---------------------------------------------------------------------------


class TestResolveIdsExtended:
    """Extended tests for resolve_ids fixes."""

    async def test_services_cache_fallback_when_product_cache_misses(self):
        """When product_cache misses, services_cache is tried as fallback."""
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()  # Only goods
        services_cache = CatalogCache("test_services", ttl=3600)
        services_cache.add_index(
            "name",
            key_fn=lambda e: (e.get("nameShortEn") or "").strip().lower() or None,
            normalize_query=lambda q: q.strip().lower(),
        )
        services_cache.add_index(
            "id",
            key_fn=lambda e: str(e["productId"]) if "productId" in e else None,
        )
        services_cache.populate(
            [
                {"productId": 1234, "nameShortEn": "Travel & tourism"},
            ]
        )
        mock_llm = MagicMock()

        state = _base_graphql_state(
            graphql_question="Kenya's travel and tourism exports?",
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(
                product_name="Travel & tourism",
                product_code_guess=None,
            ),
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        assert result["graphql_resolved_params"]["product_id"] == 1234

    async def test_lookback_years_passes_through_to_resolved(self):
        """lookback_years from extraction passes through to resolved params."""
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()
        mock_llm = MagicMock()

        state = _base_graphql_state(
            graphql_question="Kenya growth dynamics over 10 years?",
            graphql_classification=_explore_classification(
                query_type="country_lookback",
                api_target="country_pages",
            ),
            graphql_entity_extraction=_explore_extraction(lookback_years=10),
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        assert result["graphql_resolved_params"]["lookback_years"] == 10

    async def test_resolution_notes_for_unresolved_country(self):
        """Unresolved country adds a resolution note."""
        country_cache = _make_country_cache()
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()
        mock_llm = MagicMock()

        state = _base_graphql_state(
            graphql_question="Narnia's exports?",
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(
                country_name="Narnia",
                country_code_guess="NAR",
            ),
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        notes = result["graphql_resolved_params"].get("resolution_notes", [])
        assert any("Narnia" in n for n in notes)

    async def test_llm_disambiguation_selects_best_match(self):
        """LLM disambiguates when multiple candidates match."""
        # Create cache with two similar entries
        country_cache = CatalogCache("test_country_ambig", ttl=3600)
        country_cache.add_index(
            "iso3",
            key_fn=lambda e: (e.get("iso3Code") or "").upper() or None,
            normalize_query=lambda q: q.strip().upper(),
        )
        country_cache.add_index(
            "name",
            key_fn=lambda e: (e.get("nameShortEn") or "").strip().lower() or None,
            normalize_query=lambda q: q.strip().lower(),
        )
        country_cache.add_index(
            "id",
            key_fn=lambda e: str(e["countryId"]) if "countryId" in e else None,
        )
        country_cache.populate(
            [
                {"countryId": 792, "iso3Code": "TUR", "nameShortEn": "Turkiye"},
                {"countryId": 795, "iso3Code": "TKM", "nameShortEn": "Turkmenistan"},
            ]
        )
        product_cache = _make_product_cache()
        services_cache = _make_services_cache()

        # Mock LLM to return "1" (select first candidate: Turkiye)
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "1"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        state = _base_graphql_state(
            graphql_question="What does Turkey export?",
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(
                country_name="Turk",
                country_code_guess=None,
            ),
        )

        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        # LLM selected "1" → Turkiye (792)
        assert result["graphql_resolved_params"]["country_id"] == 792

        # Now mock LLM to return "2" (select second: Turkmenistan)
        mock_response_2 = MagicMock()
        mock_response_2.content = "2"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response_2)

        result2 = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=country_cache,
            product_cache=product_cache,
            services_cache=services_cache,
        )

        assert result2["graphql_resolved_params"]["country_id"] == 795


# ---------------------------------------------------------------------------
# 12. Builder tests (Fixes 2.5, 2.11)
# ---------------------------------------------------------------------------


class TestBuildersExtended:
    """Extended tests for new and updated query builders."""

    def test_country_lookback_includes_year_range(self):
        """lookback_years=5 maps to yearRange='FiveYears' in variables."""
        query, variables = build_graphql_query(
            "country_lookback",
            {"location": "location-404", "lookback_years": 5},
        )
        assert variables["yearRange"] == "FiveYears"
        assert "countryLookback" in query

    def test_country_lookback_omits_year_range_when_missing(self):
        """Without lookback_years, yearRange is not in variables."""
        query, variables = build_graphql_query(
            "country_lookback",
            {"location": "location-404"},
        )
        assert "yearRange" not in variables
        assert "countryLookback" in query

    def test_growth_opportunities_builder(self):
        """growth_opportunities builder produces productSpace query."""
        query, variables = build_graphql_query(
            "growth_opportunities",
            {"location": "location-404", "year": 2024},
        )
        assert "productSpace" in query
        assert variables["id"] == "location-404"
        assert variables["year"] == 2024

    def test_product_table_builder(self):
        """product_table builder produces countryProductYear query."""
        query, variables = build_graphql_query(
            "product_table",
            {
                "country_id": 404,
                "product_level": "fourDigit",
                "product_class": "HS92",
                "year": 2024,
            },
        )
        assert "countryProductYear" in query
        assert variables["countryId"] == 404
        assert variables["productLevel"] == 4

    def test_all_query_types_have_builders(self):
        """Every query type in the dispatch table should be present."""
        from src.graphql_pipeline import _QUERY_BUILDERS

        expected_builder_types = {
            "treemap_products",
            "treemap_partners",
            "treemap_bilateral",
            "overtime_products",
            "overtime_partners",
            "marketshare",
            "product_space",
            "feasibility",
            "feasibility_table",
            "growth_opportunities",
            "product_table",
            "country_year",
            "product_info",
            "explore_bilateral",
            "explore_group",
            "explore_data_availability",
            "country_profile",
            "country_profile_exports",
            "country_profile_complexity",
            "country_lookback",
            "new_products",
            "global_datum",
        }
        assert set(_QUERY_BUILDERS.keys()) == expected_builder_types


# ---------------------------------------------------------------------------
# 13. Error handling tests (Fixes 2.12, 2.13)
# ---------------------------------------------------------------------------


class TestErrorHandlingExtended:
    """Tests for improved error handling."""

    async def test_build_and_execute_writes_error_dict_to_raw_response(self):
        """GraphQL errors write error dict (not None) to graphql_raw_response."""
        mock_client = MagicMock()
        mock_client.execute = AsyncMock(
            side_effect=GraphQLError("Schema validation error")
        )

        state = _base_graphql_state(
            graphql_classification=_explore_classification(),
            graphql_resolved_params={
                "country_id": 404,
                "product_level": 4,
                "product_class": "HS92",
                "year": 2024,
            },
        )

        result = await build_and_execute_graphql(state, graphql_client=mock_client)

        raw = result["graphql_raw_response"]
        assert isinstance(raw, dict)
        assert "error" in raw
        assert raw["error"] == "graphql_error"
        assert "Schema validation error" in raw["detail"]

    async def test_format_results_shows_extraction_failure(self):
        """When extraction is None but classification is not reject, shows extraction failure."""
        state = _base_graphql_state(
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=None,
            graphql_raw_response=None,
            last_error="",
        )

        result = await format_graphql_results(state)

        content = result["messages"][0].content
        assert "Entity extraction failed" in content

    async def test_format_results_handles_error_dict_in_raw_response(self):
        """Error dicts in raw_response are displayed as error messages."""
        state = _base_graphql_state(
            graphql_classification=_explore_classification(),
            graphql_entity_extraction=_explore_extraction(),
            graphql_raw_response={
                "error": "budget_exhausted",
                "detail": "limit reached",
            },
            last_error="GraphQL API budget exhausted: limit reached",
        )

        result = await format_graphql_results(state)

        content = result["messages"][0].content
        assert "budget_exhausted" in content
        assert "limit reached" in content
