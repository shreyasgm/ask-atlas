"""GraphQL pipeline node functions for the Atlas agent graph.

Provides 5 async node functions that form a linear pipeline:

    extract_graphql_question → plan_query
    → resolve_ids → build_and_execute_graphql → format_graphql_results

``plan_query`` combines the former ``classify_query`` + ``extract_entities``
into a single LLM call, roughly halving latency for those two steps.

Each node reads from ``AtlasAgentState`` and returns a partial dict update.
Nodes that need external dependencies (LLM, caches, HTTP client) receive
them via ``functools.partial`` binding at graph-construction time.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Literal

from langchain_core.messages import ToolMessage
from pydantic import BaseModel, Field

from src.atlas_links import generate_atlas_links
from src.cache import CatalogCache
from src.graphql_client import AtlasGraphQLClient, BudgetExhaustedError, GraphQLError
from src.prompts import (
    GRAPHQL_DATA_MAX_YEAR,
    build_classification_prompt,
    build_extraction_prompt,
    build_id_resolution_prompt,
    build_query_plan_prompt,
)
from src.state import AtlasAgentState
from src.token_usage import (
    make_usage_record_from_callback,
    make_usage_record_from_msg,
    node_timer,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MAX_RESPONSE_CHARS: int = 15_000
"""Cap on formatted response content sent to the LLM (~3.7K tokens).

