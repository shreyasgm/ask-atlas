"""GraphQL pipeline node functions for the Atlas agent graph.

Provides 6 async node functions that form a linear pipeline:

    extract_graphql_question → classify_query → extract_entities
    → resolve_ids → build_and_execute_graphql → format_graphql_results

Each node reads from ``AtlasAgentState`` and returns a partial dict update.
Nodes that need external dependencies (LLM, caches, HTTP client) receive
them via ``functools.partial`` binding at graph-construction time.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Literal, Optional

from langchain_core.messages import ToolMessage
from pydantic import BaseModel, Field, field_validator

from src.atlas_links import generate_atlas_links
from src.cache import CatalogCache
from src.graphql_client import AtlasGraphQLClient, BudgetExhaustedError, GraphQLError
from src.prompts import (
    build_classification_prompt,
    build_extraction_prompt,
    build_id_resolution_prompt,
)
from src.state import AtlasAgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

GRAPHQL_PIPELINE_NODES = frozenset(
    {
        "extract_graphql_question",
        "classify_query",
        "extract_entities",
        "resolve_ids",
        "build_and_execute_graphql",
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
    "graphql_atlas_links": [],
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
    "                            composition, export diversification (countryProfile API).\n"
    "                            Use when the user asks about what a country exports, its\n"
    "                            export basket, or export composition.\n"
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
    "- growth_opportunities : Growth opportunity metrics including COG, distance, RCA\n"
    "                         for products in a country's product space (productSpace API).\n"
    "- product_table : Tabular product-level data for a country — export values, RCA,\n"
    "                  complexity metrics (countryProductYear API).\n"
    "- country_year : Country aggregate data by year — GDP, ECI, total trade values (countryYear API).\n"
    "- product_info : Global product-level data — trade value, PCI, number of exporters (productYear API).\n"
    "- bilateral_aggregate : Total/aggregate bilateral trade value between two countries —\n"
    "                        NOT product-level breakdown. Use when the question asks for the\n"
    "                        total export or import value between two specific countries\n"
    "                        (countryCountryYear API).\n"
    "- explore_bilateral : Bilateral trade data between two countries (countryCountryProductYear API).\n"
    "- explore_group : Regional or group-level trade data — continents, income groups,\n"
    "                  trade blocs (groupYear, groupGroupProductYear APIs).\n"
    "- global_datum : Global-level questions not tied to a specific country.\n"
    "- explore_data_availability : Questions about data coverage — which years, products,\n"
    "                              or countries have data available (dataAvailability API).\n"
    "\n"
    "Routing guidance:\n"
    "- For time-series questions ('how has X changed since Y'), prefer overtime_* or marketshare.\n"
    "- For growth opportunity / diversification questions, prefer feasibility, feasibility_table, or growth_opportunities.\n"
    "- For 'what does country X export' snapshot questions, prefer treemap_products or product_table.\n"
    "- For country overview / profile questions, prefer country_profile.\n"
    "- For questions specifically about a country's export basket or export composition,\n"
    "  prefer country_profile_exports.\n"
    "- For questions about economic complexity, ECI, COI, or complexity rankings,\n"
    "  prefer country_profile_complexity.\n"
    "- For questions about services trade, use servicesClass: unilateral in the Explore API."
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
    "                  newProductsCountry, growth_opportunities (productSpace), peer comparisons, and\n"
    "                  policy recommendations. Used by country_profile, country_profile_exports,\n"
    "                  country_profile_complexity, country_lookback, new_products, and\n"
    "                  growth_opportunities query types."
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
    "- HS92 : Harmonized System 1992 revision (default). Data available 1995-2024. Most commonly used.\n"
    "- HS12 : Harmonized System 2012 revision. Data available 2012-2024.\n"
    "- HS22 : Harmonized System 2022 revision. Data available 2022-2024. Only available in the Explore API\n"
    "         (not Country Pages or SQL pipeline).\n"
    "- SITC : Standard International Trade Classification. Data available 1962-2024. Use for long historical time series."
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
        "global_datum",
        "explore_data_availability",
        "reject",
    ] = Field(description=QUERY_TYPE_DESCRIPTION)
    rejection_reason: Optional[str] = Field(
        default=None,
        description="Why the query was rejected. Only set when query_type is 'reject'.",
    )
    api_target: Literal["explore", "country_pages"] | None = Field(
        default=None,
        description=API_TARGET_DESCRIPTION,
    )

    @field_validator("reasoning", mode="before")
    @classmethod
    def truncate_reasoning(cls, v: str) -> str:
        """Truncate reasoning to 300 chars to avoid LLM over-generation failures."""
        if isinstance(v, str) and len(v) > 300:
            return v[:297] + "..."
        return v


class GraphQLEntityExtraction(BaseModel):
    """Entities extracted from a user question for GraphQL query construction."""

    reasoning: str = Field(
        description="Step-by-step reasoning for entity extraction decisions (max 300 chars).",
    )
    country_name: Optional[str] = Field(
        default=None, description="Primary country mentioned in the question."
    )
    country_code_guess: Optional[str] = Field(
        default=None, description="ISO 3166-1 alpha-3 code guess (e.g., 'KEN')."
    )
    partner_name: Optional[str] = Field(
        default=None, description="Partner/destination country for bilateral queries."
    )
    partner_code_guess: Optional[str] = Field(
        default=None, description="ISO3 code guess for the partner country."
    )
    product_name: Optional[str] = Field(
        default=None, description="Product or commodity mentioned."
    )
    product_code_guess: Optional[str] = Field(
        default=None, description="HS code guess (e.g., '0901' for coffee)."
    )
    product_level: Optional[Literal["section", "twoDigit", "fourDigit", "sixDigit"]] = (
        Field(
            default="fourDigit",
            description=PRODUCT_LEVEL_DESCRIPTION,
        )
    )
    product_class: Optional[Literal["HS92", "HS12", "HS22", "SITC"]] = Field(
        default=None,
        description=PRODUCT_CLASS_DESCRIPTION,
    )
    year: Optional[int] = Field(
        default=None, description="Specific year mentioned (e.g., 2024)."
    )
    year_min: Optional[int] = Field(
        default=None, description="Start of time range for overtime queries."
    )
    year_max: Optional[int] = Field(
        default=None, description="End of time range for overtime queries."
    )
    group_name: Optional[str] = Field(
        default=None, description="Country group name (e.g., 'ASEAN', 'EU')."
    )
    group_type: Optional[str] = Field(
        default=None,
        description=GROUP_TYPE_DESCRIPTION,
    )
    lookback_years: Literal[3, 5, 10, 15] | None = Field(
        default=None,
        description="Lookback period in years for Country Pages growth dynamics (country_lookback query type).",
    )

    @field_validator("reasoning", mode="before")
    @classmethod
    def truncate_reasoning(cls, v: str) -> str:
        """Truncate reasoning to 300 chars to avoid LLM over-generation failures."""
        if isinstance(v, str) and len(v) > 300:
            return v[:297] + "..."
        return v


# ---------------------------------------------------------------------------
# Node 1: extract_graphql_question
# ---------------------------------------------------------------------------


async def extract_graphql_question(state: AtlasAgentState) -> dict:
    """Extract the question from the agent's GraphQL tool_call args.

    Resets all graphql_* state fields to prevent cross-turn leakage.
    """
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
    question = state["graphql_question"]
    context = state.get("graphql_context", "")

    # No try/except — errors propagate so LangGraph RetryPolicy can retry
    # transient failures (rate limits, timeouts). After max retries, the
    # exception reaches the streaming layer which returns a user-friendly error.
    # method="function_calling" avoids OpenAI's Structured Output API
    # (ParsedChatCompletion), which triggers spurious Pydantic serialization
    # warnings due to unresolved TypeVar on ParsedChatCompletionMessage.parsed.
    chain = lightweight_model.with_structured_output(
        GraphQLQueryClassification, method="function_calling"
    )
    prompt = build_classification_prompt(question, context)
    classification: GraphQLQueryClassification = await chain.ainvoke(prompt)

    return {
        "graphql_classification": classification.model_dump(),
        "graphql_api_target": classification.api_target,
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
    classification = state.get("graphql_classification")
    if classification and classification.get("query_type") == "reject":
        return {"graphql_entity_extraction": None}

    question = state["graphql_question"]
    context = state.get("graphql_context", "")
    query_type = classification.get("query_type", "") if classification else ""

    # No try/except — errors propagate so LangGraph RetryPolicy can retry
    # transient failures. After max retries, the exception reaches the
    # streaming layer which returns a user-friendly error.
    # method="function_calling" avoids OpenAI ParsedChatCompletion warnings.
    chain = lightweight_model.with_structured_output(
        GraphQLEntityExtraction, method="function_calling"
    )
    prompt = build_extraction_prompt(question, query_type, context)
    extraction: GraphQLEntityExtraction = await chain.ainvoke(prompt)
    return {"graphql_entity_extraction": extraction.model_dump()}


# ---------------------------------------------------------------------------
# Node 4: resolve_ids
# ---------------------------------------------------------------------------


async def resolve_ids(
    state: AtlasAgentState,
    *,
    lightweight_model: Any,
    country_cache: CatalogCache,
    product_cache: CatalogCache,
    services_cache: CatalogCache,
) -> dict:
    """Resolve extracted entity names/codes to Atlas internal IDs.

    Uses CatalogCache lookups (code → ID, name → ID) with LLM fallback
    for ambiguous matches.

    Args:
        state: Current agent state with extraction populated.
        lightweight_model: LangChain chat model for disambiguation.
        country_cache: Country catalog cache.
        product_cache: Product catalog cache.
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

    api_target = classification.get("api_target", "explore")
    query_type = classification.get("query_type", "")
    question = state["graphql_question"]

    resolved: dict[str, Any] = {}
    resolution_notes: list[str] = []

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
        )
        if partner:
            resolved["partner_id"] = partner["countryId"]
            resolved["partner_name"] = partner.get("nameShortEn", partner_name)

    # Resolve product
    product_name = extraction.get("product_name")
    product_code = extraction.get("product_code_guess")
    if product_name or product_code:
        product = await _resolve_entity(
            name=product_name,
            code_guess=product_code,
            cache=product_cache,
            index_name="code",
            search_field="nameShortEn",
            llm=lightweight_model,
            question=question,
        )
        if product:
            resolved["product_id"] = product["productId"]
            resolved["product_name"] = product.get("nameShortEn", product_name)

    # If product not found in product_cache, try services_cache
    if "product_id" not in resolved and (product_name or product_code):
        service_entry = await _resolve_entity(
            name=product_name,
            code_guess=product_code,
            cache=services_cache,
            index_name="name",
            search_field="nameShortEn",
            llm=lightweight_model,
            question=question,
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
    ):
        val = extraction.get(field_name)
        if val is not None:
            resolved[field_name] = val

    # Generate atlas links BEFORE formatting IDs (links expect canonical form)
    atlas_links: list[dict] = []
    try:
        links = generate_atlas_links(query_type, resolved)
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

    return {
        "graphql_resolved_params": resolved,
        "graphql_atlas_links": atlas_links,
    }


