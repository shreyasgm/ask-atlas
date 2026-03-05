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
    GRAPHQL_PIPELINE_NODES,
    MAX_RESPONSE_CHARS,
    GraphQLEntityExtraction,
    GraphQLQueryClassification,
    GraphQLQueryPlan,
    _POST_PROCESS_RULES,
    _QUERY_TYPE_TO_API,
    _strip_id_prefix,
    build_graphql_query,
    build_and_execute_graphql,
    classify_query,
    extract_entities,
    extract_graphql_question,
    format_graphql_results,
    format_ids_for_api,
    plan_query,
    post_process_response,
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
    context: str = "",
) -> AIMessage:
    """Create an AIMessage with a single tool_call for graphql_tool."""
    args: dict = {"question": question}
    if context:
        args["context"] = context
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": tool_call_id,
                "name": "graphql_tool",
                "args": args,
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
    """Create and populate a test product catalog cache (HS92)."""
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


def _make_product_caches() -> dict[str, CatalogCache]:
    """Create product_caches dict with HS92 cache for tests."""
    return {"HS92": _make_product_cache(), "HS12": _make_product_cache()}


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
        assert result["graphql_context"] == ""

    async def test_extracts_context_from_tool_call(self):
        """Context arg is extracted into graphql_context state field."""
        msg = _make_graphql_tool_call_message(
            question="What does Kenya export?",
            context="User is comparing Kenya and Brazil exports.",
        )
        state = _base_graphql_state(messages=[msg])

        result = await extract_graphql_question(state)

        assert result["graphql_question"] == "What does Kenya export?"
        assert (
            result["graphql_context"] == "User is comparing Kenya and Brazil exports."
        )

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
        # graphql_atlas_links uses a reducer and is NOT reset between calls
        assert "graphql_atlas_links" not in result

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
        """The query_type literal must include all valid types + reject."""
        import typing

        field_info = GraphQLQueryClassification.model_fields["query_type"]
        args = typing.get_args(field_info.annotation)
        expected = {
            "country_profile",
            "country_profile_exports",
            "country_profile_partners",
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
            "bilateral_aggregate",
            "explore_bilateral",
            "explore_group",
            "group_products",
            "group_bilateral",
            "group_membership",
            "global_product",
            "global_datum",
            "explore_data_availability",
            "reject",
        }
        assert set(args) == expected

    def test_bilateral_aggregate_validates_with_explore_target(self):
        """bilateral_aggregate is an Explore API query type, so api_target must be 'explore'."""
        c = GraphQLQueryClassification(
            reasoning="Total bilateral trade between two countries",
            query_type="bilateral_aggregate",
            api_target="explore",
        )
        assert c.query_type == "bilateral_aggregate"
        assert c.api_target == "explore"

    def test_classification_prompt_includes_key_query_types(self):
        """build_classification_prompt mentions key query types for routing."""
        from src.prompts import build_classification_prompt

        prompt = build_classification_prompt("test question")
        assert "bilateral_aggregate" in prompt
        assert "country_profile" in prompt
        assert "country_lookback" in prompt


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
        # Verify with_structured_output was called with json_schema method
        mock_llm.with_structured_output.assert_called_once_with(
            GraphQLQueryClassification, method="json_schema"
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

    async def test_context_included_in_classification_prompt(self):
        """When graphql_context is set, it's included in the LLM prompt."""
        classification = GraphQLQueryClassification(
            reasoning="Bilateral trade question",
            query_type="treemap_bilateral",
            rejection_reason=None,
            api_target="explore",
        )
        mock_llm = MagicMock()
        mock_chain = AsyncMock(return_value=classification)
        mock_llm.with_structured_output.return_value.ainvoke = mock_chain

        state = _base_graphql_state(
            graphql_question="What does it export there?",
            graphql_context="User previously asked about Kenya's trade with Brazil.",
        )

        await classify_query(state, lightweight_model=mock_llm)

        prompt = mock_chain.call_args[0][0]
        assert "Kenya" in prompt
        assert "Brazil" in prompt
        assert "What does it export there?" in prompt

    async def test_empty_context_excluded_from_classification_prompt(self):
        """When graphql_context is empty, no context section appears in prompt."""
        classification = GraphQLQueryClassification(
            reasoning="Country profile question",
            query_type="country_profile",
            rejection_reason=None,
            api_target="country_pages",
        )
        mock_llm = MagicMock()
        mock_chain = AsyncMock(return_value=classification)
        mock_llm.with_structured_output.return_value.ainvoke = mock_chain

        state = _base_graphql_state(
            graphql_question="Tell me about Kenya",
            graphql_context="",
        )

        await classify_query(state, lightweight_model=mock_llm)

        prompt = mock_chain.call_args[0][0]
        assert "Context from the conversation" not in prompt


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
        # Verify json_schema method is used
        mock_llm.with_structured_output.assert_called_once_with(
            GraphQLEntityExtraction, method="json_schema"
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

    async def test_context_included_in_extraction_prompt(self):
        """When graphql_context is set, it's included in the LLM prompt."""
        extraction = GraphQLEntityExtraction(
            reasoning="Bilateral trade",
            country_name="Kenya",
            country_code_guess="KEN",
            partner_name="Brazil",
            partner_code_guess="BRA",
        )
        mock_llm = MagicMock()
        mock_chain = AsyncMock(return_value=extraction)
        mock_llm.with_structured_output.return_value.ainvoke = mock_chain

        state = _base_graphql_state(
            graphql_question="What does it export there?",
            graphql_context="User previously asked about Kenya's trade with Brazil.",
            graphql_classification=_explore_classification(),
        )

        await extract_entities(state, lightweight_model=mock_llm)

        prompt = mock_chain.call_args[0][0]
        assert "Kenya" in prompt
        assert "Brazil" in prompt
        assert "What does it export there?" in prompt

    async def test_empty_context_excluded_from_extraction_prompt(self):
        """When graphql_context is empty, no context section appears in prompt."""
        extraction = GraphQLEntityExtraction(
            reasoning="Kenya exports",
            country_name="Kenya",
            country_code_guess="KEN",
        )
        mock_llm = MagicMock()
        mock_chain = AsyncMock(return_value=extraction)
        mock_llm.with_structured_output.return_value.ainvoke = mock_chain

        state = _base_graphql_state(
            graphql_question="What does Kenya export?",
            graphql_context="",
            graphql_classification=_explore_classification(),
        )

        await extract_entities(state, lightweight_model=mock_llm)

        prompt = mock_chain.call_args[0][0]
        assert "Context from the conversation" not in prompt


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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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

    def test_country_profile_exports_uses_treemap_builder(self):
        """country_profile_exports uses treeMap(CPY_C) for product-level data."""
        query, variables = build_graphql_query(
            "country_profile_exports",
            {"location": "location-404"},
        )
        assert "treeMap" in query
        assert "CPY_C" in query
        assert "exportValue" in query
        assert variables["location"] == "location-404"

    def test_country_profile_partners_uses_treemap_builder(self):
        """country_profile_partners uses treeMap(CCY_C) for partner-level data."""
        query, variables = build_graphql_query(
            "country_profile_partners",
            {"location": "location-404"},
        )
        assert "treeMap" in query
        assert "CCY_C" in query
        assert "exportValue" in query
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

    def test_explore_api_strips_prefixed_country_id(self):
        """Catalog may store countryId as 'country-76'; explore needs int 76."""
        result = format_ids_for_api(
            {"country_id": "country-76", "year": 2022},
            "explore",
        )
        assert result["country_id"] == 76
        assert result["year"] == 2022

    def test_explore_api_strips_prefixed_product_id(self):
        result = format_ids_for_api(
            {"product_id": "product-726", "country_id": "country-404"},
            "explore",
        )
        assert result["product_id"] == 726
        assert result["country_id"] == 404

    def test_explore_api_strips_prefixed_partner_id(self):
        result = format_ids_for_api(
            {"country_id": 404, "partner_id": "country-76"},
            "explore",
        )
        assert result["partner_id"] == 76

    def test_country_pages_handles_prefixed_ids(self):
        """Even if catalog gives prefixed IDs, country_pages output is correct."""
        result = format_ids_for_api(
            {"country_id": "country-404", "product_id": "product-726"},
            "country_pages",
        )
        assert result["location"] == "location-404"
        assert result["product"] == "product-HS-726"

    def test_does_not_mutate_input_dict(self):
        original = {"country_id": 404, "year": 2024}
        format_ids_for_api(original, "country_pages")
        # Original dict should be unchanged
        assert "country_id" in original
        assert "location" not in original


class TestStripIdPrefix:
    """Tests for _strip_id_prefix helper."""

    def test_integer_passthrough(self):
        assert _strip_id_prefix(76) == 76

    def test_country_prefix(self):
        assert _strip_id_prefix("country-76") == 76

    def test_location_prefix(self):
        assert _strip_id_prefix("location-404") == 404

    def test_product_hs_prefix(self):
        assert _strip_id_prefix("product-HS-726") == 726

    def test_plain_numeric_string(self):
        assert _strip_id_prefix("404") == 404

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _strip_id_prefix("not-a-number-abc")


# ---------------------------------------------------------------------------
# 10. post_process_response
# ---------------------------------------------------------------------------


class TestPostProcessResponse:
    """Tests for post_process_response — sort, truncate, enrich large API responses."""

    def test_small_response_passes_through_unchanged(self):
        """Response with <= top_n items returns as-is (no truncation)."""
        raw = {
            "countryProductYear": [
                {"productId": 726, "exportValue": 5000, "year": 2024},
                {"productId": 897, "exportValue": 3000, "year": 2024},
            ]
        }
        result = post_process_response("treemap_products", raw)
        assert result == raw

    def test_sorts_by_export_value_descending_and_truncates(self):
        """treemap_products with 100 items → returns top 20 sorted by exportValue."""
        items = [
            {"productId": i, "exportValue": i * 100, "year": 2024} for i in range(100)
        ]
        raw = {"countryProductYear": items}
        result = post_process_response("treemap_products", raw)

        processed_items = result["countryProductYear"]
        assert len(processed_items) == 20
        # Top item has highest exportValue (99 * 100 = 9900)
        assert processed_items[0]["exportValue"] == 9900
        # Items are sorted descending
        values = [item["exportValue"] for item in processed_items]
        assert values == sorted(values, reverse=True)

    def test_enriches_product_ids_with_names_from_cache(self):
        """productId → adds productName and productCode from cache."""
        items = [
            {"productId": i, "exportValue": (100 - i) * 100, "year": 2024}
            for i in range(30)
        ]
        # Include a known product ID (726 = Coffee)
        items[0]["productId"] = 726
        items[0]["exportValue"] = 999999
        raw = {"countryProductYear": items}

        product_cache = _make_product_cache()
        result = post_process_response(
            "treemap_products", raw, product_caches={"HS92": product_cache}
        )

        top_item = result["countryProductYear"][0]
        assert top_item["productName"] == "Coffee"
        assert top_item["productCode"] == "0901"

    def test_enriches_country_ids_with_names_from_cache(self):
        """partnerCountryId → adds partnerName from cache."""
        items = [
            {"partnerCountryId": i, "exportValue": (100 - i) * 100, "year": 2024}
            for i in range(30)
        ]
        # Include a known country ID (76 = Brazil)
        items[0]["partnerCountryId"] = 76
        items[0]["exportValue"] = 999999
        raw = {"countryCountryYear": items}

        country_cache = _make_country_cache()
        result = post_process_response(
            "treemap_partners", raw, country_cache=country_cache
        )

        top_item = result["countryCountryYear"][0]
        assert top_item["partnerName"] == "Brazil"

    def test_feasibility_filter_keeps_only_rca_lt_1(self):
        """Feasibility query filters to products where exportRca < 1, sorts by cog desc."""
        items = [
            {"productId": 1, "exportRca": 0.5, "cog": 0.9, "exportValue": 100},
            {
                "productId": 2,
                "exportRca": 1.5,
                "cog": 0.8,
                "exportValue": 200,
            },  # filtered out
            {"productId": 3, "exportRca": 0.0, "cog": 0.7, "exportValue": 300},
            {"productId": 4, "exportRca": 0.3, "cog": 0.95, "exportValue": 50},
            {
                "productId": 5,
                "exportRca": 2.0,
                "cog": 0.99,
                "exportValue": 500,
            },  # filtered out
        ] + [
            {"productId": 100 + i, "exportRca": 0.1, "cog": 0.01 * i, "exportValue": 10}
            for i in range(20)
        ]
        raw = {"countryProductYear": items}

        result = post_process_response("feasibility", raw)

        processed = result["countryProductYear"]
        # RCA >= 1 items should be excluded
        for item in processed:
            assert (item.get("exportRca") or 0) < 1
        # Should be sorted by cog descending
        cog_values = [item["cog"] for item in processed]
        assert cog_values == sorted(cog_values, reverse=True)

    def test_includes_metadata(self):
        """Truncated response includes _postProcessed metadata."""
        items = [
            {"productId": i, "exportValue": i * 100, "year": 2024} for i in range(50)
        ]
        raw = {"countryProductYear": items}

        result = post_process_response("treemap_products", raw)

        meta = result["_postProcessed"]
        assert meta["totalItems"] == 50
        assert meta["shownItems"] == 20
        assert meta["sortField"] == "exportValue"

    def test_unknown_query_type_passes_through(self):
        """Query types without post-processing config return raw response unchanged."""
        raw = {"someData": [{"value": 42}]}
        result = post_process_response("country_profile", raw)
        assert result == raw

    def test_handles_null_sort_field_gracefully(self):
        """Items with null exportValue sort to end."""
        items = [
            {"productId": 1, "exportValue": None, "year": 2024},
            {"productId": 2, "exportValue": 5000, "year": 2024},
            {"productId": 3, "exportValue": None, "year": 2024},
            {"productId": 4, "exportValue": 3000, "year": 2024},
        ] + [
            {"productId": 10 + i, "exportValue": (30 - i) * 100, "year": 2024}
            for i in range(20)
        ]
        raw = {"countryProductYear": items}

        result = post_process_response("treemap_products", raw)

        processed = result["countryProductYear"]
        # Non-null values should come first and be sorted descending
        non_null_values = [
            item["exportValue"] for item in processed if item["exportValue"] is not None
        ]
        assert non_null_values == sorted(non_null_values, reverse=True)


# ---------------------------------------------------------------------------
# 10b. post_process_response: multi-level product enrichment
# ---------------------------------------------------------------------------


class TestPostProcessSectionLevelEnrichment:
    """post_process_response enriches section/chapter-level product IDs correctly."""

    def test_enriches_section_level_product_ids(self):
        """Section-level (level 1) product IDs get enriched when in cache."""
        cache = CatalogCache("multi_level", ttl=3600)
        cache.add_index(
            "id",
            key_fn=lambda e: str(e["productId"]) if "productId" in e else None,
        )
        cache.add_index(
            "code",
            key_fn=lambda e: (e.get("code") or "").strip() or None,
            normalize_query=lambda q: q.strip(),
        )
        cache.populate(
            [
                {
                    "productId": 1,
                    "productLevel": 1,
                    "code": "1",
                    "nameShortEn": "Animal & Animal Products",
                },
                {
                    "productId": 2,
                    "productLevel": 1,
                    "code": "2",
                    "nameShortEn": "Vegetable Products",
                },
                {
                    "productId": 726,
                    "productLevel": 4,
                    "code": "0901",
                    "nameShortEn": "Coffee",
                },
            ]
        )

        items = [
            {"productId": i, "exportValue": (100 - i) * 1000, "year": 2024}
            for i in range(1, 25)
        ]
        raw = {"countryProductYear": items}

        result = post_process_response(
            "treemap_products", raw, product_caches={"HS92": cache}
        )

        processed = result["countryProductYear"]
        # Product ID 1 (section level) should be enriched
        item_1 = next(i for i in processed if i["productId"] == 1)
        assert item_1["productName"] == "Animal & Animal Products"
        assert item_1["productCode"] == "1"

        # Product ID 2 (section level) should also be enriched
        item_2 = next(i for i in processed if i["productId"] == 2)
        assert item_2["productName"] == "Vegetable Products"


# ---------------------------------------------------------------------------
# 11. Slim query builders
# ---------------------------------------------------------------------------


class TestSlimQueryBuilders:
    """Tests that slim query builders request only essential fields."""

    def test_treemap_products_requests_only_essential_fields(self):
        """treemap_products query should have slim fields, not normalized* fields."""
        query, _ = build_graphql_query(
            "treemap_products",
            {
                "country_id": 404,
                "product_level": 4,
                "product_class": "HS92",
                "year": 2024,
            },
        )
        assert "productId" in query
        assert "exportValue" in query
        assert "year" in query
        # Should NOT contain heavy analytical fields
        assert "normalizedPci" not in query
        assert "normalizedCog" not in query
        assert "normalizedDistance" not in query
        assert "normalizedExportRca" not in query

    def test_treemap_partners_requests_only_essential_fields(self):
        """treemap_partners should have slim fields."""
        query, _ = build_graphql_query(
            "treemap_partners",
            {"country_id": 404, "year": 2024},
        )
        assert "partnerCountryId" in query
        assert "exportValue" in query
        assert "year" in query
        # Should NOT contain reported values
        assert "exportValueReported" not in query
        assert "importValueReported" not in query

    def test_treemap_bilateral_requests_only_essential_fields(self):
        """treemap_bilateral should have slim fields."""
        query, _ = build_graphql_query(
            "treemap_bilateral",
            {
                "country_id": 404,
                "partner_id": 76,
                "product_level": 4,
                "product_class": "HS92",
                "year": 2024,
            },
        )
        assert "productId" in query
        assert "exportValue" in query
        # Should NOT contain import reported values
        assert "importValueReported" not in query

    def test_feasibility_requests_rca_and_complexity_fields(self):
        """feasibility query should request RCA + complexity fields only."""
        query, _ = build_graphql_query(
            "feasibility",
            {
                "country_id": 404,
                "product_level": 4,
                "product_class": "HS92",
                "year": 2024,
            },
        )
        assert "productId" in query
        assert "exportRca" in query
        assert "cog" in query
        assert "distance" in query
        assert "exportValue" in query
        assert "year" in query
        # Should NOT contain normalized fields
        assert "normalizedCog" not in query
        assert "normalizedDistance" not in query
        assert "normalizedExportRca" not in query


# ---------------------------------------------------------------------------
# Integration tests — real LLM + real Atlas GraphQL API
# ---------------------------------------------------------------------------

ATLAS_EXPLORE_URL = "https://atlas.hks.harvard.edu/api/graphql"
ATLAS_COUNTRY_PAGES_URL = "https://atlas.hks.harvard.edu/api/countries/graphql"


@pytest.mark.integration
class TestPlanQueryIntegration:
    """Run plan_query (merged classify + extract) with a real LLM.

    Verifies that the combined GraphQLQueryPlan schema, merged prompt,
    and LLM interaction produce reasonable classification + entities
    in a single call.
    """

    @pytest.fixture()
    def lightweight_model(self):
        from src.config import get_prompt_model

        return get_prompt_model("graphql_classification")

    async def test_kenya_exports_classified_and_extracted(self, lightweight_model):
        """'What did Kenya export in 2024?' → sensible classification + entities in one call."""
        state = _base_graphql_state(graphql_question="What did Kenya export in 2024?")

        result = await plan_query(state, lightweight_model=lightweight_model)

        classification = result["graphql_classification"]
        assert classification["query_type"] != "reject", (
            f"Expected a trade-related classification, got reject: "
            f"{classification.get('rejection_reason')}"
        )
        assert classification["query_type"] in {
            "treemap_products",
            "country_profile",
            "country_profile_exports",
            "country_year",
            "overtime_products",
        }
        assert classification["api_target"] in {"explore", "country_pages"}

        extraction = result["graphql_entity_extraction"]
        assert extraction is not None, "Entity extraction returned None"
        assert extraction["country_name"] is not None
        assert "kenya" in extraction["country_name"].lower()
        assert extraction["country_code_guess"] == "KEN"

    async def test_bilateral_trade_question(self, lightweight_model):
        """'What does the US export to China?' → bilateral classification + both countries."""
        state = _base_graphql_state(
            graphql_question="What does the United States export to China?"
        )

        result = await plan_query(state, lightweight_model=lightweight_model)

        classification = result["graphql_classification"]
        assert classification["query_type"] != "reject"
        assert classification["query_type"] in {
            "treemap_bilateral",
            "explore_bilateral",
            "treemap_products",
        }

        extraction = result["graphql_entity_extraction"]
        assert extraction is not None
        assert extraction["country_name"] is not None
        assert extraction["partner_name"] is not None

    async def test_reject_non_trade_question(self, lightweight_model):
        """A clearly non-trade question should be rejected with no entities."""
        state = _base_graphql_state(graphql_question="What is the meaning of life?")

        result = await plan_query(state, lightweight_model=lightweight_model)

        classification = result["graphql_classification"]
        assert classification["query_type"] == "reject"
        assert classification["rejection_reason"] is not None
        assert result["graphql_entity_extraction"] is None

    async def test_import_direction_detected(self, lightweight_model):
        """'What is the top imported product for USA?' → trade_direction: imports."""
        state = _base_graphql_state(
            graphql_question="What is the top imported product for the United States?"
        )

        result = await plan_query(state, lightweight_model=lightweight_model)

        classification = result["graphql_classification"]
        assert classification["query_type"] != "reject"

        extraction = result["graphql_entity_extraction"]
        assert extraction is not None
        assert (
            extraction.get("trade_direction") == "imports"
        ), f"Expected trade_direction='imports', got {extraction.get('trade_direction')!r}"
        assert extraction["country_name"] is not None

    async def test_group_membership_classified(self, lightweight_model):
        """'Which countries belong to the EU?' → group_membership."""
        state = _base_graphql_state(
            graphql_question="Which countries belong to the EU?"
        )

        result = await plan_query(state, lightweight_model=lightweight_model)

        classification = result["graphql_classification"]
        assert classification["query_type"] == "group_membership", (
            f"Expected group_membership, got {classification['query_type']!r} "
            f"(reasoning: {classification.get('reasoning', 'n/a')})"
        )

        extraction = result["graphql_entity_extraction"]
        assert extraction is not None
        assert extraction.get("group_name") is not None
        assert "eu" in extraction["group_name"].lower()

    @pytest.mark.parametrize(
        "question, expected_types",
        [
            pytest.param(
                "What is Nigeria's diversification grade?",
                {"country_profile"},
                id="diversification_grade_routes_to_country_profile",
            ),
            pytest.param(
                "Is Thailand's export growth pattern promising or troubling?",
                {"country_lookback"},
                id="export_growth_classification_routes_to_country_lookback",
            ),
            pytest.param(
                "What is the total export value from Brazil to China in 2023?",
                {"bilateral_aggregate"},
                id="bilateral_aggregate_trade_value",
            ),
            pytest.param(
                "What does Kenya export to the EU?",
                {"group_products"},
                id="country_to_group_products",
            ),
            pytest.param(
                "How have Brazil's coffee exports changed since 2010?",
                {"overtime_products"},
                id="overtime_products_with_product",
            ),
        ],
    )
    async def test_routing_improvements(
        self, lightweight_model, question, expected_types
    ):
        """Verify plan_query routes various question types correctly."""
        state = _base_graphql_state(graphql_question=question)

        result = await plan_query(state, lightweight_model=lightweight_model)

        classification = result["graphql_classification"]
        assert classification["query_type"] != "reject", (
            f"Expected one of {expected_types} for '{question}', "
            f"got reject: {classification.get('rejection_reason')}"
        )
        assert classification["query_type"] in expected_types, (
            f"Expected one of {expected_types} for '{question}', "
            f"got {classification['query_type']!r} "
            f"(reasoning: {classification.get('reasoning', 'n/a')})"
        )


@pytest.mark.integration
class TestResolveIdsIntegration:
    """Run plan_query → resolve_ids with real LLM and CatalogCache data.

    Tests that the merged plan_query output feeds correctly into
    resolve_ids, producing valid internal Atlas IDs.
    """

    @pytest.fixture()
    def lightweight_model(self):
        from src.config import get_prompt_model

        return get_prompt_model("id_resolution_selection")

    @pytest.fixture()
    def classification_model(self):
        from src.config import get_prompt_model

        return get_prompt_model("graphql_classification")

    async def test_resolves_kenya_by_code_and_name(self, lightweight_model):
        """Kenya (KEN) resolves to countryId 404 via cache lookup."""
        country_cache = _make_country_cache()
        product_caches = _make_product_caches()
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
            product_caches=product_caches,
            services_cache=services_cache,
        )

        params = result["graphql_resolved_params"]
        assert params is not None
        assert params["country_id"] == 404
        assert params["year"] == 2024

    async def test_end_to_end_plan_and_resolve(
        self, lightweight_model, classification_model
    ):
        """Full LLM pipeline: plan_query → resolve_ids for a known question."""
        country_cache = _make_country_cache()
        product_caches = _make_product_caches()
        services_cache = _make_services_cache()

        state = _base_graphql_state(graphql_question="What did Kenya export in 2024?")

        # Step 1: plan_query (classify + extract in one call)
        plan_result = await plan_query(state, lightweight_model=classification_model)
        state.update(plan_result)
        assert state["graphql_classification"]["query_type"] != "reject"
        assert state["graphql_entity_extraction"] is not None

        # Step 2: resolve IDs
        res_result = await resolve_ids(
            state,
            lightweight_model=lightweight_model,
            country_cache=country_cache,
            product_caches=product_caches,
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

    async def test_import_direction_preserved_through_resolve(
        self, lightweight_model, classification_model
    ):
        """plan_query → resolve_ids preserves trade_direction=imports in resolved params."""
        country_cache = _make_country_cache()
        product_caches = _make_product_caches()
        services_cache = _make_services_cache()

        state = _base_graphql_state(
            graphql_question="What is the top imported product for Kenya?"
        )

        plan_result = await plan_query(state, lightweight_model=classification_model)
        state.update(plan_result)

        assert state["graphql_classification"]["query_type"] != "reject"

        res_result = await resolve_ids(
            state,
            lightweight_model=lightweight_model,
            country_cache=country_cache,
            product_caches=product_caches,
            services_cache=services_cache,
        )

        params = res_result["graphql_resolved_params"]
        assert params is not None
        assert (
            params.get("trade_direction") == "imports"
        ), f"Expected trade_direction='imports' in resolved params, got {params.get('trade_direction')!r}"


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

    async def test_group_membership_against_real_api(self, explore_client):
        """locationGroup query returns groups with members list."""
        state = _base_graphql_state(
            graphql_classification=_explore_classification(
                query_type="group_membership",
                api_target="explore",
            ),
            graphql_resolved_params={"group_type": "trade"},
        )

        result = await build_and_execute_graphql(state, graphql_client=explore_client)

        assert result["last_error"] == "", f"API error: {result['last_error']}"
        response = result["graphql_raw_response"]
        assert response is not None
        assert "locationGroup" in response
        groups = response["locationGroup"]
        assert len(groups) > 0, "Expected at least one trade group"
        # Each group should have members
        sample_group = groups[0]
        assert "groupName" in sample_group
        assert "members" in sample_group

    async def test_treemap_products_includes_import_value(self, explore_client):
        """countryProductYear slim builder now includes importValue for direction support."""
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
        rows = response["countryProductYear"]
        assert len(rows) > 0
        row = rows[0]
        assert "exportValue" in row
        assert (
            "importValue" in row
        ), "Slim builder should include importValue for direction support"


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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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
            product_caches={"HS92": product_cache},
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
        """growth_opportunities builder produces productSpace query with correct args."""
        query, variables = build_graphql_query(
            "growth_opportunities",
            {"location": "location-404", "year": 2024},
        )
        assert "productSpace" in query
        assert variables["location"] == "location-404"
        assert variables["productClass"] == "HS"  # Country Pages uses "HS" not "HS92"
        assert variables["year"] == 2024
        # Must NOT use the old $id variable
        assert "$id" not in query
        assert "id" not in variables
        # Must use $location and $productClass
        assert "$location: ID!" in query
        assert "$productClass: ProductClass!" in query

    def test_growth_opportunities_uses_valid_product_space_fields(self):
        """growth_opportunities must only request fields that exist on ProductSpaceDatum.

        ProductSpaceDatum fields (from Country Pages API introspection):
        product, exportValue, importValue, rca, x, y, connections
        """
        query, _ = build_graphql_query(
            "growth_opportunities",
            {"location": "location-404", "year": 2024},
        )
        # These fields DO exist on ProductSpaceDatum
        assert "exportValue" in query
        assert "rca" in query
        assert "importValue" in query
        # These fields do NOT exist — builder must not request them
        assert (
            "exportRca" not in query
        ), "exportRca does not exist on ProductSpaceDatum — use 'rca' instead"
        assert (
            " cog" not in query and "\ncog" not in query
        ), "cog does not exist on ProductSpaceDatum"
        assert "cogRank" not in query, "cogRank does not exist on ProductSpaceDatum"
        assert "distance" not in query, "distance does not exist on ProductSpaceDatum"
        assert (
            "distanceRank" not in query
        ), "distanceRank does not exist on ProductSpaceDatum"

    def test_growth_opportunities_builder_custom_product_class(self):
        """growth_opportunities builder respects product_class param."""
        query, variables = build_graphql_query(
            "growth_opportunities",
            {"location": "location-76", "product_class": "SITC"},
        )
        assert variables["productClass"] == "SITC"
        assert "year" not in variables

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
            "bilateral_aggregate",
            "explore_bilateral",
            "explore_group",
            "group_products",
            "group_bilateral",
            "group_membership",
            "explore_data_availability",
            "country_profile",
            "country_profile_exports",
            "country_profile_partners",
            "country_profile_complexity",
            "country_lookback",
            "new_products",
            "global_datum",
            "global_product",
        }
        assert set(_QUERY_BUILDERS.keys()) == expected_builder_types

    def test_bilateral_aggregate_includes_partner_filter(self):
        """bilateral_aggregate builder passes partnerCountryId when partner_id is given."""
        query, variables = build_graphql_query(
            "bilateral_aggregate",
            {"country_id": 76, "partner_id": 156, "year": 2024},
        )
        assert "countryCountryYear" in query
        assert "partnerCountryId" in query
        assert variables["countryId"] == 76
        assert variables["partnerCountryId"] == 156
        assert variables["yearMin"] == 2024
        assert variables["yearMax"] == 2024

    def test_treemap_partners_omits_partner_when_absent(self):
        """treemap_partners (countryCountryYear) omits partnerCountryId when not provided."""
        query, variables = build_graphql_query(
            "treemap_partners",
            {"country_id": 404, "year": 2024},
        )
        assert "countryCountryYear" in query
        assert variables["countryId"] == 404
        assert "partnerCountryId" not in variables


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


# ---------------------------------------------------------------------------
# 14. Bug-fix regression tests
# ---------------------------------------------------------------------------


class TestBugFixRegressions:
    """Regression tests for specific bug fixes."""

    def test_group_year_uses_gdpPpp_not_gdppc(self):
        """GroupYear query must request gdpPpp (valid field), not gdppc (invalid)."""
        query, variables = build_graphql_query(
            "explore_group",
            {"group_id": 1, "group_type": "continent", "year": 2024},
        )
        assert "gdpPpp" in query
        assert "gdppc" not in query

    def test_post_process_unpopulated_product_cache_no_crash(self):
        """Unpopulated product cache should not raise RuntimeError."""
        unpopulated_cache = CatalogCache("empty_product", ttl=3600)
        unpopulated_cache.add_index(
            "id",
            key_fn=lambda e: str(e["productId"]) if "productId" in e else None,
        )
        # Don't call populate() — cache is empty

        items = [
            {"productId": i, "exportValue": (100 - i) * 100, "year": 2024}
            for i in range(30)
        ]
        raw = {"countryProductYear": items}

        # Should NOT raise RuntimeError
        result = post_process_response(
            "treemap_products", raw, product_caches={"HS92": unpopulated_cache}
        )

        # Items returned but without enrichment
        processed = result["countryProductYear"]
        assert len(processed) == 20
        assert "productName" not in processed[0]

    def test_post_process_unpopulated_country_cache_no_crash(self):
        """Unpopulated country cache should not raise RuntimeError."""
        unpopulated_cache = CatalogCache("empty_country", ttl=3600)
        unpopulated_cache.add_index(
            "id",
            key_fn=lambda e: str(e["countryId"]) if "countryId" in e else None,
        )
        # Don't call populate()

        items = [
            {"partnerCountryId": i, "exportValue": (100 - i) * 100, "year": 2024}
            for i in range(30)
        ]
        raw = {"countryCountryYear": items}

        # Should NOT raise RuntimeError
        result = post_process_response(
            "treemap_partners", raw, country_cache=unpopulated_cache
        )

        processed = result["countryCountryYear"]
        assert len(processed) == 20
        assert "partnerName" not in processed[0]


# ---------------------------------------------------------------------------
# 15. CGPY / GCPY query builder tests
# ---------------------------------------------------------------------------


class TestCGPYGCPYBuilders:
    """Tests for the new countryGroupProductYear and groupCountryProductYear builders."""

    def test_group_products_builder_basic(self):
        """group_products builds countryGroupProductYear query with correct variables."""
        query, variables = build_graphql_query(
            "group_products",
            {
                "country_id": 404,
                "partner_group_id": 5,
                "product_level": "fourDigit",
                "product_class": "HS92",
                "year": 2023,
            },
        )
        assert "countryGroupProductYear" in query
        assert variables["countryId"] == 404
        assert variables["partnerGroupId"] == 5
        assert variables["productLevel"] == 4
        assert variables["productClass"] == "HS92"
        assert variables["yearMin"] == 2023
        assert variables["yearMax"] == 2023

    def test_group_products_builder_defaults(self):
        """group_products uses default year and product class when not specified."""
        from src.prompts import GRAPHQL_DATA_MAX_YEAR

        query, variables = build_graphql_query(
            "group_products",
            {"country_id": 404, "partner_group_id": 5},
        )
        assert "countryGroupProductYear" in query
        assert variables["yearMin"] == GRAPHQL_DATA_MAX_YEAR
        assert variables["yearMax"] == GRAPHQL_DATA_MAX_YEAR
        assert variables["productClass"] == "HS12"

    def test_group_bilateral_builder_basic(self):
        """group_bilateral builds groupCountryProductYear query with correct variables."""
        query, variables = build_graphql_query(
            "group_bilateral",
            {
                "group_id": 5,
                "partner_id": 404,
                "product_level": "fourDigit",
                "product_class": "HS92",
                "year": 2023,
            },
        )
        assert "groupCountryProductYear" in query
        assert variables["groupId"] == 5
        assert variables["partnerCountryId"] == 404
        assert variables["productLevel"] == 4
        assert variables["productClass"] == "HS92"
        assert variables["yearMin"] == 2023
        assert variables["yearMax"] == 2023

    def test_group_bilateral_builder_defaults(self):
        """group_bilateral uses default year and product class when not specified."""
        from src.prompts import GRAPHQL_DATA_MAX_YEAR

        query, variables = build_graphql_query(
            "group_bilateral",
            {"group_id": 5, "partner_id": 404},
        )
        assert "groupCountryProductYear" in query
        assert variables["yearMin"] == GRAPHQL_DATA_MAX_YEAR
        assert variables["yearMax"] == GRAPHQL_DATA_MAX_YEAR
        assert variables["productClass"] == "HS12"

    def test_group_products_post_process(self):
        """group_products post-processing sorts by exportValue and truncates."""
        items = [
            {"productId": i, "exportValue": (100 - i) * 1000, "year": 2023}
            for i in range(30)
        ]
        raw = {"countryGroupProductYear": items}
        result = post_process_response("group_products", raw)
        processed = result["countryGroupProductYear"]
        assert len(processed) == 20
        assert processed[0]["exportValue"] >= processed[-1]["exportValue"]

    def test_group_bilateral_post_process(self):
        """group_bilateral post-processing sorts by exportValue and truncates."""
        items = [
            {"productId": i, "exportValue": (100 - i) * 1000, "year": 2023}
            for i in range(30)
        ]
        raw = {"groupCountryProductYear": items}
        result = post_process_response("group_bilateral", raw)
        processed = result["groupCountryProductYear"]
        assert len(processed) == 20
        assert processed[0]["exportValue"] >= processed[-1]["exportValue"]


# ---------------------------------------------------------------------------
# 16. Correctness fix tests (2C, 2D, 4B, 4D)
# ---------------------------------------------------------------------------


class TestCorrectnessFixTests:
    """Tests for Priority 1 correctness fixes."""

    def test_format_ids_country_pages_sitc_product_prefix(self):
        """Country Pages product prefix uses SITC when product_class is SITC (Fix 2C)."""
        params = {
            "country_id": 404,
            "product_id": 5412,
            "product_class": "SITC",
        }
        result = format_ids_for_api(params, "country_pages")
        assert result["product"] == "product-SITC-5412"

    def test_format_ids_country_pages_hs_product_prefix(self):
        """Country Pages product prefix uses HS when product_class is HS92 (Fix 2C)."""
        params = {
            "country_id": 404,
            "product_id": 726,
            "product_class": "HS92",
        }
        result = format_ids_for_api(params, "country_pages")
        assert result["product"] == "product-HS-726"

    def test_format_ids_country_pages_default_hs_prefix(self):
        """Country Pages product prefix defaults to HS when product_class absent (Fix 2C)."""
        params = {
            "country_id": 404,
            "product_id": 726,
        }
        result = format_ids_for_api(params, "country_pages")
        assert result["product"] == "product-HS-726"

    def test_services_cache_fallback_in_enrichment(self):
        """Product enrichment falls back to services_cache when product not in product_cache (Fix 2D)."""
        # Empty product cache
        product_cache = CatalogCache("products", ttl=3600)
        product_cache.add_index(
            "id",
            key_fn=lambda e: str(e["productId"]) if "productId" in e else None,
        )
        product_cache.populate([])

        # Services cache with a service product
        services_cache = CatalogCache("services", ttl=3600)
        services_cache.add_index(
            "id",
            key_fn=lambda e: str(e["productId"]) if "productId" in e else None,
        )
        services_cache.populate(
            [
                {"productId": 14, "nameShortEn": "Total Services", "code": "services"},
            ]
        )

        items = [
            {"productId": 14, "exportValue": 50000, "year": 2024},
            {"productId": 400, "exportValue": 30000, "year": 2024},
        ] + [
            {"productId": i + 100, "exportValue": (100 - i) * 100, "year": 2024}
            for i in range(25)
        ]
        raw = {"countryProductYear": items}

        result = post_process_response(
            "treemap_products",
            raw,
            product_caches={"HS92": product_cache},
            services_cache=services_cache,
        )
        processed = result["countryProductYear"]
        # The service product (id=14) should get enriched via services_cache
        service_item = next((i for i in processed if i["productId"] == 14), None)
        assert service_item is not None
        assert service_item["productName"] == "Total Services"

    def test_year_defaults_use_constant(self):
        """Builder year defaults use GRAPHQL_DATA_MAX_YEAR, not hardcoded 2024 (Fix 4D)."""
        from src.prompts import GRAPHQL_DATA_MAX_YEAR

        query, variables = build_graphql_query(
            "treemap_products",
            {"country_id": 404},
        )
        assert variables["yearMin"] == GRAPHQL_DATA_MAX_YEAR
        assert variables["yearMax"] == GRAPHQL_DATA_MAX_YEAR

    def test_explore_api_strips_group_id_prefixes(self):
        """Explore API format_ids strips prefixes from group_id and partner_group_id."""
        params = {
            "country_id": "country-404",
            "partner_group_id": "group-5",
            "group_id": "group-3",
        }
        result = format_ids_for_api(params, "explore")
        assert result["country_id"] == 404
        assert result["partner_group_id"] == 5
        assert result["group_id"] == 3


# ---------------------------------------------------------------------------
# 17. Deterministic api_target override tests (Fix 4B)
# ---------------------------------------------------------------------------


class TestDeterministicApiTarget:
    """Tests for the deterministic api_target override after classification."""

    def test_treemap_products_always_explore(self):
        """treemap_products should always target explore, regardless of LLM output."""
        from src.graphql_pipeline import _QUERY_TYPE_TO_API

        assert _QUERY_TYPE_TO_API["treemap_products"] == "explore"

    def test_country_profile_always_country_pages(self):
        """country_profile should always target country_pages."""
        from src.graphql_pipeline import _QUERY_TYPE_TO_API

        assert _QUERY_TYPE_TO_API["country_profile"] == "country_pages"

    def test_group_products_always_explore(self):
        """group_products should always target explore."""
        from src.graphql_pipeline import _QUERY_TYPE_TO_API

        assert _QUERY_TYPE_TO_API["group_products"] == "explore"

    def test_group_bilateral_always_explore(self):
        """group_bilateral should always target explore."""
        from src.graphql_pipeline import _QUERY_TYPE_TO_API

        assert _QUERY_TYPE_TO_API["group_bilateral"] == "explore"

    def test_all_query_types_have_api_target(self):
        """Every non-reject query type should have a deterministic api_target mapping."""
        from src.graphql_pipeline import _QUERY_BUILDERS, _QUERY_TYPE_TO_API

        for qt in _QUERY_BUILDERS:
            assert (
                qt in _QUERY_TYPE_TO_API
            ), f"Query type {qt!r} missing from _QUERY_TYPE_TO_API"


# ---------------------------------------------------------------------------
# 18. Entity extraction schema tests (new fields)
# ---------------------------------------------------------------------------


class TestEntityExtractionNewFields:
    """Tests for new partner_group fields in GraphQLEntityExtraction."""

    def test_partner_group_fields_exist(self):
        """GraphQLEntityExtraction should have partner_group_name and partner_group_type."""
        extraction = GraphQLEntityExtraction(
            reasoning="Testing partner group fields",
            country_name="Kenya",
            partner_group_name="EU",
            partner_group_type="trade",
        )
        assert extraction.partner_group_name == "EU"
        assert extraction.partner_group_type == "trade"

    def test_partner_group_fields_default_none(self):
        """Partner group fields default to None."""
        extraction = GraphQLEntityExtraction(
            reasoning="Testing defaults",
            country_name="Kenya",
        )
        assert extraction.partner_group_name is None
        assert extraction.partner_group_type is None


# ---------------------------------------------------------------------------
# 19. Atlas link tests for new query types
# ---------------------------------------------------------------------------


class TestAtlasLinksGroupQueries:
    """Tests for atlas links generated by group_products and group_bilateral."""

    def test_group_products_link(self):
        """group_products generates a treemap explore link with country exporter and group importer."""
        from src.atlas_links import generate_atlas_links

        links = generate_atlas_links(
            "group_products",
            {
                "country_id": 404,
                "country_name": "Kenya",
                "partner_group_id": 5,
                "partner_group_name": "EU",
                "year": 2023,
            },
        )
        assert len(links) == 1
        link = links[0]
        assert "country-404" in link.url
        assert "group-5" in link.url
        assert link.link_type == "explore_page"
        assert "Kenya" in link.label
        assert "EU" in link.label

    def test_group_bilateral_link(self):
        """group_bilateral generates a treemap explore link with group exporter and country importer."""
        from src.atlas_links import generate_atlas_links

        links = generate_atlas_links(
            "group_bilateral",
            {
                "group_id": 5,
                "group_name": "EU",
                "partner_id": 404,
                "partner_name": "Kenya",
                "year": 2023,
            },
        )
        assert len(links) == 1
        link = links[0]
        assert "group-5" in link.url
        assert "country-404" in link.url
        assert link.link_type == "explore_page"
        assert "EU" in link.label
        assert "Kenya" in link.label

    def test_group_products_link_missing_ids_returns_empty(self):
        """group_products returns empty list when required IDs are missing."""
        from src.atlas_links import generate_atlas_links

        links = generate_atlas_links(
            "group_products",
            {"country_id": 404, "country_name": "Kenya"},
        )
        assert links == []


# ---------------------------------------------------------------------------
# Fix 1: Import/export direction support
# ---------------------------------------------------------------------------


class TestTradeDirection:
    """Tests for import/export trade_direction support."""

    def test_entity_extraction_has_trade_direction_field(self):
        """GraphQLEntityExtraction schema includes trade_direction field."""
        fields = set(GraphQLEntityExtraction.model_fields.keys())
        assert "trade_direction" in fields

    def test_trade_direction_accepts_imports(self):
        """trade_direction field accepts 'imports' value."""
        ext = GraphQLEntityExtraction(
            reasoning="User asks about imports",
            trade_direction="imports",
        )
        assert ext.trade_direction == "imports"

    def test_trade_direction_accepts_exports(self):
        """trade_direction field accepts 'exports' value."""
        ext = GraphQLEntityExtraction(
            reasoning="User asks about exports",
            trade_direction="exports",
        )
        assert ext.trade_direction == "exports"

    def test_trade_direction_defaults_to_none(self):
        """trade_direction defaults to None when not specified."""
        ext = GraphQLEntityExtraction(reasoning="Generic question")
        assert ext.trade_direction is None

    async def test_trade_direction_passed_through_resolve_ids(self):
        """trade_direction scalar is preserved through resolve_ids."""
        state = _base_graphql_state(
            graphql_question="What does USA import?",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(
                country_name="Kenya",
                country_code_guess="KEN",
                trade_direction="imports",
            ),
        )
        mock_llm = MagicMock()
        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=_make_country_cache(),
            product_caches=_make_product_caches(),
            services_cache=_make_services_cache(),
            group_cache=None,
        )
        resolved = result["graphql_resolved_params"]
        assert resolved["trade_direction"] == "imports"

    async def test_trade_direction_fallback_to_override_direction(self):
        """When LLM doesn't extract trade_direction, override_direction is used."""
        state = _base_graphql_state(
            graphql_question="What does USA import?",
            override_direction="imports",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(
                country_name="Kenya",
                country_code_guess="KEN",
                # No trade_direction set
            ),
        )
        mock_llm = MagicMock()
        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=_make_country_cache(),
            product_caches=_make_product_caches(),
            services_cache=_make_services_cache(),
            group_cache=None,
        )
        resolved = result["graphql_resolved_params"]
        assert resolved["trade_direction"] == "imports"

    def test_post_process_sorts_by_import_value_when_imports(self):
        """post_process_response sorts by importValue when trade_direction='imports'."""
        raw = {
            "countryProductYear": [
                {"productId": 1, "year": 2024, "exportValue": 100, "importValue": 500},
                {"productId": 2, "year": 2024, "exportValue": 900, "importValue": 200},
                {"productId": 3, "year": 2024, "exportValue": 50, "importValue": 800},
            ]
            * 10  # Exceed top_n threshold
        }
        result = post_process_response(
            "treemap_products", raw, trade_direction="imports"
        )
        items = result["countryProductYear"]
        # Should be sorted by importValue descending
        import_vals = [item["importValue"] for item in items]
        assert import_vals == sorted(import_vals, reverse=True)

    def test_post_process_defaults_to_export_value(self):
        """post_process_response defaults to exportValue sort without trade_direction."""
        raw = {
            "countryProductYear": [
                {"productId": 1, "year": 2024, "exportValue": 100, "importValue": 500},
                {"productId": 2, "year": 2024, "exportValue": 900, "importValue": 200},
                {"productId": 3, "year": 2024, "exportValue": 50, "importValue": 800},
            ]
            * 10
        }
        result = post_process_response("treemap_products", raw)
        items = result["countryProductYear"]
        export_vals = [item["exportValue"] for item in items]
        assert export_vals == sorted(export_vals, reverse=True)

    def test_post_process_metadata_includes_trade_direction(self):
        """_postProcessed metadata includes tradeDirection field."""
        raw = {
            "countryProductYear": [
                {
                    "productId": i,
                    "year": 2024,
                    "exportValue": i * 10,
                    "importValue": i * 5,
                }
                for i in range(25)
            ]
        }
        result = post_process_response(
            "treemap_products", raw, trade_direction="imports"
        )
        assert result["_postProcessed"]["tradeDirection"] == "imports"

    def test_slim_builders_include_import_value(self):
        """Slim builders _build_treemap_cpy, _build_treemap_ccpy, _build_feasibility_cpy include importValue."""
        from src.graphql_pipeline import (
            _build_treemap_cpy,
            _build_treemap_ccpy,
            _build_feasibility_cpy,
        )

        params = {
            "country_id": 404,
            "product_level": "fourDigit",
            "product_class": "HS92",
            "year": 2024,
        }
        query_cpy, _ = _build_treemap_cpy(params)
        assert "importValue" in query_cpy

        params_ccpy = {**params, "partner_id": 76}
        query_ccpy, _ = _build_treemap_ccpy(params_ccpy)
        assert "importValue" in query_ccpy

        query_feas, _ = _build_feasibility_cpy(params)
        assert "importValue" in query_feas

    async def test_format_graphql_results_passes_direction(self):
        """format_graphql_results passes trade_direction from entity_extraction to post_process_response."""
        import json

        raw_response = {
            "countryProductYear": [
                {
                    "productId": i,
                    "year": 2024,
                    "exportValue": i * 10,
                    "importValue": i * 5,
                }
                for i in range(25)
            ]
        }
        state = _base_graphql_state(
            graphql_question="What does USA import?",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(
                trade_direction="imports",
            ),
            graphql_raw_response=raw_response,
        )
        result = await format_graphql_results(state)
        content = result["messages"][0].content
        # Content may have warning/note prefixes before the JSON
        json_start = content.index("{")
        parsed = json.loads(content[json_start:])
        assert parsed["_postProcessed"]["tradeDirection"] == "imports"


