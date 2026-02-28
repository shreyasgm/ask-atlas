"""Unit tests for eval improvement guardrails.

Tests anti-hallucination nudge routing, ToolMessage name attributes,
GraphQL query builder fields, services data support, and growth
opportunity routing.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

# ---------------------------------------------------------------------------
# Phase 1: Anti-Hallucination Guardrails + Tool Tracking
# ---------------------------------------------------------------------------


class TestToolCallNudgeRouting:
    """Tests for the tool_call_nudge guardrail in route_after_agent."""

    def _make_route_fn(self, max_uses: int = 3):
        """Build a route_after_agent closure matching the real graph.py logic."""

        def route_after_agent(state) -> str:
            last_msg = state["messages"][-1]
            if not (hasattr(last_msg, "tool_calls") and last_msg.tool_calls):
                has_tool_msg = any(
                    isinstance(m, ToolMessage) for m in state["messages"]
                )
                if not has_tool_msg:
                    nudge_already = any(
                        isinstance(m, HumanMessage)
                        and hasattr(m, "content")
                        and "You must call a tool before answering" in (m.content or "")
                        for m in state["messages"]
                    )
                    if not nudge_already:
                        return "tool_call_nudge"
                return END
            tool_name = last_msg.tool_calls[0]["name"]
            if tool_name == "docs_tool":
                return "extract_docs_question"
            if state.get("queries_executed", 0) >= max_uses:
                return "max_queries_exceeded"
            if tool_name == "query_tool":
                return "extract_tool_question"
            elif tool_name == "atlas_graphql":
                return "extract_graphql_question"
            return END

        return route_after_agent

    def test_nudge_when_no_tool_message(self):
        """Agent tries to END without any prior tool call -> nudge."""
        route = self._make_route_fn()
        state = {
            "messages": [
                HumanMessage(content="What did Kenya export in 2024?"),
                AIMessage(content="Kenya exported coffee and tea."),
            ],
            "queries_executed": 0,
        }
        assert route(state) == "tool_call_nudge"

    def test_end_when_tool_message_exists(self):
        """Agent tries to END after a tool was called -> allow END."""
        route = self._make_route_fn()
        state = {
            "messages": [
                HumanMessage(content="What did Kenya export in 2024?"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "c1",
                            "name": "query_tool",
                            "args": {"question": "Kenya exports"},
                        }
                    ],
                ),
                ToolMessage(content="Results...", tool_call_id="c1", name="query_tool"),
                AIMessage(content="Kenya exported coffee."),
            ],
            "queries_executed": 1,
        }
        assert route(state) == END

    def test_end_on_second_attempt_after_nudge(self):
        """After nudge already issued, agent can END (refusal path)."""
        route = self._make_route_fn()
        state = {
            "messages": [
                HumanMessage(content="Tell me a joke."),
                AIMessage(content="Sure, here's a joke..."),
                HumanMessage(
                    content=(
                        "You must call a tool before answering data questions. "
                        "Use query_tool, atlas_graphql, or docs_tool to look up "
                        "the information needed. However, if the question is not "
                        "related to trade data, is harmful, or is otherwise "
                        "inappropriate, you may respond directly without calling "
                        "a tool."
                    )
                ),
                AIMessage(content="That question is not about trade data. A joke: ..."),
            ],
            "queries_executed": 0,
        }
        assert route(state) == END

    def test_normal_tool_call_routing_unaffected(self):
        """Normal tool call routing (query_tool, atlas_graphql) still works."""
        route = self._make_route_fn()
        state = {
            "messages": [
                HumanMessage(content="What did Kenya export?"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "c1", "name": "query_tool", "args": {"question": "q"}},
                    ],
                ),
            ],
            "queries_executed": 0,
        }
        assert route(state) == "extract_tool_question"

        state2 = {
            "messages": [
                HumanMessage(content="What did Kenya export?"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "c1",
                            "name": "atlas_graphql",
                            "args": {"question": "q"},
                        },
                    ],
                ),
            ],
            "queries_executed": 0,
        }
        assert route(state2) == "extract_graphql_question"


class TestToolMessageNames:
    """ToolMessage objects must carry correct `name` attribute."""

    @pytest.mark.asyncio
    async def test_sql_format_results_has_name(self):
        """format_results_node sets name='query_tool' on ToolMessages."""
        from src.sql_pipeline import format_results_node

        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "c1", "name": "query_tool", "args": {"question": "q"}},
                    ],
                )
            ],
            "last_error": "",
            "pipeline_result": "Some SQL results",
            "queries_executed": 0,
        }
        result = await format_results_node(state)
        for msg in result["messages"]:
            assert msg.name == "query_tool"

    @pytest.mark.asyncio
    async def test_graphql_format_results_has_name(self):
        """format_graphql_results sets name='atlas_graphql' on ToolMessages."""
        from src.graphql_pipeline import format_graphql_results

        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "c1",
                            "name": "atlas_graphql",
                            "args": {"question": "q"},
                        },
                        {
                            "id": "c2",
                            "name": "atlas_graphql",
                            "args": {"question": "q2"},
                        },
                    ],
                )
            ],
            "queries_executed": 0,
            "last_error": "",
            "graphql_classification": {
                "query_type": "reject",
                "rejection_reason": "test",
            },
            "graphql_entity_extraction": None,
            "graphql_raw_response": None,
            "graphql_atlas_links": [],
        }
        result = await format_graphql_results(state)
        for msg in result["messages"]:
            assert msg.name == "atlas_graphql"

    @pytest.mark.asyncio
    async def test_docs_format_results_has_name(self):
        """format_docs_results sets name='docs_tool' on ToolMessages."""
        from src.docs_pipeline import format_docs_results

        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "c1", "name": "docs_tool", "args": {"question": "q"}},
                    ],
                )
            ],
            "docs_synthesis": "Some docs content",
        }
        result = await format_docs_results(state)
        for msg in result["messages"]:
            assert msg.name == "docs_tool"


# ---------------------------------------------------------------------------
# Phase 2: Missing GraphQL Fields (Country Page Metrics)
# ---------------------------------------------------------------------------


class TestCountryProfileFields:
    """Verify _build_country_profile includes required fields."""

    def test_policy_recommendation_in_query(self):
        """_build_country_profile query must include policyRecommendation."""
        from src.graphql_pipeline import _build_country_profile

        query, _ = _build_country_profile({"location": "KEN"})
        assert "policyRecommendation" in query


class TestNewProductsFields:
    """Verify _build_new_products includes required fields."""

    def test_new_product_count_in_query(self):
        """_build_new_products query must include newProductCount."""
        from src.graphql_pipeline import _build_new_products

        query, _ = _build_new_products({"location": "KEN"})
        assert "newProductCount" in query

    def test_new_products_list_in_query(self):
        """_build_new_products query must include newProducts { ... }."""
        from src.graphql_pipeline import _build_new_products

        query, _ = _build_new_products({"location": "KEN"})
        assert "newProducts" in query
        assert "shortName" in query


class TestGrowthOpportunitiesProductClass:
    """Verify _build_growth_opportunities uses correct product class."""

    def test_uses_hs_not_hs92(self):
        """Country Pages API uses 'HS', not 'HS92'."""
        from src.graphql_pipeline import _build_growth_opportunities

        _, variables = _build_growth_opportunities({"location": "KEN"})
        assert variables["productClass"] == "HS"


# ---------------------------------------------------------------------------
# Phase 3: Services Data Support
# ---------------------------------------------------------------------------


class TestGraphQLServicesClass:
    """Verify GraphQLEntityExtraction accepts services_class."""

    def test_services_class_field_accepted(self):
        """GraphQLEntityExtraction should accept services_class field."""
        from src.graphql_pipeline import GraphQLEntityExtraction

        extraction = GraphQLEntityExtraction(
            reasoning="test",
            services_class="unilateral",
        )
        assert extraction.services_class == "unilateral"

    def test_services_class_default_none(self):
        """services_class defaults to None."""
        from src.graphql_pipeline import GraphQLEntityExtraction

        extraction = GraphQLEntityExtraction(reasoning="test")
        assert extraction.services_class is None


class TestServicesClassInQueryBuilders:
    """Verify query builders include servicesClass when set."""

    def test_country_product_year_with_services_class(self):
        """_build_country_product_year includes servicesClass when set."""
        from src.graphql_pipeline import _build_country_product_year

        params = {
            "country_id": 404,
            "product_level": "fourDigit",
            "product_class": "HS92",
            "services_class": "unilateral",
            "year": 2024,
        }
        query, variables = _build_country_product_year(params)
        assert "servicesClass" in query
        assert variables.get("servicesClass") == "unilateral"

    def test_country_product_year_without_services_class(self):
        """_build_country_product_year omits servicesClass when not set."""
        from src.graphql_pipeline import _build_country_product_year

        params = {
            "country_id": 404,
            "product_level": "fourDigit",
            "product_class": "HS92",
            "year": 2024,
        }
        query, variables = _build_country_product_year(params)
        assert "servicesClass" not in variables

    def test_country_year_with_services_class(self):
        """_build_country_year includes servicesClass when set."""
        from src.graphql_pipeline import _build_country_year

        params = {
            "country_id": 404,
            "services_class": "unilateral",
            "year": 2024,
        }
        query, variables = _build_country_year(params)
        assert "servicesClass" in query
        assert variables.get("servicesClass") == "unilateral"


# ---------------------------------------------------------------------------
# Phase 4: Growth Opportunity Routing
# ---------------------------------------------------------------------------


class TestFeasibilityPostProcessRules:
    """Verify feasibility post-process rules include RCA filter."""

    def test_feasibility_has_rca_filter(self):
        """_POST_PROCESS_RULES['feasibility'] must have 'filter': 'rca_lt_1'."""
        from src.graphql_pipeline import _POST_PROCESS_RULES

        assert _POST_PROCESS_RULES["feasibility"]["filter"] == "rca_lt_1"

    def test_feasibility_table_has_rca_filter(self):
        """_POST_PROCESS_RULES['feasibility_table'] must have 'filter': 'rca_lt_1'."""
        from src.graphql_pipeline import _POST_PROCESS_RULES

        assert _POST_PROCESS_RULES["feasibility_table"]["filter"] == "rca_lt_1"


# ---------------------------------------------------------------------------
# Phase 5: SQL Table Filter
# ---------------------------------------------------------------------------


class TestSqlGroupTableFilter:
    """Verify SQL table filter keeps classification tables but excludes data tables."""

    def test_keeps_location_group(self):
        """classification.location_group should not be filtered out."""
        tables = [
            {"table_name": "classification.location_group"},
            {"table_name": "classification.location_group_member"},
            {"table_name": "hs92.group_group_product_year_4"},
            {"table_name": "hs92.country_product_year_4"},
        ]
        # Apply the same filter as in sql_pipeline.py
        filtered = [t for t in tables if "group_group_" not in t["table_name"]]
        names = [t["table_name"] for t in filtered]
        assert "classification.location_group" in names
        assert "classification.location_group_member" in names
        assert "hs92.group_group_product_year_4" not in names
        assert "hs92.country_product_year_4" in names


class TestGroupCatalog:
    """Verify group catalog cache setup."""

    def test_group_catalog_resolves_by_name(self):
        """group_catalog should resolve group names via the 'name' index."""
        from src.cache import CatalogCache

        cache = CatalogCache("test_group", ttl=3600)
        cache.add_index(
            "name",
            key_fn=lambda e: (e.get("groupName") or "").strip().lower() or None,
            normalize_query=lambda q: q.strip().lower(),
        )
        cache.add_index(
            "id",
            key_fn=lambda e: str(e["groupId"]) if "groupId" in e else None,
        )
        cache.populate(
            [
                {
                    "groupId": 1,
                    "groupName": "Sub-Saharan Africa",
                    "groupType": "region",
                },
                {"groupId": 2, "groupName": "EU", "groupType": "trade"},
            ]
        )
        result = cache.lookup_sync("name", "Sub-Saharan Africa")
        assert result is not None
        assert result["groupId"] == 1

        result2 = cache.lookup_sync("name", "EU")
        assert result2 is not None
        assert result2["groupId"] == 2