Prevents context-window overflow when post-processed data is still large.
"""

GRAPHQL_PIPELINE_NODES = frozenset(
    {
        "extract_graphql_question",
        "plan_query",
        "resolve_ids",
        "build_and_execute_graphql",
        "assess_graphql_result",
        "graphql_correction_agent",
        "format_graphql_results",
    }
)

# Default state values for graphql_* fields (used to reset between turns)
_GRAPHQL_STATE_DEFAULTS: dict[str, Any] = {
    "graphql_question": "",
    "graphql_context": "",
    "graphql_classification": None,
    "graphql_entity_extraction": None,
    "graphql_resolved_params": None,
    "graphql_query": None,
    "graphql_api_target": None,
    "graphql_raw_response": None,
    "graphql_execution_time_ms": 0,
    "graphql_assessment": "",
    "graphql_surface_to_agent": False,
}

# ---------------------------------------------------------------------------
# Deterministic api_target override: every query_type has exactly one valid
# API target.  After classification, we override whatever the LLM chose.
# ---------------------------------------------------------------------------

_QUERY_TYPE_TO_API: dict[str, str] = {
    "country_profile": "country_pages",
    "country_profile_exports": "country_pages",
    "country_profile_partners": "country_pages",
    "country_profile_complexity": "country_pages",
    "country_lookback": "country_pages",
    "new_products": "country_pages",
    "growth_opportunities": "country_pages",
    "global_datum": "country_pages",
    "treemap_products": "explore",
    "treemap_partners": "explore",
    "treemap_bilateral": "explore",
    "overtime_products": "explore",
    "overtime_partners": "explore",
    "marketshare": "explore",
    "product_space": "explore",
    "feasibility": "explore",
    "feasibility_table": "explore",
    "product_table": "explore",
    "country_year": "explore",
    "product_info": "explore",
    "bilateral_aggregate": "explore",
    "explore_bilateral": "explore",
    "explore_group": "explore",
    "explore_data_availability": "explore",
    "group_products": "explore",
    "group_bilateral": "explore",
    "group_membership": "explore",
    "global_product": "explore",
}

# ---------------------------------------------------------------------------
# Pydantic schemas — description constants
# Updated per design doc (docs/backend_redesign_analysis.md)
# ---------------------------------------------------------------------------

QUERY_TYPE_DESCRIPTION = (
    "The Atlas GraphQL query type that best answers the user's question. Choose exactly one:\n"
    "- reject : Query doesn't fit any GraphQL API type — use when the question requires\n"
    "           custom SQL aggregation, multi-table joins, or data not available via the Atlas APIs.\n"
    "- country_profile : Country overview including GDP, population, ECI, top exports,\n"
    "                    diversification grade, growth projection relative to income,\n"
    "                    peer comparisons (countryProfile API).\n"
    "- country_profile_exports : Country export basket — top exports by product, export\n"
    "                            composition, export diversification (treeMap CPY_C API).\n"
    "                            Use when the user asks about what a country exports, its\n"
    "                            export basket, or export composition.\n"
    "- country_profile_partners : Country trade partner breakdown — top export destinations,\n"
    "                             trade partner composition (treeMap CCY_C API). Use when the\n"
    "                             user asks who a country trades with, its main export\n"
    "                             destinations, or trade partner breakdown.\n"
    "- country_profile_complexity : Country complexity metrics — ECI, COI, complexity\n"
    "                               rankings, growth projections (countryProfile API).\n"
    "                               Use when the user asks about a country's economic\n"
    "                               complexity, ECI ranking, or COI.\n"
    "- country_lookback : Growth dynamics over a lookback period — how a country's exports\n"
    "                     and complexity have changed, export growth classification\n"
    "                     (promising/troubling/mixed/static) (countryLookback API).\n"
    "- new_products : Products a country has started exporting recently (newProductsCountry API).\n"
    "- treemap_products : What products does a country export in a given year — breakdown\n"
    "                     by product (countryProductYear API).\n"
    "- treemap_partners : Where does a country export to — breakdown by trading partner\n"
    "                     (countryCountryYear API).\n"
    "- treemap_bilateral : What products does country A export to country B — bilateral\n"
    "                      product breakdown (countryCountryProductYear API).\n"
    "- overtime_products : How have a country's product exports changed over time — time\n"
    "                      series by product (countryProductYear API).\n"
    "- overtime_partners : How have a country's trading partners changed over time — time\n"
    "                      series by partner (countryCountryYear API).\n"
    "- marketshare : A country's share of global exports for a product over time\n"
    "                (countryProductYear + productYear APIs).\n"
    "- product_space : Product space network — proximity and relatedness of exported products\n"
    "                  (countryProductYear + productProduct APIs).\n"
    "- feasibility : Growth opportunity scatter — products plotted by complexity vs.\n"
    "                distance/feasibility (countryProductYear + productYear APIs).\n"
    "- feasibility_table : Growth opportunity table — same data as feasibility in tabular form.\n"
    "- growth_opportunities : Pre-computed growth opportunity metrics from the Atlas\n"
    "                         Country Pages (treeMap API). Returns opportunityGain,\n"
    "                         distance, PCI, and normalized variants per product.\n"
    "                         **Only supports HS (= HS92) classification.**\n"
    "                         For SITC or custom product classifications, use\n"
    "                         feasibility or feasibility_table instead (Explore API).\n"
    "- product_table : Tabular product-level data for a country — export values, RCA,\n"
    "                  complexity metrics (countryProductYear API).\n"
    "- country_year : Country aggregate data by year — GDP, ECI, total trade values (countryYear API).\n"
    "- product_info : Global product-level data — trade value, PCI, number of exporters (productYear API).\n"
    "- bilateral_aggregate : Total/aggregate bilateral trade value between two countries —\n"
    "                        NOT product-level breakdown. Use when the question asks for the\n"
    "                        total export or import value between two specific countries\n"
    "                        (countryCountryYear API).\n"
    "- explore_bilateral : Bilateral trade data between two countries (countryCountryProductYear API).\n"
    "- explore_group : Regional or group-level aggregate trade data — continents, income groups,\n"
    "                  trade blocs. Use for total export/import values of a group (groupYear API).\n"
    "- group_products : Product-level trade between a country and a group — e.g. 'What does Kenya\n"
    "                   export to the EU?' (countryGroupProductYear API). Requires country AND\n"
    "                   partner group.\n"
    "- group_bilateral : Product-level trade between a group (as exporter) and a country — e.g.\n"
    "                    'What does the EU export to Kenya?' (groupCountryProductYear API).\n"
    "                    Requires group AND partner country.\n"
    "- group_membership : Lists countries belonging to a regional/trade group (e.g., 'Which countries\n"
    "                      are in the EU?', 'Members of ASEAN'). Returns member country IDs.\n"
    "- global_product : Global product-level data — total world exports/imports, PCI for products\n"
    "                    without a specific country (e.g., 'What are the top exported products globally?').\n"
    "- global_datum : Global-level questions not tied to a specific country.\n"
    "- explore_data_availability : Questions about data coverage — which years, products,\n"
    "                              or countries have data available (dataAvailability API).\n"
    "\n"
    "Routing guidance:\n"
    "- For time-series questions ('how has X changed since Y'), prefer overtime_* or marketshare.\n"
    "- For growth opportunity / diversification questions:\n"
    "  - If using HS/HS92 classification (or unspecified): prefer growth_opportunities\n"
    "    (richer pre-computed metrics from Country Pages treeMap).\n"
    "  - If using SITC or a non-HS classification: prefer feasibility or feasibility_table\n"
    "    (Explore API countryProductYear, which supports all classifications).\n"
    "  - If the user wants custom weighting of COG/distance/PCI: prefer feasibility.\n"
    "- For 'what does country X export' snapshot questions, prefer treemap_products or product_table.\n"
    "- For country overview / profile questions, prefer country_profile.\n"
    "- For questions specifically about a country's export basket or export composition,\n"
    "  prefer country_profile_exports.\n"
    "- For questions about a country's trade partners or export destinations,\n"
    "  prefer country_profile_partners.\n"
    "- For questions about economic complexity, ECI, COI, or complexity rankings,\n"
    "  prefer country_profile_complexity.\n"
    "- For questions about what a country exports to a GROUP (e.g., EU, Africa), prefer group_products.\n"
    "- For questions about what a GROUP exports to a country, prefer group_bilateral.\n"
    "- For questions about services trade, use servicesClass: unilateral in the Explore API.\n"
    "- For questions about whether a product is a natural resource or green product,\n"
    "  use product_info with productClass HS92 (naturalResource metadata is only in HS92).\n"
    "- For questions about a country's strategic approach, policy recommendation, or\n"
    "  diversification strategy for growth opportunities, use country_profile (which has\n"
    "  policyRecommendation) — NOT growth_opportunities (which only has product-level data)."
)

API_TARGET_DESCRIPTION = (
    "Which Atlas API endpoint to query. Choose one:\n"
    "- explore : The Explore API at /api/graphql — provides raw trade data, bilateral flows,\n"
    "            product relatedness, time series, and feasibility/opportunity data. Used by\n"
    "            treemap_*, overtime_*, marketshare, product_space, feasibility*, product_table,\n"
    "            country_year, product_info, explore_bilateral, explore_group, global_datum, and\n"
    "            explore_data_availability query types.\n"
    "- country_pages : The Country Pages API at /api/countries/graphql — provides derived analytical\n"
    "                  profiles including countryProfile (46 fields), countryLookback (growth dynamics),\n"
    "                  newProductsCountry, growth_opportunities (treeMap), peer comparisons, and\n"
    "                  policy recommendations. Used by country_profile, country_profile_exports,\n"
    "                  country_profile_partners, country_profile_complexity, country_lookback,\n"
    "                  new_products, and growth_opportunities query types.\n"
    "                  Note: Country Pages only supports productClass 'HS' (= HS92) and 'SITC'."
)

PRODUCT_LEVEL_DESCRIPTION = (
    "Product aggregation level. Choose one:\n"
    "- section : Broadest grouping (~20 sectors like 'Agriculture', 'Machinery'). Best for high-level overviews.\n"
    "- twoDigit : HS 2-digit chapters (~97 categories like 'Coffee, tea, spices'). Good for sector-level analysis.\n"
    "- fourDigit : HS 4-digit headings (~1200 products like 'Coffee, not roasted'). Default and most commonly used.\n"
    "- sixDigit : Most detailed level (~5000 products). Only available in the Explore API, not Country Pages."
)

PRODUCT_CLASS_DESCRIPTION = (
    "Product classification system. Choose one:\n"
    "- HS92 : Harmonized System 1992 revision. Data available 1995-2024.\n"
    "- HS12 : Harmonized System 2012 revision (default for Explore API). Data available 2012-2024.\n"
    "- HS22 : Harmonized System 2022 revision. Data available 2022-2024. Only available in the Explore API.\n"
    "- SITC : Standard International Trade Classification. Data available 1962-2024.\n"
    "\n"
    "Important: The Country Pages API only supports 'HS' (equivalent to HS92) and 'SITC'.\n"
    "When querying the Country Pages API, product_class is effectively HS92 regardless of what is set here.\n"
    "Note: The Explore API countryYear.exportValue returns goods+services total regardless of productClass/servicesClass parameter."
)

GROUP_TYPE_DESCRIPTION = (
    "Group type for regional/group queries (explore_group query type). Choose one:\n"
    "- continent : Continental grouping (e.g., Africa, Asia, Europe).\n"
    "- region : Sub-continental regions.\n"
    "- subregion : Finer sub-regional groupings.\n"
    "- trade : Trade blocs (e.g., EU, NAFTA, ASEAN).\n"
    "- wdi_income_level : World Bank income groups (high, upper_middle, lower_middle, low).\n"
    "- wdi_region : World Bank regional classifications.\n"
    "- political : Political groupings.\n"
    "- world : The entire world as a single group."
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class GraphQLQueryClassification(BaseModel):
    """Classification of a user question for the Atlas GraphQL API."""

    reasoning: str = Field(
        description="Step-by-step reasoning for the classification decision (max 300 chars).",
    )
    query_type: Literal[
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
    ] = Field(description=QUERY_TYPE_DESCRIPTION)
    rejection_reason: str | None = Field(
        default=None,
        description="Why the query was rejected. Only set when query_type is 'reject'.",
    )
    api_target: Literal["explore", "country_pages"] | None = Field(
        default=None,
        description=API_TARGET_DESCRIPTION,
    )


class GraphQLEntityExtraction(BaseModel):
    """Entities extracted from a user question for GraphQL query construction."""

    reasoning: str = Field(
        description="Step-by-step reasoning for entity extraction decisions (max 300 chars).",
    )
    country_name: str | None = Field(
        default=None, description="Primary country mentioned in the question."
    )
    country_code_guess: str | None = Field(
        default=None, description="ISO 3166-1 alpha-3 code guess (e.g., 'KEN')."
    )
    partner_name: str | None = Field(
        default=None, description="Partner/destination country for bilateral queries."
    )
    partner_code_guess: str | None = Field(
        default=None, description="ISO3 code guess for the partner country."
    )
    product_name: str | None = Field(
        default=None, description="Product or commodity mentioned."
    )
    product_code_guess: str | None = Field(
        default=None, description="HS code guess (e.g., '0901' for coffee)."
    )
    product_level: Literal["section", "twoDigit", "fourDigit", "sixDigit"] | None = (
        Field(
            default="fourDigit",
            description=PRODUCT_LEVEL_DESCRIPTION,
        )
    )
    product_class: Literal["HS92", "HS12", "HS22", "SITC"] | None = Field(
        default=None,
        description=PRODUCT_CLASS_DESCRIPTION,
    )
    year: int | None = Field(
        default=None, description="Specific year mentioned (e.g., 2024)."
    )
    year_min: int | None = Field(
        default=None, description="Start of time range for overtime queries."
    )
    year_max: int | None = Field(
        default=None, description="End of time range for overtime queries."
    )
    group_name: str | None = Field(
        default=None,
        description="Country group name for the exporter side (e.g., 'ASEAN', 'EU'). "
        "Used for explore_group and group_bilateral query types.",
    )
    group_type: str | None = Field(
        default=None,
        description=GROUP_TYPE_DESCRIPTION,
    )
    partner_group_name: str | None = Field(
        default=None,
        description="Partner group name for the importer/destination side (e.g., 'EU', 'Africa'). "
        "Used for group_products query type where a country exports TO a group.",
    )
    partner_group_type: str | None = Field(
        default=None,
        description="Group type for the partner group (same options as group_type).",
    )
    lookback_years: Literal[3, 5, 10, 15] | None = Field(
        default=None,
        description="Lookback period in years for Country Pages growth dynamics (country_lookback query type).",
    )
    services_class: Literal["unilateral", "bilateral"] | None = Field(
        default=None,
        description=(
            "Set to 'unilateral' when the question asks about total/all exports/products "
            "or doesn't specifically limit to goods. Set to 'bilateral' for bilateral "
            "services trade. Leave null when the question explicitly says 'goods' or "
            "names a specific goods product."
        ),
    )
    trade_direction: Literal["exports", "imports"] | None = Field(
        default=None,
        description=(
            "Trade direction inferred from the question. Set to 'imports' when the question "
            "asks about imports, imported products, import sources, or top import partners. "
            "Set to 'exports' when the question explicitly asks about exports. "
            "Leave null when direction is ambiguous or not mentioned (defaults to exports)."
        ),
    )
    strategy: (
        Literal["balanced", "low_hanging_fruit", "long_jumps", "custom"] | None
    ) = Field(
        default=None,
        description=(
            "Growth opportunity weighting strategy. Only set when the user explicitly "
            "requests a strategy for growth opportunities or feasibility queries. "
            "'low_hanging_fruit' = favour nearby products, 'long_jumps' = favour distant "
            "high-complexity products, 'balanced' = country-specific default mix, "
            "'custom' = user specifies their own weights. Leave null for the default."
        ),
    )
    custom_weights_distance: float | None = Field(
        default=None,
        description=(
            "Custom weight for distance (0-1). Only set when strategy is 'custom' "
            "and the user specifies weights. Must sum to ~1.0 with pci and og weights."
        ),
    )
    custom_weights_pci: float | None = Field(
        default=None,
        description=(
            "Custom weight for product complexity (0-1). Only set when strategy is 'custom'."
        ),
    )
    custom_weights_og: float | None = Field(
        default=None,
        description=(
            "Custom weight for opportunity gain (0-1). Only set when strategy is 'custom'."
        ),
    )


class GraphQLQueryPlan(BaseModel):
    """Combined classification + entity extraction for a user question.

    Merges GraphQLQueryClassification and GraphQLEntityExtraction into a single
    schema so both steps can be performed in one LLM call, roughly halving the
    latency of the GraphQL pipeline.

    Downstream code splits this back into classification and extraction dicts
    for compatibility with resolve_ids, build_and_execute_graphql, etc.
    """

    # --- Classification fields ---
    reasoning: str = Field(
        description="Step-by-step reasoning for classification and entity extraction (max 300 chars).",
    )
    query_type: Literal[
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
    ] = Field(description=QUERY_TYPE_DESCRIPTION)
    rejection_reason: str | None = Field(
        default=None,
        description="Why the query was rejected. Only set when query_type is 'reject'.",
    )
    api_target: Literal["explore", "country_pages"] | None = Field(
        default=None,
        description=API_TARGET_DESCRIPTION,
    )

    # --- Entity extraction fields ---
    country_name: str | None = Field(
        default=None, description="Primary country mentioned in the question."
    )
    country_code_guess: str | None = Field(
        default=None, description="ISO 3166-1 alpha-3 code guess (e.g., 'KEN')."
    )
    partner_name: str | None = Field(
        default=None, description="Partner/destination country for bilateral queries."
    )
    partner_code_guess: str | None = Field(
        default=None, description="ISO3 code guess for the partner country."
    )
    product_name: str | None = Field(
        default=None, description="Product or commodity mentioned."
    )
    product_code_guess: str | None = Field(
        default=None, description="HS code guess (e.g., '0901' for coffee)."
    )
    product_level: Literal["section", "twoDigit", "fourDigit", "sixDigit"] | None = (
        Field(
            default="fourDigit",
            description=PRODUCT_LEVEL_DESCRIPTION,
        )
    )
    product_class: Literal["HS92", "HS12", "HS22", "SITC"] | None = Field(
        default=None,
        description=PRODUCT_CLASS_DESCRIPTION,
    )
    year: int | None = Field(
        default=None, description="Specific year mentioned (e.g., 2024)."
    )
    year_min: int | None = Field(
        default=None, description="Start of time range for overtime queries."
    )
    year_max: int | None = Field(
        default=None, description="End of time range for overtime queries."
    )
    group_name: str | None = Field(
        default=None,
        description="Country group name for the exporter side (e.g., 'ASEAN', 'EU'). "
        "Used for explore_group and group_bilateral query types.",
    )
    group_type: str | None = Field(
        default=None,
        description=GROUP_TYPE_DESCRIPTION,
    )
    partner_group_name: str | None = Field(
        default=None,
        description="Partner group name for the importer/destination side (e.g., 'EU', 'Africa'). "
        "Used for group_products query type where a country exports TO a group.",
    )
    partner_group_type: str | None = Field(
        default=None,
        description="Group type for the partner group (same options as group_type).",
    )
    lookback_years: Literal[3, 5, 10, 15] | None = Field(
        default=None,
        description="Lookback period in years for Country Pages growth dynamics (country_lookback query type).",
    )
    services_class: Literal["unilateral", "bilateral"] | None = Field(
        default=None,
        description=(
            "Set to 'unilateral' when the question asks about total/all exports/products "
            "or doesn't specifically limit to goods. Set to 'bilateral' for bilateral "
            "services trade. Leave null when the question explicitly says 'goods' or "
            "names a specific goods product."
        ),
    )
    trade_direction: Literal["exports", "imports"] | None = Field(
        default=None,
        description=(
            "Trade direction inferred from the question. Set to 'imports' when the question "
            "asks about imports, imported products, import sources, or top import partners. "
            "Set to 'exports' when the question explicitly asks about exports. "
            "Leave null when direction is ambiguous or not mentioned (defaults to exports)."
        ),
    )
    strategy: (
        Literal["balanced", "low_hanging_fruit", "long_jumps", "custom"] | None
    ) = Field(
        default=None,
        description=(
            "Growth opportunity weighting strategy. Only set when the user explicitly "
            "requests a strategy for growth opportunities or feasibility queries. "
            "'low_hanging_fruit' = favour nearby products, 'long_jumps' = favour distant "
            "high-complexity products, 'balanced' = country-specific default mix, "
            "'custom' = user specifies their own weights. Leave null for the default."
        ),
    )
    custom_weights_distance: float | None = Field(
        default=None,
        description=(
            "Custom weight for distance (0-1). Only set when strategy is 'custom' "
            "and the user specifies weights. Must sum to ~1.0 with pci and og weights."
        ),
    )
    custom_weights_pci: float | None = Field(
        default=None,
        description=(
            "Custom weight for product complexity (0-1). Only set when strategy is 'custom'."
        ),
    )
    custom_weights_og: float | None = Field(
        default=None,
        description=(
            "Custom weight for opportunity gain (0-1). Only set when strategy is 'custom'."
        ),
    )

    # Fields that belong to the classification dict (not entity extraction)
    _CLASSIFICATION_FIELDS: frozenset[str] = frozenset(
        {"reasoning", "query_type", "rejection_reason", "api_target"}
    )

    def split(self) -> tuple[dict, dict | None]:
        """Split into (classification_dict, extraction_dict) for downstream compat.

        Returns:
            Tuple of (classification dict, extraction dict or None if rejected).
        """
        full = self.model_dump()
        classification = {k: full[k] for k in self._CLASSIFICATION_FIELDS}
        if classification["query_type"] == "reject":
            return classification, None
        extraction = {
            k: v for k, v in full.items() if k not in self._CLASSIFICATION_FIELDS
        }
        return classification, extraction


# ---------------------------------------------------------------------------
# Node 1: extract_graphql_question
# ---------------------------------------------------------------------------


async def extract_graphql_question(state: AtlasAgentState) -> dict:
    """Extract the question from the agent's GraphQL tool_call args.

    Resets all graphql_* state fields to prevent cross-turn leakage.
    """
    async with node_timer("extract_graphql_question", "atlas_graphql") as t:
        last_msg = state["messages"][-1]
        if len(last_msg.tool_calls) > 1:
            logger.warning(
                "LLM produced %d parallel tool_calls; only the first will be executed.",
                len(last_msg.tool_calls),
            )
        question = last_msg.tool_calls[0]["args"]["question"]
        context = last_msg.tool_calls[0]["args"].get("context", "")

    # Reset all graphql state fields + set the new question and context
    result = dict(_GRAPHQL_STATE_DEFAULTS)
    result["graphql_question"] = question
    result["graphql_context"] = context
    result["step_timing"] = [t.record]
    return result


# ---------------------------------------------------------------------------
# Node 2: classify_query
# ---------------------------------------------------------------------------


async def classify_query(state: AtlasAgentState, *, lightweight_model: Any) -> dict:
    """Classify the question into a GraphQL query type.

    Args:
        state: Current agent state with ``graphql_question`` populated.
        lightweight_model: LangChain chat model for classification.

    Returns:
        Dict with ``graphql_classification`` and ``graphql_api_target``.
    """
    import time

    from langchain_core.callbacks import UsageMetadataCallbackHandler

    async with node_timer("classify_query", "atlas_graphql") as t:
        question = state["graphql_question"]
        context = state.get("graphql_context", "")

        chain = lightweight_model.with_structured_output(
            GraphQLQueryClassification, method="function_calling"
        )
        prompt = build_classification_prompt(question, context)
        usage_handler = UsageMetadataCallbackHandler()
        llm_start = time.monotonic()
        classification: GraphQLQueryClassification = await chain.ainvoke(
            prompt, config={"callbacks": [usage_handler]}
        )
        t.mark_llm(llm_start, time.monotonic())

    usage_record = make_usage_record_from_callback(
        "classify_query", "atlas_graphql", usage_handler
    )

    classification_dict = classification.model_dump()

    # Deterministic api_target override: the LLM should not need to decide
    # which API to hit — each query_type maps to exactly one target.
    qt = classification_dict.get("query_type", "")
    if qt in _QUERY_TYPE_TO_API:
        canonical_target = _QUERY_TYPE_TO_API[qt]
        # Special case: country_year routes to country_pages when the LLM
        # explicitly chose it (for per-classification ECI queries).
        if (
            qt == "country_year"
            and classification_dict.get("api_target") == "country_pages"
        ):
            canonical_target = "country_pages"
        classification_dict["api_target"] = canonical_target

    return {
        "graphql_classification": classification_dict,
        "graphql_api_target": classification_dict.get("api_target"),
        "token_usage": [usage_record],
        "step_timing": [t.record],
    }


# ---------------------------------------------------------------------------
# Node 3: extract_entities
# ---------------------------------------------------------------------------


async def extract_entities(state: AtlasAgentState, *, lightweight_model: Any) -> dict:
    """Extract entities (countries, products, years) from the question.

    Skips extraction when the query has been rejected.

    Args:
        state: Current agent state with classification populated.
        lightweight_model: LangChain chat model for extraction.

    Returns:
        Dict with ``graphql_entity_extraction``.
    """
    import time

    from langchain_core.callbacks import UsageMetadataCallbackHandler

    async with node_timer("extract_entities", "atlas_graphql") as t:
        classification = state.get("graphql_classification")
        if classification and classification.get("query_type") == "reject":
            return {"graphql_entity_extraction": None, "step_timing": [t.record]}

        question = state["graphql_question"]
        context = state.get("graphql_context", "")
        query_type = classification.get("query_type", "") if classification else ""

        chain = lightweight_model.with_structured_output(
            GraphQLEntityExtraction, method="function_calling"
        )
        prompt = build_extraction_prompt(question, query_type, context)
        usage_handler = UsageMetadataCallbackHandler()
        llm_start = time.monotonic()
        extraction: GraphQLEntityExtraction = await chain.ainvoke(
            prompt, config={"callbacks": [usage_handler]}
        )
        t.mark_llm(llm_start, time.monotonic())

    usage_record = make_usage_record_from_callback(
        "extract_entities", "atlas_graphql", usage_handler
    )
    return {
        "graphql_entity_extraction": extraction.model_dump(),
        "token_usage": [usage_record],
        "step_timing": [t.record],
    }


# ---------------------------------------------------------------------------
# Node 2+3 merged: plan_query (replaces classify_query + extract_entities)
# ---------------------------------------------------------------------------


async def plan_query(state: AtlasAgentState, *, lightweight_model: Any) -> dict:
    """Classify AND extract entities in a single LLM call.

    Replaces the sequential ``classify_query`` → ``extract_entities`` pair,
    roughly halving the latency for these two steps.

    Args:
        state: Current agent state with ``graphql_question`` populated.
        lightweight_model: LangChain chat model for structured output.

    Returns:
        Dict with ``graphql_classification``, ``graphql_entity_extraction``,
        ``graphql_api_target``, ``token_usage``, and ``step_timing``.
    """
    import time

    from langchain_core.callbacks import UsageMetadataCallbackHandler

    async with node_timer("plan_query", "atlas_graphql") as t:
        question = state["graphql_question"]
        context = state.get("graphql_context", "")

        chain = lightweight_model.with_structured_output(
            GraphQLQueryPlan, method="function_calling"
        )
        prompt = build_query_plan_prompt(question, context)
        usage_handler = UsageMetadataCallbackHandler()
        llm_start = time.monotonic()
        plan: GraphQLQueryPlan = await chain.ainvoke(
            prompt, config={"callbacks": [usage_handler]}
        )
        t.mark_llm(llm_start, time.monotonic())

    usage_record = make_usage_record_from_callback(
        "plan_query", "atlas_graphql", usage_handler
    )

    classification_dict, extraction_dict = plan.split()

    # Deterministic api_target override (same logic as classify_query)
    qt = classification_dict.get("query_type", "")
    if qt in _QUERY_TYPE_TO_API:
        canonical_target = _QUERY_TYPE_TO_API[qt]
        # Allow Country Pages for single-year / classification-specific ECI
        if (
            qt == "country_year"
            and classification_dict.get("api_target") == "country_pages"
        ):
            canonical_target = "country_pages"
        # Force Explore when a year range is detected — Country Pages only
        # supports a single year parameter and silently drops year_min/year_max.
        if qt == "country_year" and extraction_dict:
            year_min = extraction_dict.get("year_min")
            year_max = extraction_dict.get("year_max")
            if year_min and year_max and year_min != year_max:
                canonical_target = "explore"
        classification_dict["api_target"] = canonical_target

    return {
        "graphql_classification": classification_dict,
        "graphql_api_target": classification_dict.get("api_target"),
        "graphql_entity_extraction": extraction_dict,
        "token_usage": [usage_record],
        "step_timing": [t.record],
    }


# ---------------------------------------------------------------------------
# Node 4: resolve_ids
# ---------------------------------------------------------------------------


async def resolve_ids(
    state: AtlasAgentState,
    *,
    lightweight_model: Any,
    country_cache: CatalogCache,
    product_caches: dict[str, CatalogCache],
    group_cache: CatalogCache | None = None,
    services_cache: CatalogCache,
) -> dict:
    """Resolve extracted entity names/codes to Atlas internal IDs.

    Uses CatalogCache lookups (code → ID, name → ID) with LLM fallback
    for ambiguous matches.

    Args:
        state: Current agent state with extraction populated.
        lightweight_model: LangChain chat model for disambiguation.
        country_cache: Country catalog cache.
        product_caches: Product catalog caches keyed by classification
            (e.g. ``{"HS92": ..., "HS12": ...}``).  The extracted
            ``product_class`` selects which cache to use; defaults to HS12.
        services_cache: Services catalog cache.

    Returns:
        Dict with ``graphql_resolved_params`` and ``graphql_atlas_links``.
    """
    classification = state.get("graphql_classification")
    extraction = state.get("graphql_entity_extraction")

    if not classification or classification.get("query_type") == "reject":
        return {"graphql_resolved_params": None, "graphql_atlas_links": []}

    if not extraction:
        return {"graphql_resolved_params": None, "graphql_atlas_links": []}

    async with node_timer("resolve_ids", "atlas_graphql") as _t:
        return await _resolve_ids_inner(
            state,
            _t,
            lightweight_model=lightweight_model,
            country_cache=country_cache,
            product_caches=product_caches,
            group_cache=group_cache,
            services_cache=services_cache,
        )


async def _resolve_ids_inner(
    state: AtlasAgentState,
    t: Any,
    *,
    lightweight_model: Any,
    country_cache: CatalogCache,
    product_caches: dict[str, CatalogCache],
    group_cache: CatalogCache | None = None,
    services_cache: CatalogCache,
) -> dict:
    """Inner logic for resolve_ids, extracted so node_timer wraps the whole body."""
    classification = state.get("graphql_classification")
    extraction = state.get("graphql_entity_extraction")

    api_target = classification.get("api_target", "explore")
    query_type = classification.get("query_type", "")
    question = state["graphql_question"]
    context = state.get("graphql_context", "")

    resolved: dict[str, Any] = {}
    resolution_notes: list[str] = []
    usage_sink: list[dict] = []

    # Resolve country
    country_name = extraction.get("country_name")
    country_code = extraction.get("country_code_guess")
    if country_name or country_code:
        country = await _resolve_entity(
            name=country_name,
            code_guess=country_code,
            cache=country_cache,
            index_name="iso3",
            search_field="nameShortEn",
            llm=lightweight_model,
            question=question,
            usage_sink=usage_sink,
            context=context,
        )
        if country:
            resolved["country_id"] = country["countryId"]
            resolved["country_name"] = country.get("nameShortEn", country_name)

    if (country_name or country_code) and "country_id" not in resolved:
        resolution_notes.append(
            f"Could not resolve country '{country_name or country_code}' in catalog"
        )

    # Resolve partner country
    partner_name = extraction.get("partner_name")
    partner_code = extraction.get("partner_code_guess")
    if partner_name or partner_code:
        partner = await _resolve_entity(
            name=partner_name,
            code_guess=partner_code,
            cache=country_cache,
            index_name="iso3",
            search_field="nameShortEn",
            llm=lightweight_model,
            question=question,
            usage_sink=usage_sink,
            context=context,
        )
        if partner:
            resolved["partner_id"] = partner["countryId"]
            resolved["partner_name"] = partner.get("nameShortEn", partner_name)

    # Resolve product — select cache by product_class
    product_name = extraction.get("product_name")
    product_code = extraction.get("product_code_guess")
    if product_name or product_code:
        product_class = extraction.get("product_class") or "HS12"
        active_product_cache = product_caches.get(
            product_class, next(iter(product_caches.values()))
        )
        product = await _resolve_entity(
            name=product_name,
            code_guess=product_code,
            cache=active_product_cache,
            index_name="code",
            search_field="nameShortEn",
            llm=lightweight_model,
            question=question,
            usage_sink=usage_sink,
            context=context,
        )
        if product:
            resolved["product_id"] = product["productId"]
            resolved["product_name"] = product.get("nameShortEn", product_name)

    # If product not found in the selected cache, try services_cache
    if "product_id" not in resolved and (product_name or product_code):
        service_entry = await _resolve_entity(
            name=product_name,
            code_guess=product_code,
            cache=services_cache,
            index_name="name",
            search_field="nameShortEn",
            llm=lightweight_model,
            question=question,
            usage_sink=usage_sink,
            context=context,
        )
        if service_entry:
            resolved["product_id"] = service_entry.get("productId")
            resolved["product_name"] = service_entry.get("nameShortEn", product_name)

    if (product_name or product_code) and "product_id" not in resolved:
        resolution_notes.append(
            f"Could not resolve product '{product_name or product_code}' in catalog"
        )

    # Include resolution notes in resolved params
    if resolution_notes:
        resolved["resolution_notes"] = resolution_notes

    # Pass through scalar fields
    for field_name in (
        "year",
        "year_min",
        "year_max",
        "lookback_years",
        "product_level",
        "product_class",
        "group_name",
        "group_type",
        "partner_group_name",
        "partner_group_type",
        "services_class",
        "trade_direction",
    ):
        val = extraction.get(field_name)
        if val is not None:
            resolved[field_name] = val

    # Fallback: use override_direction from frontend UI toggle if LLM didn't extract
    if "trade_direction" not in resolved and state.get("override_direction"):
        resolved["trade_direction"] = state["override_direction"]

    # Resolve group_name to group_id if a group cache is available
    group_name = extraction.get("group_name")
    if group_name and group_cache is not None:
        group_entry = await _resolve_entity(
            name=group_name,
            code_guess=None,
            cache=group_cache,
            index_name="name",
            search_field="groupName",
            llm=lightweight_model,
            question=question,
            usage_sink=usage_sink,
            context=context,
        )
        if group_entry:
            resolved["group_id"] = group_entry["groupId"]
            resolved["group_name"] = group_entry.get("groupName", group_name)
        else:
            resolution_notes.append(
                f"Could not resolve group '{group_name}' in catalog"
            )

    # Resolve partner_group_name to partner_group_id (for CGPY queries)
    partner_group_name = extraction.get("partner_group_name")
    if partner_group_name and group_cache is not None:
        partner_group_entry = await _resolve_entity(
            name=partner_group_name,
            code_guess=None,
            cache=group_cache,
            index_name="name",
            search_field="groupName",
            llm=lightweight_model,
            question=question,
            usage_sink=usage_sink,
            context=context,
        )
        if partner_group_entry:
            resolved["partner_group_id"] = partner_group_entry["groupId"]
            resolved["partner_group_name"] = partner_group_entry.get(
                "groupName", partner_group_name
            )
        else:
            resolution_notes.append(
                f"Could not resolve partner group '{partner_group_name}' in catalog"
            )

    # Entity-derived query type validation: auto-correct the query_type when
    # resolved entities clearly indicate a different type than what the LLM
    # classified.  Mirrors the frontend's determineEndpointFacet pattern.
    has_country = "country_id" in resolved
    has_partner = "partner_id" in resolved
    has_group = "group_id" in resolved
    has_partner_group = "partner_group_id" in resolved

    if has_country and has_partner_group and query_type not in ("group_products",):
        logger.info(
            "Auto-correcting query_type from %r to 'group_products' "
            "(country + partner_group resolved)",
            query_type,
        )
        query_type = "group_products"
        api_target = _QUERY_TYPE_TO_API["group_products"]
    elif has_group and has_partner and query_type not in ("group_bilateral",):
        logger.info(
            "Auto-correcting query_type from %r to 'group_bilateral' "
            "(group + partner resolved)",
            query_type,
        )
        query_type = "group_bilateral"
        api_target = _QUERY_TYPE_TO_API["group_bilateral"]

    # Strip ID prefixes for link generation (links expect bare integers)
    link_params = dict(resolved)
    for key in (
        "country_id",
        "product_id",
        "partner_id",
        "group_id",
        "partner_group_id",
    ):
        if key in link_params:
            try:
                link_params[key] = _strip_id_prefix(link_params[key])
            except (ValueError, TypeError):
                pass

    atlas_links: list[dict] = []
    try:
        links = generate_atlas_links(query_type, link_params)
        atlas_links = [
            {
                "label": link.label,
                "link_type": link.link_type,
                "resolution_notes": link.resolution_notes,
                "url": link.url,
            }
            for link in links
        ]
    except Exception as e:
        logger.warning("Failed to generate atlas links: %s", e)

    # Format IDs for the target API (after link generation)
    resolved = format_ids_for_api(resolved, api_target)

    result: dict[str, Any] = {
        "graphql_resolved_params": resolved,
        "graphql_atlas_links": atlas_links,
        "step_timing": [t.record],
    }
    if usage_sink:
        result["token_usage"] = usage_sink
    return result


async def _resolve_entity(
    *,
    name: str | None,
    code_guess: str | None,
    cache: CatalogCache,
    index_name: str,
    search_field: str,
    llm: Any,
    question: str,
    usage_sink: list[dict] | None = None,
    context: str = "",
) -> dict[str, Any] | None:
    """Resolve an entity name/code to a catalog entry.

    Strategy:
    1. Step A: Try exact code lookup via the named index
    2. Step B: Search by name (case-insensitive substring)
    3. Step C: LLM disambiguation when multiple candidates exist

    Args:
        usage_sink: Optional list to append token usage records to when
            the LLM is invoked for disambiguation.
        context: Optional agent guidance passed through to the disambiguation prompt.
    """
    candidates: list[dict[str, Any]] = []

    # Step A: Code lookup
    if code_guess:
        entry = await cache.lookup(index_name, code_guess)
        if entry:
            candidates.append(entry)

    # Step B: Name search
    if name:
        results = await cache.search(search_field, name, limit=5)
        # Deduplicate against Step A results
        existing_ids = {id(c) for c in candidates}
        for r in results:
            if id(r) not in existing_ids:
                candidates.append(r)

    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    # Try exact name match first (fast path, no LLM needed)
    if name:
        name_lower = name.strip().lower()
        for c in candidates:
            if (c.get(search_field) or "").strip().lower() == name_lower:
                return c

    # Step C: LLM selects best from multiple candidates
    try:
        options = "\n".join(
            f"{i + 1}. {c.get(search_field, c.get('nameShortEn', 'unknown'))} "
            f"(code: {c.get('code', c.get('iso3Code', 'N/A'))})"
            for i, c in enumerate(candidates)
        )
        prompt = build_id_resolution_prompt(
            question=question,
            options=options,
            num_candidates=len(candidates),
            context=context,
        )
        response = await llm.ainvoke(prompt)

        # Record token usage from the disambiguation LLM call
        if usage_sink is not None:
            usage_sink.append(
                make_usage_record_from_msg("resolve_ids", "atlas_graphql", response)
            )

        text = response.content.strip()
        idx = int(text) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except Exception:
        logger.debug("LLM entity selection failed, falling back to first result")

    # Fallback to first result
    return candidates[0]


# ---------------------------------------------------------------------------
# ID formatting helpers
# ---------------------------------------------------------------------------


def _strip_id_prefix(value: Any) -> int:
    """Extract the numeric ID from a possibly-prefixed catalog value.

    Catalog entries may store IDs as integers (``76``) or as prefixed
    strings (``"country-76"``, ``"product-HS-726"``).  This helper
    normalises both forms to a plain ``int``.

    Args:
        value: Raw ID value from a catalog entry.

    Returns:
        The numeric integer ID.

    Raises:
        ValueError: If the numeric part cannot be extracted.
    """
    if isinstance(value, int):
        return value
    s = str(value)
    # Strip known prefixes: "country-76", "location-404", "product-HS-726"
    # Walk from the right to find the trailing integer segment.
    parts = s.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return int(parts[1])
    # Fallback: try converting the whole thing
    return int(s)


def format_ids_for_api(
    params: dict[str, Any],
    api_target: str,
) -> dict[str, Any]:
    """Transform resolved params to the correct ID format for the target API.

    Args:
        params: Dict with resolved entity IDs (country_id, product_id, etc.)
        api_target: Either "explore" or "country_pages".

    Returns:
        New dict with IDs formatted for the target API.

    Explore API uses bare integer IDs:
        ``countryId: 404``, ``productId: 726``

    Country Pages API uses prefixed string IDs:
        ``location: "location-404"``, ``product: "product-HS-726"``
    """
    result = dict(params)

    if api_target == "country_pages":
        # Transform IDs to prefixed strings for the Country Pages API
        if "country_id" in result:
            country_id = _strip_id_prefix(result.pop("country_id"))
            result["location"] = f"location-{country_id}"
        if "product_id" in result:
            product_id = _strip_id_prefix(result.pop("product_id"))
            pc = result.get("product_class", "HS")
            prefix = "SITC" if pc == "SITC" else "HS"
            result["product"] = f"product-{prefix}-{product_id}"
        if "partner_id" in result:
            partner_id = _strip_id_prefix(result.pop("partner_id"))
            result["partner"] = f"location-{partner_id}"
    else:
        # Explore API: ensure IDs are bare integers (strip any prefixes
        # that the catalog may have stored)
        for key in (
            "country_id",
            "product_id",
            "partner_id",
            "group_id",
            "partner_group_id",
        ):
            if key in result:
                try:
                    result[key] = _strip_id_prefix(result[key])
                except (ValueError, TypeError):
                    pass  # leave as-is if conversion fails

    return result


# ---------------------------------------------------------------------------
# Node 5: build_and_execute_graphql
# ---------------------------------------------------------------------------


async def build_and_execute_graphql(
    state: AtlasAgentState,
    *,
    graphql_client: AtlasGraphQLClient,
    country_pages_client: AtlasGraphQLClient | None = None,
) -> dict:
    """Build a GraphQL query and execute it against the Atlas API.

    Routes to the appropriate API endpoint based on ``graphql_api_target``:
    - ``"country_pages"`` → uses ``country_pages_client`` (falls back to
      ``graphql_client`` if not provided)
    - ``"explore"`` or any other value → uses ``graphql_client``

    Never raises — catches all exceptions and records errors in state.

    Args:
        state: Current agent state with resolved params.
        graphql_client: HTTP client for the Atlas Explore API.
        country_pages_client: Optional HTTP client for the Country Pages API.
            Defaults to ``graphql_client`` when not provided.

    Returns:
        Dict with ``graphql_raw_response``, ``graphql_query``,
        ``graphql_execution_time_ms``, and ``last_error``.
    """
    classification = state.get("graphql_classification")
    resolved_params = state.get("graphql_resolved_params")

    async with node_timer("build_and_execute_graphql", "atlas_graphql") as t:
        if (
            not classification
            or classification.get("query_type") == "reject"
            or resolved_params is None
        ):
            return {
                "graphql_raw_response": None,
                "graphql_query": None,
                "graphql_execution_time_ms": 0,
                "last_error": "",
                "step_timing": [t.record],
            }

        query_type = classification["query_type"]
        api_target = (
            classification.get("api_target")
            or state.get("graphql_api_target")
            or "explore"
        )

        # Route to the correct client based on api_target
        client = (
            country_pages_client
            if (api_target == "country_pages" and country_pages_client is not None)
            else graphql_client
        )

        try:
            query_string, variables = build_graphql_query(query_type, resolved_params)
        except (ValueError, KeyError) as e:
            logger.error("Failed to build GraphQL query: %s", e)
            return {
                "graphql_raw_response": {"error": "build_failed", "detail": str(e)},
                "graphql_query": None,
                "graphql_execution_time_ms": 0,
                "last_error": f"Failed to build query: {e}",
                "step_timing": [t.record],
            }

        start = time.monotonic()
        try:
            data = await client.execute(query_string, variables)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            t.mark_io(start, time.monotonic())
            return {
                "graphql_raw_response": data,
                "graphql_query": query_string,
                "graphql_execution_time_ms": elapsed_ms,
                "last_error": "",
                "step_timing": [t.record],
            }
        except BudgetExhaustedError as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            t.mark_io(start, time.monotonic())
            logger.warning("GraphQL budget exhausted: %s", e)
            return {
                "graphql_raw_response": {"error": "budget_exhausted", "detail": str(e)},
                "graphql_query": query_string,
                "graphql_execution_time_ms": elapsed_ms,
                "last_error": f"GraphQL API budget exhausted: {e}",
                "step_timing": [t.record],
            }
        except GraphQLError as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            t.mark_io(start, time.monotonic())
            logger.error("GraphQL error: %s", e)
            return {
                "graphql_raw_response": {"error": "graphql_error", "detail": str(e)},
                "graphql_query": query_string,
                "graphql_execution_time_ms": elapsed_ms,
                "last_error": f"GraphQL query failed: {e}",
                "step_timing": [t.record],
            }
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            t.mark_io(start, time.monotonic())
            logger.error("Unexpected error executing GraphQL query: %s", e)
            return {
                "graphql_raw_response": {"error": "unexpected_error", "detail": str(e)},
                "graphql_query": query_string,
                "graphql_execution_time_ms": elapsed_ms,
                "last_error": f"Unexpected error: {e}",
                "step_timing": [t.record],
            }


def _dedupe_links(links: list[dict]) -> list[dict]:
    """Deduplicate atlas links by URL, preserving insertion order."""
    seen: set[str] = set()
    result: list[dict] = []
    for link in links:
        url = link.get("url", "")
        if url not in seen:
            seen.add(url)
            result.append(link)
    return result


# ---------------------------------------------------------------------------
# Node 6: format_graphql_results
# ---------------------------------------------------------------------------


async def format_graphql_results(
    state: AtlasAgentState,
    *,
    product_caches: dict[str, CatalogCache] | None = None,
    country_cache: CatalogCache | None = None,
    services_cache: CatalogCache | None = None,
) -> dict:
    """Create a ToolMessage from the GraphQL pipeline results.

    Handles three cases:
    - Rejection: returns a ToolMessage explaining why the query was rejected.
    - Error: returns an error ToolMessage and discards atlas links.
    - Success: post-processes and formats the response data into a ToolMessage.

    Also handles parallel tool_calls by creating stub messages for extras.

    Args:
        state: Current agent state with raw GraphQL response.
        product_caches: Product catalog caches keyed by classification
            (e.g. ``{"HS92": ..., "HS12": ...}``).
        country_cache: Optional country catalog cache for name enrichment.
        services_cache: Optional services catalog cache for name enrichment.
    """
    async with node_timer("format_graphql_results", "atlas_graphql") as _fmt_t:
        last_msg = state["messages"][-1]
        tool_calls = last_msg.tool_calls
        classification = state.get("graphql_classification") or {}
        query_type = classification.get("query_type", "")
        raw_response = state.get("graphql_raw_response")
        last_error = state.get("last_error", "")

        # Determine content and atlas links
        atlas_links: list[dict] = []
        entity_extraction = state.get("graphql_entity_extraction")

        if query_type == "reject":
            reason = classification.get("rejection_reason", "Question not supported")
            content = (
                f"This question could not be answered via the Atlas GraphQL API. "
                f"Rejection reason: {reason}\n\n"
                f"ACTION REQUIRED: Call query_tool to answer this question using SQL instead."
            )
        elif query_type != "reject" and entity_extraction is None and classification:
            content = (
                "Entity extraction failed — could not parse entities from the question. "
                "Please try rephrasing your question."
            )
        elif isinstance(raw_response, dict) and "error" in raw_response:
            content = f"Error executing GraphQL query: {raw_response['error']} — {raw_response.get('detail', '')}"
        elif last_error or raw_response is None:
            content = (
                f"Error executing GraphQL query: {last_error or 'No response received'}"
            )
            # Discard links on failure
        else:
            # Success — warm caches before synchronous post-processing
            import asyncio
            import json

            warm_tasks = []
            if product_caches:
                warm_tasks.extend(
                    c._ensure_populated()
                    for c in product_caches.values()
                    if not c.is_populated
                )
            if country_cache and not country_cache.is_populated:
                warm_tasks.append(country_cache._ensure_populated())
            if services_cache and not services_cache.is_populated:
                warm_tasks.append(services_cache._ensure_populated())
            if warm_tasks:
                await asyncio.gather(*warm_tasks, return_exceptions=True)

            trade_direction = (entity_extraction or {}).get(
                "trade_direction"
            ) or "exports"
            resolved = state.get("graphql_resolved_params") or {}
            product_class = (entity_extraction or {}).get("product_class") or "HS12"
            if query_type == "group_membership":
                processed = post_process_group_membership(
                    raw_response,
                    group_id=resolved.get("group_id"),
                    group_name=resolved.get("group_name"),
                    country_cache=country_cache,
                )
            else:
                # Resolve country_id for country-specific post-processing
                pp_country_id: int | None = None
                raw_cid = resolved.get("country_id")
                if raw_cid is not None:
                    try:
                        pp_country_id = int(str(raw_cid).replace("location-", ""))
                    except (ValueError, TypeError):
                        pass

                # Extract strategy and custom weights from entity extraction
                ee = entity_extraction or {}
                pp_strategy = ee.get("strategy") or "balanced"
                pp_custom_weights: dict[str, float] | None = None
                if pp_strategy == "custom":
                    wd = ee.get("custom_weights_distance")
                    wp = ee.get("custom_weights_pci")
                    wo = ee.get("custom_weights_og")
                    if wd is not None and wp is not None and wo is not None:
                        pp_custom_weights = {
                            "normalizedDistance": float(wd),
                            "normalizedPci": float(wp),
                            "normalizedOpportunityGain": float(wo),
                        }

                processed = post_process_response(
                    query_type,
                    raw_response,
                    trade_direction=trade_direction,
                    product_caches=product_caches,
                    product_class=product_class,
                    country_cache=country_cache,
                    services_cache=services_cache,
                    country_id=pp_country_id,
                    strategy=pp_strategy,
                    custom_weights=pp_custom_weights,
                )
            content = json.dumps(processed, indent=2, default=str)

            # Cap response size to prevent context-window overflow
            if len(content) > MAX_RESPONSE_CHARS:
                truncated_notice = (
                    f"\n\n[Response truncated from {len(content):,} to "
                    f"{MAX_RESPONSE_CHARS:,} chars. "
                    f"The data above is partial — answer based on what is shown.]"
                )
                content = (
                    content[: MAX_RESPONSE_CHARS - len(truncated_notice)]
                    + truncated_notice
                )

            # Build data-quality warnings
            warnings: list[str] = []
            rules = _POST_PROCESS_RULES.get(query_type)
            if rules:
                root_key = rules["root"]
                items = processed.get(root_key, [])
                if isinstance(items, list) and len(items) == 0:
                    warnings.append(
                        "WARNING: The query returned zero results. This may mean the data "
                        "is not available for this country/product/year combination. "
                        "Report this to the user — do NOT guess or fabricate data."
                    )

                # Year range mismatch
                if entity_extraction:
                    requested_min = entity_extraction.get("year_min")
                    requested_max = entity_extraction.get("year_max")
                    if (
                        requested_min
                        and requested_max
                        and isinstance(items, list)
                        and items
                    ):
                        years = sorted(
                            {item.get("year") for item in items if item.get("year")}
                        )
                        if years and (
                            years[0] > requested_min or years[-1] < requested_max
                        ):
                            warnings.append(
                                f"WARNING: Requested year range {requested_min}-{requested_max} "
                                f"but data only covers {years[0]}-{years[-1]}. "
                                f"Report the actual coverage to the user."
                            )

                # Null values in key time-series fields
                _TS_NULL_FIELDS = (
                    "eci",
                    "eciFixed",
                    "exportValue",
                    "importValue",
                    "gdppc",
                )
                if isinstance(items, list) and items:
                    null_fields: dict[str, list[int]] = {}
                    for item in items:
                        yr = item.get("year")
                        if yr is None:
                            continue
                        for key in _TS_NULL_FIELDS:
                            if key in item and item[key] is None:
                                null_fields.setdefault(key, []).append(yr)
                    if null_fields:
                        field_summary = "; ".join(
                            f"{k} is null for years {sorted(v)}"
                            for k, v in null_fields.items()
                        )
                        warnings.append(
                            f"WARNING: Some fields contain null values: {field_summary}. "
                            "Report ONLY the years/fields with actual data. "
                            "Do NOT fill gaps with estimates or your own knowledge."
                        )

            # Import direction note
            if trade_direction == "imports":
                warnings.append(
                    "NOTE: This query was for IMPORTS. Use the importValue field "
                    "(not exportValue) when reporting results to the user."
                )

            # Field-interpretation guides — moved from agent prompt so
            # the agent sees them only when relevant results arrive.
            if query_type == "country_profile":
                warnings.append(
                    "NOTE: Field interpretation guide — use these to describe results accurately:\n"
                    "- structuralTransformationStep: NotStarted = 'has not yet started structural transformation', "
                    "TextilesOnly = 'textiles/apparel stage', ElectronicsOnly = 'electronics stage', "
                    "MachineryOnly = 'machinery stage', Completed = 'has completed structural transformation'\n"
                    "- structuralTransformationDirection: risen/fallen/stagnated (sector market share trend)\n"
                    "- marketShareMainSectorPositiveGrowth: true = gaining global market share in main sector; "
                    "false = main sector is growing globally (country riding tailwind, not gaining competitive share)\n"
                    "- growthProjection: moderate → 'moderately', slow → 'slowly', rapid → 'rapidly'\n"
                    "- growthProjectionRelativeToIncome: More/ModeratelyMore/Same/ModeratelyLess/Less "
                    "(how growth projection compares to others in same income group)\n"
                    "- PCI is null for services and some natural resources in default responses\n"
                    "- exportValueConstGrowthCagr: constant-dollar CAGR — use directly, do not recompute from nominal values\n"
                    "- Classification labels (diversificationGrade, complexityIncome, etc.): report as-is\n"
                    "- When question asks about total exports under a specific classification, "
                    "sum product-level values from countryProductYear rather than using countryYear.exportValue"
                )
            elif query_type == "country_lookback":
                warnings.append(
                    "NOTE: Field interpretation guide — use these to describe results accurately:\n"
                    "- eciRankChange: POSITIVE = worsened (higher rank number = less complex), "
                    "NEGATIVE = improved (lower rank number = more complex). "
                    "Example: eciRankChange = +5 means 'dropped 5 places'\n"
                    "- exportValueConstGrowthCagr: constant-dollar CAGR — use directly, do not recompute\n"
                    "- Labels (promising/troubling/mixed/static): from constant-price dynamics, report as-is\n"
                    "- gdpPcConstantCagrRegionalDifference: Above/InLine/Below (vs regional average)"
                )
            elif query_type in ("feasibility", "feasibility_table"):
                warnings.append(
                    "NOTE: Products are ranked by compositeScore = "
                    "normalizedDistance × 0.50 + normalizedPci × 0.15 + normalizedCOG × 0.35 "
                    "(fixed Explore-page weights). Higher compositeScore = better opportunity. "
                    "This mirrors the Atlas Explore feasibility page which uses a fixed formula.\n"
                    "Available preset strategies for the Country Pages growth opportunities view "
                    "(use growth_opportunities query type instead): "
                    "Low-Hanging Fruit (dist 60%, PCI 15%, OG 25%), "
                    "Balanced Portfolio (varies by country policy), "
                    "Long Jumps (dist 45%, PCI 20%, OG 35%). "
                    "Users can also request custom weights."
                )
            elif query_type == "growth_opportunities":
                # Extract actual weights used from post-processed metadata
                pp_meta = processed.get("_postProcessed", {})
                used_strategy = pp_meta.get("strategy", "balanced")
                used_policy = pp_meta.get("policyRecommendation", "unknown")
                used_weights = pp_meta.get("weights", {})
                weight_parts = [
                    f"{k.replace('normalized', '')} × {v}"
                    for k, v in used_weights.items()
                ]
                weight_str = " + ".join(weight_parts) if weight_parts else "default"

                warnings.append(
                    f"NOTE: Products are ranked by compositeScore ({weight_str}) "
                    f"using the '{used_strategy}' strategy based on this country's "
                    f"'{used_policy}' policy recommendation. "
                    "Higher compositeScore = better opportunity.\n"
                    "Available strategies: "
                    "Low-Hanging Fruit (dist 60%, PCI 15%, OG 25%) — "
                    "focuses on most feasible products; "
                    "Balanced Portfolio (default — weights vary by country policy: "
                    "StrategicBets=50/15/35, ParsimoniousIndustrial=55/20/25, "
                    "LightTouch=60/20/20); "
                    "Long Jumps (dist 45%, PCI 20%, OG 35%) — "
                    "favors ambitious diversification; "
                    "Custom — user specifies weights.\n"
                    "The Atlas does not display growth opportunity products for countries "
                    "classified under the 'Technological Frontier' strategic approach "
                    "(highest-complexity economies). If results are empty, tell the user this "
                    "data is unavailable for frontier economies and suggest exploring existing "
                    "export strengths instead."
                )

            if warnings:
                content = "\n".join(warnings) + "\n\n" + content

            atlas_links = _dedupe_links(state.get("graphql_atlas_links", []))

        messages: list[ToolMessage] = [
            ToolMessage(
                content=content, tool_call_id=tool_calls[0]["id"], name="atlas_graphql"
            )
        ]
        for tc in tool_calls[1:]:
            messages.append(
                ToolMessage(
                    content="Only one query can be executed at a time. Please make additional queries sequentially.",
                    tool_call_id=tc["id"],
                    name=tc["name"],
                )
            )

    # Build per-call snapshot for graphql_call_history accumulator
    from src.state import cap_snapshot_result

    call_snapshot = {
        "question": state.get("graphql_question", ""),
        "classification": state.get("graphql_classification"),
        "entity_extraction": state.get("graphql_entity_extraction"),
        "resolved_params": state.get("graphql_resolved_params"),
        "query": state.get("graphql_query"),
        "api_target": state.get("graphql_api_target"),
        "atlas_links": atlas_links,
        "result_content": cap_snapshot_result(content),
    }

    return {
        "messages": messages,
        "queries_executed": state.get("queries_executed", 0) + 1,
        "graphql_atlas_links": atlas_links,
        "graphql_call_history": [call_snapshot],
        "step_timing": [_fmt_t.record],
    }


# ---------------------------------------------------------------------------
# Growth opportunity composite scoring
# ---------------------------------------------------------------------------
# The Atlas ranks growth opportunities by a weighted composite of three
# normalized components: distance (proximity), PCI (complexity), and
# COG / opportunity gain.  Weights depend on context:
#
# Country Pages — vary by strategy (Low-Hanging Fruit / Balanced Portfolio /
# Long Jumps) AND by the country's policyRecommendation for Balanced Portfolio.
# Default is Balanced Portfolio with weights selected by policyRecommendation.
#
# Explore page — fixed at 50% distance, 15% PCI, 35% COG (mirrors the
# Explore feasibility page which has no strategy selector).
#
# Users may override the default strategy or request custom weights.

# -- Strategy weight tables (Country Pages treeMap field names) ----------

# Balanced Portfolio weights vary by policyRecommendation:
_CP_BALANCED_WEIGHTS: dict[str, dict[str, float]] = {
    "StrategicBets": {
        "normalizedDistance": 0.50,
        "normalizedPci": 0.15,
        "normalizedOpportunityGain": 0.35,
    },
    "ParsimoniousIndustrial": {
        "normalizedDistance": 0.55,
        "normalizedPci": 0.20,
        "normalizedOpportunityGain": 0.25,
    },
    "LightTouch": {
        "normalizedDistance": 0.60,
        "normalizedPci": 0.20,
        "normalizedOpportunityGain": 0.20,
    },
}

# Fixed strategy weights (same regardless of policyRecommendation):
_CP_LOW_HANGING_FRUIT_WEIGHTS: dict[str, float] = {
    "normalizedDistance": 0.60,
    "normalizedPci": 0.15,
    "normalizedOpportunityGain": 0.25,
}
_CP_LONG_JUMPS_WEIGHTS: dict[str, float] = {
    "normalizedDistance": 0.45,
    "normalizedPci": 0.20,
    "normalizedOpportunityGain": 0.35,
}

# Explore API countryProductYear field names → weights (fixed, no strategy)
_EXPLORE_GO_WEIGHTS: dict[str, float] = {
    "normalizedDistance": 0.50,
    "normalizedPci": 0.15,
    "normalizedCog": 0.35,
}

# Default fallback for Country Pages when policyRecommendation is unknown
_CP_DEFAULT_WEIGHTS = _CP_BALANCED_WEIGHTS["StrategicBets"]

# PCI ceiling thresholds — applied only for growth_opportunities (Country
# Pages) when GDP per capita ≤ $6,000.  Products with raw PCI exceeding
# countryECI + ceiling_offset are excluded as infeasible aspirations.
_PCI_CEILING_GDP_THRESHOLD = 6000
_PCI_CEILING_OFFSET: dict[str, float] = {
    "low_hanging_fruit": 2.0,
    "balanced": 2.0,
    "long_jumps": 2.5,
    "custom": 2.0,
}


def _get_cp_weights(
    strategy: str,
    policy_recommendation: str | None = None,
) -> dict[str, float]:
    """Resolve Country Pages composite-score weights.

    Args:
        strategy: One of ``"balanced"``, ``"low_hanging_fruit"``,
            ``"long_jumps"``, or ``"custom"``.
        policy_recommendation: The country's ``policyRecommendation`` from
            ``countryProfile`` (e.g. ``"StrategicBets"``).  Only used when
            *strategy* is ``"balanced"``.

    Returns:
        Dict mapping normalized field names to weights.
    """
    if strategy == "low_hanging_fruit":
        return _CP_LOW_HANGING_FRUIT_WEIGHTS
    if strategy == "long_jumps":
        return _CP_LONG_JUMPS_WEIGHTS
    # Balanced (default) — varies by policyRecommendation
    return _CP_BALANCED_WEIGHTS.get(policy_recommendation or "", _CP_DEFAULT_WEIGHTS)


# Country policy data loaded from CSV generated by
# src/setup/update_country_policy_data.py.  Run that script to refresh.
_COUNTRY_POLICY_DATA: dict[int, tuple[str, float | None, int | None]] = {}
_COUNTRY_POLICY_DATA_LOADED = False


def _ensure_country_policy_data() -> dict[int, tuple[str, float | None, int | None]]:
    """Lazy-load country policy data from CSV on first access."""
    global _COUNTRY_POLICY_DATA, _COUNTRY_POLICY_DATA_LOADED  # noqa: PLW0603
    if _COUNTRY_POLICY_DATA_LOADED:
        return _COUNTRY_POLICY_DATA
    import csv
    from pathlib import Path

    csv_path = Path(__file__).parent / "data" / "country_policy_data.csv"
    if not csv_path.exists():
        logger.warning(
            "country_policy_data.csv not found at %s — growth opportunity "
            "scoring will use default StrategicBets weights for all countries. "
            "Run src/setup/update_country_policy_data.py to generate it.",
            csv_path,
        )
        _COUNTRY_POLICY_DATA_LOADED = True
        return _COUNTRY_POLICY_DATA

    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            m49 = int(row["country_id"])
            policy = row["policy_recommendation"]
            eci_raw = row.get("eci", "")
            gdppc_raw = row.get("gdp_per_capita", "")
            eci = float(eci_raw) if eci_raw else None
            gdppc = int(float(gdppc_raw)) if gdppc_raw else None
            _COUNTRY_POLICY_DATA[m49] = (policy, eci, gdppc)

    logger.info(
        "Loaded country policy data for %d countries from %s",
        len(_COUNTRY_POLICY_DATA),
        csv_path,
    )
    _COUNTRY_POLICY_DATA_LOADED = True
    return _COUNTRY_POLICY_DATA


def _lookup_country_policy(
    country_id: int | None,
) -> tuple[str | None, float | None, int | None]:
    """Look up a country's policy recommendation, ECI, and GDP per capita.

    Returns:
        Tuple of (policyRecommendation, eci, gdppc).  All ``None`` if the
        country is not in the lookup table.
    """
    if country_id is None:
        return None, None, None
    data = _ensure_country_policy_data()
    return data.get(country_id, (None, None, None))


def _compute_composite_score(item: dict, weights: dict[str, float]) -> float:
    """Compute weighted composite score from normalized opportunity fields."""
    return sum((item.get(f) or 0.0) * w for f, w in weights.items())


# ---------------------------------------------------------------------------
# Response post-processing — sort, truncate, enrich large responses
# ---------------------------------------------------------------------------

_POST_PROCESS_RULES: dict[str, dict] = {
    "treemap_products": {
        "root": "countryProductYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "product",
    },
    "treemap_partners": {
        "root": "countryCountryYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "country",
    },
    "treemap_bilateral": {
        "root": "countryCountryProductYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "product",
    },
    "overtime_products": {
        "root": "countryProductYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "product",
    },
    "overtime_partners": {
        "root": "countryCountryYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "country",
    },
    "marketshare": {
        "root": "countryProductYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "product",
    },
    "product_space": {
        "root": "countryProductYear",
        "sort": "exportValue",
        "top_n": 50,
        "enrich": "product",
    },
    "feasibility": {
        "root": "countryProductYear",
        "sort": "compositeScore",
        "score_weights": _EXPLORE_GO_WEIGHTS,
        "top_n": 20,
        "enrich": "product",
        "filter": "rca_lt_1",
    },
    "feasibility_table": {
        "root": "countryProductYear",
        "sort": "compositeScore",
        "score_weights": _EXPLORE_GO_WEIGHTS,
        "top_n": 20,
        "enrich": "product",
        "filter": "rca_lt_1",
    },
    "product_table": {
        "root": "countryProductYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "product",
    },
    "bilateral_aggregate": {
        "root": "countryCountryYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "country",
    },
    "explore_bilateral": {
        "root": "countryCountryProductYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "product",
    },
    "growth_opportunities": {
        "root": "treeMap",
        "sort": "compositeScore",
        # score_weights resolved dynamically in post_process_response
        # based on country_id → policyRecommendation + strategy
        "top_n": 20,
        "enrich": "none",
        "filter": "rca_lt_1_treemap",
    },
    "country_profile_exports": {
        "root": "treeMap",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "none",
    },
    "country_profile_partners": {
        "root": "treeMap",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "none",
    },
    "group_products": {
        "root": "countryGroupProductYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "product",
    },
    "group_bilateral": {
        "root": "groupCountryProductYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "product",
    },
    "global_product": {
        "root": "productYear",
        "sort": "exportValue",
        "top_n": 20,
        "enrich": "product",
    },
    "country_year": {
        "root": "countryYear",
        "sort": "year",
        "sort_ascending": True,
        "top_n": 30,
        "enrich": "none",
    },
}

_FILTERS: dict[str, Callable] = {
    "rca_lt_1": lambda item: (item.get("exportRca") or 0) < 1,
    "rca_lt_1_treemap": lambda item: (item.get("rca") or 0) < 1,
}


def _enrich_items(
    items: list[dict],
    enrich_type: str,
    product_caches: dict | None,
    product_class: str,
    country_cache: CatalogCache | None,
    services_cache: CatalogCache | None,
    query_type: str,
) -> None:
    """Enrich items in-place with human-readable names from catalog caches."""
    if enrich_type == "product" and product_caches:
        product_cache = product_caches.get(
            product_class, next(iter(product_caches.values()))
        )
        if not product_cache.is_populated:
            logger.warning(
                "Product cache (%s) not populated — skipping enrichment for %s",
                product_class,
                query_type,
            )
        else:
            for item in items:
                pid = item.get("productId")
                if pid is not None:
                    entry = product_cache.lookup_sync("id", str(pid))
                    # Fall back to services cache if the product cache
                    # misses (e.g., stale cache or classification mismatch)
                    if (
                        entry is None
                        and services_cache is not None
                        and services_cache.is_populated
                    ):
                        entry = services_cache.lookup_sync("id", str(pid))
                    if entry:
                        item["productName"] = entry.get("nameShortEn", "")
                        item["productCode"] = entry.get("code", "")
    elif enrich_type == "country" and country_cache is not None:
        if not country_cache.is_populated:
            logger.warning(
                "Country cache not populated — skipping enrichment for %s",
                query_type,
            )
        else:
            for item in items:
                cid = item.get("partnerCountryId")
                if cid is not None:
                    entry = country_cache.lookup_sync("id", str(cid))
                    if entry:
                        item["partnerName"] = entry.get("nameShortEn", "")


def post_process_response(
    query_type: str,
    raw_response: dict,
    *,
    trade_direction: str = "exports",
    product_caches: dict[str, CatalogCache] | None = None,
    product_class: str = "HS12",
    country_cache: CatalogCache | None = None,
    services_cache: CatalogCache | None = None,
    country_id: int | None = None,
    strategy: str = "balanced",
    custom_weights: dict[str, float] | None = None,
) -> dict:
    """Sort, truncate, and enrich large GraphQL responses before sending to the LLM.

    Args:
        query_type: Classified query type (e.g., "treemap_products").
        raw_response: Raw GraphQL API response dict.
        trade_direction: "exports" or "imports" — determines which value field to sort by.
        product_caches: Product catalog caches keyed by classification
            (e.g. ``{"HS92": ..., "HS12": ...}``).
        product_class: Which classification to use for enrichment (default HS12).
        country_cache: Optional country catalog cache for name enrichment.
        services_cache: Optional services catalog cache for name enrichment.
        country_id: M49 country code — used to look up policy recommendation
            for country-specific growth opportunity weights.
        strategy: Growth opportunity strategy.  One of ``"balanced"`` (default),
            ``"low_hanging_fruit"``, ``"long_jumps"``, or ``"custom"``.
        custom_weights: When *strategy* is ``"custom"``, a dict mapping
            normalized field names to weights (must sum to ~1.0).

    Returns:
        Post-processed response dict, or raw_response if no rules apply.
    """
    rules = _POST_PROCESS_RULES.get(query_type)
    if rules is None:
        return raw_response

    root_key = rules["root"]
    items = raw_response.get(root_key)
    if not isinstance(items, list):
        return raw_response

    top_n = rules["top_n"]

    # Resolve composite score weights — country-specific for growth_opportunities
    score_weights = rules.get("score_weights")
    policy_rec: str | None = None
    country_eci: float | None = None
    country_gdppc: int | None = None
    applied_strategy = strategy

    if query_type == "growth_opportunities":
        policy_rec, country_eci, country_gdppc = _lookup_country_policy(country_id)
        if strategy == "custom" and custom_weights:
            score_weights = custom_weights
        else:
            score_weights = _get_cp_weights(strategy, policy_rec)
    elif (
        query_type in ("feasibility", "feasibility_table")
        and strategy == "custom"
        and custom_weights
    ):
        # For Explore-API feasibility, custom weights use normalizedCog instead of
        # normalizedOpportunityGain.  Remap if the caller used OG keys.
        if (
            "normalizedOpportunityGain" in custom_weights
            and "normalizedCog" not in custom_weights
        ):
            custom_weights = {
                k.replace("normalizedOpportunityGain", "normalizedCog"): v
                for k, v in custom_weights.items()
            }
        score_weights = custom_weights

    if score_weights:
        for item in items:
            item["compositeScore"] = round(
                _compute_composite_score(item, score_weights), 4
            )

    # Small result sets: enrich only, no sort/truncate/metadata
    if len(items) <= top_n:
        _enrich_items(
            items,
            rules.get("enrich", "none"),
            product_caches,
            product_class,
            country_cache,
            services_cache,
            query_type,
        )
        return {root_key: items}

    total_items = len(items)

    # Apply filter if specified
    filter_name = rules.get("filter")
    if filter_name and filter_name in _FILTERS:
        items = [item for item in items if _FILTERS[filter_name](item)]

    # PCI ceiling filter — growth_opportunities only, for low-income countries
    if (
        query_type == "growth_opportunities"
        and country_gdppc is not None
        and country_gdppc <= _PCI_CEILING_GDP_THRESHOLD
        and country_eci is not None
    ):
        ceiling_offset = _PCI_CEILING_OFFSET.get(applied_strategy, 2.0)
        pci_ceiling = country_eci + ceiling_offset
        before_ceiling = len(items)
        items = [item for item in items if (item.get("pci") or 0) < pci_ceiling]
        if len(items) < before_ceiling:
            logger.info(
                "PCI ceiling filter (%.2f) removed %d items for country %s (GDPPC=%d)",
                pci_ceiling,
                before_ceiling - len(items),
                country_id,
                country_gdppc,
            )

    # Override sort field for imports
    sort_field = rules["sort"]
    if trade_direction == "imports" and sort_field == "exportValue":
        sort_field = "importValue"

    sort_ascending = rules.get("sort_ascending", False)
    items.sort(
        key=lambda x: (x.get(sort_field) is not None, x.get(sort_field) or 0),
        reverse=not sort_ascending,
    )

    # Truncate
    items = items[:top_n]

    # Enrich with human-readable names
    _enrich_items(
        items,
        rules.get("enrich", "none"),
        product_caches,
        product_class,
        country_cache,
        services_cache,
        query_type,
    )

    # Build metadata
    meta: dict = {
        "totalItems": total_items,
        "shownItems": len(items),
        "sortField": sort_field,
        "tradeDirection": trade_direction,
        "summary": f"Showing top {len(items)} of {total_items} items, sorted by {sort_field} ({trade_direction}).",
    }
    if query_type == "growth_opportunities" and score_weights:
        meta["strategy"] = applied_strategy
        meta["policyRecommendation"] = policy_rec
        meta["weights"] = {k: v for k, v in score_weights.items()}

    return {root_key: items, "_postProcessed": meta}


# ---------------------------------------------------------------------------
# GraphQL query builders
# ---------------------------------------------------------------------------


def build_graphql_query(
    query_type: str,
    resolved_params: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Build a GraphQL query string and variables for the given query type.

    Args:
        query_type: Classified query type (e.g., "treemap_products").
        resolved_params: Resolved entity IDs and parameters.

    Returns:
        Tuple of (query_string, variables_dict).

    Raises:
        ValueError: If query_type is not recognized.
    """
    builder = _QUERY_BUILDERS.get(query_type)
    if builder is None:
        raise ValueError(
            f"Unknown GraphQL query type: {query_type!r}. "
            f"Valid types: {sorted(_QUERY_BUILDERS.keys())}"
        )
    return builder(resolved_params)