# ---------------------------------------------------------------------------
# Fix 2: Group membership query
# ---------------------------------------------------------------------------


class TestGroupMembership:
    """Tests for group_membership query type support."""

    def test_group_membership_in_query_type_literal(self):
        """group_membership is a valid query_type in the classification schema."""
        import typing

        field_info = GraphQLQueryClassification.model_fields["query_type"]
        args = typing.get_args(field_info.annotation)
        assert "group_membership" in args

    def test_group_membership_in_query_type_to_api(self):
        """group_membership maps to 'explore' in _QUERY_TYPE_TO_API."""
        from src.graphql_pipeline import _QUERY_TYPE_TO_API

        assert "group_membership" in _QUERY_TYPE_TO_API
        assert _QUERY_TYPE_TO_API["group_membership"] == "explore"

    def test_build_group_membership_query_has_members_field(self):
        """_build_group_membership produces a query with the 'members' field."""
        from src.graphql_pipeline import _QUERY_BUILDERS

        builder = _QUERY_BUILDERS["group_membership"]
        query, variables = builder({"group_type": "political"})
        assert "members" in query
        assert "locationGroup" in query
        assert variables.get("groupType") == "political"

    def test_build_group_membership_without_group_type(self):
        """_build_group_membership works without a group_type."""
        from src.graphql_pipeline import _QUERY_BUILDERS

        builder = _QUERY_BUILDERS["group_membership"]
        query, variables = builder({})
        assert "locationGroup" in query
        assert "groupType" not in variables

    def test_post_process_group_membership_enriches_members(self):
        """Group membership post-processing enriches member IDs with country names."""
        from src.graphql_pipeline import post_process_group_membership

        raw = {
            "locationGroup": [
                {
                    "groupId": 5,
                    "groupName": "Test Group",
                    "groupType": "political",
                    "members": ["country-404", "country-76"],
                },
            ]
        }
        country_cache = _make_country_cache()
        result = post_process_group_membership(
            raw, group_id=5, country_cache=country_cache
        )
        assert "groupName" in result
        assert result["groupName"] == "Test Group"
        members = result["members"]
        assert len(members) == 2
        # Check enrichment
        names = {m["name"] for m in members}
        assert "Kenya" in names
        assert "Brazil" in names

    def test_post_process_group_membership_filters_by_group_id(self):
        """Group membership post-processing filters to the target group."""
        from src.graphql_pipeline import post_process_group_membership

        raw = {
            "locationGroup": [
                {
                    "groupId": 5,
                    "groupName": "EU",
                    "groupType": "political",
                    "members": ["country-404"],
                },
                {
                    "groupId": 10,
                    "groupName": "ASEAN",
                    "groupType": "political",
                    "members": ["country-76"],
                },
            ]
        }
        result = post_process_group_membership(raw, group_id=5, country_cache=None)
        assert result["groupName"] == "EU"

    def test_post_process_group_membership_substring_name_match(self):
        """When group_id is missing, substring matching finds 'European Union' for 'EU'."""
        from src.graphql_pipeline import post_process_group_membership

        raw = {
            "locationGroup": [
                {
                    "groupId": 10,
                    "groupName": "NAFTA",
                    "groupType": "trade",
                    "members": ["country-484"],
                },
                {
                    "groupId": 5,
                    "groupName": "European Union",
                    "groupType": "political",
                    "members": ["country-276", "country-250"],
                },
            ]
        }
        # group_name="EU" should match "European Union" via substring
        result = post_process_group_membership(raw, group_name="EU", country_cache=None)
        assert result["groupName"] == "European Union"
        assert result["groupId"] == 5

    def test_post_process_group_membership_exact_match_preferred_over_substring(self):
        """Exact name match takes priority over substring match."""
        from src.graphql_pipeline import post_process_group_membership

        raw = {
            "locationGroup": [
                {
                    "groupId": 5,
                    "groupName": "EU",
                    "groupType": "political",
                    "members": ["country-276"],
                },
                {
                    "groupId": 6,
                    "groupName": "European Union (expanded)",
                    "groupType": "political",
                    "members": ["country-250"],
                },
            ]
        }
        # Exact match "EU" should win over substring match "European Union (expanded)"
        result = post_process_group_membership(raw, group_name="EU", country_cache=None)
        assert result["groupName"] == "EU"
        assert result["groupId"] == 5

    def test_post_process_group_membership_no_match_falls_back_to_first(self):
        """When neither group_id nor group_name match, fall back to first group."""
        from src.graphql_pipeline import post_process_group_membership

        raw = {
            "locationGroup": [
                {
                    "groupId": 10,
                    "groupName": "NAFTA",
                    "groupType": "trade",
                    "members": ["country-484"],
                },
            ]
        }
        result = post_process_group_membership(
            raw, group_name="Nonexistent Group", country_cache=None
        )
        assert result["groupName"] == "NAFTA"


