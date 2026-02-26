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
from pydantic import BaseModel, Field

from src.atlas_links import generate_atlas_links
from src.cache import CatalogCache
from src.graphql_client import AtlasGraphQLClient, BudgetExhaustedError, GraphQLError
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
# ---------------------------------------------------------------------------

QUERY_TYPE_DESCRIPTION = (
    "The type of Atlas visualization query this question maps to.\n"
    "- country_profile : Country overview page with GDP, ECI, growth projections\n"
    "- country_lookback : Historical growth dynamics and structural change\n"
    "- new_products : Newly exported products for a country\n"
    "- treemap_products : Export/import basket by product (treemap)\n"
    "- treemap_partners : Trade partners breakdown (treemap)\n"
    "- treemap_bilateral : Bilateral trade by product between two countries\n"
    "- overtime_products : Trade over time by product\n"
    "- overtime_partners : Trade over time by partner\n"
    "- marketshare : Global market share over time\n"
    "- product_space : Product space network visualization\n"
    "- feasibility : Growth opportunity scatter plot\n"
    "- feasibility_table : Growth opportunity table\n"
    "- country_year : Country-level aggregate data (GDP, ECI, trade totals)\n"
    "- product_info : Global product-level data (PCI, trade values)\n"
    "- explore_bilateral : Bilateral trade detailed breakdown\n"
    "- explore_group : Regional/group trade data\n"
    "- global_datum : Global-level aggregate data\n"
    "- explore_data_availability : Year coverage and data availability\n"
    "- reject : Question cannot be answered by the Atlas GraphQL API"
)

API_TARGET_DESCRIPTION = (
    "Which Atlas API endpoint to query.\n"
    "- explore : Explore API (/api/graphql) — raw trade data, bilateral, product space\n"
    "- country_pages : Country Pages API (/api/countries/graphql) — profiles, lookback, derived analytics\n"
    "- null/None : When query_type is 'reject'"
)

PRODUCT_LEVEL_DESCRIPTION = (
    "Product aggregation level.\n"
    "- section : Top-level HS section\n"
    "- twoDigit : 2-digit HS chapter\n"
    "- fourDigit : 4-digit HS heading (most common)\n"
    "- sixDigit : 6-digit HS subheading (most detailed)"
)

PRODUCT_CLASS_DESCRIPTION = (
    "Product classification system.\n"
    "- HS92 : Harmonized System 1992 revision (default, broadest coverage 1995-2024)\n"
    "- HS12 : Harmonized System 2012 revision (2012-2024)\n"
    "- HS22 : Harmonized System 2022 revision (2022-2024)\n"
    "- SITC : Standard International Trade Classification (1962-2024)"
)

