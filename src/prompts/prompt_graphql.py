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

**Decision flowchart:**
1. Is this about a specific country's profile, overview, or key metrics?
   -> country_profile or country_profile_exports or country_profile_partners or country_profile_complexity
2. Is this about how a country's trade changed over time?
   -> country_lookback (summary) or overtime_products / overtime_partners (time-series)
3. Is this about what products a country exports (composition)?
   -> treemap_products or country_profile_exports
4. Is this about who a country trades with (export destinations)?
   -> country_profile_partners or treemap_partners
5. Is this about trade between TWO specific countries?
   -> treemap_bilateral, explore_bilateral, or bilateral_aggregate
6. Is this about a product's global market share?
   -> marketshare
7. Is this about past export diversification or new products gained so far?
   -> new_products
8. Is this about future growth opportunities or diversification potential?
   -> feasibility, feasibility_table, or growth_opportunities
9. Is this about a region or country group (aggregate totals)?
   -> explore_group
10. Is this about what a country exports TO a group (e.g., Kenya's exports to the EU)?
    -> group_products
11. Is this about what a group exports TO a country (e.g., EU exports to Kenya)?
    -> group_bilateral
12. Is this asking which countries belong to a group?
    -> group_membership
13. Is this about global/worldwide product statistics without a specific country?
    -> global_product
14. Does this require custom aggregation, multi-country comparison, or complex SQL?
    -> reject (fall back to SQL tool)

**High-level routing heuristics:**
- Country overview / profile / economy summary -> country_profile
- What a country exports (breakdown/composition) -> country_profile_exports or treemap_products
- Who a country trades with / export destinations -> country_profile_partners or treemap_partners
- Economic complexity, ECI, COI rankings -> country_profile_complexity
- How exports changed over N years (growth dynamics) -> country_lookback
- New products gained RCA in -> new_products
- Time-series of exports by product -> overtime_products
- Time-series of exports by partner -> overtime_partners
- Market share of a product -> marketshare
- PAST diversification: "diversification changed", "new products gained", \
"recently started exporting", "export basket changed" -> new_products
- FUTURE opportunities: "growth opportunities", "diversification potential", \
"feasibility", "what could they export", "promising new products to target" -> feasibility or growth_opportunities
- Product space / relatedness -> product_space
- Product-level bilateral trade between two countries -> explore_bilateral or treemap_bilateral
- Total/aggregate bilateral trade value between two countries -> bilateral_aggregate
- Regional/group-level aggregate data (Africa, EU, income groups) -> explore_group
- Product-level exports FROM a country TO a group (e.g., Kenya → EU) -> group_products
- Product-level exports FROM a group TO a country (e.g., EU → Kenya) -> group_bilateral
- Members of a group, countries in a group, which countries belong -> group_membership
- Global-level aggregate data -> global_datum
- Data coverage questions (what years/countries available) -> explore_data_availability
- Diversification grade, growth projection relative to income -> country_profile
- Export growth classification (promising, troubling, static, mixed) -> country_lookback
- Country-year time series (ECI, GDP, exports OVER TIME with year range) -> country_year, api_target: explore
- Country-year ECI/COI with specific classification (SITC), single year -> country_year, api_target: country_pages
- Top products globally, world's most exported, global product rankings -> global_product
- If the question requires custom SQL aggregation, complex multi-table joins, calculations \
across many countries, or data not in the Atlas APIs -> reject

**Services note:** Services class routing (unilateral/bilateral) is handled at the entity \
extraction stage, not here. Classify based on the question type regardless of whether it \
involves services.

**Growth opportunities caveat:** The Atlas does not display growth opportunity products for \
countries classified under the "Technological Frontier" strategic approach (the highest-complexity \
economies). If the tool returns empty results, this is likely the reason.

**Examples:**

Example 1:
Question: "What is Kenya's economic complexity ranking?"
-> query_type: country_profile_complexity, api_target: country_pages

Example 2:
Question: "What products did Brazil export to China in 2023?"
-> query_type: treemap_bilateral, api_target: explore

Example 3:
Question: "How has Germany's export basket changed since 2010?"
-> query_type: overtime_products, api_target: explore

Example 4:
Question: "What new products did Vietnam start exporting recently?"
-> query_type: new_products, api_target: country_pages

Example 5:
Question: "What are the top growth opportunities for Rwanda?"
-> query_type: feasibility, api_target: explore

Example 6:
Question: "What percentage of global coffee exports does Colombia account for?"
-> query_type: marketshare, api_target: explore

Example 7:
Question: "Calculate the average ECI across all OECD countries for the last 5 years"
-> query_type: reject (requires cross-country aggregation not available via single API call)

Example 8:
Question: "Which 10 countries have the highest RCA in semiconductors?"
-> query_type: reject (requires ranking across all countries — custom SQL aggregation)

Example 9:
Question: "Tell me about Kenya's economy and its main exports"
-> query_type: country_profile, api_target: country_pages

Example 10:
Question: "What are the most complex products Kenya could diversify into?"
-> query_type: growth_opportunities, api_target: country_pages

Example 11:
Question: "Show me product-level data for Thailand's exports — RCA, PCI, export values"
-> query_type: product_table, api_target: explore

Example 12:
Question: "What years of trade data are available for South Sudan?"
-> query_type: explore_data_availability, api_target: explore

Example 13:
Question: "What is the total global trade value in 2023?"
-> query_type: global_datum, api_target: explore

Example 14:
Question: "How have African countries' exports changed over time?"
-> query_type: explore_group, api_target: explore (group_type: continent)

Example 15:
Question: "Show me the product space for South Korea"
-> query_type: product_space, api_target: explore

Example 16:
Question: "What is the global PCI ranking for electronic integrated circuits?"
-> query_type: product_info, api_target: explore

Example 17:
Question: "What are Kenya's top services exports — tourism, transport, ICT?"
-> query_type: treemap_products, api_target: explore

Example 18:
Question: "What is Spain's ECI value? Use SITC classification."
-> query_type: country_year, api_target: country_pages

Example 19:
Question: "What does Kenya export to the EU?"
-> query_type: group_products, api_target: explore

Example 20:
Question: "What does the EU export to Kenya?"
-> query_type: group_bilateral, api_target: explore

Example 21:
Question: "What products does Brazil sell to ASEAN?"
-> query_type: group_products, api_target: explore

Example 22:
Question: "Which countries belong to the EU?"
-> query_type: group_membership, api_target: explore

Example 23:
Question: "What are the top 10 most exported products in the world?"
-> query_type: global_product, api_target: explore

Example 24:
Question: "What has been Brazil's ECI trend over the last 15 years?"
-> query_type: country_year, api_target: explore

Example 25:
Question: "How has Mexico's export diversification changed in the past decade?"
-> query_type: new_products, api_target: country_pages

Example 26:
Question: "What are the best growth opportunities for Mexico to diversify?"
-> query_type: feasibility, api_target: explore

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
- country_lookback: country (required), lookback_years (if mentioned, default 5), product_class (if mentioned). \
The API supports per-metric yearRange overrides (e.g., `eciRankChangeYearRange`). Match the \
lookback period to the question: "past five years" → 5, "past decade" → 10.
- new_products: country (required). Note: `newProductsCountry` returns goods-only product counts. \
The combined goods+services count is not available from a single API call.
- treemap_products / overtime_products / product_space / feasibility*: country (required), year or year range
- treemap_partners / overtime_partners: country (required), year or year range
- treemap_bilateral / explore_bilateral: country AND partner_country (both required)
- marketshare: country (required), product (required), year range
- product_info: product (required), year
- explore_group: group_name and group_type (required)
- group_products: country (required), partner_group_name and partner_group_type (required)
- group_bilateral: group_name and group_type (required), partner country (required — use partner_name/partner_code_guess)
- group_membership: group_name (required), group_type (required)
- country_year: country (required), year or year_min/year_max (for time series), product_class (if SITC or non-default classification explicitly mentioned)
- global_product: product_class (optional, default HS92), product_level (optional), year (optional)
- global_datum: year or year range (if mentioned)

**Services class:**
- Set `services_class` to "unilateral" when the question asks about total/all exports, top products,
  or overall trade without specifically limiting to goods. This includes services in the response.
- Set `services_class` to "bilateral" for bilateral services trade questions.
- Leave `services_class` as null when the question explicitly says "goods" or names a specific
  goods product (e.g., "coffee", "automotive", "electronics").
- When in doubt, leave `services_class` as null — the system will use a sensible default.

**Trade direction:**
- Set `trade_direction` to "imports" when the question asks about imports, imported products,
  import sources, top import partners, or what a country buys/sources.
  Keywords: "imports", "imported", "buys from", "sources from", "import partners", "top imports".
- Set `trade_direction` to "exports" when the question explicitly asks about exports.
- Leave `trade_direction` as null when direction is not mentioned or ambiguous (defaults to exports).

**Year handling:**
- If no year mentioned, leave year fields as null (the system defaults to latest available).
- Do not guess or assume a year — let the system handle defaults.
- For time-series query types (overtime_*, marketshare, country_lookback), extract year_min/year_max if a range is stated.
- "since 2010" -> year_min: 2010, year_max: null
- "between 2015 and 2020" -> year_min: 2015, year_max: 2020
- "in 2023" -> year: 2023

**Product classification:**
- Default to HS12 unless the user explicitly mentions a different classification.
- Leave product_class as null unless explicitly specified — null means the system default.
- "HS 1992" or "HS92" -> product_class: HS92
- "HS 2012" or "HS12" -> product_class: HS12
- "SITC" -> product_class: SITC
- Country Pages API only supports HS and SITC product classes.
- For questions about whether a product is a natural resource or green product, use product_class: HS92.
  The naturalResource and greenProduct metadata fields are only available in the HS92 classification.
- If the user mentions a service (tourism, transport, ICT, etc.), the product_code_guess should be \
the service category name as it appears in the Atlas (e.g., "Travel & tourism", "Transport")
{services_catalog_block}

**Examples:**

Example 1:
Question: "What did Kenya export in 2024?" (query_type: treemap_products)
-> country_name: Kenya, country_code_guess: KEN, year: 2024

Example 2:
Question: "How have Brazil's coffee exports changed since 2010?" (query_type: overtime_products)
-> country_name: Brazil, country_code_guess: BRA, product_name: coffee, product_code_guess: 0901, year_min: 2010

Example 3:
Question: "What products does Japan export to the US?" (query_type: treemap_bilateral)
-> country_name: Japan, country_code_guess: JPN, partner_name: United States, partner_code_guess: USA

Example 4:
Question: "What are Rwanda's growth opportunities?" (query_type: feasibility)
-> country_name: Rwanda, country_code_guess: RWA

Example 5:
Question: "What's Colombia's share of global coffee exports over time?" (query_type: marketshare)
-> country_name: Colombia, country_code_guess: COL, product_name: coffee, product_code_guess: 0901

Example 6:
Question: "Kenya's tourism service exports" (query_type: treemap_products)
-> country_name: Kenya, country_code_guess: KEN, product_name: tourism, product_code_guess: Travel & tourism

Example 7:
Question: "EU trade data for 2023" (query_type: explore_group)
-> group_name: EU, group_type: trade, year: 2023

Example 8:
Question: "How has Vietnam's economy changed in the last decade?" (query_type: country_lookback)
-> country_name: Vietnam, country_code_guess: VNM, lookback_years: 10

Example 9:
Question: "What are Kenya's total exports?" (query_type: treemap_products)
-> country_name: Kenya, country_code_guess: KEN, services_class: unilateral

Example 10:
Question: "What are Kenya's coffee exports?" (query_type: treemap_products)
-> country_name: Kenya, country_code_guess: KEN, product_name: coffee, product_code_guess: 0901, services_class: null

Example 11:
Question: "What does Kenya export to the EU?" (query_type: group_products)
-> country_name: Kenya, country_code_guess: KEN, partner_group_name: EU, partner_group_type: trade

Example 12:
Question: "What does the EU export to Kenya?" (query_type: group_bilateral)
-> group_name: EU, group_type: trade, partner_name: Kenya, partner_code_guess: KEN

Example 13:
Question: "What is the top imported product for USA?" (query_type: treemap_products)
-> country_name: United States, country_code_guess: USA, trade_direction: imports

Example 14:
Question: "Which countries are in the EU?" (query_type: group_membership)
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