# ---------------------------------------------------------------------------
# Fix 3: Anti-hallucination guardrails
# ---------------------------------------------------------------------------


class TestDataQualityWarnings:
    """Tests for data-quality warnings in format_graphql_results."""

    async def test_format_warns_on_empty_results(self):
        """Empty result set triggers a WARNING in the ToolMessage content."""
        raw_response = {"countryProductYear": []}
        state = _base_graphql_state(
            graphql_question="What did Kenya export in 2024?",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(),
            graphql_raw_response=raw_response,
        )
        result = await format_graphql_results(state)
        content = result["messages"][0].content
        assert "WARNING" in content
        assert "zero results" in content.lower()

    async def test_format_warns_on_year_range_mismatch(self):
        """Year range mismatch triggers a WARNING."""
        raw_response = {
            "countryProductYear": [
                {
                    "productId": i,
                    "year": 2020 + (i % 5),
                    "exportValue": i * 10,
                    "importValue": 0,
                }
                for i in range(25)
            ]
        }
        state = _base_graphql_state(
            graphql_question="Kenya exports 2010 to 2024",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(
                year_min=2010,
                year_max=2024,
            ),
            graphql_raw_response=raw_response,
        )
        result = await format_graphql_results(state)
        content = result["messages"][0].content
        assert "WARNING" in content
        assert "2010" in content
        assert "2024" in content

    async def test_format_notes_import_direction(self):
        """Import direction triggers a NOTE about using importValue."""
        raw_response = {
            "countryProductYear": [
                {
                    "productId": i,
                    "year": 2024,
                    "exportValue": i * 10,
                    "importValue": i * 5,
                }
                for i in range(25)
            ]
        }
        state = _base_graphql_state(
            graphql_question="What does USA import?",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(
                trade_direction="imports",
            ),
            graphql_raw_response=raw_response,
        )
        result = await format_graphql_results(state)
        content = result["messages"][0].content
        assert "NOTE" in content
        assert "IMPORTS" in content or "imports" in content.lower()

    async def test_format_no_warning_on_normal_results(self):
        """Normal results produce no WARNING or NOTE prefixes."""
        raw_response = {
            "countryProductYear": [
                {
                    "productId": i,
                    "year": 2024,
                    "exportValue": i * 10,
                    "importValue": i * 5,
                }
                for i in range(25)
            ]
        }
        state = _base_graphql_state(
            graphql_question="What did Kenya export in 2024?",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(year=2024),
            graphql_raw_response=raw_response,
        )
        result = await format_graphql_results(state)
        content = result["messages"][0].content
        assert not content.startswith("WARNING")
        assert not content.startswith("NOTE")

    def test_post_process_metadata_includes_summary(self):
        """_postProcessed metadata includes a human-readable summary."""
        raw = {
            "countryProductYear": [
                {"productId": i, "year": 2024, "exportValue": i * 10, "importValue": 0}
                for i in range(25)
            ]
        }
        result = post_process_response("treemap_products", raw)
        meta = result["_postProcessed"]
        assert "summary" in meta
        assert "20" in meta["summary"]
        assert "25" in meta["summary"]