# --- Explore API query builders ---


def _build_country_product_year(params: dict) -> tuple[str, dict]:
    """Build countryProductYear query (Explore API)."""
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS12"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    if "product_id" in params:
        variables["productId"] = params["product_id"]

    services_class = params.get("services_class")
    if services_class:
        variables["servicesClass"] = services_class

    sc_var = ", $servicesClass: ServicesClass" if services_class else ""
    sc_arg = "\n        servicesClass: $servicesClass" if services_class else ""
    query = f"""
    query CPY($countryId: Int, $productLevel: Int!, $productClass: ProductClass,
              $productId: Int, $yearMin: Int, $yearMax: Int{sc_var}) {{
      countryProductYear(
        countryId: $countryId
        productLevel: $productLevel
        productClass: $productClass
        productId: $productId
        yearMin: $yearMin
        yearMax: $yearMax{sc_arg}
      ) {{
        countryId productId productLevel year
        exportValue importValue globalMarketShare
        exportRca exportRpop
        isNew productStatus
        cog distance
        normalizedPci normalizedCog normalizedDistance normalizedExportRca
      }}
    }}
    """
    return query, variables


def _build_country_country_year(params: dict) -> tuple[str, dict]:
    """Build countryCountryYear query (Explore API).

    Supports optional ``partner_id`` for bilateral aggregate filtering.
    When absent, returns all partner rows (treemap_partners / overtime_partners).

    NOTE: The API resolver performs a pandas groupby after unioning goods and
    services tables, so CCY.exportValue is the goods+services total aggregated
    into a single row per (country, partner, year).  This differs from CPY/CCPY
    where goods and services appear as separate product rows.
    """
    variables: dict[str, Any] = {"countryId": params.get("country_id")}
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    if "partner_id" in params:
        variables["partnerCountryId"] = params["partner_id"]

    services_class = params.get("services_class")
    if services_class:
        variables["servicesClass"] = services_class

    sc_var = ", $servicesClass: ServicesClass" if services_class else ""
    sc_arg = "\n        servicesClass: $servicesClass" if services_class else ""
    query = f"""
    query CCY($countryId: Int, $partnerCountryId: Int, $yearMin: Int, $yearMax: Int{sc_var}) {{
      countryCountryYear(
        countryId: $countryId
        partnerCountryId: $partnerCountryId
        yearMin: $yearMin
        yearMax: $yearMax{sc_arg}
      ) {{
        countryId partnerCountryId year
        exportValue importValue
        exportValueReported importValueReported
      }}
    }}
    """
    return query, variables