GROUP_TYPE_DESCRIPTION = (
    "Type of country grouping.\n"
    "- continent : Continental grouping\n"
    "- region : Geographic region\n"
    "- subregion : Geographic subregion\n"
    "- trade : Trade agreement bloc\n"
    "- political : Political grouping\n"
    "- wdi_income_level : World Bank income classification\n"
    "- wdi_region : World Bank region\n"
    "- world : Entire world"
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class GraphQLQueryClassification(BaseModel):
    """Classification of a user question for the Atlas GraphQL API."""

    reasoning: str = Field(
        description="Step-by-step reasoning for the classification decision."
    )
    query_type: Literal[
        "country_profile",
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
        "country_year",
        "product_info",
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
    api_target: Optional[str] = Field(
        default=None,
        description=API_TARGET_DESCRIPTION,
    )


class GraphQLEntityExtraction(BaseModel):
    """Entities extracted from a user question for GraphQL query construction."""

    reasoning: str = Field(
        description="Step-by-step reasoning for entity extraction decisions."
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
            default=None,
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

    # Reset all graphql state fields + set the new question
    result = dict(_GRAPHQL_STATE_DEFAULTS)
    result["graphql_question"] = question
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

    try:
        chain = lightweight_model.with_structured_output(
            GraphQLQueryClassification, include_raw=True
        )
        prompt = _build_classification_prompt(question)
        result = await chain.ainvoke(prompt)
        classification: GraphQLQueryClassification = result["parsed"]

        return {
            "graphql_classification": classification.model_dump(),
            "graphql_api_target": classification.api_target,
        }
    except Exception as e:
        logger.error("Classification failed: %s", e)
        fallback = GraphQLQueryClassification(
            reasoning=f"Classification failed due to error: {e}",
            query_type="reject",
            rejection_reason=str(e),
            api_target=None,
        )
        return {
            "graphql_classification": fallback.model_dump(),
            "graphql_api_target": None,
        }


def _build_classification_prompt(question: str) -> str:
    """Build the classification prompt for the LLM.

    [PLACEHOLDER PROMPT — REQUIRES USER VETTING]
    """
    return (
        "You are classifying a user question about international trade and economic data "
        "to determine which Atlas GraphQL API query type can best answer it.\n\n"
        f"Question: {question}\n\n"
        "Classify the question into one of the supported query types. "
        "If the question cannot be answered by the Atlas API, use 'reject'."
    )


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
    query_type = classification.get("query_type", "") if classification else ""

    try:
        chain = lightweight_model.with_structured_output(
            GraphQLEntityExtraction, include_raw=True
        )
        prompt = _build_extraction_prompt(question, query_type)
        result = await chain.ainvoke(prompt)
        extraction: GraphQLEntityExtraction = result["parsed"]
        return {"graphql_entity_extraction": extraction.model_dump()}
    except Exception as e:
        logger.error("Entity extraction failed: %s", e)
        return {"graphql_entity_extraction": None}


def _build_extraction_prompt(question: str, query_type: str) -> str:
    """Build the entity extraction prompt for the LLM.

    [PLACEHOLDER PROMPT — REQUIRES USER VETTING]
    """
    return (
        "Extract entities from this international trade question.\n\n"
        f"Question: {question}\n"
        f"Query type: {query_type}\n\n"
        "Extract countries (with ISO3 code guesses), products (with HS code guesses), "
        "years or year ranges, and any other relevant entities."
    )


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

    # Pass through scalar fields
    for field_name in (
        "year",
        "year_min",
        "year_max",
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
    1. Try exact code lookup via the named index
    2. Fall back to name search (case-insensitive substring)
    3. Return the first match (or None if nothing found)
    """
    # Step A: Try code guess lookup
    if code_guess:
        entry = await cache.lookup(index_name, code_guess)
        if entry:
            return entry

    # Step B: Try name search
    if name:
        results = await cache.search(search_field, name, limit=5)
        if results:
            # If exactly one match, use it directly
            if len(results) == 1:
                return results[0]
            # Try exact name match first
            name_lower = name.strip().lower()
            for r in results:
                if (r.get(search_field) or "").strip().lower() == name_lower:
                    return r
            # Fall back to first result
            return results[0]

    return None


# ---------------------------------------------------------------------------
# ID formatting helpers
# ---------------------------------------------------------------------------


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
        # Transform integer IDs to prefixed strings
        if "country_id" in result:
            country_id = result.pop("country_id")
            result["location"] = f"location-{country_id}"
        if "product_id" in result:
            product_id = result.pop("product_id")
            result["product"] = f"product-HS-{product_id}"
        if "partner_id" in result:
            partner_id = result.pop("partner_id")
            result["partner"] = f"location-{partner_id}"

    return result


# ---------------------------------------------------------------------------
# Node 5: build_and_execute_graphql
# ---------------------------------------------------------------------------


async def build_and_execute_graphql(
    state: AtlasAgentState,
    *,
    graphql_client: AtlasGraphQLClient,
) -> dict:
    """Build a GraphQL query and execute it against the Atlas API.

    Never raises — catches all exceptions and records errors in state.

    Args:
        state: Current agent state with resolved params.
        graphql_client: HTTP client for the Atlas GraphQL API.

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

    try:
        query_string, variables = build_graphql_query(query_type, resolved_params)
    except (ValueError, KeyError) as e:
        logger.error("Failed to build GraphQL query: %s", e)
        return {
            "graphql_raw_response": None,
            "graphql_query": None,
            "graphql_execution_time_ms": 0,
            "last_error": f"Failed to build query: {e}",
        }

    start = time.monotonic()
    try:
        data = await graphql_client.execute(query_string, variables)
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
            "graphql_raw_response": None,
            "graphql_query": query_string,
            "graphql_execution_time_ms": elapsed_ms,
            "last_error": f"GraphQL API budget exhausted: {e}",
        }
    except GraphQLError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error("GraphQL error: %s", e)
        return {
            "graphql_raw_response": None,
            "graphql_query": query_string,
            "graphql_execution_time_ms": elapsed_ms,
            "last_error": f"GraphQL query failed: {e}",
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error("Unexpected error executing GraphQL query: %s", e)
        return {
            "graphql_raw_response": None,
            "graphql_query": query_string,
            "graphql_execution_time_ms": elapsed_ms,
            "last_error": f"Unexpected error: {e}",
        }


# ---------------------------------------------------------------------------
# Node 6: format_graphql_results
# ---------------------------------------------------------------------------


async def format_graphql_results(state: AtlasAgentState) -> dict:
    """Create a ToolMessage from the GraphQL pipeline results.

    Handles three cases:
    - Rejection: returns a ToolMessage explaining why the query was rejected.
    - Error: returns an error ToolMessage and discards atlas links.
    - Success: formats the response data into a ToolMessage.

    Also handles parallel tool_calls by creating stub messages for extras.
    """
    last_msg = state["messages"][-1]
    tool_calls = last_msg.tool_calls
    classification = state.get("graphql_classification") or {}
    query_type = classification.get("query_type", "")
    raw_response = state.get("graphql_raw_response")
    last_error = state.get("last_error", "")

    # Determine content and atlas links
    atlas_links: list[dict] = []

    if query_type == "reject":
        reason = classification.get("rejection_reason", "Question not supported")
        content = (
            f"This question could not be answered via the Atlas GraphQL API. "
            f"Rejection reason: {reason}"
        )
    elif last_error or raw_response is None:
        content = (
            f"Error executing GraphQL query: {last_error or 'No response received'}"
        )
        # Discard links on failure
    else:
        # Success
        import json

        content = json.dumps(raw_response, indent=2, default=str)
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
    """Build countryCountryYear query (Explore API)."""
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
        population gdp gdppc
      }
    }
    """
    return query, variables


# --- Country Pages API query builders ---


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

    query = """
    query CL($id: ID!) {
      countryLookback(id: $id) {
        location { id shortName }
        exportValue importValue
        eci coi
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
# Query type → builder dispatch table
# ---------------------------------------------------------------------------

_QUERY_BUILDERS: dict[str, Callable[[dict], tuple[str, dict]]] = {
    # Explore API queries
    "treemap_products": _build_country_product_year,
    "treemap_partners": _build_country_country_year,
    "treemap_bilateral": _build_country_country_product_year,
    "overtime_products": _build_country_product_year,
    "overtime_partners": _build_country_country_year,
    "marketshare": _build_marketshare,
    "product_space": _build_product_space,
    "feasibility": _build_country_product_year,
    "feasibility_table": _build_country_product_year,
    "country_year": _build_country_year,
    "product_info": _build_product_year,
    "explore_bilateral": _build_country_country_product_year,
    "explore_group": _build_group_year,
    "explore_data_availability": _build_data_availability,
    # Country Pages API queries
    "country_profile": _build_country_profile,
    "country_lookback": _build_country_lookback,
    "new_products": _build_new_products,
    "global_datum": _build_global_datum,
}