# ---------------------------------------------------------------------------
# Fix 5: HS12 product catalog
# ---------------------------------------------------------------------------


def _make_hs12_product_cache() -> CatalogCache:
    """Create and populate a test HS12 product catalog cache."""
    cache = CatalogCache("test_hs12_product", ttl=3600)
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
            {"productId": 9001, "code": "0901", "nameShortEn": "Coffee HS12"},
            {"productId": 9002, "code": "2709", "nameShortEn": "Petroleum oils HS12"},
        ]
    )
    return cache


class TestHS12ProductCatalog:
    """Tests for HS12 product catalog support via product_caches dict."""

    async def test_resolve_ids_uses_hs12_cache_when_product_class_hs12(self):
        """When product_class is HS12, resolve_ids uses the HS12 cache."""
        hs12_cache = _make_hs12_product_cache()
        state = _base_graphql_state(
            graphql_question="Top HS12 products for Kenya",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(
                country_name="Kenya",
                country_code_guess="KEN",
                product_name="Coffee HS12",
                product_class="HS12",
            ),
        )
        mock_llm = MagicMock()
        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=_make_country_cache(),
            product_caches={"HS92": _make_product_cache(), "HS12": hs12_cache},
            services_cache=_make_services_cache(),
            group_cache=None,
        )
        resolved = result["graphql_resolved_params"]
        # Should have resolved from HS12 cache (productId 9001)
        assert resolved["product_id"] == 9001

    async def test_resolve_ids_uses_hs92_cache_when_product_class_hs92(self):
        """When product_class is HS92, resolve_ids uses the HS92 cache."""
        hs12_cache = _make_hs12_product_cache()
        state = _base_graphql_state(
            graphql_question="Kenya coffee exports",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(
                country_name="Kenya",
                country_code_guess="KEN",
                product_name="Coffee",
                product_class="HS92",
            ),
        )
        mock_llm = MagicMock()
        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=_make_country_cache(),
            product_caches={"HS92": _make_product_cache(), "HS12": hs12_cache},
            services_cache=_make_services_cache(),
            group_cache=None,
        )
        resolved = result["graphql_resolved_params"]
        # Should have resolved from HS92 cache (productId 726)
        assert resolved["product_id"] == 726

    async def test_resolve_ids_defaults_to_hs12_when_no_product_class(self):
        """When product_class is None, resolve_ids defaults to HS12 cache."""
        hs12_cache = _make_hs12_product_cache()
        state = _base_graphql_state(
            graphql_question="Top products for Kenya",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(
                country_name="Kenya",
                country_code_guess="KEN",
                product_name="Coffee HS12",
                # No product_class — defaults to HS12
            ),
        )
        mock_llm = MagicMock()
        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=_make_country_cache(),
            product_caches={"HS92": _make_product_cache(), "HS12": hs12_cache},
            services_cache=_make_services_cache(),
            group_cache=None,
        )
        resolved = result["graphql_resolved_params"]
        # Should default to HS12 cache (productId 9001)
        assert resolved["product_id"] == 9001

    async def test_resolve_ids_falls_back_when_classification_missing(self):
        """When product_caches has only HS92 and product_class is HS12, falls back to first cache."""
        state = _base_graphql_state(
            graphql_question="Top HS12 products for Kenya",
            graphql_classification=_explore_classification(
                query_type="treemap_products"
            ),
            graphql_entity_extraction=_explore_extraction(
                country_name="Kenya",
                country_code_guess="KEN",
                product_name="Coffee",
                product_class="HS12",
            ),
        )
        mock_llm = MagicMock()
        # Only HS92 in product_caches — HS12 key is missing
        result = await resolve_ids(
            state,
            lightweight_model=mock_llm,
            country_cache=_make_country_cache(),
            product_caches={"HS92": _make_product_cache()},
            services_cache=_make_services_cache(),
            group_cache=None,
        )
        resolved = result["graphql_resolved_params"]
        # Falls back to first (HS92) cache — resolves "Coffee" as productId 726
        assert resolved["product_id"] == 726

    def test_enrichment_uses_hs12_cache_for_hs12_products(self):
        """post_process_response uses HS12 cache from product_caches when product_class is HS12."""
        raw = {
            "countryProductYear": [
                {
                    "productId": 9001,
                    "year": 2024,
                    "exportValue": i * 10,
                    "importValue": 0,
                }
                for i in range(25)
            ]
        }
        hs12_cache = _make_hs12_product_cache()
        result = post_process_response(
            "treemap_products",
            raw,
            product_caches={"HS92": _make_product_cache(), "HS12": hs12_cache},
            product_class="HS12",
        )
        items = result["countryProductYear"]
        # Should have HS12 names from hs12 cache
        assert items[0].get("productName") == "Coffee HS12"


