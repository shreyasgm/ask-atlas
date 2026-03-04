"""GraphQL classification, entity extraction, and ID resolution prompts.

Contains the prompts and builder functions used by the GraphQL pipeline
to classify questions, extract entities, plan queries, and resolve IDs.

Design rule: **zero imports from other ``src/`` modules**.
"""

# =========================================================================
# 4. GraphQL Pipeline Prompts
# =========================================================================

# --- GRAPHQL_CLASSIFICATION_PROMPT ---
# System prompt for classifying a user question into a GraphQL query type.
# The Pydantic schema (QUERY_TYPE_DESCRIPTION) provides the type catalog —
# this prompt gives routing heuristics, examples, and rejection guidance.
# Pipeline: graphql_pipeline (classify_query)
# Placeholders: {question}, {context_block}

GRAPHQL_CLASSIFICATION_PROMPT = """\
You are classifying a user question about international trade and economic data to determine \
which Atlas GraphQL API query type can best answer it.

**Your task:** Given the question (and optional conversation context), select the single best \
query_type from the available options. The field descriptions in the output schema explain \
each query type in detail — read them carefully before classifying.

**Routing guide:**
1. Country overview, profile, economy summary, diversification grade, or growth projection \
-> country_profile (api_target: country_pages)
2. What products a country exports (export composition or breakdown) \
-> country_profile_exports (country_pages) or treemap_products (explore)
3. Who a country trades with, export destinations or trading partners \
-> country_profile_partners (country_pages) or treemap_partners (explore)
4. Economic complexity rankings (ECI, COI) or complexity metrics \
-> country_profile_complexity (api_target: country_pages)
5. How a country's exports changed over a lookback period, or export growth classification \
(promising, troubling, mixed, static) -> country_lookback (api_target: country_pages)
6. PAST diversification: questions about new products gained, products recently started \
exporting, or how the export basket changed historically -> new_products (api_target: country_pages)
7. FUTURE opportunities: growth opportunities, diversification potential, feasibility, \
or what products a country could start exporting -> feasibility (explore) or growth_opportunities (country_pages)
8. Time-series of a country's exports broken down by product over multiple years \
-> overtime_products (api_target: explore)
9. Time-series of a country's exports broken down by trading partner over multiple years \
-> overtime_partners (api_target: explore)
10. A specific product's global market share for a country -> marketshare (api_target: explore)
11. Product-level bilateral trade between two specific countries \
-> explore_bilateral (explore) or treemap_bilateral (explore)
12. Total or aggregate bilateral trade value between two countries \
-> bilateral_aggregate (api_target: explore)
13. Product space visualization or product relatedness -> product_space (api_target: explore)
14. Product-level exports FROM a country TO a group (e.g., Kenya's exports to the EU) \
-> group_products (api_target: explore)
15. Product-level exports FROM a group TO a country (e.g., EU exports to Kenya) \
-> group_bilateral (api_target: explore)
16. Which countries belong to a group or list group members -> group_membership (api_target: explore)
17. Regional or group-level aggregate data (Africa, EU, income groups) \
-> explore_group (api_target: explore)
18. Global-level aggregate trade data (world totals) -> global_datum (api_target: explore)
19. Global product statistics, top products worldwide, or world product rankings \
-> global_product (api_target: explore)
20. Information about a specific product (PCI, global ranking) -> product_info (api_target: explore)
21. Product-level tabular data with multiple metrics (RCA, PCI, export values) \
-> product_table (api_target: explore)
22. Data coverage questions (what years or countries are available) \
-> explore_data_availability (api_target: explore)
23. Country-year time series over a year range (ECI, GDP, exports OVER TIME) \
-> country_year (api_target: explore)
24. Country-year metrics for a single year with a specific classification (e.g., SITC) \
-> country_year (api_target: country_pages)
25. Diversification grade or growth projection relative to income group \
-> country_profile (api_target: country_pages)
26. Export growth classification (promising, troubling, static, mixed) \
-> country_lookback (api_target: country_pages)
27. Questions requiring custom SQL aggregation, complex multi-table joins, or calculations \
across many countries -> reject (fall back to SQL tool)

**Services note:** Services class routing (unilateral/bilateral) is handled at entity \
extraction, not here. Classify based on question type regardless of services.

**Growth opportunities caveat:** The Atlas does not display growth opportunity products for \
"Technological Frontier" economies (highest-complexity). Empty results likely indicate this.

**Examples:**

"What is Kenya's economic complexity ranking?" -> query_type: country_profile_complexity, api_target: country_pages
"What products did Brazil export to China in 2023?" -> query_type: treemap_bilateral, api_target: explore
"How has Germany's export basket changed since 2010?" -> query_type: overtime_products, api_target: explore
"What new products did Vietnam start exporting recently?" -> query_type: new_products, api_target: country_pages
"What are the top growth opportunities for Rwanda?" -> query_type: feasibility, api_target: explore
"What percentage of global coffee exports does Colombia account for?" -> query_type: marketshare, api_target: explore
"Calculate the average ECI across all OECD countries for the last 5 years" -> query_type: reject (cross-country aggregation)
"Which 10 countries have the highest RCA in semiconductors?" -> query_type: reject (ranking across all countries)
"Tell me about Kenya's economy and its main exports" -> query_type: country_profile, api_target: country_pages
"What are the most complex products Kenya could diversify into?" -> query_type: growth_opportunities, api_target: country_pages
"Show me product-level data for Thailand's exports — RCA, PCI, export values" -> query_type: product_table, api_target: explore
"What years of trade data are available for South Sudan?" -> query_type: explore_data_availability, api_target: explore
"What is the total global trade value in 2023?" -> query_type: global_datum, api_target: explore
"How have African countries' exports changed over time?" -> query_type: explore_group, api_target: explore
"Show me the product space for South Korea" -> query_type: product_space, api_target: explore
"What is the global PCI ranking for electronic integrated circuits?" -> query_type: product_info, api_target: explore
"What are Kenya's top services exports — tourism, transport, ICT?" -> query_type: treemap_products, api_target: explore
"What is Spain's ECI value? Use SITC classification." -> query_type: country_year, api_target: country_pages
"What does Kenya export to the EU?" -> query_type: group_products, api_target: explore
"What does the EU export to Kenya?" -> query_type: group_bilateral, api_target: explore
"What products does Brazil sell to ASEAN?" -> query_type: group_products, api_target: explore
"Which countries belong to the EU?" -> query_type: group_membership, api_target: explore
"What are the top 10 most exported products in the world?" -> query_type: global_product, api_target: explore
"What has been Brazil's ECI trend over the last 15 years?" -> query_type: country_year, api_target: explore
"How has Mexico's export diversification changed in the past decade?" -> query_type: new_products, api_target: country_pages
"What are the best growth opportunities for Mexico to diversify?" -> query_type: feasibility, api_target: explore

{context_block}

**Question:** {question}

Classify this question into one of the supported query types. If it cannot be answered \
by a single Atlas API call, use 'reject' with a clear reason."""

