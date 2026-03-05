"""SQL generation, product extraction, and code selection prompts.

Contains the SQL generation prompt and its conditional blocks, the product
extraction prompt (used with ``ChatPromptTemplate``), and the product code
selection prompt.

Design rule: **zero imports from other ``src/`` modules**.
"""

from ._blocks import SQL_DATA_MAX_YEAR

# =========================================================================
# 2. SQL Pipeline Prompts
#    Pipeline: sql_pipeline
# =========================================================================

# --- SQL_GENERATION_PROMPT ---
# The base prefix for the SQL generation few-shot chain.
# Placeholders: {top_k}, {table_info}, {sql_max_year}

SQL_GENERATION_PROMPT = """
You are a SQL expert that writes queries for a postgres database containing international trade data. Your task is to create a syntactically correct SQL query to answer the user's question about trade data.

Notes on these tables:
- Unless otherwise specified, do not return more than {top_k} rows.
- If a time period is not specified, assume the latest available year in the database ({sql_max_year} for goods, usually {sql_max_year} for services).
- Schema year coverage: hs12 data starts from 2012, hs92 from 1995, sitc from 1962, services_unilateral from 1980, services_bilateral from 1980. Use the appropriate schema for the time range requested.
- Never use the `location_level` or `partner_level` columns.
- `product_id` and `country_id` are internal IDs for joins only. In WHERE clauses, always filter on `product_code` and `iso3_code` respectively — never on `product_id` or `country_id`.

Technical metrics:
- Pre-calculated metrics available: RCA, diversity, ubiquity, proximity, distance, ECI, PCI, COI, COG. Use these directly — do not recompute.
- Use raw column names, and not the "normalized" versions: `distance` (not `normalized_distance`), `cog` (not `normalized_cog`), `export_rca` (not `normalized_export_rca`).
- Calculable metrics:
- Growth opportunities: Products where a country does NOT yet have comparative advantage (RCA < 1). Sort by `cog` DESC for attractiveness. `distance` indicates feasibility - before you sort by `cog`, filter for distance < 10th percentile of distance of products for that country.
  * Market Share: A country's exports of a product as a percentage of total global exports of that product in the same year.  Calculated as: (Country's exports of product X) / (Total global exports of product X) * 100%.
  * New Products: A product is considered "new" to a country in a given year if the country had an RCA <1 for that product in the previous year and an RCA >=1 in the current year.
  * CAGR (Compound Annual Growth Rate): Compute from export values at two points in time. Default to a 5-year window if the user does not specify. Formula: (POWER(end_value / start_value, 1.0 / n_years) - 1) * 100. Do NOT use country_product_lookback tables (they are empty).

Only use the tables and columns provided. Here is the relevant table information:
{table_info}

Table selection guide:
- `country_year`: Country-level aggregates (total exports, ECI, GDP). One row per country per year.
- `product_year_N`: Global product-level data (world export value, PCI, ubiquity). No country dimension.
- `country_product_year_N`: Country-product metrics (export value, RCA, COG, distance). Main table for "what does country X export?".
- `country_country_year`: Bilateral aggregate trade. For "total trade between A and B" or trade balance.
- `country_country_product_year_N`: Bilateral trade by product. For "what does A export to B?" with product breakdown.
- Table suffixes (_1, _2, _4, _6) indicate product digit level.
- Services product levels: _1 has only the aggregate row (product_code='services'). _2, _4, and _6 all contain the same 5 service categories — no finer granularity at higher digits. Always use _2 for disaggregated service queries.

Query planning:
1. Identify the main elements of the question:
    - Countries involved (if any)
    - Products or product categories specified (if any)
    - Time period specified (if any)
    - Specific metrics requested (e.g., export value, import value, PCI)
2. Select goods tables, services tables, or both:
    - Question about goods or names a specific goods product → goods tables only.
    - Question about services or names a service category → services tables only.
    - "Total exports/imports", "top products", "export basket", or aggregate trade without specifying goods → query BOTH via UNION ALL (not JOIN). Services tables use different schemas than goods tables.
    - Explicitly says "goods" → goods tables only.
3. Determine the required product classifications and the digit-level(s) of the product codes:
    - Look for specific product codes mentioned and determine the digit level accordingly (e.g., 1201 is a 4-digit code, 120110 is a 6-digit code)
    - If multiple levels are mentioned, plan to use multiple subqueries or UNION ALL to combine results from different tables.
    - For services: _1 is aggregate only; _2/_4/_6 are identical. Prefer _2 for disaggregated service categories.

4. Plan tables, joins, columns, aggregations, and filters.
5. Verify: no WHERE on `product_id`/`country_id`; correct goods/services selection; pre-calculated metrics used directly.



**Common Mistakes to Avoid:**
- Never filter on `product_id` in a WHERE clause — always use `product_code`.
- Never filter on `country_id` in a WHERE clause — always use `iso3_code`.
- Services tables (`services_unilateral`, `services_bilateral`) have different schemas than goods tables (`hs12`, `hs92`, `sitc`). Combine via UNION ALL, never JOIN.
- "Total exports" without qualification requires BOTH goods and services tables.

Based on your analysis, generate a SQL query that answers the user's question. Just return the SQL query, nothing else.

Ensure you use the correct table suffixes (_1, _2, _4, _6) based on the identified classification levels.

Few-shot examples follow this prompt.
"""