# ==========================================================================
# Fix 4: Combine classify + extract into single LLM call (plan_query)
# ==========================================================================


class TestGraphQLQueryPlan:
    """Tests for the merged GraphQLQueryPlan schema."""

    def test_schema_has_all_classification_fields(self):
        """GraphQLQueryPlan contains all fields from GraphQLQueryClassification."""
        plan_fields = set(GraphQLQueryPlan.model_fields.keys())
        classification_fields = set(GraphQLQueryClassification.model_fields.keys())
        assert classification_fields.issubset(
            plan_fields
        ), f"Missing classification fields: {classification_fields - plan_fields}"

    def test_schema_has_all_extraction_fields(self):
        """GraphQLQueryPlan contains all fields from GraphQLEntityExtraction."""
        plan_fields = set(GraphQLQueryPlan.model_fields.keys())
        extraction_fields = set(GraphQLEntityExtraction.model_fields.keys())
        assert extraction_fields.issubset(
            plan_fields
        ), f"Missing extraction fields: {extraction_fields - plan_fields}"

    def test_schema_accepts_valid_plan(self):
        """GraphQLQueryPlan can be instantiated with typical classification+extraction data."""
        plan = GraphQLQueryPlan(
            reasoning="Kenya export treemap",
            query_type="treemap_products",
            api_target="explore",
            country_name="Kenya",
            country_code_guess="KEN",
            year=2024,
        )
        assert plan.query_type == "treemap_products"
        assert plan.country_name == "Kenya"
        assert plan.year == 2024

    def test_schema_accepts_long_reasoning(self):
        """Reasoning cap is advisory (in description), not enforced by validator."""
        long_reasoning = "x" * 500
        plan = GraphQLQueryPlan(
            reasoning=long_reasoning,
            query_type="reject",
            rejection_reason="test",
        )
        assert plan.reasoning == long_reasoning