# --- GRAPHQL_ENTITY_EXTRACTION_PROMPT ---
# System prompt for extracting structured entities from a classified question.
# Pipeline: graphql_pipeline (extract_entities)
# Placeholders: {question}, {query_type}, {context_block},
#               {services_catalog_block}

GRAPHQL_ENTITY_EXTRACTION_PROMPT = """\
You are extracting structured entities from a user question about international trade data.
The question has already been classified as query type "{query_type}".

**Your task:** Extract countries, products, years, and other entities mentioned in the question.
For countries, provide your best-guess ISO 3166-1 alpha-3 code (e.g., KEN for Kenya).
For products, provide your best-guess HS code (e.g., 0901 for coffee) or service category name.

**Field relevance by query type:**
- country_profile / country_profile_exports / country_profile_partners / country_profile_complexity: country (required)
- country_lookback: country (required), lookback_years (default 5), product_class (if mentioned). \
Supports per-metric yearRange overrides. Match lookback to question: "past five years" -> 5, "past decade" -> 10.
- new_products: country (required). Returns goods-only product counts.
- treemap_products / overtime_products / product_space / feasibility*: country (required), year or year range
- treemap_partners / overtime_partners: country (required), year or year range
- treemap_bilateral / explore_bilateral: country AND partner_country (both required)
- marketshare: country (required), product (required), year range
- product_info: product (required), year
- explore_group: group_name and group_type (required)
- group_products: country (required), partner_group_name and partner_group_type (required)
- group_bilateral: group_name and group_type (required), partner country (required — use partner_name/partner_code_guess)
- group_membership: group_name (required), group_type (required)
- country_year: country (required), year or year_min/year_max, product_class (if non-default classification mentioned)
- global_product: product_class (optional, default HS92), product_level (optional), year (optional)
- global_datum: year or year range (if mentioned)

**Services class** (`services_class`):
- Set to "unilateral" when the question asks about total/all exports, top products, or \
overall trade without limiting to goods. This includes services in the response.
- Set to "bilateral" for bilateral services trade questions.
- Leave as null when the question says "goods" or names a specific goods product.

**Trade direction** (`trade_direction`):
- Set to "imports" for import questions (keywords: "imports", "imported", "buys from", \
"sources from", "import partners", "top imports").
- Set to "exports" when the question explicitly asks about exports.
- Leave as null when direction is not mentioned or ambiguous (defaults to exports).

**Year handling:**
- If no year is mentioned, leave year fields as null (the system defaults to latest available).
- For time-series query types (overtime_*, marketshare, country_lookback), extract \
year_min/year_max if a range is stated.
- "since 2010" -> year_min: 2010, year_max: null
- "between 2015 and 2020" -> year_min: 2015, year_max: 2020
- "in 2023" -> year: 2023

**Product classification** (`product_class`):
- Default to HS12 unless the user explicitly mentions a different classification.
- Leave product_class as null unless explicitly specified — null means the system default.
- Available classifications and their year coverage:
  - HS92 (HS 1992): trade data from 1995 onward
  - HS12 (HS 2012): trade data from 2012 onward
  - SITC (Rev. 2): trade data from 1962 onward — use for pre-1995 historical analysis
- Country Pages API only supports HS and SITC product classes.
- For questions about whether a product is a natural resource or green product, use HS92 \
— the naturalResource and greenProduct metadata fields are only available in HS92.
- If the user mentions a service (tourism, transport, ICT, etc.), the product_code_guess \
should be the service category name as it appears in the Atlas (e.g., "Travel & tourism").
{services_catalog_block}

**Examples:**

"What did Kenya export in 2024?" (query_type: treemap_products)
-> country_name: Kenya, country_code_guess: KEN, year: 2024

"How have Brazil's coffee exports changed since 2010?" (query_type: overtime_products)
-> country_name: Brazil, country_code_guess: BRA, product_name: coffee, product_code_guess: 0901, year_min: 2010

"What products does Japan export to the US?" (query_type: treemap_bilateral)
-> country_name: Japan, country_code_guess: JPN, partner_name: United States, partner_code_guess: USA

"What are Rwanda's growth opportunities?" (query_type: feasibility)
-> country_name: Rwanda, country_code_guess: RWA

"Colombia's share of global coffee exports over time?" (query_type: marketshare)
-> country_name: Colombia, country_code_guess: COL, product_name: coffee, product_code_guess: 0901

"Kenya's tourism service exports" (query_type: treemap_products)
-> country_name: Kenya, country_code_guess: KEN, product_name: tourism, product_code_guess: Travel & tourism

"EU trade data for 2023" (query_type: explore_group)
-> group_name: EU, group_type: trade, year: 2023

"How has Vietnam's economy changed in the last decade?" (query_type: country_lookback)
-> country_name: Vietnam, country_code_guess: VNM, lookback_years: 10

"What are Kenya's total exports?" (query_type: treemap_products)
-> country_name: Kenya, country_code_guess: KEN, services_class: unilateral

"What are Kenya's coffee exports?" (query_type: treemap_products)
-> country_name: Kenya, country_code_guess: KEN, product_name: coffee, product_code_guess: 0901, services_class: null

"What does Kenya export to the EU?" (query_type: group_products)
-> country_name: Kenya, country_code_guess: KEN, partner_group_name: EU, partner_group_type: trade

"What does the EU export to Kenya?" (query_type: group_bilateral)
-> group_name: EU, group_type: trade, partner_name: Kenya, partner_code_guess: KEN

"What is the top imported product for USA?" (query_type: treemap_products)
-> country_name: United States, country_code_guess: USA, trade_direction: imports

"Which countries are in the EU?" (query_type: group_membership)
-> group_name: EU, group_type: trade

{context_block}

**Question:** {question}

Extract all relevant entities from this question."""