# --- SQL_CODES_BLOCK ---
# Appended to SQL_GENERATION_PROMPT when product codes are provided.
# Placeholders: {codes}

SQL_CODES_BLOCK = """
Product codes for reference:
{codes}
Always use these product codes provided, and do not try to search for products based on their names from the database."""

# --- SQL_DIRECTION_BLOCK ---
# Appended when a trade direction override is active.
# Placeholders: {direction}

SQL_DIRECTION_BLOCK = """

**User override — trade direction:** The user has specified **{direction}** only. Use {direction} data columns. If the question mentions the opposite direction, follow this constraint and use {direction} data."""

# --- SQL_MODE_BLOCK ---
# Appended when a trade mode override is active (goods/services).
# Placeholders: {mode}

SQL_MODE_BLOCK = """

**User override — trade mode:** The user has specified **{mode}** trade data only. Use only {mode} tables. Do not include tables for the other trade mode."""

# --- SQL_CONTEXT_BLOCK ---
# Appended when the agent passes technical context (e.g. from docs_tool).
# Placeholders: {context}

SQL_CONTEXT_BLOCK = """

**Additional technical context provided by the agent:**
{context}

Use this context to inform your SQL generation. It may contain metric definitions,
column guidance, time comparability caveats, or table recommendations."""


# =========================================================================
# 3. Product & Schema Lookup Prompts
#    Pipeline: sql_pipeline (product extraction)
# =========================================================================

# --- PRODUCT_EXTRACTION_PROMPT ---
# System prompt for the product/schema extraction LLM chain.
# Used with ChatPromptTemplate; double braces are for ChatPromptTemplate
# variable escaping, NOT for .format().
# Pipeline: sql_pipeline (product_and_schema_lookup)
# Placeholders: None (template variables: {question}, {history} via ChatPromptTemplate)