class TestPlanQuery:
    """Tests for the plan_query node that merges classify + extract."""

    @pytest.mark.asyncio
    async def test_plan_query_writes_classification_and_extraction(self):
        """plan_query should write both graphql_classification and graphql_entity_extraction."""
        mock_plan = GraphQLQueryPlan(
            reasoning="Kenya treemap",
            query_type="treemap_products",
            api_target="explore",
            country_name="Kenya",
            country_code_guess="KEN",
            year=2024,
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=mock_plan
        )

        state = {
            **GRAPHQL_STATE_DEFAULTS,
            "messages": [],
            "graphql_question": "What did Kenya export in 2024?",
            "graphql_context": "",
        }
        result = await plan_query(state, lightweight_model=mock_llm)

        assert "graphql_classification" in result
        assert "graphql_entity_extraction" in result
        classification = result["graphql_classification"]
        assert classification["query_type"] == "treemap_products"
        assert classification["api_target"] == "explore"
        extraction = result["graphql_entity_extraction"]
        assert extraction["country_name"] == "Kenya"
        assert extraction["year"] == 2024

    @pytest.mark.asyncio
    async def test_plan_query_applies_deterministic_api_target(self):
        """plan_query should override api_target with _QUERY_TYPE_TO_API mapping."""
        mock_plan = GraphQLQueryPlan(
            reasoning="Country profile",
            query_type="country_profile",
            api_target="explore",  # Wrong — should be overridden to country_pages
            country_name="Kenya",
            country_code_guess="KEN",
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=mock_plan
        )

        state = {
            **GRAPHQL_STATE_DEFAULTS,
            "messages": [],
            "graphql_question": "Tell me about Kenya",
            "graphql_context": "",
        }
        result = await plan_query(state, lightweight_model=mock_llm)
        assert result["graphql_classification"]["api_target"] == "country_pages"
        assert result["graphql_api_target"] == "country_pages"

    @pytest.mark.asyncio
    async def test_plan_query_skips_extraction_on_reject(self):
        """plan_query should set graphql_entity_extraction to None when query_type is reject."""
        mock_plan = GraphQLQueryPlan(
            reasoning="Not a trade question",
            query_type="reject",
            rejection_reason="Custom SQL needed",
            country_name="Kenya",  # Even if LLM extracts entities, they should be dropped
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=mock_plan
        )

        state = {
            **GRAPHQL_STATE_DEFAULTS,
            "messages": [],
            "graphql_question": "Compare 50 countries",
            "graphql_context": "",
        }
        result = await plan_query(state, lightweight_model=mock_llm)
        assert result["graphql_classification"]["query_type"] == "reject"
        assert result["graphql_entity_extraction"] is None

    @pytest.mark.asyncio
    async def test_plan_query_error_propagates_for_retry(self):
        """plan_query should let LLM errors propagate so RetryPolicy can kick in."""
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            side_effect=RuntimeError("Rate limit")
        )

        state = {
            **GRAPHQL_STATE_DEFAULTS,
            "messages": [],
            "graphql_question": "What did Kenya export?",
            "graphql_context": "",
        }
        with pytest.raises(RuntimeError, match="Rate limit"):
            await plan_query(state, lightweight_model=mock_llm)

    def test_pipeline_nodes_includes_plan_query(self):
        """GRAPHQL_PIPELINE_NODES should include plan_query instead of classify_query/extract_entities."""
        assert "plan_query" in GRAPHQL_PIPELINE_NODES
        assert "classify_query" not in GRAPHQL_PIPELINE_NODES
        assert "extract_entities" not in GRAPHQL_PIPELINE_NODES

    @pytest.mark.asyncio
    async def test_plan_query_records_timing_and_usage(self):
        """plan_query should include step_timing and token_usage in result."""
        mock_plan = GraphQLQueryPlan(
            reasoning="Kenya treemap",
            query_type="treemap_products",
            api_target="explore",
            country_name="Kenya",
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=mock_plan
        )

        state = {
            **GRAPHQL_STATE_DEFAULTS,
            "messages": [],
            "graphql_question": "What did Kenya export?",
            "graphql_context": "",
        }
        result = await plan_query(state, lightweight_model=mock_llm)
        assert "step_timing" in result
        assert len(result["step_timing"]) == 1
        assert result["step_timing"][0]["node"] == "plan_query"
        assert "token_usage" in result