async def _resolve_entity(
    *,
    name: str | None,
    code_guess: str | None,
    cache: CatalogCache,
    index_name: str,
    search_field: str,
    llm: Any,
    question: str,
) -> dict[str, Any] | None:
    """Resolve an entity name/code to a catalog entry.

    Strategy:
    1. Step A: Try exact code lookup via the named index
    2. Step B: Search by name (case-insensitive substring)
    3. Step C: LLM disambiguation when multiple candidates exist
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
            f"{i+1}. {c.get(search_field, c.get('nameShortEn', 'unknown'))} "
            f"(code: {c.get('code', c.get('iso3Code', 'N/A'))})"
            for i, c in enumerate(candidates)
        )
        prompt = build_id_resolution_prompt(
            question=question,
            options=options,
            num_candidates=len(candidates),
        )
        response = await llm.ainvoke(prompt)
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
            result["product"] = f"product-HS-{product_id}"
        if "partner_id" in result:
            partner_id = _strip_id_prefix(result.pop("partner_id"))
            result["partner"] = f"location-{partner_id}"
    else:
        # Explore API: ensure IDs are bare integers (strip any prefixes
        # that the catalog may have stored)
        for key in ("country_id", "product_id", "partner_id"):
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
        }

    query_type = classification["query_type"]
    api_target = (
        classification.get("api_target") or state.get("graphql_api_target") or "explore"
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
        }

    start = time.monotonic()
    try:
        data = await client.execute(query_string, variables)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "graphql_raw_response": data,
            "graphql_query": query_string,
            "graphql_execution_time_ms": elapsed_ms,
            "last_error": "",
        }
    except BudgetExhaustedError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.warning("GraphQL budget exhausted: %s", e)
        return {
            "graphql_raw_response": {"error": "budget_exhausted", "detail": str(e)},
            "graphql_query": query_string,
            "graphql_execution_time_ms": elapsed_ms,
            "last_error": f"GraphQL API budget exhausted: {e}",
        }
    except GraphQLError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error("GraphQL error: %s", e)
        return {
            "graphql_raw_response": {"error": "graphql_error", "detail": str(e)},
            "graphql_query": query_string,
            "graphql_execution_time_ms": elapsed_ms,
            "last_error": f"GraphQL query failed: {e}",
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error("Unexpected error executing GraphQL query: %s", e)
        return {
            "graphql_raw_response": {"error": "unexpected_error", "detail": str(e)},
            "graphql_query": query_string,
            "graphql_execution_time_ms": elapsed_ms,
            "last_error": f"Unexpected error: {e}",
        }


# ---------------------------------------------------------------------------
# Node 6: format_graphql_results
# ---------------------------------------------------------------------------


async def format_graphql_results(
    state: AtlasAgentState,
    *,
    product_cache: CatalogCache | None = None,
    country_cache: CatalogCache | None = None,
) -> dict:
    """Create a ToolMessage from the GraphQL pipeline results.

    Handles three cases:
    - Rejection: returns a ToolMessage explaining why the query was rejected.
    - Error: returns an error ToolMessage and discards atlas links.
    - Success: post-processes and formats the response data into a ToolMessage.

    Also handles parallel tool_calls by creating stub messages for extras.

    Args:
        state: Current agent state with raw GraphQL response.
        product_cache: Optional product catalog cache for name enrichment.
        country_cache: Optional country catalog cache for name enrichment.
    """
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
            f"Rejection reason: {reason}"
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
        # Success — post-process then serialize
        import json

        processed = post_process_response(
            query_type,
            raw_response,
            product_cache=product_cache,
            country_cache=country_cache,
        )
        content = json.dumps(processed, indent=2, default=str)
        atlas_links = state.get("graphql_atlas_links", [])

    messages: list[ToolMessage] = [
        ToolMessage(content=content, tool_call_id=tool_calls[0]["id"])
    ]
    for tc in tool_calls[1:]:
        messages.append(
            ToolMessage(
                content="Only one query can be executed at a time. Please make additional queries sequentially.",
                tool_call_id=tc["id"],
            )
        )

    return {
        "messages": messages,
        "queries_executed": state.get("queries_executed", 0) + 1,
        "graphql_atlas_links": atlas_links,
    }


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
        "sort": "cog",
        "top_n": 20,
        "enrich": "product",
        "filter": "rca_lt_1",
    },
    "feasibility_table": {
        "root": "countryProductYear",
        "sort": "cog",
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
        "root": "productSpace",
        "sort": "cog",
        "top_n": 20,
        "enrich": "none",
    },
}

_FILTERS: dict[str, Callable] = {
    "rca_lt_1": lambda item: (item.get("exportRca") or 0) < 1,
}


def post_process_response(
    query_type: str,
    raw_response: dict,
    *,
    product_cache: CatalogCache | None = None,
    country_cache: CatalogCache | None = None,
) -> dict:
    """Sort, truncate, and enrich large GraphQL responses before sending to the LLM.

    Args:
        query_type: Classified query type (e.g., "treemap_products").
        raw_response: Raw GraphQL API response dict.
        product_cache: Optional product catalog cache for name enrichment.
        country_cache: Optional country catalog cache for name enrichment.

    Returns:
        Post-processed response dict, or raw_response if no rules apply.
    """
    rules = _POST_PROCESS_RULES.get(query_type)
    if rules is None:
        return raw_response

    root_key = rules["root"]
    items = raw_response.get(root_key)
    if not isinstance(items, list) or len(items) <= rules["top_n"]:
        return raw_response

    total_items = len(items)

    # Apply filter if specified
    filter_name = rules.get("filter")
    if filter_name and filter_name in _FILTERS:
        items = [item for item in items if _FILTERS[filter_name](item)]

    # Sort by sort field descending (nulls last)
    sort_field = rules["sort"]
    items.sort(
        key=lambda x: (x.get(sort_field) is not None, x.get(sort_field) or 0),
        reverse=True,
    )

    # Truncate
    top_n = rules["top_n"]
    items = items[:top_n]

    # Enrich with human-readable names
    enrich_type = rules.get("enrich", "none")
    if enrich_type == "product" and product_cache is not None:
        if not product_cache.is_populated:
            logger.warning(
                "Product cache not populated — skipping enrichment for %s",
                query_type,
            )
        else:
            for item in items:
                pid = item.get("productId")
                if pid is not None:
                    entry = product_cache.lookup_sync("id", str(pid))
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

    return {
        root_key: items,
        "_postProcessed": {
            "totalItems": total_items,
            "shownItems": len(items),
            "sortField": sort_field,
        },
    }


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
        "productClass": params.get("product_class", "HS92"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

    if "product_id" in params:
        variables["productId"] = params["product_id"]

    query = """
    query CPY($countryId: Int, $productLevel: Int!, $productClass: ProductClass,
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


def _build_country_country_year(params: dict) -> tuple[str, dict]:
    """Build countryCountryYear query (Explore API).

    Supports optional ``partner_id`` for bilateral aggregate filtering.
    When absent, returns all partner rows (treemap_partners / overtime_partners).
    """
    variables: dict[str, Any] = {"countryId": params.get("country_id")}
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

    if "partner_id" in params:
        variables["partnerCountryId"] = params["partner_id"]

    query = """
    query CCY($countryId: Int, $partnerCountryId: Int, $yearMin: Int, $yearMax: Int) {
      countryCountryYear(
        countryId: $countryId
        partnerCountryId: $partnerCountryId
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        countryId partnerCountryId year
        exportValue importValue
        exportValueReported importValueReported
      }
    }
    """
    return query, variables


def _build_country_country_product_year(params: dict) -> tuple[str, dict]:
    """Build countryCountryProductYear query (Explore API)."""
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "partnerCountryId": params.get("partner_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS92"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

    query = """
    query CCPY($countryId: Int, $partnerCountryId: Int,
               $productLevel: Int!, $productClass: ProductClass,
               $yearMin: Int, $yearMax: Int) {
      countryCountryProductYear(
        countryId: $countryId
        partnerCountryId: $partnerCountryId
        productLevel: $productLevel
        productClass: $productClass
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        countryId partnerCountryId productId productLevel year
        exportValue importValue
      }
    }
    """
    return query, variables


def _build_country_year(params: dict) -> tuple[str, dict]:
    """Build countryYear query (Explore API)."""
    variables: dict[str, Any] = {"countryId": params.get("country_id")}
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

    query = """
    query CY($countryId: Int, $yearMin: Int, $yearMax: Int) {
      countryYear(
        countryId: $countryId
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        countryId year
        exportValue importValue
        population gdp gdppc gdpPpp gdppcPpp
        gdpConst gdpPppConst gdppcConst gdppcPppConst
        eci eciFixed coi
        currentAccount growthProj
      }
    }
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
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

    query = """
    query PY($productId: Int, $productLevel: Int!, $yearMin: Int, $yearMax: Int) {
      productYear(
        productId: $productId
        productLevel: $productLevel
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        productId productLevel year
        exportValue importValue
        exportValueConstGrowth5 importValueConstGrowth5
        exportValueConstCagr5 importValueConstCagr5
        pci complexityEnum
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
    """Build groupYear query (Explore API)."""
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
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

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


# --- Country Pages API query builders ---

_LOOKBACK_YEAR_MAP = {
    3: "ThreeYears",
    5: "FiveYears",
    10: "TenYears",
    15: "FifteenYears",
}


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
        diversificationGrade diversityRank diversity
        currentAccount { quantity year }
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

    query = """
    query CL($id: ID!, $yearRange: LookBackYearRange) {
      countryLookback(id: $id, yearRange: $yearRange) {
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
    """Build newProductsCountry query (Country Pages API)."""
    location = params.get("location", "")
    year = params.get("year", 2024)
    variables: dict[str, Any] = {"location": location, "year": year}
    query = """
    query NP($location: ID!, $year: Int!) {
      newProductsCountry(location: $location, year: $year) {
        location { id shortName }
        newProductExportValue
        newProductExportValuePerCapita
      }
    }
    """
    return query, variables


def _build_growth_opportunities(params: dict) -> tuple[str, dict]:
    """Build growth opportunities query (Country Pages productSpace API)."""
    location = params.get("location", "")
    product_class = params.get("product_class", "HS92")
    year = params.get("year")
    variables: dict[str, Any] = {"location": location, "productClass": product_class}
    if year:
        variables["year"] = int(year)
    query = """
    query GO($location: ID!, $productClass: ProductClass!, $year: Int) {
      productSpace(location: $location, productClass: $productClass, year: $year) {
        product { id shortName code }
        exportValue exportRca
        cog cogRank distance distanceRank
      }
    }
    """
    return query, variables


def _build_product_table(params: dict) -> tuple[str, dict]:
    """Build product table query (Explore API countryProductYear)."""
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS92"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

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
        "productClass": params.get("product_class", "HS92"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

    if "product_id" in params:
        variables["productId"] = params["product_id"]

    query = """
    query CPY($countryId: Int, $productLevel: Int!, $productClass: ProductClass,
              $productId: Int, $yearMin: Int, $yearMax: Int) {
      countryProductYear(
        countryId: $countryId
        productLevel: $productLevel
        productClass: $productClass
        productId: $productId
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        productId year exportValue
      }
    }
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
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

    query = """
    query CCY($countryId: Int, $yearMin: Int, $yearMax: Int) {
      countryCountryYear(
        countryId: $countryId
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        countryId partnerCountryId year
        exportValue importValue
      }
    }
    """
    return query, variables


def _build_treemap_ccpy(params: dict) -> tuple[str, dict]:
    """Slim builder for treemap_bilateral — only sort+display fields."""
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "partnerCountryId": params.get("partner_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS92"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

    query = """
    query CCPY($countryId: Int, $partnerCountryId: Int,
               $productLevel: Int!, $productClass: ProductClass,
               $yearMin: Int, $yearMax: Int) {
      countryCountryProductYear(
        countryId: $countryId
        partnerCountryId: $partnerCountryId
        productLevel: $productLevel
        productClass: $productClass
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        productId year exportValue
      }
    }
    """
    return query, variables


def _build_feasibility_cpy(params: dict) -> tuple[str, dict]:
    """Slim builder for feasibility — RCA + complexity fields only."""
    variables: dict[str, Any] = {
        "countryId": params.get("country_id"),
        "productLevel": _product_level_to_int(params.get("product_level", "fourDigit")),
        "productClass": params.get("product_class", "HS92"),
    }
    year = params.get("year")
    if year:
        variables["yearMin"] = year
        variables["yearMax"] = year
    else:
        variables["yearMin"] = params.get("year_min", 2024)
        variables["yearMax"] = params.get("year_max", 2024)

    if "product_id" in params:
        variables["productId"] = params["product_id"]

    query = """
    query CPY($countryId: Int, $productLevel: Int!, $productClass: ProductClass,
              $productId: Int, $yearMin: Int, $yearMax: Int) {
      countryProductYear(
        countryId: $countryId
        productLevel: $productLevel
        productClass: $productClass
        productId: $productId
        yearMin: $yearMin
        yearMax: $yearMax
      ) {
        productId year exportValue exportRca cog distance
      }
    }
    """
    return query, variables


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
    "explore_data_availability": _build_data_availability,
    "product_table": _build_product_table,
    # Country Pages API queries
    "country_profile": _build_country_profile,
    "country_profile_exports": _build_country_profile,
    "country_profile_complexity": _build_country_profile,
    "country_lookback": _build_country_lookback,
    "new_products": _build_new_products,
    "global_datum": _build_global_datum,
    "growth_opportunities": _build_growth_opportunities,
}