# --- ID_RESOLUTION_SELECTION_PROMPT ---
# Used when multiple catalog candidates match an entity reference and the
# LLM must disambiguate.
# Pipeline: graphql_pipeline (resolve_ids -> _resolve_entity)
# Placeholders: {question}, {options}, {num_candidates}

ID_RESOLUTION_SELECTION_PROMPT = """\
You are resolving an entity reference from a trade data question to the correct entry \
in the Atlas database catalog.

**Question context:** "{question}"

**Candidate matches:**
{options}

Which candidate is the best match for the entity referenced in the question?
Consider the full question context to disambiguate (e.g., "Turkey" as a country vs. \
"turkey" as a poultry product).

Reply with just the number (1-{num_candidates}) of the best match, or 0 if none match."""


# =========================================================================
# Builder functions
# =========================================================================


def build_classification_prompt(question: str, context: str = "") -> str:
    """Assemble the GraphQL classification prompt.

    Args:
        question: The user's trade-related question.
        context: Optional conversation context.

    Returns:
        Formatted classification prompt string.
    """
    context_block = ""
    if context:
        context_block = f"**Context from conversation:**\n{context}\n"
    return GRAPHQL_CLASSIFICATION_PROMPT.format(
        question=question,
        context_block=context_block,
    )


def build_extraction_prompt(
    question: str,
    query_type: str,
    context: str = "",
    services_catalog: str = "",
) -> str:
    """Assemble the GraphQL entity extraction prompt.

    Args:
        question: The user's trade-related question.
        query_type: The classified query type string.
        context: Optional conversation context.
        services_catalog: Optional formatted services catalog for reference.

    Returns:
        Formatted entity extraction prompt string.
    """
    context_block = ""
    if context:
        context_block = f"**Context from conversation:**\n{context}\n"

    services_catalog_block = ""
    if services_catalog:
        services_catalog_block = (
            f"\n**Available service categories for reference:**\n{services_catalog}"
        )

    return GRAPHQL_ENTITY_EXTRACTION_PROMPT.format(
        question=question,
        query_type=query_type,
        context_block=context_block,
        services_catalog_block=services_catalog_block,
    )


