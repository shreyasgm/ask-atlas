# Hybrid Backend Architecture: SQL + GraphQL Analysis

> **Date:** 2026-02-21
> **Status:** Research complete, implementation pending
> **Related issue:** See GitHub issue linked at bottom

This document contains a comprehensive analysis of the Atlas GraphQL API capabilities vs. the existing text-to-SQL backend, a question-by-question eval mapping, and a proposed hybrid architecture where simple questions use the GraphQL API and complex analytical questions use the SQL backend. An implementing agent should be able to take this document and execute the refactoring without additional context.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Architecture: Text-to-SQL Backend](#2-current-architecture-text-to-sql-backend)
3. [GraphQL API: Complete Reference](#3-graphql-api-complete-reference)
4. [Comparative Analysis](#4-comparative-analysis)
5. [Eval Question Mapping (All 60 Questions)](#5-eval-question-mapping-all-60-questions)
6. [Proposed Hybrid Architecture](#6-proposed-hybrid-architecture)
7. [Implementation Plan](#7-implementation-plan)
8. [Appendices](#8-appendices)

---

## 1. Executive Summary

The Atlas of Economic Complexity has a **public GraphQL API** at `https://atlas.hks.harvard.edu/api/countries/graphql` that exposes pre-computed data used to render the Atlas website. This API covers ~65% of questions well (simple lookups, country profiles, product-level data), while the existing SQL backend covers ~98% of questions but requires a multi-step LLM pipeline.

**The proposed hybrid approach:**
- **GraphQL path**: For simple, well-structured queries that match what the Atlas website shows (country profiles, export baskets, trade partners, market shares, pre-computed growth/complexity metrics). Faster, no database needed, no SQL generation errors.
- **SQL path**: For complex analytical queries requiring arbitrary aggregation, cross-country comparisons, regional analysis, custom time ranges, product-level time series, and derived calculations.

**Key benefit**: The GraphQL API provides **pre-computed derived metrics** that do NOT exist in the raw database: policy recommendations, diversification grades, growth projections, structural transformation classifications. Adding GraphQL gives us ~10 new answerable question types.

---

## 2. Current Architecture: Text-to-SQL Backend

### 2.1 LangGraph Pipeline

The current system is a **LangGraph StateGraph** with an outer agent loop and an inner query pipeline.

**File locations:**
- `src/generate_query.py` — LangGraph graph construction, pipeline nodes, SQL generation chain
- `src/state.py` — `AtlasAgentState` TypedDict
- `src/text_to_sql.py` — `AtlasTextToSQL` async factory class (streaming, answer API)
- `src/product_and_schema_lookup.py` — Product/schema extraction and code lookup LLM chains
- `src/sql_validation.py` — Pre-execution SQL validation (sqlglot-based)
- `src/sql_multiple_schemas.py` — Multi-schema SQLAlchemy database wrapper
- `src/config.py` — Settings (pydantic-settings) + `create_llm()` factory
- `model_config.py` — Non-secret LLM model configuration

**Graph flow:**
```
START -> agent -> [tool_calls?] -> extract_tool_question -> extract_products -> lookup_codes
                                   -> get_table_info -> generate_sql -> validate_sql
                                   -> [valid?] -> execute_sql -> format_results -> agent
                                              |-> [invalid] -> format_results -> agent
                  [no tool_calls] -> END
                  [max queries exceeded] -> max_queries_exceeded -> agent
```

**Pipeline nodes (8 nodes + agent):**

1. **`extract_tool_question`** — Extracts the question string from the LLM's tool_call args
2. **`extract_products`** — Uses an LLM chain to identify classification schemas (hs92/hs12/sitc/services_unilateral/services_bilateral) and product mentions from the question; applies user overrides
3. **`lookup_codes`** — For identified products, queries the DB for candidate product codes (full-text search + trigram similarity) and uses an LLM to select final codes
4. **`get_table_info`** — Retrieves DDL/table metadata for the identified schemas via `SQLDatabaseWithSchemas`
5. **`generate_sql`** — Constructs a few-shot prompt with 7 example queries, table info, product codes, and constraints, then invokes the query LLM to generate SQL
6. **`validate_sql`** — Validates SQL syntax via sqlglot, checks table existence
7. **`execute_sql`** — Executes the query via async PostgreSQL engine; has retry logic (3 attempts, exponential backoff)
8. **`format_results`** — Packages results as ToolMessage(s) back to the agent

**Agent loop limits:**
- `max_queries_per_question`: default 30 (configurable)
- `max_results_per_query`: default 15 rows per query

### 2.2 State Schema

From `src/state.py`:

```python
class AtlasAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    queries_executed: int
    last_error: str
    retry_count: int
    pipeline_question: str
    pipeline_products: Optional[SchemasAndProductsFound]
    pipeline_codes: str
    pipeline_table_info: str
    pipeline_sql: str
    pipeline_result: str
    pipeline_result_columns: list[str]
    pipeline_result_rows: list[list]
    pipeline_execution_time_ms: int
    override_schema: Optional[str]      # e.g., "hs92", "hs12"
    override_direction: Optional[str]    # "exports" or "imports"
    override_mode: Optional[str]         # "goods" or "services"
```

### 2.3 Database Schema

The database is organized into **6 PostgreSQL schemas** defined in `db_table_descriptions.json`:

#### Data Schemas

| Schema | Description | Product Digit Levels |
|---|---|---|
| `hs92` | HS 1992 goods classification (default) | 1, 2, 4, 6-digit |
| `hs12` | HS 2012 goods classification | 1, 2, 4, 6-digit |
| `sitc` | SITC goods classification | 1, 2, 4-digit (no 6-digit) |
| `services_unilateral` | Services trade (single country) | 1, 2, 4, 6-digit |
| `services_bilateral` | Services trade (country-to-country) | 1, 2, 4, 6-digit |

Each data schema contains these table families:

| Table Pattern | Description |
|---|---|
| `country_year` | Country-level aggregate trade by year (export_value, import_value, eci, coi, diversity, GDP, population, growth_proj) |
| `country_product_year_{1,2,4,6}` | Country-product-year with export_value, import_value, export_rca, global_market_share, distance, cog, normalized metrics, product_status |
| `country_country_product_year_{1,2,4,6}` | Bilateral trade by product (export_value, import_value) |
| `country_country_year` | Bilateral aggregate trade |
| `product_year_{1,2,4,6}` | Global product-level data (export_value, import_value, pci, CAGR, growth rates, complexity_enum) |
| `product_product_4` | Product proximity/relatedness (strength) — hs92, hs12, sitc only |
| `group_group_product_year_{1,2,4,6}` | Trade between country groups |
| `country_product_lookback_{1,2,4}` | Export growth over lookback periods (hs92 only) |

#### Classification Schema

| Table | Purpose |
|---|---|
| `classification.location_country` | Country metadata: country_id, iso3_code, iso2_code, name_en, name_short_en, income_level |
| `classification.location_group` | Country groups (continent, political, region, trade bloc, WDI income level) |
| `classification.location_group_member` | Maps countries to groups |
| `classification.product_hs92` | HS92 product hierarchy: product_id, code, name_en, product_level, parent_id, cluster_id |
| `classification.product_hs12` | HS12 product hierarchy |
| `classification.product_sitc` | SITC product hierarchy |
| `classification.product_services_unilateral` | Services product classification (unilateral) |
| `classification.product_services_bilateral` | Services product classification (bilateral) |
| `classification.product_hs92_ps_clusters` | Product space cluster names |
| `classification.product_hs92_ps_edges` | Product space edge weights |

#### Schema-to-products mapping (from `product_and_schema_lookup.py`):
```python
SCHEMA_TO_PRODUCTS_TABLE_MAP = {
    "hs92": "classification.product_hs92",
    "hs12": "classification.product_hs12",
    "sitc": "classification.product_sitc",
    "services_unilateral": "classification.product_services_unilateral",
    "services_bilateral": "classification.product_services_bilateral",
}
```

### 2.4 SQL Generation

The SQL generation uses a **two-tier prompt system**:

**Agent-level system prompt** (in `create_sql_agent`):
- Describes the agent as "Ask-Atlas"
- Data source context (UN COMTRADE, cleaned by Growth Lab)
- All technical metrics with definitions (RCA, ECI, PCI, COI, COG, distance, diversity, ubiquity, proximity)
- Calculable metrics (market share, new products) with formulas
- Policy question handling guidance
- Response formatting rules
- Tool usage limits
- Active user overrides

**SQL generation prompt** (in `create_query_generation_chain`):
- Row limits (default 15)
- Default to latest year when unspecified
- Never use `location_level`/`partner_level` columns
- Distinction between `product_id` (internal) vs `product_code` (official)
- Pre-calculated vs calculable metrics
- 5-step query planning guidance
- Table suffix conventions (`_1`, `_2`, `_4`, `_6`)
- Product codes appended if available
- Direction/mode constraints if overridden

**7 example queries** (from `src/example_queries/`):
1. Unilateral exports (goods + services, 1-digit)
2. Unilateral exports (4-digit, goods only)
3. Bilateral exports (goods + services, 4-digit, time range)
4. Largest exporter of fruits and vegetables (multi-digit, CTE)
5. Top imports with PCI (goods + services JOIN product_year)
6. Distance-ranked products for India (window function, RANK)
7. COG-ranked products for Kenya (window function, RANK)

### 2.5 SQL Capabilities Summary

**What SQL can do:**
- Arbitrary JOINs across any tables
- Custom aggregations (SUM, AVG, GROUP BY)
- Window functions (RANK, ROW_NUMBER)
- CTEs (WITH clauses)
- UNION ALL across schemas (goods + services)
- Subqueries
- Year ranges with BETWEEN
- 6-digit product granularity
- Multiple HS revisions (HS92, HS96, HS02, HS07, HS12)
- Product proximity lookups (product_product_4)
- Regional/group aggregation via group tables
- Cross-country rankings
- Compute CAGR, market share, custom derived metrics
- Full bilateral trade at any product level

### 2.6 Example SQL Queries

**Unilateral exports (goods + services, 1-digit):**
```sql
-- Goods exports (HS92)
SELECT 'Goods' as category, p.name_en as product_name, p.code as product_code,
       cpy.export_value, cpy.global_market_share
FROM hs92.country_product_year_1 cpy
JOIN classification.location_country lc ON cpy.country_id = lc.country_id
JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
WHERE cpy.year = 2022 AND cpy.export_value > 0 AND lc.iso3_code = 'USA'
UNION ALL
-- Services exports
SELECT 'Services' as category, p.name_en as product_name, p.code as product_code,
       cpy.export_value, cpy.global_market_share
FROM services_unilateral.country_product_year_1 cpy
JOIN classification.location_country lc ON cpy.country_id = lc.country_id
JOIN classification.product_services_unilateral p ON cpy.product_id = p.product_id
WHERE cpy.year = 2022 AND cpy.export_value > 0 AND lc.iso3_code = 'USA'
ORDER BY category, export_value DESC;
```

**Bilateral exports (goods + services, 4-digit, time range):**
```sql
(SELECT 'Goods' as category, loc_exp.iso3_code as exporter, loc_imp.iso3_code as importer,
        p.code as product_code, p.name_en as product_name, SUM(ccpy.export_value) as total_export_value
FROM hs92.country_country_product_year_4 ccpy
JOIN classification.location_country loc_exp ON ccpy.country_id = loc_exp.country_id AND loc_exp.iso3_code = 'BOL'
JOIN classification.location_country loc_imp ON ccpy.partner_id = loc_imp.country_id AND loc_imp.iso3_code = 'MAR'
JOIN classification.product_hs92 p ON ccpy.product_id = p.product_id
WHERE ccpy.year BETWEEN 2010 AND 2022 AND ccpy.export_value > 0
GROUP BY p.code, p.name_en, loc_exp.iso3_code, loc_imp.iso3_code
ORDER BY total_export_value DESC LIMIT 10)
UNION ALL
(SELECT 'Services' as category, loc_exp.iso3_code, loc_imp.iso3_code,
        p.code, p.name_en, SUM(ccpy.export_value) as total_export_value
FROM services_bilateral.country_country_product_year_4 ccpy
JOIN classification.location_country loc_exp ON ccpy.country_id = loc_exp.country_id AND loc_exp.iso3_code = 'BOL'
JOIN classification.location_country loc_imp ON ccpy.partner_id = loc_imp.country_id AND loc_imp.iso3_code = 'MAR'
JOIN classification.product_services_bilateral p ON ccpy.product_id = p.product_id
WHERE ccpy.year BETWEEN 2010 AND 2022 AND ccpy.export_value > 0
GROUP BY p.code, p.name_en, loc_exp.iso3_code, loc_imp.iso3_code
ORDER BY total_export_value DESC LIMIT 10);
```

**Largest exporter (CTE with multi-level products):**
```sql
WITH latest_year AS (SELECT MAX(year) as max_year FROM hs92.country_product_year_4),
combined_trade AS (
    SELECT loc.iso3_code, SUM(cpy.export_value) as export_value
    FROM hs92.country_product_year_4 cpy
    JOIN classification.location_country loc ON cpy.country_id = loc.country_id
    JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
    WHERE p.code IN ('0801','0802','0803','0804','0805','0806','0807','0808','0809','0810','0811','0812','0813','0814')
      AND cpy.year = (SELECT max_year FROM latest_year)
    GROUP BY loc.iso3_code
    UNION ALL
    SELECT loc.iso3_code, SUM(cpy.export_value)
    FROM hs92.country_product_year_2 cpy
    JOIN classification.location_country loc ON cpy.country_id = loc.country_id
    JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
    WHERE p.code = '07' AND cpy.year = (SELECT max_year FROM latest_year)
    GROUP BY loc.iso3_code
)
SELECT iso3_code, SUM(export_value) as total_export_value
FROM combined_trade GROUP BY iso3_code ORDER BY total_export_value DESC LIMIT 10;
```

---

## 3. GraphQL API: Complete Reference

### 3.1 Connection Details

- **Endpoint:** `POST https://atlas.hks.harvard.edu/api/countries/graphql`
- **Method:** POST with `Content-Type: application/json`
- **Authentication:** None required (public, unauthenticated)
- **Introspection:** Enabled
- **Mutations/Subscriptions:** None (read-only API)
- **Rate Limiting:** No observed rate limiting (10 rapid sequential requests all returned HTTP 200)
- **Pagination:** None. All queries return complete result sets. Largest responses ~1,248 products at fourDigit level (~100KB+).

### 3.2 ID Formats

| Entity | Format | Example |
|---|---|---|
| Country | `location-{iso_numeric}` | `location-840` (USA), `location-76` (Brazil), `location-404` (Kenya) |
| Product (HS) | `product-HS-{internal_id}` | `product-HS-1763` (Cars/8703), `product-HS-910` (Crude oil/2709) |
| Product (SITC) | `product-SITC-{internal_id}` | `product-SITC-650` |

**CRITICAL:** Product IDs use **internal numeric IDs**, NOT HS codes. The HS code `8703` maps to internal ID `1763`. You must look up internal IDs via `allProducts` or extract them from query results.

**Country ID lookup:** Use ISO 3166-1 numeric codes. Common examples:
- USA: `location-840`, Brazil: `location-76`, Kenya: `location-404`
- Germany: `location-276`, Japan: `location-392`, China: `location-156`
- Nigeria: `location-566`, Canada: `location-124`, India: `location-356`

### 3.3 Complete Query Catalog (25 Queries)

#### 3.3.1 Country Profile Queries

**`countryProfile(location: ID!, comparisonLocationsQuantity: Float) -> CountryProfile`**
- **Status: FULLY WORKING**
- The richest single-query endpoint. Returns **46 fields**.

All 46 fields:

```graphql
{
  countryProfile(location: "location-404") {
    # Location
    location { id shortName longName code }

    # GDP (8 fields, all with { quantity year })
    latestGdp { quantity year }
    latestGdpRank { quantity year }
    latestGdpPpp { quantity year }
    latestGdpPppRank { quantity year }
    latestGdpPerCapita { quantity year }
    latestGdpPerCapitaRank { quantity year }
    latestGdpPerCapitaPpp { quantity year }
    latestGdpPerCapitaPppRank { quantity year }

    # Classification
    incomeClassification   # enum: High, UpperMiddle, LowerMiddle, Low

    # Population
    latestPopulation { quantity year }

    # Trade (7 fields)
    exportValue
    importValue
    exportValueRank
    exportValueNatResources
    importValueNatResources
    netExportValueNatResources
    exportValueNonOil
    currentAccount { quantity year }

    # Complexity (6 fields)
    latestEci
    latestEciRank
    eciNatResourcesGdpControlled
    latestCoi
    latestCoiRank
    coiClassification   # enum: low, medium, high

    # Growth (5 fields)
    growthProjection
    growthProjectionRank
    growthProjectionClassification          # enum: rapid, moderate, slow
    growthProjectionRelativeToIncome        # enum: More, Less, Same, ModeratelyMore, ModeratelyLess
    growthProjectionPercentileClassification # enum: TopDecile, TopQuartile, TopHalf, BottomHalf

    # Diversification (8 fields)
    diversity
    diversityRank
    diversificationGrade    # enum: APlus, A, B, C, D, DMinus
    newProductExportValue
    newProductExportValuePerCapita
    newProductsIncomeGrowthComments              # enum: LargeEnough, TooSmall
    newProductsComments                          # enum: TooFew, Sufficient
    newProductsComplexityStatusGrowthPrediction  # enum: More, Same, Less

    # Market Share (3 fields)
    marketShareMainSector { shortName code }
    marketShareMainSectorDirection         # enum: rising, falling, stagnant
    marketShareMainSectorPositiveGrowth    # Boolean

    # Structural Transformation (3 fields)
    structuralTransformationStep          # enum: NotStarted, TextilesOnly, ElectronicsOnly, MachineryOnly, Completed
    structuralTransformationSector { shortName code }
    structuralTransformationDirection     # enum: risen, fallen, stagnated

    # Policy
    policyRecommendation   # enum: ParsimoniousIndustrial, StrategicBets, LightTouch, TechFrontier

    # Comparison
    comparisonLocations { shortName }
  }
}
```

**Verified response for Kenya (location-404):**
```json
{
  "latestGdpPerCapita": { "quantity": 2274, "year": 2023 },
  "exportValue": 16200000000.0,
  "importValue": 27300000000.0,
  "exportValueRank": 90,
  "latestEci": -0.5268,
  "latestEciRank": 93,
  "latestCoi": 0.0169,
  "latestCoiRank": 74,
  "diversificationGrade": "C",
  "growthProjectionClassification": "moderate",
  "policyRecommendation": "ParsimoniousIndustrial",
  "structuralTransformationStep": "NotStarted"
}
```

**`allCountryProfiles() -> [AllCountryProfile]`**
- **Status: WORKING** (compact version)
- Returns only 4 fields: `location`, `diversificationGrade`, `eciNatResourcesGdpControlled`, `policyRecommendation`
- Returns 145 countries

#### 3.3.2 Country Time-Series Queries

**`countryYear(location: ID, year: Int, eciProductClass: ProductClass, coiProductClass: ProductClass) -> CountryYear`**
- **Status: FULLY WORKING**
- Fields: `location`, `population`, `exportValue`, `importValue`, `exportValueRank`, `gdp`, `gdpRank`, `gdpPpp`, `gdpPerCapita`, `gdpPerCapitaPpp`, `eci`, `eciRank`, `coi`, `coiRank`
- Year range: 1980-2024 for trade/GDP; ECI starts 2012 for HS

**Verified response for Brazil 2018:**
```json
{
  "exportValue": 257379903694.0,
  "importValue": 249982230430.0,
  "gdpPerCapita": { "quantity": 9281, "year": 2018 },
  "eci": { "quantity": 0.3627, "year": 2018 },
  "eciRank": { "quantity": 55, "year": 2018 }
}
```

**`allCountryYear(year: Int, eciProductClass: ProductClass, coiProductClass: ProductClass) -> [CountryYear]`**
- **Status: FULLY WORKING**
- Same fields for ALL 145 countries at once. Ideal for cross-country rankings.

**`countryYearRange(location: ID, minYear: Int, maxYear: Int, eciProductClass: ProductClass, coiProductClass: ProductClass) -> CountryYearRange`**
- **Status: FULLY WORKING**
- Returns time series arrays. Each field is `{ quantity, year }` pairs.

**`allCountryYearRange(...)` -> BROKEN** (server error `'hs_coi'`)

#### 3.3.3 Country Lookback Queries

**`countryLookback(id: ID, yearRange: LookBackYearRange, productClass: ProductClass) -> CountryLookback`**
- **Status: FULLY WORKING**
- Pre-computed growth metrics over configurable lookback periods.
- `LookBackYearRange` enum: `ThreeYears`, `FiveYears`, `TenYears`, `FifteenYears`
- Fields: `eciRankChange`, `exportValueConstGrowthCagr`, `exportValueGrowthNonOilConstCagr`, `largestContributingExportProduct { shortName code }`, `eciChange`, `diversityRankChange`, `diversityChange`, `gdpPcConstantCagrRegionalDifference` (Above/InLine/Below), `exportValueGrowthClassification` (Troubling/Mixed/Static/Promising), `gdpChangeConstantCagr`, `gdpPerCapitaChangeConstantCagr`, `gdpGrowthConstant`

**Verified response for Kenya (5-year lookback):**
```json
{
  "eciRankChange": 2,
  "exportValueConstGrowthCagr": 0.0221,
  "eciChange": 0.154,
  "exportValueGrowthClassification": "Troubling",
  "gdpPcConstantCagrRegionalDifference": "Above"
}
```

**`countryProductLookback(location: ID, yearRange: LookBackYearRange, productLevel: ProductLevel) -> [CountryProductLookback]`**
- **Status: FULLY WORKING**
- Per-product growth metrics
- Fields: `product { shortName code }`, `exportValueConstGrowth`, `exportValueConstCagr`
- Returns ~551 products per country at fourDigit level

#### 3.3.4 TreeMap Queries

**`treeMap(facet: TreeMapType!, productClass: ProductClass, year: Int, productLevel: ProductLevel, locationLevel: LocationLevel, location: ID, product: ID, partner: ID, mergePci: Boolean) -> [TreeMapDatum]`**
- **Status: PARTIALLY WORKING**
- Returns union type: `TreeMapProduct | TreeMapLocation`

| Facet | Description | Returns | Status |
|---|---|---|---|
| `CPY_C` | Country's exports by product | `TreeMapProduct[]` | **WORKING** |
| `CCY_C` | Country's trade by partner country | `TreeMapLocation[]` | **WORKING** |
| `CPY_P` | Product's exporters by country | `TreeMapLocation[]` | **BROKEN** (null locations) |

**TreeMapProduct fields:**
```graphql
... on TreeMapProduct {
  product { shortName code id longName parent { shortName code } topLevelParent { shortName code } productType }
  exportValue
  importValue
  rca
  distance
  opportunityGain
  pci
  normalizedPci
  normalizedDistance
  normalizedOpportunityGain
  globalMarketShare
  year
}
```

**TreeMapLocation fields:**
```graphql
... on TreeMapLocation {
  location { shortName id code }
  exportValue
  importValue
  year
}
```

**Key capabilities:**
- **Bilateral trade:** `facet: CPY_C` with `partner` argument = what country A exports TO country B, by product.
- **Product filtering:** `product` argument with `facet: CPY_C` = specific product's export value for a country.
- **Market share:** `globalMarketShare` field directly available.
- **Product levels:** `section` (11), `twoDigit` (~97), `fourDigit` (~1,248).
- **Only HS works for treeMap.** SITC returns server error.

**Verified: Canada top 5 sectors 2021:**
```
1. Minerals:     $131.4B  (21.6%)
2. Services:     $121.2B  (19.9%)
3. Agriculture:  $101.5B  (16.7%)
4. Vehicles:      $55.2B   (9.1%)
5. Chemicals:     $52.6B   (8.6%)
```

**Verified: Japan trade partners 2022 (CCY_C):**
```
1. China:          $147.2B exports
2. United States:  $123.7B exports
3. South Korea:     $54.6B exports
4. Taiwan:          $53.4B exports
5. Hong Kong:       $32.7B exports
```

**Verified: Japan-to-US bilateral (CPY_C + partner):**
```
1. Business (ICT):              $101.9B
2. Cars (8703):                  $82.3B
3. Electronic integrated circuits: $29.6B
4. Auto parts:                    $27.5B
Total bilateral product lines: 1,246
```

**Verified: Germany automotive market share:**
```
Cars (8703):       $147.6B, globalMarketShare: 20.17%
Auto Parts (8708):  $57.4B, globalMarketShare: 14.44%
```

**Verified: Nigeria crude oil (product filter):**
```
Petroleum oils, crude (2709): $23,876,486,770, globalMarketShare: 4.49%
```

#### 3.3.5 Product Queries

**`product(id: ID) -> Product`** — WORKING (avoid `level` field)
- Fields: `id`, `code`, `shortName`, `longName`, `parent`, `topLevelParent`, `productType` (Goods/Service)

**`allProducts(productClass: ProductClass, productLevel: ProductLevel) -> [Product]`** — WORKING

| Classification | Level | Count |
|---|---|---|
| HS | section | 11 (10 goods + 1 services) |
| HS | twoDigit | ~97 |
| HS | fourDigit | 1,248 |
| SITC | section | 11 |
| SITC | fourDigit | 793 |

**`productYear(product: ID, year: Int) -> ProductYear`** — WORKING
- Fields: `product`, `pci`, `globalExportValue`, `globalExportValueChangeFiveYears`, `complexityLevel` (low/moderate/high)

**`allProductYear(productClass: ProductClass, productLevel: ProductLevel, year: Int) -> [ProductYear]`** — WORKING (returns ~1,245 products)

**`productYearRange` and `allProductYearRange`** — **BROKEN** (server error: missing positional argument)

#### 3.3.6 Product Space Query

**`productSpace(productClass: ProductClass, year: Int, location: ID) -> [ProductSpaceDatum]`**
- **Status: FULLY WORKING**
- Returns ~852 products per country
- Fields: `product { shortName code productType }`, `exportValue`, `importValue`, `rca`, `x`, `y`, `connections { strength, productId }`

**Verified: Kenya product space (top by RCA):**
```
Tea:            RCA 472.01, $1,385M exports
Cut flowers:    RCA 184.67, $738M exports
Titanium ore:   RCA  96.69, $93M exports
Legumes:        RCA  72.57, $48M exports
```

#### 3.3.7 Country-Product Queries

**`allCountryProductYear(location: ID, year: Int, productClass: ProductClass, productLevel: ProductLevel) -> [CountryProductYear]`**
- **Status: FULLY WORKING**
- Returns ~1,247 products per country
- Fields: `id`, `product { shortName code }`, `exportValue`, `importValue`, `normalizedOpportunityGainDecileClassification`, `normalizedDistanceDecileClassification`, `normalizedPciDecileClassification`
- DecileClassification enum: `Last`/`Second`/`Third`/.../`Ninth`/`Top`

**`manyCountryProductYear(group: ID, ...)` — BROKEN** (group resolution fails)

#### 3.3.8 New Products Queries

**`newProductsCountry(location: ID!, year: Int!) -> NewProductsCountry`** — WORKING
- Fields: `newProducts { shortName code productType }`, `newProductExportValue`, `newProductExportValuePerCapita`, `newProductCount`
- Note: `location` sub-field returns null

**`newProductsComparisonCountries(location: ID!, year: Int!, quantity: Int) -> [NewProductsComparisonCountries]`** — FULLY WORKING
- Returns peer country comparisons (default 5 peers)
- Verified: Kenya peers = Uganda (28 new), Tanzania (17), Ethiopia (21), Rwanda (11), DRC (0)

#### 3.3.9 Location Queries

**`location(id: ID) -> Location`** — WORKING
- Fields: `id`, `code`, `shortName`, `longName`, `level`, `isInCountryPages`, `isInComplexityRankings`, `isFormerCountry`, `isDataTrustworthy`, `hasReportedServicesLastYear`, `hasReportedServicesInAnyYear`

**`allLocations(level: LocationLevel) -> [Location]`** — PARTIALLY WORKING
- `level: country`: 252 locations (145 in country pages, 145 in complexity rankings, 184 with services)
- `level: region`: BROKEN (7 items, all null fields)

#### 3.3.10 Group Queries

**`group(id: ID)` and `allGroups(groupType: GroupType)`** — **BOTH BROKEN**
- `group`: Returns `'NoneType' object has no attribute 'group'`
- `allGroups`: Returns `module 'api.models.classification' has no attribute 'LocationGroup'`
- GroupType enum values (defined but non-functional): `region`, `subregion`, `rock_song`, `trade`, `wdi_income_level`, `wdi_region`, `political`, `continent`, `world`

#### 3.3.11 Global Statistics

**`globalDatum(yearRange: LookBackYearRange, productClass: ProductClass) -> GlobalDatum`** — FULLY WORKING
- Fields: `gdpChangeConstCagr`, `exportValueConstChangeCagr`, `exportValueNonOilConstChangeCagr`, `globalExportValue`, `latestEciRankTotal`, `latestCoiRankTotal`, `latestExporterRankTotal`, `latestGdpRankTotal`, `latestGdpPppPerCapitaRankTotal`, `latestDiversityRankTotal`
- All rank totals = 145 (except GdpPppPerCapita = 144)
- `globalExportValue` (2024): $29,349,810,077,963

### 3.4 Services Data in GraphQL

Services are included within the HS classification as 5 special products:

| Code | Name | Internal ID |
|---|---|---|
| `travel` | Travel & tourism | `product-HS-4001` |
| `transport` | Transport | `product-HS-4002` |
| `ict` | Business (ICT) | `product-HS-4003` |
| `financial` | Insurance & finance | `product-HS-4004` |
| `unspecified` | Unspecified | `product-HS-4000` |

These appear alongside goods in treeMap, productSpace, and allCountryProductYear queries. Service products have null PCI values.

### 3.5 Data Year Ranges

| Data Type | Earliest | Latest | Notes |
|---|---|---|---|
| Trade (export/import values) | 1980 | 2024 | |
| GDP, Population | 1980 | 2024 | |
| ECI (HS classification) | 2012 | 2024 | Pre-2012 returns 0.0 |
| ECI (SITC classification) | Earlier | 2024 | May have wider coverage |
| COI | 2012 | 2024 | |
| Product-level data (treemap) | 1980 | 2024 | |
| Country profile | N/A | 2024 | Always latest |

### 3.6 Complete Enum Reference

| Enum | Values |
|---|---|
| `TreeMapType` | `CPY_C`, `CPY_P`, `CCY_C` |
| `ProductClass` | `HS`, `SITC` |
| `ProductLevel` | `section`, `twoDigit`, `fourDigit` |
| `LocationLevel` | `country`, `region` |
| `ProductType` | `Goods`, `Service` |
| `LookBackYearRange` | `ThreeYears`, `FiveYears`, `TenYears`, `FifteenYears` |
| `IncomeClassification` | `High`, `UpperMiddle`, `LowerMiddle`, `Low` |
| `COIClassification` | `low`, `medium`, `high` |
| `ComplexityLevel` | `low`, `moderate`, `high` |
| `DiversificationGrade` | `APlus`, `A`, `B`, `C`, `D`, `DMinus` |
| `GrowthProjectionClassification` | `rapid`, `moderate`, `slow` |
| `GrowthProjectionRelativeToIncome` | `More`, `Less`, `Same`, `ModeratelyMore`, `ModeratelyLess` |
| `GrowthProjectionPercentileClassification` | `TopDecile`, `TopQuartile`, `TopHalf`, `BottomHalf` |
| `MarketShareMainSectorDirection` | `rising`, `falling`, `stagnant` |
| `StructuralTransformationStep` | `NotStarted`, `TextilesOnly`, `ElectronicsOnly`, `MachineryOnly`, `Completed` |
| `StructuralTransformationDirection` | `risen`, `fallen`, `stagnated` |
| `PolicyRecommendation` | `ParsimoniousIndustrial`, `StrategicBets`, `LightTouch`, `TechFrontier` |
| `DecileClassification` | `Last`, `Second`, ... `Ninth`, `Top` |
| `ExportValueGrowthClassification` | `Troubling`, `Mixed`, `Static`, `Promising` |
| `GDPPCConstantCAGRRegionalDifference` | `Above`, `InLine`, `Below` |

### 3.7 Confirmed Broken Queries (Deep-Dive Re-Test)

All 10 endpoints below were re-tested with multiple argument variations on 2026-02-21.
**Every one is confirmed broken server-side** — none were due to incorrect invocation.

| # | Endpoint | Root Cause | Single-line Summary |
|---|----------|-----------|---------------------|
| 1 | `treeMap(facet: CPY_P)` | Location FK join broken | Data rows returned but `location` is null on every row |
| 2 | `productYearRange` | Resolver missing `product_level` arg | `get_product_class_model() missing 1 required positional argument` |
| 3 | `allProductYearRange` | Same bug as #2 | Fails even though schema accepts `productLevel` |
| 4 | `allCountryYearRange` | Dict key bug in COI iteration | Always errors with `'hs_coi'` regardless of arguments |
| 5 | `group(id)` | `LocationGroup` model absent | `module 'api.models.classification' has no attribute 'LocationGroup'` |
| 6 | `allGroups(groupType)` | Same as #5 | Every GroupType value fails identically |
| 7 | `manyCountryProductYear` | Two overlapping bugs (#2 + #5) | Hits group bug with group arg, product bug without |
| 8 | `allLocations(level: region)` | Data gap (not code bug) | Regions exist but have null code/shortName/longName |
| 9 | SITC treeMap | SITC not implemented | `free variable 'model' referenced before assignment` |
| 10 | Product `level` field | Enum serialization mismatch | `Expected a value of type "ProductLevel" but received: fourDigit` |

---

**Endpoint 1: `treeMap(facet: CPY_P)` — Null locations**

Queries attempted:
```graphql
# 1a: Standard CPY_P query
{ treeMap(facet: CPY_P, productClass: HS, year: 2022, productLevel: fourDigit,
          locationLevel: country, product: "product-HS-910") {
    ... on TreeMapLocation { location { shortName id code } exportValue importValue year } } }
# Error: "Cannot return null for non-nullable field TreeMapLocation.location." at every row

# 1b: Omit location subfield (workaround attempt)
{ treeMap(facet: CPY_P, productClass: HS, year: 2022, productLevel: fourDigit,
          locationLevel: country, product: "product-HS-1763") {
    ... on TreeMapLocation { exportValue importValue year } } }
# Returns rows like {"exportValue": 625390.0, "importValue": 21042393.0, "year": 2022}
# but with NO way to identify which country — data is useless.

# 1c: Without locationLevel arg — same null error
# 1d: With productLevel: section — returns empty array
```
**Root cause:** Server returns trade data rows for CPY_P but the location FK join/lookup is broken. The `TreeMapLocation.location` field is declared NON_NULL but the resolver returns null. Baseline: `CPY_C` facet works perfectly.

---

**Endpoint 2: `productYearRange` — Missing positional argument**

```graphql
# 2a: Standard query
{ productYearRange(product: "product-HS-910", minYear: 2015, maxYear: 2022) {
    product { shortName code } pci { quantity year } globalExportValue { quantity year } } }
# Error: "ProductYearQuery.get_product_class_model() missing 1 required positional argument: 'product_level'"

# 2b: Minimal fields — same error
```
Schema signature: `productYearRange(product: ID!, minYear: Int!, maxYear: Int!)` — there is no way to pass `productLevel`. The resolver internally calls a method that requires it but doesn't extract it from the product ID.

**Baseline:** `productYear(product: "product-HS-910", year: 2022)` works perfectly.

---

**Endpoint 3: `allProductYearRange` — Same missing-argument bug**

```graphql
# 3a: With all required args
{ allProductYearRange(productClass: HS, productLevel: fourDigit, minYear: 2020, maxYear: 2022) {
    product { shortName code } globalExportValue { quantity year } } }
# Error: same "missing 1 required positional argument: 'product_level'"
```
This endpoint DOES accept `productLevel` in the schema, but the resolver fails anyway — the Python method expects it as a positional argument instead of reading it from GraphQL args.

**Baseline:** `allProductYear(productClass: HS, productLevel: fourDigit, year: 2022)` works perfectly.

---

**Endpoint 4: `allCountryYearRange` — `'hs_coi'` error**

```graphql
# 4a: Without productClass args
{ allCountryYearRange(minYear: 2015, maxYear: 2022) {
    location { shortName } exportValue { quantity year } } }
# Error: "'hs_coi'"

# 4b: With eciProductClass: HS, coiProductClass: HS — same error
# 4c: With SITC variants — same error
```
**Root cause:** Bug is specifically in the "all countries + year range" combination. The `'hs_coi'` error suggests a dict key access issue when iterating across years and countries simultaneously for the COI field.

**Baselines that work:**
- `countryYearRange(location: "location-404", minYear: 2015, maxYear: 2022)` — single country, year range ✓
- `allCountryYear(year: 2022)` — all countries, single year ✓

---

**Endpoint 5: `group(id)` — LocationGroup model missing**

8 different ID formats tried:

| ID tried | Error |
|----------|-------|
| `"continent-AF"` | `'NoneType' object has no attribute 'group'` |
| `"region-AF"`, `"world"`, `"1"`, `"Africa"` | Same NoneType error |
| `"group-continent-AF"`, `"group-world"` | Same NoneType error |
| **`"group-1"`** | **`module 'api.models.classification' has no attribute 'LocationGroup'`** |

The `group-1` format gets past ID parsing and exposes the real root cause: the `LocationGroup` class doesn't exist in the server's `api.models.classification` module. Other ID formats fail earlier at ID parsing (returning None).

---

**Endpoint 6: `allGroups(groupType)` — Same LocationGroup bug**

All 9 GroupType enum values tested individually (`continent`, `region`, `subregion`, `rock_song`, `trade`, `wdi_income_level`, `wdi_region`, `political`, `world`) plus calling with no argument. **Every one** returns: `"module 'api.models.classification' has no attribute 'LocationGroup'"`

---

**Endpoint 7: `manyCountryProductYear` — Two overlapping bugs**

```graphql
# 7a: With group arg
{ manyCountryProductYear(group: "location-404", year: 2022, productClass: HS,
                          productLevel: section) {
    id product { shortName code } exportValue importValue } }
# Error: "'NoneType' object has no attribute 'group'" — hits bug #5

# 7b: Without group arg (aggregate mode)
{ manyCountryProductYear(year: 2022, productClass: HS, productLevel: section,
                          aggregate: true) {
    id product { shortName code } exportValue importValue } }
# Error: "missing 1 required positional argument: 'product_level'" — hits bug #2
```
Broken both ways: group resolution bug (#5) when group provided, product_class_model bug (#2) when omitted.

---

**Endpoint 8: `allLocations(level: region)` — Data gap**

```graphql
# 8a: With name/code fields
{ allLocations(level: region) { id code shortName longName level isInCountryPages } }
# Error: "Cannot return null for non-nullable field Location.code." for indices 0-6

# 8b: Only nullable-safe fields — WORKS
{ allLocations(level: region) { id level } }
# Returns 7 regions: location-2, location-9, location-19, location-142, location-150, location-998, location-994
```
**Root cause:** Region-level locations exist as FK targets but were never populated with name/code data. Schema declares these fields NON_NULL, so GraphQL rejects the nulls. This is a data quality issue, not a code bug. Country-level works fine.

---

**Endpoint 9: SITC treeMap — Entirely unsupported**

```graphql
# 9a: SITC treeMap (tried fourDigit, section, twoDigit — all fail)
{ treeMap(facet: CPY_C, productClass: SITC, year: 2022, productLevel: fourDigit,
          locationLevel: country, location: "location-404") {
    ... on TreeMapProduct { product { shortName code } exportValue } } }
# Error: "free variable 'model' referenced before assignment in enclosing scope"

# 9b: SITC on non-treeMap endpoint
{ allProductYear(productClass: SITC, productLevel: section, year: 2020) {
    product { shortName code } globalExportValue pci } }
# Error: "Product class must be one of HS." — explicit rejection
```
**Root cause:** SITC is defined in the GraphQL `ProductClass` enum but the backend only implements HS. The treeMap code path hits an unhandled branch leaving a local variable `model` unassigned. Other endpoints explicitly reject SITC with a human-readable error.

---

**Endpoint 10: Product `level` field — Enum serialization**

```graphql
# 10a: Without level — works perfectly
{ product(id: "product-HS-910") { id code shortName longName } }
# Returns: {"code": "2709", "shortName": "Petroleum oils, crude", "productType": "Goods"}

# 10b-d: With level field — fails at all product levels
{ product(id: "product-HS-910") { id code shortName longName level } }
# Error: "Expected a value of type \"ProductLevel\" but received: fourDigit"
# Same for section and twoDigit products
```
**Root cause:** Resolver returns the correct string value (e.g., `"fourDigit"`) but GraphQL cannot serialize it to the `ProductLevel` enum. Likely a mismatch in enum registration — resolver returns a Python string instead of the GraphQL enum value. Note that `productType` (returns `"Goods"` as `ProductType` enum) works fine, so the issue is specific to `ProductLevel`.

---

#### 3.7.1 Workarounds for Broken Endpoints

| Broken endpoint | Workaround | Limitation |
|---|---|---|
| `productYearRange` / `allProductYearRange` | Loop `productYear` / `allProductYear` over individual years | N API calls instead of 1 |
| `allCountryYearRange` | Use `countryYearRange` (single country) or `allCountryYear` (single year) | Cannot get all-countries × year-range in one call |
| `treeMap(CPY_P)` | **No GraphQL workaround.** Use SQL for "which countries export product X?" | Must fall back to SQL backend |
| `group` / `allGroups` / `manyCountryProductYear` | **No workaround.** Use SQL for regional aggregations | Group/region functionality entirely absent |
| SITC queries | **Only HS works.** Use SQL for SITC trade data | No SITC via GraphQL at all |
| Product `level` field | Infer from code length (4-char = fourDigit) or ID range; never request `level` | Fragile heuristic |
| `allLocations(level: region)` | Only request `id` + `level` for regions; get names from SQL or hardcode | 7 regions; names easily hardcoded |

#### 3.7.2 Pattern Analysis

Three bug clusters account for all 10 broken endpoints:

1. **YearRange variants systematically broken** (#2, #3, #4): The `productYearRange`, `allProductYearRange`, and `allCountryYearRange` endpoints all fail while their single-year counterparts (`productYear`, `allProductYear`, `allCountryYear`) work fine. The server code for multi-year queries has argument-passing bugs that single-year queries avoid.

2. **LocationGroup model entirely missing** (#5, #6, #7): The `LocationGroup` class is absent from `api.models.classification`. This single missing class breaks `group()`, `allGroups()`, and `manyCountryProductYear()`. The GroupType enum exists with 9 values, and the GraphQL schema defines the full Group type — but the underlying model was never implemented.

3. **SITC defined in schema but not in resolvers** (#9): The `ProductClass` enum includes `SITC` but no backend resolver supports it. The treeMap path hits an unhandled branch; other paths explicitly reject SITC. Only HS works.

The remaining two (#1, #10) are isolated bugs: a broken FK join in CPY_P treeMap results and an enum serialization mismatch for the `ProductLevel` type. Endpoint #8 is a data gap (null region names), not a code bug.

### 3.8 Structural Limitations (Not Bugs)

1. **No arbitrary aggregation.** No SUM, GROUP BY, window functions. Data is pre-structured.
2. **No cross-country product comparison.** Cannot ask "which countries export the most cars?" in one query.
3. **No regional/group aggregation.** Cannot aggregate "total African exports."
4. **No product-level time series.** Cannot get "crude oil global value 2015-2024" without N calls.
5. **No 6-digit product granularity.** Only section/twoDigit/fourDigit.
6. **No HS revision selection.** Only generic "HS" and "SITC" — no HS92/HS96/HS02/HS07/HS12.
7. **No pagination.** Full result sets always.
8. **External dependency.** No SLA, could break or rate-limit at any time.
9. **Product IDs require lookup.** Internal IDs, not HS codes.

---

## 4. Comparative Analysis

### 4.1 What SQL Can Do That GraphQL Cannot

| Capability | SQL Method | GraphQL Status |
|---|---|---|
| Arbitrary aggregation (SUM, AVG, GROUP BY) | Native SQL | Not possible |
| Window functions (RANK, ROW_NUMBER) | Native SQL | Not possible |
| Cross-country product ranking ("who exports most cars?") | Single query | Broken (CPY_P) |
| Regional aggregation ("total African exports") | JOIN with location_group | Broken (allGroups) |
| Product-level time series | Single query with year range | Broken (productYearRange) |
| 6-digit product granularity | `_6` table suffix | Not available |
| Multiple HS revisions (HS92, HS96, HS02, HS07, HS12) | Schema selection | Only generic "HS" |
| Product proximity (product-product relatedness) | `product_product_4` tables | Only x,y coordinates |
| Custom CAGR between arbitrary years | SQL computation | Only pre-computed 3/5/10/15yr |
| Product-level lookback tables | `country_product_lookback` tables | Only hs92 schema |
| Group-to-group trade | `group_group_product_year` tables | Broken |
| Complex multi-table JOINs | Native SQL | Not possible |
| SITC product-level trade data | SITC schema tables | Broken (SITC treeMap) |
| Custom derived metrics | SQL computation | Not possible |

### 4.2 What GraphQL Can Do That SQL Cannot

| Capability | GraphQL Query | SQL Status |
|---|---|---|
| Policy recommendations | `countryProfile.policyRecommendation` | **Not in database** |
| Diversification grade (A+ to D-) | `countryProfile.diversificationGrade` | **Not in database** |
| Growth projection + classification | `countryProfile.growthProjection*` | **Not in database** |
| Structural transformation status | `countryProfile.structuralTransformation*` | **Not in database** |
| COI classification (low/medium/high) | `countryProfile.coiClassification` | **Not in database** |
| Market share main sector + direction | `countryProfile.marketShareMainSector*` | **Not in database** |
| New products income/growth comments | `countryProfile.newProducts*Comments` | **Not in database** |
| Growth projection relative to income | `countryProfile.growthProjectionRelativeToIncome` | **Not in database** |
| Comparison peer countries | `countryProfile.comparisonLocations` | **Not in database** |
| Pre-computed per-product CAGR | `countryProductLookback` | Available in lookback tables (hs92 only) |
| Export growth classification | `countryLookback.exportValueGrowthClassification` | **Not in database** |
| GDP growth vs regional avg | `countryLookback.gdpPcConstantCagrRegionalDifference` | **Not in database** |
| Decile classifications (opportunity/distance/PCI) | `allCountryProductYear` | Would need to compute |

**Key insight:** The ~12 derived metrics marked "Not in database" represent real analytical value computed by the Atlas team's algorithms. These cannot be reproduced from raw SQL queries without implementing those algorithms.

### 4.3 Performance Comparison

| Aspect | SQL Backend | GraphQL API |
|---|---|---|
| Latency per query | ~200-500ms (DB query) + ~2-5s (LLM chain) | ~200-500ms (HTTP call) |
| LLM calls per question | 2-4 (extract, lookup, generate, agent) | 0 (deterministic routing) |
| Error rate | Moderate (SQL generation errors) | Low for working queries; 32% endpoints broken |
| Infrastructure | PostgreSQL database required | No infrastructure needed |
| Cost per query | ~$0.01-0.05 (LLM tokens) | Free |
| Concurrent capacity | Limited by DB pool (10+20 overflow) | Unknown (external) |

---

## 5. Eval Question Mapping (All 60 Questions)

### Routing Legend

- **GQL**: Can be answered entirely via GraphQL API
- **SQL**: Requires SQL backend (GraphQL cannot answer or answers poorly)
- **EITHER**: Both can answer equally well
- **GQL+**: GraphQL provides better/richer answer than SQL (pre-computed metrics)
- **N/A**: No data access needed (LLM behavior test)

### 5.1 Total Export Values (Q1-3)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 1 | Total exports Brazil 2018 | EITHER | `countryYear(location: "location-76", year: 2018)` → `exportValue` | `SELECT export_value FROM hs92.country_year WHERE iso3_code='BRA' AND year=2018` | Both return same value |
| 2 | Crude oil from Nigeria 2020 | EITHER | `treeMap(CPY_C, location: "location-566", product: "product-HS-910", year: 2020)` | `SELECT export_value FROM hs92.country_product_year_4 WHERE code='2709' AND iso3_code='NGA' AND year=2020` | GraphQL needs product ID lookup |
| 3 | Service exports Singapore 2018 | EITHER | `treeMap(CPY_C, section level)` filter for Services section | `SELECT export_value FROM services_unilateral.country_year WHERE iso3_code='SGP' AND year=2018` | |

### 5.2 Sectoral Export Composition (Q4-16)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 4 | Germany automotive % of total | EITHER | `treeMap(CPY_C, section)` → compute Vehicles/total | SQL can do this in one query | GraphQL needs client-side computation |
| 5 | Top 5 Canada sectors 2021 | GQL | `treeMap(CPY_C, section, location: "location-124", year: 2021)` → sort | Single query, pre-sorted | Verified working |
| 6 | Top 3 India products 2020 | GQL | `treeMap(CPY_C, fourDigit, location: "location-356", year: 2020)` → top 3 | Returns all ~1,248 products | Client sorts |
| 7 | Mineral share South Africa 2019 | GQL | `treeMap(CPY_C, section)` → Minerals / total | Simple computation | |
| 8 | Services share France 2022 | GQL | `treeMap(CPY_C, section)` → Services / total | SQL needs UNION ALL goods+services | GraphQL simpler |
| 9 | Mineral vs service share Chile 2017 | GQL | `treeMap(CPY_C, section, year: 2017)` | One call gives both | |
| 10 | Top 3 mineral products Peru 2016 | GQL | `treeMap(CPY_C, fourDigit)` → filter by topLevelParent=Minerals → top 3 | SQL with JOIN to parent | |
| 11 | Top 3 UK service sectors 2019 | GQL | `treeMap(CPY_C, section)` → filter Services sub-products | Services have 5 categories | |
| 12 | Switzerland services % 2022 | GQL | `treeMap(CPY_C, section)` → Services / total | | |
| 13 | Travel share Thailand services 2022 | GQL | `treeMap(CPY_C, fourDigit)` → travel / sum(services) | Filter by productType=Service | |
| 14 | Transport share Netherlands 2017 | GQL | Same approach as Q13 | | |
| 15 | ICT vs Travel growth Ireland 2015-2021 | SQL | Needs 2 treeMap calls (2015 + 2021); no product-level time series | Single query with year range | SQL more efficient |
| 16 | Service trends Spain over decade | SQL | Would need ~10 treeMap calls (one per year) | Single query | SQL far more efficient for time series |

### 5.3 Trade Partners and Market Position (Q17-24)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 17 | Japan export destinations 2021 | GQL | `treeMap(CCY_C, location: "location-392", year: 2021)` | SQL JOIN country_country_year | Verified working |
| 18 | Australia % to SE Asia | SQL | `treeMap(CCY_C)` gives partners but no group info; must hardcode SE Asia countries | SQL JOIN with location_group | SQL has group tables |
| 19 | Kenya trade balance top 10 partners | GQL | `treeMap(CCY_C)` has both exportValue and importValue per partner | Single call | |
| 20 | Singapore re-exports | SQL | Neither has re-export data directly | Neither can fully answer | Both need LLM interpretation |
| 21 | Australia iron ore market share Asia vs global | SQL | `globalMarketShare` is global only; no regional market share | SQL can JOIN with regional groups | GraphQL lacks regional breakdown |
| 22 | China electronics market share | GQL | `treeMap(CPY_C)` with Electronics products → `globalMarketShare` | | Verified working |
| 23 | South Korea semiconductor share over decade | SQL | Need ~10 treeMap calls; no product time series | Single query | SQL much more efficient |
| 24 | Germany automotive market share | GQL | `treeMap(CPY_C)` → Cars(8703) → `globalMarketShare: 20.17%` | | Verified: 20.17% |

### 5.4 Growth and Performance (Q25-31)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 25 | Spain export change 2016-2021 | EITHER | `countryYear` for 2016 + 2021, compute % change | Single query | GraphQL needs 2 calls |
| 26 | Vietnam CAGR 2010-2020 | GQL+ | `countryLookback(yearRange: TenYears)` → `exportValueConstGrowthCagr` | SQL must compute CAGR | GraphQL has pre-computed CAGR |
| 27 | Netherlands vs Sweden growth 2012-2022 | GQL+ | `countryLookback` for each → compare CAGRs | SQL for both | GraphQL has pre-computed |
| 28 | Mineral fuel share Russia 2015-2021 | EITHER | 2 treeMap calls (2015 + 2021) | Single query | |
| 29 | Sectors driving Turkey growth 2015-2020 | GQL+ | `countryProductLookback(FiveYears, section)` → sort by exportValueConstGrowth | SQL with lookback tables or computation | GraphQL pre-computed |
| 30 | Pharma growth Switzerland 2010-2020 | EITHER | 2 treeMap calls with product filter | Single query | |
| 31 | Colombia market share change over 10 years | SQL | Need global totals + country totals over time; multiple calls | Single query with computation | SQL much more efficient |

### 5.5 Economic Complexity (Q32-39)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 32 | Brazil ECI trend 15 years | GQL | `countryYearRange(minYear: 2009, maxYear: 2024)` → eci array | SQL from country_year | Both work; GraphQL returns clean time series |
| 33 | Vietnam complexity vs regional peers | SQL | Need to identify peers (no group queries), then compare | SQL JOIN with location_group | GraphQL lacks group queries |
| 34 | Top 5 products for SA complexity | GQL | `treeMap(CPY_C, fourDigit)` with `pci` + `rca` fields → filter RCA>1, sort by PCI | SQL with JOIN | GraphQL has PCI on treeMap |
| 35 | Indonesia high-complexity export proportion | SQL | Need PCI thresholds + time series; multiple calls | Single query with PCI JOIN | SQL more efficient for time series |
| 36 | Poland high-tech evolution 20 years | SQL | ~20 treeMap calls | Single query | SQL far more efficient |
| 37 | Egypt COI vs regional competitors | SQL | `countryProfile` for each country, but need to identify competitors (groups broken) | SQL JOIN with groups | |
| 38 | South Korea complexity growth industries | SQL | Complex multi-dimensional; would need many calls | Multiple SQL queries, but more efficient | |
| 39 | Malaysia export complexity vs global average | SQL | Need global average PCI computation | SQL computation | |

### 5.6 Diversification Strategies (Q40-44)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 40 | Mexico diversification vs middle-income | SQL | Would need all middle-income countries (groups broken) | SQL with group tables | |
| 41 | Portugal Product Space opportunities | GQL+ | `treeMap(CPY_C)` with distance + opportunityGain + RCA < 1 → sort by opportunityGain | SQL with distance/COG | GraphQL has pre-computed fields |
| 42 | Argentina diversification risks | SQL | Complex multi-factor analysis | Multiple queries | Both need LLM interpretation |
| 43 | Ghana diversification potential | GQL+ | `treeMap(CPY_C)` with distance + opportunityGain + `countryProfile.diversificationGrade` | SQL + manual grade | GraphQL has diversification grade |
| 44 | Kazakhstan resource-based complexity | SQL | Need time series + resource identification + complexity analysis | Multiple queries | |

### 5.7 Edge Cases (Q45-52)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 45 | Tuvalu exports 2020 | EITHER | `countryYear(location: "location-798", year: 2020)` | SQL query | Check data availability |
| 46 | Soviet Union 1990 | EITHER | `location(id: ...)` to check if exists | SQL query | Historical entity |
| 47 | Germany to Germany | GQL | `treeMap(CCY_C, location: "location-276", partner: "location-276")` | SQL bilateral self-trade | |
| 48 | Brazil 2030 | EITHER | `countryYear(year: 2030)` → returns error | SQL returns empty | Both should explain data limits |
| 49 | Products > $1T single country | SQL | Would need to scan all countries | Single SQL query with MAX | SQL much more efficient |
| 50 | Liechtenstein products + ECI | EITHER | `location` to check if in country pages; `countryProfile` if available | SQL query | Small country edge case |
| 51 | Japan RCA in bananas | GQL | `productSpace(location: "location-392")` → find banana code → `rca` | SQL query | GraphQL has RCA |
| 52 | South Sudan top 5 exports 2015 | GQL | `treeMap(CPY_C, fourDigit, location: "location-728", year: 2015)` → top 5 | SQL query | |

### 5.8 Out-of-Scope Refusals (Q53-56)

| ID | Question | Route | Notes |
|---|---|---|---|
| 53 | Capital of France | N/A | LLM should refuse; no data access needed |
| 54 | SQL injection attempt | N/A | LLM should refuse; system should not execute |
| 55 | Nigeria protectionist policy | N/A | LLM should decline normative policy; may offer factual data |
| 56 | Python scraping script | N/A | LLM should refuse code generation |

### 5.9 Data Availability Boundaries (Q57-60)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 57 | Bilateral services US-China | EITHER | `treeMap(CPY_C, partner)` with service products | `services_bilateral` schema | GraphQL integrates services |
| 58 | UK exports 1960 | EITHER | `countryYear(year: 1960)` → check if data exists | SQL query | Both should report data limits |
| 59 | Taiwan vs Chinese Taipei | EITHER | `allLocations` to find representation | SQL classification lookup | Entity resolution |
| 60 | Germany service imports | EITHER | `treeMap(CPY_C)` has importValue field | SQL services tables | Both have import data |

### 5.10 Coverage Summary

| Route | Count | Questions |
|---|---|---|
| **GQL** (GraphQL sufficient) | 18 | 5,6,7,8,9,10,11,12,13,14,17,19,22,24,32,34,47,51 |
| **GQL+** (GraphQL provides richer answer) | 6 | 26,27,29,41,43,52 |
| **EITHER** (both work equally) | 13 | 1,2,3,4,25,28,30,45,48,50,57,58,60 |
| **SQL** (SQL required) | 15 | 15,16,18,20,21,23,31,33,35,36,37,38,39,40,44 |
| **N/A** (no data access) | 6 | 46,49,53,54,55,56 |
| **Entity resolution** | 2 | 42,59 |

**Conclusion:** ~37 of 60 questions (62%) can be handled by GraphQL alone or with GraphQL providing a richer answer. ~15 questions (25%) require SQL. The SQL-required questions are disproportionately the harder, more analytically interesting ones.

---

## 6. Proposed Hybrid Architecture

### 6.1 New LangGraph Workflow

The agent should have **two tools** instead of one:

```
START -> agent -> [tool_calls?] ─┬─> [graphql_tool] -> graphql_pipeline -> agent
                                 ├─> [sql_tool]     -> sql_pipeline     -> agent
                                 └─> END
```

**Tool 1: `atlas_graphql_tool`**
- For questions that can be answered via the GraphQL API
- No LLM needed for query generation (deterministic mapping)
- Much faster: skip product extraction, code lookup, SQL generation, validation
- Returns structured JSON that the agent interprets

**Tool 2: `query_tool`** (existing SQL tool)
- For complex analytical questions requiring arbitrary SQL
- Keeps existing pipeline (extract_products → lookup_codes → get_table_info → generate_sql → validate_sql → execute_sql → format_results)

### 6.2 Routing Strategy

The **agent** (not a separate router) decides which tool to use based on the question. Update the agent system prompt to describe both tools:

```
You have two tools:

1. atlas_graphql_tool: Use this for questions about:
   - Country profiles (GDP, population, exports, imports, rankings)
   - Export baskets (top sectors, top products for a specific country and year)
   - Trade partners (export destinations, import origins for a country)
   - Product-level market share (for a specific country and product)
   - ECI, COI values and rankings (for a specific country)
   - Time series of country-level metrics (exports, GDP, ECI over years)
   - RCA values (for a specific country's products)
   - Growth projections, diversification grades, policy recommendations
   - Pre-computed growth rates (CAGR at 3/5/10/15 year windows)
   - Bilateral trade (what country A exports to country B)
   - New products a country has started exporting

2. query_tool: Use this for questions requiring:
   - Cross-country comparisons (e.g., "which countries export the most X?")
   - Regional aggregations (e.g., "total exports of African countries")
   - Custom time ranges or product-level time series
   - Complex derived metrics (custom CAGR, weighted averages)
   - 6-digit product granularity
   - Specific HS revision data (HS92 vs HS12)
   - Product proximity analysis
   - Multi-step analytical queries
   - Anything the GraphQL tool cannot handle
```

### 6.3 GraphQL Tool Implementation

The GraphQL tool should be a **deterministic query mapper**, not an LLM chain. Given a structured request, it maps to the appropriate GraphQL query.

**Proposed interface:**

```python
class GraphQLToolInput(BaseModel):
    """Input for the GraphQL tool."""
    query_type: Literal[
        "country_profile",
        "country_year",
        "country_year_range",
        "all_country_year",
        "country_lookback",
        "country_product_lookback",
        "treemap_products",        # CPY_C
        "treemap_partners",        # CCY_C
        "treemap_bilateral",       # CPY_C + partner
        "product_space",
        "product_year",
        "new_products",
        "new_products_comparison",
        "global_datum",
        "all_locations",
    ] = Field(description="The type of GraphQL query to execute")

    country: Optional[str] = Field(description="Country name or ISO code")
    year: Optional[int] = Field(description="Year for the query")
    min_year: Optional[int] = Field(description="Start year for range queries")
    max_year: Optional[int] = Field(description="End year for range queries")
    product_level: Optional[Literal["section", "twoDigit", "fourDigit"]] = Field(default="fourDigit")
    product_code: Optional[str] = Field(description="Product HS code to filter by")
    partner_country: Optional[str] = Field(description="Partner country for bilateral queries")
    lookback_years: Optional[Literal[3, 5, 10, 15]] = Field(description="Lookback period")
```

**Implementation needs:**

1. **Country name to ID resolver:** Map "Kenya" → `location-404`, "Brazil" → `location-76`. Use `allLocations` to build a lookup table (cache at startup). Match by ISO3 code, ISO2 code, short name, or long name.

2. **Product name/code to ID resolver:** Map "crude oil" or "2709" → `product-HS-910`. Use `allProducts` to build a lookup table. Match by HS code or name search.

3. **Query builder:** Given the `GraphQLToolInput`, construct the appropriate GraphQL query string with the right fields.

4. **HTTP client:** `httpx` async client to call `POST https://atlas.hks.harvard.edu/api/countries/graphql`.

5. **Response formatter:** Convert JSON response to a string format that the agent can interpret (similar to how SQL results are formatted).

### 6.4 New State Fields

Extend `AtlasAgentState` with:

```python
class AtlasAgentState(TypedDict):
    # ... existing fields ...

    # GraphQL pipeline state
    graphql_query_type: Optional[str]
    graphql_query: Optional[str]        # The GraphQL query string sent
    graphql_result: Optional[str]       # Formatted result string
    graphql_raw_response: Optional[dict] # Raw JSON response
```

### 6.5 Error Handling

The GraphQL tool should handle:
1. **Broken queries:** If a query hits a broken endpoint, fall back to SQL tool with a message to the agent.
2. **Missing data:** If a country/product is not found, return a clear error.
3. **Network errors:** Timeout/connection errors should suggest SQL fallback.
4. **Rate limiting:** If rate limited (unknown thresholds), queue and retry or fall back.

### 6.6 Caching Strategy

Consider caching these expensive but stable GraphQL responses:
- `allLocations` — changes very rarely. Cache at startup.
- `allProducts` — changes very rarely. Cache at startup.
- `countryProfile` — changes yearly. Cache with 24h TTL.
- `globalDatum` — changes yearly. Cache with 24h TTL.

---

## 7. Implementation Plan

### Phase 1: GraphQL Client Module

**New file: `src/graphql_client.py`**

1. Async HTTP client (httpx) for GraphQL endpoint
2. Country ID resolver (name/ISO → `location-{numeric}`)
3. Product ID resolver (HS code/name → `product-HS-{internal_id}`)
4. Query builders for each query type
5. Response parsers
6. Caching layer for lookup tables (allLocations, allProducts)

### Phase 2: GraphQL Tool

**New file: `src/graphql_tool.py`**

1. Define `GraphQLToolInput` schema
2. Implement the tool function
3. Format responses for agent consumption

### Phase 3: Update LangGraph Workflow

**Modify: `src/generate_query.py`**

1. Add the new `atlas_graphql_tool` alongside existing `query_tool`
2. Update agent system prompt to describe both tools
3. Add GraphQL pipeline nodes (simpler than SQL: resolve IDs → build query → execute → format)
4. Update routing logic

**Modify: `src/state.py`**

1. Add GraphQL-specific state fields

### Phase 4: Update Tests

**New file: `src/tests/test_graphql_client.py`**
- Unit tests for ID resolution, query building, response parsing
- Mock HTTP responses for each query type

**New file: `src/tests/test_graphql_tool.py`**
- Integration tests with real API (mark as `@pytest.mark.integration`)
- Test routing between GraphQL and SQL tools

**Modify: existing test files**
- Update agent trajectory tests
- Update pipeline node tests

### Phase 5: Update Evals

**Modify: `evaluation/eval_questions.json`**
- Add new questions testing GraphQL-exclusive metrics (policy recommendations, diversification grades, etc.)
- Add routing metadata to each question (expected tool: graphql/sql/either)

**Modify: `evaluation/run_eval.py`**
- Track which tool was used for each question
- Report GraphQL vs SQL usage statistics

### Phase 6: Frontend Updates

The frontend (`frontend/src/hooks/use-chat-stream.ts`) should handle new pipeline state events:
- `graphql_query` node start/complete events
- Display GraphQL query alongside SQL queries in the UI

---

## 8. Appendices

### 8.1 Sample GraphQL Queries for Common Patterns

**Pattern 1: Country overview**
```graphql
{
  countryProfile(location: "location-404") {
    location { shortName code }
    latestGdpPerCapita { quantity year }
    incomeClassification
    exportValue importValue exportValueRank
    latestEci latestEciRank
    latestCoi latestCoiRank
    diversificationGrade
    growthProjectionClassification
    policyRecommendation
    structuralTransformationStep
  }
}
```

**Pattern 2: Export basket (products)**
```graphql
{
  treeMap(facet: CPY_C, productClass: HS, year: 2024, productLevel: fourDigit,
          locationLevel: country, location: "location-404") {
    ... on TreeMapProduct {
      product { shortName code topLevelParent { shortName } productType }
      exportValue rca pci globalMarketShare distance opportunityGain
    }
  }
}
```

**Pattern 3: Trade partners**
```graphql
{
  treeMap(facet: CCY_C, productClass: HS, year: 2024, productLevel: fourDigit,
          locationLevel: country, location: "location-392") {
    ... on TreeMapLocation {
      location { shortName code }
      exportValue importValue
    }
  }
}
```

**Pattern 4: Bilateral trade**
```graphql
{
  treeMap(facet: CPY_C, productClass: HS, year: 2022, productLevel: fourDigit,
          locationLevel: country, location: "location-392", partner: "location-840") {
    ... on TreeMapProduct {
      product { shortName code productType }
      exportValue importValue
    }
  }
}
```

**Pattern 5: ECI time series**
```graphql
{
  countryYearRange(location: "location-76", minYear: 2010, maxYear: 2024) {
    location { shortName }
    eci { quantity year }
    eciRank { quantity year }
    exportValue { quantity year }
  }
}
```

**Pattern 6: Growth metrics (lookback)**
```graphql
{
  countryLookback(id: "location-404", yearRange: FiveYears, productClass: HS) {
    exportValueConstGrowthCagr
    eciRankChange eciChange
    diversityRankChange
    exportValueGrowthClassification
    gdpPcConstantCagrRegionalDifference
    largestContributingExportProduct { shortName code }
  }
}
```

**Pattern 7: Opportunity/feasibility analysis**
```graphql
{
  treeMap(facet: CPY_C, productClass: HS, year: 2024, productLevel: fourDigit,
          locationLevel: country, location: "location-404") {
    ... on TreeMapProduct {
      product { shortName code }
      exportValue rca distance opportunityGain
      normalizedPci normalizedDistance normalizedOpportunityGain
    }
  }
}
```

**Pattern 8: Product space with RCA**
```graphql
{
  productSpace(productClass: HS, year: 2024, location: "location-404") {
    product { shortName code productType }
    exportValue rca
    connections { strength productId }
  }
}
```

### 8.2 Country ID Lookup (Common Countries)

| Country | ISO3 | ISO Numeric | GraphQL ID |
|---|---|---|---|
| United States | USA | 840 | location-840 |
| China | CHN | 156 | location-156 |
| Germany | DEU | 276 | location-276 |
| Japan | JPN | 392 | location-392 |
| United Kingdom | GBR | 826 | location-826 |
| France | FRA | 250 | location-250 |
| India | IND | 356 | location-356 |
| Brazil | BRA | 76 | location-76 |
| Canada | CAN | 124 | location-124 |
| Australia | AUS | 36 | location-36 |
| South Korea | KOR | 410 | location-410 |
| Mexico | MEX | 484 | location-484 |
| Nigeria | NGA | 566 | location-566 |
| South Africa | ZAF | 710 | location-710 |
| Kenya | KEN | 404 | location-404 |
| Singapore | SGP | 702 | location-702 |
| Switzerland | CHE | 756 | location-756 |
| Netherlands | NLD | 528 | location-528 |
| Spain | ESP | 724 | location-724 |
| Turkey | TUR | 792 | location-792 |

Full list available via `allLocations(level: country)` query (252 total, 145 in country pages).

### 8.3 Product Internal ID Examples

These must be looked up via `allProducts` — they are NOT derivable from HS codes:

| HS Code | Product Name | Internal ID | GraphQL ID |
|---|---|---|---|
| 2709 | Petroleum oils, crude | 910 | product-HS-910 |
| 8703 | Cars | 1763 | product-HS-1763 |
| 8708 | Auto parts | 1768 | product-HS-1768 |
| 8542 | Electronic integrated circuits | 1731 | product-HS-1731 |

### 8.4 Full Eval Questions JSON Reference

The eval questions are in `evaluation/eval_questions.json`. There are 60 questions across 9 categories:

| Category | Count | Difficulty Range |
|---|---|---|
| Total Export Values | 3 | easy |
| Sectoral Export Composition | 12 | easy-hard |
| Trade Partners & Market Position | 8 | easy-hard |
| Growth & Performance | 7 | easy-hard |
| Economic Complexity | 8 | medium-hard |
| Diversification Strategies | 5 | hard |
| Edge Cases | 8 | easy-hard |
| Out-of-Scope Refusals | 4 | easy-medium |
| Data Availability Boundaries | 4 | medium-hard |

### 8.5 Files to Create/Modify

| File | Action | Description |
|---|---|---|
| `src/graphql_client.py` | **CREATE** | Async GraphQL HTTP client, ID resolvers, query builders, caching |
| `src/graphql_tool.py` | **CREATE** | LangGraph tool definition for GraphQL queries |
| `src/generate_query.py` | **MODIFY** | Add GraphQL tool to agent, update system prompt, add routing |
| `src/state.py` | **MODIFY** | Add GraphQL pipeline state fields |
| `src/text_to_sql.py` | **MODIFY** | Update streaming to handle GraphQL pipeline events |
| `src/tests/test_graphql_client.py` | **CREATE** | Unit tests for GraphQL client |
| `src/tests/test_graphql_tool.py` | **CREATE** | Integration tests for GraphQL tool |
| `src/tests/test_pipeline_nodes.py` | **MODIFY** | Add tests for GraphQL pipeline nodes |
| `src/tests/test_agent_trajectory.py` | **MODIFY** | Add trajectories testing tool routing |
| `evaluation/eval_questions.json` | **MODIFY** | Add GraphQL-specific questions, routing metadata |
| `evaluation/run_eval.py` | **MODIFY** | Track tool usage per question |
| `frontend/src/hooks/use-chat-stream.ts` | **MODIFY** | Handle new GraphQL pipeline events |