# =========================================================================
# Round 3 eval-driven fixes
# =========================================================================


class TestGlobalProductQueryType:
    """Fix 1: global_product query type + productYear builder."""

    def test_global_product_in_query_type_to_api(self):
        """global_product should map to the explore API."""
        assert "global_product" in _QUERY_TYPE_TO_API
        assert _QUERY_TYPE_TO_API["global_product"] == "explore"

    def test_global_product_in_literal_types(self):
        """global_product should be a valid value for both Pydantic schemas."""
        # GraphQLQueryClassification
        cls = GraphQLQueryClassification(
            reasoning="global product query",
            query_type="global_product",
            api_target="explore",
        )
        assert cls.query_type == "global_product"

        # GraphQLQueryPlan
        plan = GraphQLQueryPlan(
            reasoning="global product query",
            query_type="global_product",
            api_target="explore",
        )
        assert plan.query_type == "global_product"

    def test_build_global_product_year_query(self):
        """Builder should produce a productYear query with productClass."""
        query_str, variables = build_graphql_query("global_product", {})
        assert "productYear" in query_str
        assert "productClass" in query_str
        assert variables["productClass"] == "HS92"
        assert variables["productLevel"] == 4
        assert "yearMin" in variables
        assert "yearMax" in variables

    def test_build_global_product_year_custom_class(self):
        """Builder should respect explicit product_class."""
        _, variables = build_graphql_query(
            "global_product", {"product_class": "HS12", "year": 2023}
        )
        assert variables["productClass"] == "HS12"
        assert variables["yearMin"] == 2023
        assert variables["yearMax"] == 2023

    def test_post_process_rules_for_global_product(self):
        """global_product should have post-processing rules."""
        rules = _POST_PROCESS_RULES.get("global_product")
        assert rules is not None
        assert rules["root"] == "productYear"
        assert rules["sort"] == "exportValue"
        assert rules["top_n"] == 20
        assert rules["enrich"] == "product"