def _build_country_country_product_year(params: dict) -> tuple[str, dict]:
    """Build countryCountryProductYear query (Explore API)."""
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "partnerCountryId": params.get("partner_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS12"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    services_class = params.get("services_class")
    if services_class:
        variables["servicesClass"] = services_class

    sc_var = ", $servicesClass: ServicesClass" if services_class else ""
    sc_arg = "\n        servicesClass: $servicesClass" if services_class else ""
    query = f"""
    query CCPY($countryId: Int, $partnerCountryId: Int,
               $productLevel: Int!, $productClass: ProductClass,
               $yearMin: Int, $yearMax: Int{sc_var}) {{
      countryCountryProductYear(
        countryId: $countryId
        partnerCountryId: $partnerCountryId
        productLevel: $productLevel
        productClass: $productClass
        yearMin: $yearMin
        yearMax: $yearMax{sc_arg}
      ) {{
        countryId partnerCountryId productId productLevel year
        exportValue importValue
      }}
    }}
    """
    return query, variables


def _build_country_year_cp(params: dict) -> tuple[str, dict]:
    """Build countryYear query (Country Pages API).

    Supports eciProductClass for SITC/HS-specific ECI values.
    """
    location = params.get("location", "")
    year = params.get("year") or params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
    variables: dict[str, Any] = {"location": location, "year": int(year)}

    product_class = _normalize_cp_product_class(params.get("product_class"))
    if product_class:
        variables["eciProductClass"] = product_class

    pc_var = ", $eciProductClass: ProductClass" if product_class else ""
    pc_arg = "\n        eciProductClass: $eciProductClass" if product_class else ""
    query = f"""
    query CY($location: ID!, $year: Int!{pc_var}) {{
      countryYear(
        location: $location
        year: $year{pc_arg}
      ) {{
        eci eciRank coi coiRank
        exportValue importValue exportValueRank
        population gdp gdpRank gdpPpp gdpPerCapita gdpPerCapitaPpp
      }}
    }}
    """
    return query, variables


def _build_country_year(params: dict) -> tuple[str, dict]:
    """Build countryYear query (Explore or Country Pages API)."""
    # Country Pages path: format_ids_for_api sets "location" for country_pages
    if "location" in params:
        return _build_country_year_cp(params)
    # Explore API path (original)
    variables: dict[str, Any] = {"countryId": params.get("country_id")}
    year = params.get("year")
    lookback = params.get("lookback_years")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    elif params.get("year_min") or params.get("year_max"):
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)
    elif lookback:
        variables["yearMax"] = GRAPHQL_DATA_MAX_YEAR
        variables["yearMin"] = GRAPHQL_DATA_MAX_YEAR - lookback
    else:
        variables["yearMin"] = GRAPHQL_DATA_MAX_YEAR
        variables["yearMax"] = GRAPHQL_DATA_MAX_YEAR

    services_class = params.get("services_class")
    if services_class:
        variables["servicesClass"] = services_class

    sc_var = ", $servicesClass: ServicesClass" if services_class else ""
    sc_arg = "\n        servicesClass: $servicesClass" if services_class else ""
    query = f"""
    query CY($countryId: Int, $yearMin: Int, $yearMax: Int{sc_var}) {{
      countryYear(
        countryId: $countryId
        yearMin: $yearMin
        yearMax: $yearMax{sc_arg}
      ) {{
        countryId year
        exportValue importValue
        population gdp gdppc gdpPpp gdppcPpp
        gdpConst gdpPppConst gdppcConst gdppcPppConst
        eci eciFixed coi
        currentAccount growthProj
      }}
    }}
    """
    return query, variables