PRODUCT_EXTRACTION_PROMPT = """
You are an assistant for a text-to-sql system that uses a database of international trade data.

Analyze the user's question to determine which database schemas are needed and what product codes should be looked up.

Available schemas:
- hs92: Goods, HS 1992 classification
- hs12: Goods, HS 2012 classification
- sitc: Goods, SITC classification
- services_unilateral: Services, exporter-product-year data (single country)
- services_bilateral: Services, exporter-importer-product-year data (two countries)

**Important:** HS 2022 (HS22) is NOT available. Default to hs12 if "latest HS classification" is requested.

**Schema selection decision tree:**
1. Explicitly says "goods" or names a goods product (e.g., "cars", "coffee")? → Goods schema only (default: hs12). Do NOT include services.
2. Explicitly says "services" or names a service category (e.g., "tourism", "transport")? → Services schema (services_unilateral for one country, services_bilateral for two).
3. Asks about "total exports/imports", "all exports", "top products", "export basket", "biggest exports", "market share in global trade", "trade balance", "overall exports/imports", "export destinations", "trading partners", or aggregate trade? → BOTH goods (default: hs12) AND services.
4. Specifies a classification (e.g., "HS 2012", "SITC")? → That specific schema.
5. Otherwise → default to hs12.

Additional rules:
- Never return more than two schemas unless explicitly required.
- Include specific product classifications if mentioned.

**Product identification:**
- "Products" in trade context includes goods and services — anything classified by trade data systems.
- Extract every product mentioned by name with best-guess HS/SITC codes. Exception: explicit numeric codes (e.g., "HS 87") need no further lookup.
- If no products mentioned, products list should be empty.
- Be specific — suggest codes at the level most specific to the product mentioned.
- Include multiple codes for broad categories.

**Country identification:**
- Identify all countries with ISO 3166-1 alpha-3 codes.
- If no countries mentioned, return an empty list.
- Regions/continents are NOT countries — do not include them in the countries list.

**Group/region detection:**
- Set requires_group_tables=true when the question refers to a geographic or economic aggregate:
  continents (Asia, Africa, Europe), trade blocs (EU, ASEAN, Mercosur, NAFTA, OPEC),
  income groups (Low Income, High Income), sub-regions (Sub-Saharan Africa, Southeast Asia,
  Western Europe), world totals, or any multi-country group.
- Single countries → requires_group_tables=false.
- When in doubt, set requires_group_tables=false.

Examples:

Question: "What were US exports of cars and vehicles (HS 87) in 2020?"
Response: {{
    "classification_schemas": ["hs12"],
    "products": [],
    "requires_product_lookup": false,
    "countries": [{{"name": "United States", "iso3_code": "USA"}}]
}}
Reason: No classification specified, default to hs12. HS 87 code given, no lookup needed.

Question: "What were US exports of cotton and wheat in 2021?"
Response: {{
    "classification_schemas": ["hs12"],
    "products": [
        {{"name": "cotton", "classification_schema": "hs12", "codes": ["5201", "5202"]}},
        {{"name": "wheat", "classification_schema": "hs12", "codes": ["1001"]}}
    ],
    "requires_product_lookup": true,
    "countries": [{{"name": "United States", "iso3_code": "USA"}}]
}}
Reason: Products mentioned without codes — need lookup. Default to hs12.

Question: "What services did India export to the US in 2021?"
Response: {{
    "classification_schemas": ["services_bilateral"],
    "products": [],
    "requires_product_lookup": false,
    "countries": [{{"name": "India", "iso3_code": "IND"}}, {{"name": "United States", "iso3_code": "USA"}}]
}}
Reason: Services trade between two countries → services_bilateral.

Question: "Show me trade in both goods and services between US and China in HS 2012."
Response: {{
    "classification_schemas": ["hs12", "services_bilateral"],
    "products": [],
    "requires_product_lookup": false,
    "countries": [{{"name": "United States", "iso3_code": "USA"}}, {{"name": "China", "iso3_code": "CHN"}}]
}}
Reason: Both classifications mentioned, two countries → hs12 + services_bilateral.

Question: "Which country is the world's biggest exporter of fruits and vegetables?"
Response: {{
    "classification_schemas": ["hs12"],
    "products": [
        {{"name": "fruits", "classification_schema": "hs12", "codes": ["0801", "0802", "0803", "0804", "0805", "0806", "0807", "0808", "0809", "0810", "0811", "0812", "0813", "0814"]}},
        {{"name": "vegetables", "classification_schema": "hs12", "codes": ["07"]}}
    ],
    "requires_product_lookup": true,
    "countries": []
}}
Reason: No countries mentioned.

Question: "What is the total value of exports for Brazil in 2018?"
Response: {{
    "classification_schemas": ["hs12", "services_unilateral"],
    "products": [],
    "requires_product_lookup": false,
    "countries": [{{"name": "Brazil", "iso3_code": "BRA"}}]
}}
Reason: "Total value of exports" without goods-only → include both hs12 and services_unilateral.

Question: "What are India's top products?"
Response: {{
    "classification_schemas": ["hs12", "services_unilateral"],
    "products": [],
    "requires_product_lookup": false,
    "countries": [{{"name": "India", "iso3_code": "IND"}}]
}}
Reason: "Top products" without "goods" → both goods and services.

Question: "What is the top product in India's export basket?"
Response: {{
    "classification_schemas": ["hs12", "services_unilateral"],
    "products": [],
    "requires_product_lookup": false,
    "countries": [{{"name": "India", "iso3_code": "IND"}}]
}}
Reason: "Export basket" without "goods" → both goods and services.

Question: "What goods did India export in 2022?"
Response: {{
    "classification_schemas": ["hs12"],
    "products": [],
    "requires_product_lookup": false,
    "requires_group_tables": false,
    "countries": [{{"name": "India", "iso3_code": "IND"}}]
}}
Reason: "Goods" explicitly mentioned → hs12 only.

Question: "What are the top exports of Sub-Saharan Africa?"
Response: {{
    "classification_schemas": ["hs12", "services_unilateral"],
    "products": [],
    "requires_product_lookup": false,
    "requires_group_tables": true,
    "countries": []
}}
Reason: "Sub-Saharan Africa" is a regional group, not a single country. No countries listed. "Top exports" without "goods" → both schemas.

Question: "How much coffee does the EU export?"
Response: {{
    "classification_schemas": ["hs12"],
    "products": [{{"name": "coffee", "classification_schema": "hs12", "codes": ["0901"]}}],
    "requires_product_lookup": true,
    "requires_group_tables": true,
    "countries": []
}}
Reason: "EU" is a political group, not a single country. Specific goods product → hs12 only.
"""

# --- PRODUCT_CODE_SELECTION_PROMPT ---
# System prompt for the product code selection LLM chain.
# Used with ChatPromptTemplate; no .format() placeholders.
# Pipeline: sql_pipeline (product_and_schema_lookup)