def build_query_plan_prompt(
    question: str,
    context: str = "",
    services_catalog: str = "",
) -> str:
    """Assemble the combined classification + entity extraction prompt.

    Merges the classification and extraction prompts so that both steps
    can be performed in a single LLM call with the GraphQLQueryPlan schema.

    Args:
        question: The user's trade-related question.
        context: Optional conversation context.
        services_catalog: Optional formatted services catalog for reference.

    Returns:
        Formatted combined prompt string.
    """
    context_block = ""
    if context:
        context_block = f"**Context from conversation:**\n{context}\n"

    services_catalog_block = ""
    if services_catalog:
        services_catalog_block = (
            f"\n**Available service categories for reference:**\n{services_catalog}"
        )

    # Combine both prompts into a single instruction.
    # The classification prompt provides routing heuristics and examples;
    # the extraction prompt provides entity extraction guidance and examples.
    classification_part = GRAPHQL_CLASSIFICATION_PROMPT.format(
        question=question,
        context_block=context_block,
    )
    extraction_part = GRAPHQL_ENTITY_EXTRACTION_PROMPT.format(
        question=question,
        query_type="(determine from classification above)",
        context_block=context_block,
        services_catalog_block=services_catalog_block,
    )

    return (
        f"{classification_part}\n\n"
        "---\n\n"
        "In addition to classifying the query type, extract all relevant entities "
        "in the same response. The entity extraction guidance below tells you which "
        "fields to populate for each query type.\n\n"
        f"{extraction_part}"
    )


def build_id_resolution_prompt(
    question: str,
    options: str,
    num_candidates: int,
) -> str:
    """Assemble the ID resolution disambiguation prompt.

    Args:
        question: The original user question for context.
        options: Formatted numbered list of candidate entities.
        num_candidates: Total number of candidates.

    Returns:
        Formatted ID resolution prompt string.
    """
    return ID_RESOLUTION_SELECTION_PROMPT.format(
        question=question,
        options=options,
        num_candidates=num_candidates,
    )