class TestCountryYearSmartRouting:
    """Fix 2: Smart country_year routing for year ranges."""

    @pytest.mark.asyncio
    async def test_country_year_year_range_routes_to_explore(self):
        """Year range in extraction should force Explore API."""
        mock_plan = GraphQLQueryPlan(
            reasoning="Brazil ECI trend",
            query_type="country_year",
            api_target="country_pages",
            country_name="Brazil",
            year_min=2010,
            year_max=2024,
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=mock_plan
        )

        state = {
            **GRAPHQL_STATE_DEFAULTS,
            "messages": [],
            "graphql_question": "Brazil's ECI trend over 15 years",
            "graphql_context": "",
        }
        result = await plan_query(state, lightweight_model=mock_llm)
        assert result["graphql_api_target"] == "explore"
        assert result["graphql_classification"]["api_target"] == "explore"

    @pytest.mark.asyncio
    async def test_country_year_single_year_keeps_country_pages(self):
        """Single year with country_pages should be preserved."""
        mock_plan = GraphQLQueryPlan(
            reasoning="Spain SITC ECI",
            query_type="country_year",
            api_target="country_pages",
            country_name="Spain",
            year=2023,
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=mock_plan
        )

        state = {
            **GRAPHQL_STATE_DEFAULTS,
            "messages": [],
            "graphql_question": "Spain's ECI in SITC classification",
            "graphql_context": "",
        }
        result = await plan_query(state, lightweight_model=mock_llm)
        assert result["graphql_api_target"] == "country_pages"

    def test_country_year_post_process_sorts_chronologically(self):
        """country_year post-processing should sort by year ascending."""
        raw = {
            "countryYear": [
                {"year": 2024, "eci": 0.5},
                {"year": 2020, "eci": 0.3},
                {"year": 2022, "eci": 0.4},
            ]
            * 11  # 33 items, above top_n=30
        }
        result = post_process_response("country_year", raw)
        items = result["countryYear"]
        years = [item["year"] for item in items]
        assert years == sorted(years), "Items should be sorted by year ascending"

    def test_post_process_ascending_sort(self):
        """sort_ascending=True should produce ascending order."""
        raw = {
            "countryYear": [
                {"year": 2024, "eci": 0.5},
                {"year": 2010, "eci": 0.1},
                {"year": 2015, "eci": 0.3},
            ]
            * 11  # 33 items
        }
        result = post_process_response("country_year", raw)
        items = result["countryYear"]
        # Should be chronological (ascending)
        assert items[0]["year"] <= items[-1]["year"]


class TestResponseSizeCap:
    """Fix 3: Response size cap."""

    @pytest.mark.asyncio
    async def test_format_graphql_results_truncates_large_content(self):
        """Large responses should be truncated to MAX_RESPONSE_CHARS."""
        # Create a response that will produce very large JSON
        large_data = {"items": [{"id": i, "data": "x" * 500} for i in range(100)]}
        state = _base_graphql_state(
            graphql_classification={
                "query_type": "product_info",
                "api_target": "explore",
            },
            graphql_entity_extraction={"trade_direction": "exports"},
            graphql_raw_response=large_data,
            graphql_resolved_params={"product_id": 1234},
        )
        result = await format_graphql_results(state)
        content = result["messages"][0].content
        assert len(content) <= MAX_RESPONSE_CHARS + 200  # allow for warning prefix

    @pytest.mark.asyncio
    async def test_format_graphql_results_preserves_small_content(self):
        """Small responses should not be truncated."""
        small_data = {"countryProfile": {"eci": 0.5, "gdp": 1000000}}
        state = _base_graphql_state(
            graphql_classification={
                "query_type": "country_profile",
                "api_target": "country_pages",
            },
            graphql_entity_extraction={"trade_direction": "exports"},
            graphql_raw_response=small_data,
            graphql_resolved_params={"location": "location-404"},
        )
        result = await format_graphql_results(state)
        content = result["messages"][0].content
        assert "[Response truncated" not in content

    @pytest.mark.asyncio
    async def test_truncation_notice_appended(self):
        """Truncated responses should include a notice."""
        # Generate content that exceeds MAX_RESPONSE_CHARS
        large_data = {
            "countryProductYear": [
                {
                    "productId": i,
                    "exportValue": 1000 * i,
                    "year": 2024,
                    "data": "x" * 1000,
                }
                for i in range(100)
            ]
        }
        state = _base_graphql_state(
            graphql_classification={
                "query_type": "treemap_products",
                "api_target": "explore",
            },
            graphql_entity_extraction={"trade_direction": "exports"},
            graphql_raw_response=large_data,
            graphql_resolved_params={"country_id": 76},
        )
        result = await format_graphql_results(state)
        content = result["messages"][0].content
        # Post-processing truncates to top 20 items first;
        # if the JSON is still >15K chars after that, the cap applies
        if len(content) > MAX_RESPONSE_CHARS:
            assert "[Response truncated" in content


class TestNewProductsBuilder:
    """Fix 4: Enhanced new_products builder with peer comparison."""

    def test_new_products_builder_includes_comparison(self):
        """Builder should include newProductsComparisonCountries query."""
        query_str, _ = build_graphql_query(
            "new_products", {"location": "location-484", "year": 2024}
        )
        assert "newProductsCountry" in query_str
        assert "newProductsComparisonCountries" in query_str

    def test_new_products_builder_variables(self):
        """Builder should set correct variables."""
        _, variables = build_graphql_query(
            "new_products", {"location": "location-484", "year": 2022}
        )
        assert variables["location"] == "location-484"
        assert variables["year"] == 2022