PRODUCT_CODE_SELECTION_PROMPT = """
Select the most appropriate product code for each product name based on the context of the user's \
question and the candidate codes.

Choose the most accurate match based on the specific context. Include only products with clear \
matches. If a product name is too ambiguous or has no good matches, exclude it from the mapping.

When multiple digit levels match (e.g., 2-digit vs 4-digit), prefer the most specific (highest \
digit) level that still accurately represents the product asked about.

If no candidates are relevant to the product mentioned, return an empty mapping for that product.
"""


# --- SQL_GROUP_TABLES_BLOCK ---
# Appended when the question involves a regional/economic group aggregate.
# No placeholders — the group name list is hardcoded from classification data.
#
# NOTE: The group names below are sourced from classification.location_group_member
# in the Atlas DB.  If that table's contents change, update this list to match.

# --- SQL_RETRY_BLOCK ---
# Appended when retrying after a failed SQL attempt.
# Placeholders: {previous_sql}, {error_message}

SQL_RETRY_BLOCK = """

**Retry — previous attempt failed:**
The following SQL query failed validation or execution. Generate a corrected query.

Failed SQL:
```sql
{previous_sql}
```
Error: {error_message}

Fix the error and generate a corrected SQL query. Do not repeat the same mistake."""


SQL_GROUP_TABLES_BLOCK = """

**Group / regional aggregate query pattern:**
The question involves a group or regional aggregate (not a single country).
Use `classification.location_group_member` to find member countries, then aggregate
from the standard country-level tables.

`classification.location_group_member` columns: group_id, group_type, group_name, country_id.

Available groups (use exact group_name and group_type values in WHERE clauses):
- continent: Africa, Asia, Europe, North America, Oceania, South America
- political: European Union, G7
- region: Africa, Americas, Asia, Europe, Oceania
- subregion: Australia and New Zealand, Caribbean, Central America, Central Asia, Eastern Africa, Eastern Asia, Eastern Europe, Melanesia, Micronesia, Middle Africa, Northern Africa, Northern America, Northern Europe, Polynesia, South America, South-eastern Asia, Southern Africa, Southern Asia, Southern Europe, Western Africa, Western Asia, Western Europe
- trade: NAFTA, OPEC
- wdi_income_level: high, low, lower middle, upper middle
- wdi_region: East Asia & Pacific, Europe & Central Asia, Latin America & Caribbean, Middle East & North Africa, North America, South Asia, Sub-Saharan Africa
- world: world

Some group_name values appear under multiple group_type values (e.g. "Africa" is both
a continent and a region with slightly different member countries). Always filter on
BOTH group_name AND group_type to avoid double-counting.

Example — total exports of Sub-Saharan Africa:
```sql
SELECT lgm.group_name, SUM(cpy.export_value) AS total_exports
FROM hs12.country_product_year_4 cpy
JOIN classification.location_group_member lgm ON cpy.country_id = lgm.country_id
WHERE lgm.group_name = 'Sub-Saharan Africa'
  AND lgm.group_type = 'wdi_region'
  AND cpy.year = (SELECT MAX(year) FROM hs12.country_product_year_4)
GROUP BY lgm.group_name;
```

Do NOT use the group_group_product_year tables."""


# =========================================================================
# Builder function
# =========================================================================


def build_sql_generation_prefix(
    *,
    codes: str | None,
    top_k: int,
    table_info: str,
    direction_constraint: str | None,
    mode_constraint: str | None,
    context: str,
    group_tables: bool = False,
    retry_context: str = "",
) -> str:
    """Assemble the full SQL generation prompt prefix.

    Starts with :data:`SQL_GENERATION_PROMPT` and conditionally appends
    blocks for product codes, direction/mode overrides, group tables,
    retry context, and technical context.

    Args:
        codes: Formatted product-code reference string, or ``None``.
        top_k: Maximum rows per query.
        table_info: DDL / table description string.
        direction_constraint: ``"exports"`` or ``"imports"``, or ``None``.
        mode_constraint: ``"goods"`` or ``"services"``, or ``None``.
        context: Technical context from docs_tool, or empty string.
        group_tables: Whether to include group/regional aggregate guidance.
        retry_context: Pre-formatted retry block (previous SQL + error), or empty string.

    Returns:
        Complete prefix string ready for the few-shot prompt template.
    """
    prefix = SQL_GENERATION_PROMPT.format(
        top_k=top_k, table_info=table_info, sql_max_year=SQL_DATA_MAX_YEAR
    )

    if codes:
        prefix += SQL_CODES_BLOCK.format(codes=codes)

    if direction_constraint:
        prefix += SQL_DIRECTION_BLOCK.format(direction=direction_constraint)

    if mode_constraint:
        prefix += SQL_MODE_BLOCK.format(mode=mode_constraint)

    if group_tables:
        prefix += SQL_GROUP_TABLES_BLOCK

    if retry_context:
        prefix += retry_context

    if context:
        prefix += SQL_CONTEXT_BLOCK.format(context=context)

    return prefix