def _build_product_year(params: dict) -> tuple[str, dict]:
    """Build productYear query (Explore API)."""
    variables: dict[str, Any] = {
        "productId": params.get("product_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    services_class = params.get("services_class")
    if services_class:
        variables["servicesClass"] = services_class

    sc_var = ", $servicesClass: ServicesClass" if services_class else ""
    sc_arg = "\n        servicesClass: $servicesClass" if services_class else ""
    query = f"""
    query PY($productId: Int, $productLevel: Int!, $yearMin: Int, $yearMax: Int{sc_var}) {{
      productYear(
        productId: $productId
        productLevel: $productLevel
        yearMin: $yearMin
        yearMax: $yearMax{sc_arg}
      ) {{
        productId productLevel year
        exportValue importValue
        exportValueConstGrowth5 importValueConstGrowth5
        exportValueConstCagr5 importValueConstCagr5
        pci complexityEnum
        naturalResource
      }}
    }}
    """
    return query, variables


def _build_global_product_year(params: dict) -> tuple[str, dict]:
    """Build productYear query for global product data (Explore API).

    Used for country-agnostic product queries like "top exported products
    globally". Returns all products for a given year with export/import
    values and PCI.
    """
    product_class = params.get("product_class") or "HS92"
    product_level = _product_level_to_int(params.get("product_level", "fourDigit"))
    year = params.get("year") or params.get("year_max") or GRAPHQL_DATA_MAX_YEAR
    variables: dict[str, Any] = {
        "productClass": product_class,
        "productLevel": product_level,
        "yearMin": int(year),
        "yearMax": int(year),
    }
    query = """
    query PY($productClass: ProductClass!, $productLevel: Int!,
             $yearMin: Int!, $yearMax: Int!) {
      productYear(productClass: $productClass, productLevel: $productLevel,
                  yearMin: $yearMin, yearMax: $yearMax) {
        productId year exportValue importValue pci
      }
    }
    """
    return query, variables


def _build_product_space(params: dict) -> tuple[str, dict]:
    """Build product space queries (Explore API).

    Combines countryProductYear (for RCA) with productProduct (for edges).
    """
    # We build the CPY query — the caller can optionally chain productProduct
    return _build_country_product_year(params)


def _build_marketshare(params: dict) -> tuple[str, dict]:
    """Build market share query (Explore API).

    Uses countryProductYear with year range for time-series data.
    """
    return _build_country_product_year(params)


def _build_data_availability(params: dict) -> tuple[str, dict]:
    """Build dataAvailability query (Explore API)."""
    query = """
    query {
      dataAvailability {
        productClassification
        yearMin
        yearMax
      }
    }
    """
    return query, {}


def _build_group_year(params: dict) -> tuple[str, dict]:
    """Build groupYear query (Explore API).

    NOTE: The API's groupYear resolver joins classification.location_group_member
    with the goods-only CY model.  It does NOT union with services tables.
    Therefore groupYear.exportValue represents goods-only trade.  By contrast,
    countryYear explicitly unions goods + services CY tables.  When reporting
    group-level export totals, the agent should caveat that the figure excludes
    services trade.
    """
    variables: dict[str, Any] = {}
    if "group_id" in params:
        variables["groupId"] = params["group_id"]
    if "group_type" in params:
        variables["groupType"] = params["group_type"]
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    query = """
    query GY($groupId: Int, $groupType: GroupType, $yearMin: Int, $yearMax: Int) {
      groupYear(
        groupId: $groupId
        groupType: $groupType
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        groupId year
        exportValue importValue
        population gdp gdpPpp
      }
    }
    """
    return query, variables


def _build_country_group_product_year(params: dict) -> tuple[str, dict]:
    """Build countryGroupProductYear query (Explore API — CGPY).

    Answers "What does country X export to group Y?" with product-level data.
    The API joins CCPY with group membership, summing by (country, product, year).
    Special case: partnerGroupId = 1 (World) uses CPY tables internally.
    """
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "partnerGroupId": params.get("partner_group_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS12"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    query = """
    query CGPY($countryId: Int!, $partnerGroupId: Int!,
               $productLevel: Int!, $productClass: ProductClass,
               $yearMin: Int, $yearMax: Int) {
      countryGroupProductYear(
        countryId: $countryId
        partnerGroupId: $partnerGroupId
        productLevel: $productLevel
        productClass: $productClass
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        countryId partnerGroupId productId productLevel year
        exportValue importValue
      }
    }
    """
    return query, variables


def _build_group_country_product_year(params: dict) -> tuple[str, dict]:
    """Build groupCountryProductYear query (Explore API — GCPY).

    Answers "What does group X export to country Y?" with product-level data.
    The API joins CCPY with group membership (members as exporters), summing
    by (group, product, year).  Special case: groupId = 1 (World) flips
    export/import direction internally.
    """
    variables: dict[str, Any] = {
        "groupId": params.get("group_id"),
        "partnerCountryId": params.get("partner_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS12"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    query = """
    query GCPY($groupId: Int!, $partnerCountryId: Int!,
               $productLevel: Int!, $productClass: ProductClass,
               $yearMin: Int, $yearMax: Int) {
      groupCountryProductYear(
        groupId: $groupId
        partnerCountryId: $partnerCountryId
        productLevel: $productLevel
        productClass: $productClass
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        groupId partnerCountryId productId productLevel year
        exportValue importValue
      }
    }
    """
    return query, variables


# --- Country Pages API query builders ---

_LOOKBACK_YEAR_MAP = {
    3: "ThreeYears",
    5: "FiveYears",
    10: "TenYears",
    15: "FifteenYears",
}


def _normalize_cp_product_class(raw: str | None) -> str | None:
    """Normalize product_class for the Country Pages API.

    Country Pages accepts only 'HS' and 'SITC' — not the revision-specific
    codes (HS92, HS12, HS22) used by the Explore API.
    """
    if not raw:
        return raw
    if raw.upper().startswith("HS"):
        return "HS"
    return raw


def _build_country_profile(params: dict) -> tuple[str, dict]:
    """Build countryProfile query (Country Pages API)."""
    location = params.get("location", "")
    variables = {"location": location}
    query = """
    query CP($location: ID!) {
      countryProfile(location: $location) {
        location { id shortName }
        latestPopulation { quantity year }
        latestGdp { quantity year }
        latestGdpRank { quantity year }
        latestGdpPpp { quantity year }
        latestGdpPppRank { quantity year }
        latestGdpPerCapita { quantity year }
        latestGdpPerCapitaRank { quantity year }
        latestGdpPerCapitaPpp { quantity year }
        latestGdpPerCapitaPppRank { quantity year }
        incomeClassification
        exportValue importValue exportValueRank
        exportValueNatResources importValueNatResources
        latestEci latestEciRank
        latestCoi latestCoiRank coiClassification
        growthProjection growthProjectionRank
        growthProjectionClassification
        growthProjectionRelativeToIncome
        growthProjectionPercentileClassification
        diversificationGrade diversityRank diversity
        policyRecommendation
        currentAccount { quantity year }
        structuralTransformationStep
        structuralTransformationSector { shortName }
        structuralTransformationDirection
        marketShareMainSector { shortName }
        marketShareMainSectorDirection
        marketShareMainSectorPositiveGrowth
        newProductsComplexityStatusGrowthPrediction
      }
    }
    """
    return query, variables


def _build_country_lookback(params: dict) -> tuple[str, dict]:
    """Build countryLookback query (Country Pages API)."""
    location = params.get("location", "")
    variables: dict[str, Any] = {"id": location}

    lookback = params.get("lookback_years")
    if lookback and lookback in _LOOKBACK_YEAR_MAP:
        variables["yearRange"] = _LOOKBACK_YEAR_MAP[lookback]

    product_class = _normalize_cp_product_class(params.get("product_class"))
    if product_class:
        variables["productClass"] = product_class

    query = """
    query CL($id: ID!, $yearRange: LookBackYearRange, $productClass: ProductClass) {
      countryLookback(id: $id, yearRange: $yearRange, productClass: $productClass) {
        id
        eciRankChange eciChange
        exportValueConstGrowthCagr
        exportValueGrowthNonOilConstCagr
        diversityRankChange diversityChange
        exportValueGrowthClassification
        gdpPcConstantCagrRegionalDifference
        gdpChangeConstantCagr
        gdpPerCapitaChangeConstantCagr
        gdpGrowthConstant
        largestContributingExportProduct { shortName code }
      }
    }
    """
    return query, variables


def _build_new_products(params: dict) -> tuple[str, dict]:
    """Build newProductsCountry + comparison query (Country Pages API).

    Includes ``newProductsComparisonCountries`` for peer comparison context
    alongside the primary new-products data.
    """
    location = params.get("location", "")
    year = params.get("year", GRAPHQL_DATA_MAX_YEAR)
    variables: dict[str, Any] = {"location": location, "year": int(year)}
    query = """
    query NP($location: ID!, $year: Int!) {
      newProductsCountry(location: $location, year: $year) {
        location { id shortName }
        newProductCount
        newProductExportValue
        newProductExportValuePerCapita
        newProducts { id code shortName longName }
      }
      newProductsComparisonCountries(location: $location, year: $year) {
        location { id shortName }
        newProductCount
        newProductExportValue
        newProductExportValuePerCapita
      }
    }
    """
    return query, variables


def _build_growth_opportunities(params: dict) -> tuple[str, dict]:
    """Build growth opportunities query (Country Pages treeMap API).

    Uses treeMap(facet: CPY_C) which provides pre-computed opportunity
    metrics. Only supports productClass HS (= HS92). For SITC or custom
    queries, the classification prompt routes to ``feasibility`` instead.
    """
    location = params.get("location", "")
    product_class = _normalize_cp_product_class(params.get("product_class")) or "HS"
    product_level = params.get("product_level", "fourDigit")
    year = params.get("year") or GRAPHQL_DATA_MAX_YEAR
    variables: dict[str, Any] = {
        "location": location,
        "productClass": product_class,
        "productLevel": product_level,
        "year": int(year),
    }
    query = """
    query GO($location: ID!, $productClass: ProductClass!,
             $productLevel: ProductLevel!, $year: Int!) {
      treeMap(facet: CPY_C, location: $location, productClass: $productClass,
              productLevel: $productLevel, year: $year) {
        ... on TreeMapProduct {
          product { id shortName code }
          exportValue rca
          opportunityGain distance pci
          normalizedOpportunityGain normalizedDistance normalizedPci
          globalMarketShare
        }
      }
    }
    """
    return query, variables


def _build_product_table(params: dict) -> tuple[str, dict]:
    """Build product table query (Explore API countryProductYear)."""
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS12"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    if "product_id" in params:
        variables["productId"] = params["product_id"]

    query = """
    query PT($countryId: Int, $productLevel: Int!, $productClass: ProductClass,
              $productId: Int, $yearMin: Int, $yearMax: Int) {
      countryProductYear(
        countryId: $countryId
        productLevel: $productLevel
        productClass: $productClass
        productId: $productId
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        countryId productId productLevel year
        exportValue importValue globalMarketShare
        exportRca exportRpop
        isNew productStatus
        cog distance
        normalizedPci normalizedCog normalizedDistance normalizedExportRca
      }
    }
    """
    return query, variables


def _build_cp_treemap_products(params: dict) -> tuple[str, dict]:
    """Build treeMap(facet: CPY_C) query (Country Pages API).

    Returns product-level export data including services for a country's
    export basket.  Used by ``country_profile_exports`` to get individual
    product breakdown rather than just aggregate countryProfile data.
    """
    location = params.get("location", "")
    product_class = _normalize_cp_product_class(params.get("product_class")) or "HS"
    product_level = params.get("product_level", "fourDigit")
    year = params.get("year", GRAPHQL_DATA_MAX_YEAR)
    variables: dict[str, Any] = {
        "location": location,
        "productClass": product_class,
        "productLevel": product_level,
        "year": int(year),
    }
    query = """
    query TMProducts($location: ID!, $productClass: ProductClass!,
                      $productLevel: ProductLevel!, $year: Int!) {
      treeMap(facet: CPY_C, location: $location, productClass: $productClass,
              productLevel: $productLevel, year: $year) {
        ... on TreeMapProduct {
          product { id shortName code }
          exportValue
        }
      }
    }
    """
    return query, variables


def _build_cp_treemap_partners(params: dict) -> tuple[str, dict]:
    """Build treeMap(facet: CCY_C) query (Country Pages API).

    Returns bilateral trade partner breakdown (goods only) for a country.
    """
    location = params.get("location", "")
    product_class = _normalize_cp_product_class(params.get("product_class")) or "HS"
    year = params.get("year", GRAPHQL_DATA_MAX_YEAR)
    variables: dict[str, Any] = {
        "location": location,
        "productClass": product_class,
        "year": int(year),
    }
    query = """
    query TMPartners($location: ID!, $productClass: ProductClass!, $year: Int!) {
      treeMap(facet: CCY_C, location: $location, productClass: $productClass,
              year: $year) {
        ... on TreeMapLocation {
          location { id shortName longName }
          exportValue
        }
      }
    }
    """
    return query, variables


def _build_global_datum(params: dict) -> tuple[str, dict]:
    """Build globalDatum query (Country Pages API)."""
    query = """
    query {
      globalDatum {
        globalExportValue
        latestEciRankTotal
        latestCoiRankTotal
        latestExporterRankTotal
        latestGdpRankTotal
        latestGdpPppPerCapitaRankTotal
        latestDiversityRankTotal
      }
    }
    """
    return query, {}


# ---------------------------------------------------------------------------
# Helper: product level string → integer conversion
# ---------------------------------------------------------------------------

_PRODUCT_LEVEL_MAP: dict[str, int] = {
    "section": 1,
    "twoDigit": 2,
    "fourDigit": 4,
    "sixDigit": 6,
}


def _product_level_to_int(level: str | int | None) -> int:
    """Convert a product level string to integer for the Explore API."""
    if isinstance(level, int):
        return level
    if level is None:
        return 4  # default to 4-digit
    return _PRODUCT_LEVEL_MAP.get(level, 4)


# ---------------------------------------------------------------------------
# Slim query builders — reduced field sets for high-volume responses
# ---------------------------------------------------------------------------


def _build_treemap_cpy(params: dict) -> tuple[str, dict]:
    """Slim builder for treemap_products — only sort+display fields."""
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS12"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    if "product_id" in params:
        variables["productId"] = params["product_id"]

    services_class = params.get("services_class")
    if services_class:
        variables["servicesClass"] = services_class

    sc_var = ", $servicesClass: ServicesClass" if services_class else ""
    sc_arg = "\n        servicesClass: $servicesClass" if services_class else ""
    query = f"""
    query CPY($countryId: Int, $productLevel: Int!, $productClass: ProductClass,
              $productId: Int, $yearMin: Int, $yearMax: Int{sc_var}) {{
      countryProductYear(
        countryId: $countryId
        productLevel: $productLevel
        productClass: $productClass
        productId: $productId
        yearMin: $yearMin
        yearMax: $yearMax{sc_arg}
      ) {{
        productId year exportValue importValue
      }}
    }}
    """
    return query, variables


def _build_treemap_ccy(params: dict) -> tuple[str, dict]:
    """Slim builder for treemap_partners — only sort+display fields."""
    variables: dict[str, Any] = {"countryId": params.get("country_id")}
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    services_class = params.get("services_class")
    if services_class:
        variables["servicesClass"] = services_class

    sc_var = ", $servicesClass: ServicesClass" if services_class else ""
    sc_arg = "\n        servicesClass: $servicesClass" if services_class else ""
    query = f"""
    query CCY($countryId: Int, $yearMin: Int, $yearMax: Int{sc_var}) {{
      countryCountryYear(
        countryId: $countryId
        yearMin: $yearMin
        yearMax: $yearMax{sc_arg}
      ) {{
        countryId partnerCountryId year
        exportValue importValue
      }}
    }}
    """
    return query, variables


def _build_treemap_ccpy(params: dict) -> tuple[str, dict]:
    """Slim builder for treemap_bilateral — only sort+display fields."""
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "partnerCountryId": params.get("partner_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS12"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    services_class = params.get("services_class")
    if services_class:
        variables["servicesClass"] = services_class

    sc_var = ", $servicesClass: ServicesClass" if services_class else ""
    sc_arg = "\n        servicesClass: $servicesClass" if services_class else ""
    query = f"""
    query CCPY($countryId: Int, $partnerCountryId: Int,
               $productLevel: Int!, $productClass: ProductClass,
               $yearMin: Int, $yearMax: Int{sc_var}) {{
      countryCountryProductYear(
        countryId: $countryId
        partnerCountryId: $partnerCountryId
        productLevel: $productLevel
        productClass: $productClass
        yearMin: $yearMin
        yearMax: $yearMax{sc_arg}
      ) {{
        productId year exportValue importValue
      }}
    }}
    """
    return query, variables


def _build_feasibility_cpy(params: dict) -> tuple[str, dict]:
    """Slim builder for feasibility — RCA + complexity fields only."""
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS12"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", GRAPHQL_DATA_MAX_YEAR)
        variables["yearMax"] = params.get("year_max", GRAPHQL_DATA_MAX_YEAR)

    if "product_id" in params:
        variables["productId"] = params["product_id"]

    services_class = params.get("services_class")
    if services_class:
        variables["servicesClass"] = services_class

    sc_var = ", $servicesClass: ServicesClass" if services_class else ""
    sc_arg = "\n        servicesClass: $servicesClass" if services_class else ""
    query = f"""
    query CPY($countryId: Int, $productLevel: Int!, $productClass: ProductClass,
              $productId: Int, $yearMin: Int, $yearMax: Int{sc_var}) {{
      countryProductYear(
        countryId: $countryId
        productLevel: $productLevel
        productClass: $productClass
        productId: $productId
        yearMin: $yearMin
        yearMax: $yearMax{sc_arg}
      ) {{
        productId year exportValue importValue exportRca cog distance
        normalizedCog normalizedDistance normalizedPci
      }}
    }}
    """
    return query, variables


def _build_group_membership(params: dict) -> tuple[str, dict]:
    """Build locationGroup query with members (Explore API)."""
    variables: dict[str, Any] = {}
    group_type = params.get("group_type")
    if group_type:
        variables["groupType"] = group_type
    query = """
    query LG($groupType: GroupType) {
      locationGroup(groupType: $groupType) {
        groupId groupName groupType members
      }
    }
    """
    return query, variables


def post_process_group_membership(
    raw_response: dict,
    *,
    group_id: int | None = None,
    group_name: str | None = None,
    country_cache: CatalogCache | None = None,
) -> dict:
    """Post-process locationGroup response for group membership queries.

    Filters to the target group, enriches member IDs with country names.

    Args:
        raw_response: Raw GraphQL response with ``locationGroup`` key.
        group_id: Target group ID to filter to.
        group_name: Fallback group name for matching (case-insensitive).
        country_cache: Country catalog cache for member name enrichment.

    Returns:
        Dict with group info and enriched members list.
    """
    groups = raw_response.get("locationGroup", [])
    if not groups:
        return {"error": "No groups returned", "locationGroup": []}

    # Filter to target group — priority: group_id > exact name > substring name > first
    target = None
    if group_id is not None:
        target = next((g for g in groups if g.get("groupId") == group_id), None)
    if target is None and group_name:
        name_lower = group_name.strip().lower()
        # Exact name match
        target = next(
            (
                g
                for g in groups
                if (g.get("groupName") or "").strip().lower() == name_lower
            ),
            None,
        )
        # Substring match (handles abbreviations like "EU" → "European Union")
        if target is None:
            target = next(
                (
                    g
                    for g in groups
                    if name_lower in (g.get("groupName") or "").strip().lower()
                ),
                None,
            )
    if target is None:
        target = groups[0]  # Fallback to first group

    raw_members = target.get("members", [])
    enriched_members: list[dict[str, str]] = []
    for member_id_str in raw_members:
        entry: dict[str, str] = {"id": member_id_str}
        # Enrich with country name from cache
        if country_cache is not None and country_cache.is_populated:
            try:
                numeric_id = _strip_id_prefix(member_id_str)
                country_entry = country_cache.lookup_sync("id", str(numeric_id))
                if country_entry:
                    entry["name"] = country_entry.get("nameShortEn", "")
            except (ValueError, KeyError):
                pass
        enriched_members.append(entry)

    return {
        "groupId": target.get("groupId"),
        "groupName": target.get("groupName"),
        "groupType": target.get("groupType"),
        "memberCount": len(enriched_members),
        "members": enriched_members,
    }


# ---------------------------------------------------------------------------
# Query type → builder dispatch table
# ---------------------------------------------------------------------------

_QUERY_BUILDERS: dict[str, Callable[[dict], tuple[str, dict]]] = {
    # Explore API queries (slim builders for high-volume responses)
    "treemap_products": _build_treemap_cpy,
    "treemap_partners": _build_treemap_ccy,
    "treemap_bilateral": _build_treemap_ccpy,
    "feasibility": _build_feasibility_cpy,
    "feasibility_table": _build_feasibility_cpy,
    # Explore API queries (full-field builders)
    "overtime_products": _build_country_product_year,
    "overtime_partners": _build_country_country_year,
    "marketshare": _build_marketshare,
    "product_space": _build_product_space,
    "country_year": _build_country_year,
    "product_info": _build_product_year,
    "bilateral_aggregate": _build_country_country_year,
    "explore_bilateral": _build_country_country_product_year,
    "explore_group": _build_group_year,
    "group_products": _build_country_group_product_year,
    "group_bilateral": _build_group_country_product_year,
    "group_membership": _build_group_membership,
    "explore_data_availability": _build_data_availability,
    "product_table": _build_product_table,
    "global_product": _build_global_product_year,
    # Country Pages API queries
    "country_profile": _build_country_profile,
    "country_profile_exports": _build_cp_treemap_products,
    "country_profile_partners": _build_cp_treemap_partners,
    "country_profile_complexity": _build_country_profile,
    "country_lookback": _build_country_lookback,
    "new_products": _build_new_products,
    "global_datum": _build_global_datum,
    "growth_opportunities": _build_growth_opportunities,
}


# ---------------------------------------------------------------------------
# Assessment node: assess_graphql_result
# ---------------------------------------------------------------------------

TECHFRONTIER_COUNTRIES = frozenset(
    {840, 156, 276, 392, 826, 250, 380, 410, 528, 756, 124, 752, 40, 246, 203, 702}
)
"""M49 codes for TechFrontier countries where Atlas suppresses growth opportunity visualizations."""

# Country Pages classification coverage: only HS (=HS92) and SITC
_CP_SUPPORTED_CLASSES = frozenset({"HS", "HS92", "SITC", None})


class ResultAssessment(BaseModel):
    """LLM assessment of a GraphQL pipeline result."""

    verdict: Literal["pass", "fail", "suspicious"] = Field(
        description="Overall assessment of the result quality."
    )
    failure_type: Literal[
        "empty_results",
        "wrong_question_answered",
        "data_shape_mismatch",
        "api_error",
        "coverage_gap",
        "entity_resolution_error",
        "wrong_metric_or_field",
        "classification_mismatch",
        None,
    ] = Field(default=None, description="Category of failure if verdict is not pass.")
    reasoning: str = Field(
        description="Brief reasoning for the verdict (max 200 chars)."
    )


def _get_root_data_list(raw_response: dict) -> list | None:
    """Extract the root data list from a GraphQL response.

    Returns the first list value found in the response dict, or None.
    """
    if not isinstance(raw_response, dict):
        return None
    for value in raw_response.values():
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            # Nested — e.g. {"data": {"countryProductYear": [...]}}
            for inner_value in value.values():
                if isinstance(inner_value, list):
                    return inner_value
    return None


async def assess_graphql_result(
    state: AtlasAgentState,
    *,
    lightweight_model: Any,
) -> dict:
    """Assess the quality of a GraphQL pipeline result.

    Tier 1 (deterministic): catches API errors, empty results, coverage gaps.
    Tier 2 (LLM): assesses ambiguous cases (e.g. TechFrontier empty results).

    Args:
        state: Current agent state after build_and_execute_graphql.
        lightweight_model: Lightweight LLM for tier-2 assessment.

    Returns:
        Dict with ``graphql_assessment`` string in format ``"verdict|failure_type|reasoning"``.
    """
    async with node_timer("assess_graphql_result", "atlas_graphql") as t:
        raw_response = state.get("graphql_raw_response")
        classification = state.get("graphql_classification") or {}
        query_type = classification.get("query_type", "")
        resolved_params = state.get("graphql_resolved_params") or {}
        question = state.get("graphql_question", "")

        usage_sink: list[dict] = []

        # --- Tier 1: Deterministic checks ---

        # Check 1: API error
        if raw_response is None or (
            isinstance(raw_response, dict) and "error" in raw_response
        ):
            assessment = "fail|api_error|API returned an error or no response"
            return {
                "graphql_assessment": assessment,
                "step_timing": [t.record],
            }

        # Check 2: Empty results
        root_list = _get_root_data_list(raw_response)
        is_empty = root_list is not None and len(root_list) == 0

        if is_empty:
            # TechFrontier exception: empty growth opportunity data is expected
            country_id = resolved_params.get("country_id")
            if (
                query_type
                in {"feasibility", "feasibility_table", "growth_opportunities"}
                and country_id in TECHFRONTIER_COUNTRIES
            ):
                # Ambiguous — let LLM assess whether this is expected
                pass  # Fall through to tier 2
            else:
                assessment = "fail|empty_results|Query returned zero results"
                return {
                    "graphql_assessment": assessment,
                    "step_timing": [t.record],
                }

        # Check 3: Coverage gap — Country Pages with unsupported classification
        api_target = classification.get("api_target") or state.get("graphql_api_target")
        entity_extraction = state.get("graphql_entity_extraction") or {}
        product_class = entity_extraction.get("product_class")
        if (
            api_target == "country_pages"
            and product_class is not None
            and product_class not in _CP_SUPPORTED_CLASSES
        ):
            assessment = (
                f"fail|coverage_gap|Country Pages API does not support "
                f"product class {product_class}"
            )
            return {
                "graphql_assessment": assessment,
                "step_timing": [t.record],
            }

        # Tier 1 clean pass — no issues detected
        if not is_empty:
            assessment = "pass|None|Tier 1 checks passed"
            return {
                "graphql_assessment": assessment,
                "step_timing": [t.record],
            }

        # --- Tier 2: LLM assessment (only for ambiguous cases) ---
        import json

        response_sample = json.dumps(raw_response, default=str)[:4000]
        prompt = (
            f"Assess whether this GraphQL API result correctly answers the user's question.\n\n"
            f"Question: {question}\n"
            f"Query type: {query_type}\n"
            f"Resolved params: {json.dumps(resolved_params, default=str)}\n"
            f"Response (first 4K chars): {response_sample}\n\n"
            f"Consider: Is the result empty because this country/product combination "
            f"genuinely has no data (e.g. advanced economies have no growth opportunities "
            f"in the Atlas), or is it a pipeline error?"
        )

        from langchain_core.callbacks import UsageMetadataCallbackHandler

        handler = UsageMetadataCallbackHandler()
        try:
            llm = lightweight_model.with_structured_output(
                ResultAssessment, method="function_calling"
            )
            llm_start = time.monotonic()
            result: ResultAssessment = await llm.ainvoke(
                prompt, config={"callbacks": [handler]}
            )
            t.mark_llm(llm_start, time.monotonic())

            usage_sink.append(
                make_usage_record_from_callback(
                    "assess_graphql_result", "atlas_graphql", handler
                )
            )

            ft = result.failure_type if result.failure_type else "None"
            assessment = f"{result.verdict}|{ft}|{result.reasoning}"
        except Exception as e:
            logger.warning("LLM assessment failed, defaulting to pass: %s", e)
            assessment = "pass|None|LLM assessment failed, defaulting to pass"

    result_dict: dict[str, Any] = {
        "graphql_assessment": assessment,
        "step_timing": [t.record],
    }
    if usage_sink:
        result_dict["token_usage"] = usage_sink
    return result_dict


def route_after_assessment(
    state: AtlasAgentState,
) -> Literal["format_graphql_results", "graphql_correction_agent"]:
    """Route based on assessment verdict: pass → format, fail/suspicious → correct."""
    assessment = state.get("graphql_assessment", "")
    verdict = assessment.split("|", 1)[0] if assessment else "pass"
    if verdict in ("fail", "suspicious"):
        return "graphql_correction_agent"
    return "format_graphql_results"


# ---------------------------------------------------------------------------
# Catalog lookup node (for lookup_catalog tool)
# ---------------------------------------------------------------------------


async def execute_catalog_lookup(
    state: AtlasAgentState,
    *,
    product_caches: dict[str, CatalogCache] | None = None,
    country_cache: CatalogCache | None = None,
    services_cache: CatalogCache | None = None,
) -> dict:
    """Execute a lookup_catalog tool call, resolving IDs to human-readable names.

    Args:
        state: Current agent state — expects the last message to have a
            ``lookup_catalog`` tool call.
        product_caches: Product catalog caches keyed by classification.
        country_cache: Country catalog cache.
        services_cache: Services catalog cache.

    Returns:
        Dict with a ToolMessage containing JSON mapping of ID → name.
    """
    import json

    last_msg = state["messages"][-1]
    tool_call = next(tc for tc in last_msg.tool_calls if tc["name"] == "lookup_catalog")
    args = tool_call["args"]
    entity_type = args.get("entity_type", "product")
    ids = args.get("ids", [])
    product_class = args.get("product_class", "HS12")

    results: dict[int, str | None] = {}

    if entity_type == "product" and product_caches:
        cache = product_caches.get(
            product_class, next(iter(product_caches.values()), None)
        )
        for pid in ids:
            entry = None
            if cache:
                entry = await cache.lookup("id", str(pid))
            # Fall back to services cache
            if entry is None and services_cache:
                entry = await services_cache.lookup("id", str(pid))
            results[pid] = entry.get("nameShortEn") if entry else None
    elif entity_type == "country" and country_cache:
        for cid in ids:
            entry = await country_cache.lookup("id", str(cid))
            results[cid] = entry.get("nameShortEn") if entry else None
    else:
        results = {i: None for i in ids}

    return {
        "messages": [
            ToolMessage(
                content=json.dumps(results, default=str),
                tool_call_id=tool_call["id"],
                name="lookup_catalog",
            )
        ]
    }
