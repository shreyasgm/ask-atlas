# Backend Redesign Analysis: Hybrid GraphQL + SQL Architecture with Atlas Links

> **Date:** 2026-02-21
> **Status:** Analysis complete, pending review
> **Related:** GitHub issue #42, `evaluation/hybrid_backend_analysis.md`

---

## Table of Contents

1. [Current State Assessment](#1-current-state-assessment)
2. [Design Goals](#2-design-goals)
3. [The New Staging GraphQL API](#3-the-new-staging-graphql-api)
4. [Core Architectural Idea: Two-Tool ReAct Agent](#4-core-architectural-idea-two-tool-react-agent)
5. [The Two Tools](#5-the-two-tools)
6. [Graph Structure](#6-graph-structure)
7. [GraphQL Pipeline Design](#7-graphql-pipeline-design)
8. [SQL Pipeline (Existing, Minor Changes)](#8-sql-pipeline-existing-minor-changes)
9. [Agent System Prompt Design](#9-agent-system-prompt-design)
10. [Atlas Link Generation](#10-atlas-link-generation)
11. [State Schema Changes](#11-state-schema-changes)
12. [Streaming and Frontend](#12-streaming-and-frontend)
13. [Caching Architecture](#13-caching-architecture)
14. [Error Handling and Fallback](#14-error-handling-and-fallback)
15. [Worked Examples](#15-worked-examples)
16. [Design Decisions and Alternatives Considered](#16-design-decisions-and-alternatives-considered)
17. [Implementation Phases](#17-implementation-phases)
18. [What This Means for Atlas Decoupling](#18-what-this-means-for-atlas-decoupling)

---

## 1. Current State Assessment

The current backend is a **single-tool ReAct agent** with a linear 8-node SQL pipeline:

```
START → agent → extract_tool_question → extract_products → lookup_codes
              → get_table_info → generate_sql → validate_sql
              → [valid?] → execute_sql → format_results → agent → END
```

Every question, regardless of complexity, goes through the same expensive pipeline: 2-4 LLM calls (agent reasoning, product extraction, code lookup, SQL generation), database metadata reflection, SQL generation, validation, and execution. A simple question like "What are Kenya's total exports?" takes the same path as "Compare the export complexity growth of Indonesia vs Vietnam over the last decade."

### Key files

| File | Purpose |
|---|---|
| `src/generate_query.py` | LangGraph graph construction, all pipeline nodes, SQL generation chain |
| `src/state.py` | `AtlasAgentState` TypedDict |
| `src/text_to_sql.py` | `AtlasTextToSQL` async factory class (streaming, answer API) |
| `src/product_and_schema_lookup.py` | Product/schema extraction and code lookup LLM chains |
| `src/sql_validation.py` | Pre-execution SQL validation (sqlglot-based) |
| `src/sql_multiple_schemas.py` | Multi-schema SQLAlchemy database wrapper |
| `src/api.py` | FastAPI endpoints (SSE streaming, chat, threads) |
| `src/config.py` | Settings (pydantic-settings) + `create_llm()` factory |

### What the analysis in `hybrid_backend_analysis.md` showed

- **~62% of eval questions** (37 of 60) can be answered by GraphQL APIs alone or with richer data.
- These questions burn $0.01-0.05 in LLM tokens and take 3-8s, but could be answered in 200-500ms at zero LLM cost via a deterministic GraphQL call.
- **~25% of questions** (15 of 60) require SQL for complex analytical queries.
- The production GraphQL API provides **~12 derived metrics not in the database** (policy recommendations, diversification grades, etc.). The staging API does not have these but fixes all broken endpoints and adds new capabilities. Together, both APIs cover more ground than either alone (see section 3).

---

## 2. Design Goals

### A. Dual data source with dynamic routing

The **agent itself** decides which tool to call for each sub-question. Complex questions get decomposed naturally, with some sub-questions going to GraphQL and others to SQL.

### B. Atlas website links for trust and verification

For GraphQL-answerable sub-questions, generate a link to the Atlas page where the user can verify the answer and explore further. This builds trust and drives traffic to the Atlas website.

### C. Complementarity with the Atlas of Economic Complexity

By referencing the Atlas website for simple data lookups, we position this tool as a complement to (not a competitor of) the Atlas, making it more acceptable to the Atlas software development team.

### D. Decoupling from Atlas DB for simple questions

For questions that GraphQL can handle, no database is needed at all. This enables a lightweight deployment mode and reduces infrastructure costs.

---

## 3. Two Complementary GraphQL APIs

Our backend has access to **two** GraphQL APIs that complement each other. The staging API is preferred for most queries (more capable, all endpoints working), while the production API remains available as a fallback — especially for the ~12 derived narrative metrics that only it provides.

### Endpoints

**Staging API** (preferred for new development):
```
POST http://staging.atlas.growthlab-dev.com/api/graphql
Content-Type: application/json
```
This is an internal Growth Lab API. It is data-oriented (raw tables), has all endpoints working, and offers additional capabilities like data dictionaries, group-level trade, 6-digit products, HS22, and data through 2024.

**Production API** (public, always available as fallback):
```
POST https://atlas.hks.harvard.edu/api/countries/graphql
Content-Type: application/json
```
This is the main public-facing API that serves the Atlas website. It is page-oriented (pre-computed derived types) and uniquely provides `countryProfile` with 46 derived fields including narrative classifications. However, it has ~8 broken endpoints (see hybrid_backend_analysis.md for details).

### Rate Limiting and Usage Guidelines (IMPORTANT)

The Atlas website publishes an `llms.txt` file with official guidance for automated/LLM access. The key rules (which apply equally to all three endpoints — production Explore `/api/graphql`, production Country Pages `/api/countries/graphql`, and staging):

> - **Rate limit: ≤ 120 requests per minute** (= 2 req/sec). All GraphQL client code must implement rate limiting (e.g., via `asyncio.Lock` + minimum delay between requests).
> - **Include a `User-Agent` header** identifying our system (e.g., `User-Agent: ask-atlas/1.0`).
> - **Prefer small, targeted queries** — request only the fields you need. Do not fetch all subfields recursively or run exhaustive introspection queries.
> - **Cache and reuse results** when possible (see section 13).
> - No authentication is required.

This means:
- No rapid parallel GraphQL calls — requests must be serialized or throttled
- The agent should prefer fewer, broader queries over many narrow ones
- Caching is critical to minimize API load (see section 13)
- Every HTTP request must include a `User-Agent` header

### How the Two APIs Differ

The **production API** is **high-level and page-oriented** — it has pre-computed derived types like `countryProfile` with 46 fields including narrative classifications (diversification grades, policy recommendations, structural transformation). It was designed to serve the Atlas website's specific page layouts.

The **staging API** is **low-level and data-oriented** — it exposes the raw data tables directly (`countryYear`, `countryProductYear`, `countryCountryYear`, etc.) with flexible filtering via `yearMin`/`yearMax`, `countryId`, `productId`. There are no pre-computed narrative fields.

### Metrics Only Available on the Production API

The production API's `countryProfile` query provides ~12 derived narrative metrics that have no equivalent on the staging API:

| Metric | Description |
|---|---|
| `diversificationGrade` | A+ through D- grade |
| `policyRecommendation` | ParsimoniousIndustrial, StrategicBets, LightTouch, TechFrontier |
| `structuralTransformationStep` | NotStarted through Completed |
| `structuralTransformationDirection` | risen, fallen, stagnated |
| `growthProjectionClassification` | rapid, moderate, slow |
| `growthProjectionRelativeToIncome` | More, Less, Same, etc. |
| `growthProjectionPercentileClassification` | TopDecile through BottomHalf |
| `coiClassification` | low, medium, high |
| `marketShareMainSector` / `Direction` | Main export sector trend |
| `comparisonLocations` | Peer country recommendations |
| `newProductsComments` | Narrative interpretations |
| `exportValueGrowthClassification` | Troubling, Mixed, Static, Promising |
| `eciNatResourcesGdpControlled` | ECI controlled for natural resources |

Also production-only: `countryLookback` (pre-computed CAGR, ECI changes, regional comparisons) and `newProductsComparisonCountries` (peer comparison).

**Note:** The raw numeric `growthProj` field IS available on the staging API's `countryYear`, so the growth projection number itself is accessible on both — just not its narrative classification.

### Additional Capabilities on the Staging API

These endpoints either fix broken production-API queries or provide entirely new functionality:

**Fixed endpoints** (broken on production, working on staging):

| Broken Production Endpoint | Staging Equivalent |
|---|---|
| `treeMap(facet: CPY_P)` — null locations | `countryProductYear(productId: X)` without `countryId` returns all countries |
| `productYearRange` — missing arg | `productYear(yearMin, yearMax)` |
| `allCountryYearRange` — `'hs_coi'` error | `countryYear(yearMin, yearMax)` without `countryId` |
| `group` / `allGroups` — missing model | `locationGroup(groupType)` works for all 9 group types |
| `manyCountryProductYear` — two bugs | `countryProductYear` with flexible args |
| SITC treeMap — unimplemented | `countryProductYear(productClass: SITC)` works |
| Product `level` field — enum mismatch | `productLevel` is now an integer (1, 2, 4, 6) |

**New capabilities** (not available on production at all):

| Capability | Query | Notes |
|---|---|---|
| **Data dictionary** | `downloadsTable` | 70 tables with full column definitions, types, descriptions |
| **Classification crosswalks** | `conversionWeights`, `conversionPath` | Maps codes across HS92→HS12→HS22→SITC |
| **Group-level trade** | `groupYear`, `groupGroupProductYear` | Continental/regional/trade-bloc aggregates |
| **Country-to-group trade** | `countryGroupProductYear` | e.g., Kenya exports to North America |
| **Group-to-country trade** | `groupCountryProductYear` | e.g., African exports to USA |
| **6-digit products** | `productLevel: 6` | Production API limited to 4-digit |
| **HS 2022 classification** | `productClass: HS22` | Entirely new classification |
| **HS 2012 as explicit class** | `productClass: HS12` | Was ambiguous "HS" on production |
| **Data through 2024** | All queries | Production API data stops at 2022 |
| **Country data flags** | `dataFlags` | 21 flags for data eligibility |
| **Bilateral reported values** | `countryCountryYear` | Both adjusted AND as-reported-to-Comtrade |
| **Constant-dollar GDP** | `countryYear` | `gdpConst`, `gdpPppConst`, `gdppcConst` |
| **Green product flag** | Product types | `greenProduct: true/false` on products |
| **Natural resource flag** | Product types | `naturalResource: true/false` on products |
| **Product status** | `countryProductYear` | `productStatus: absent/lost/new/present` |
| **Spanish names** | Location/Product types | `nameEs`, `nameShortEs` |

### Staging API: ID Format

| Entity | Production Format | Staging Input | Staging Output |
|---|---|---|---|
| Country | `"location-404"` (string) | `countryId: 404` (integer) | `"country-404"` (string) |
| Product | `"product-HS-910"` (string) | `productId: 910` (integer) | `"product-HS92-910"` (string) |
| Group | N/A (broken on production) | `groupType: "continent"` + filter | `"group-continent-AF"` etc. |

### Staging API: Year Handling

The production API has separate queries for single year vs. range (`countryYear` vs `countryYearRange`). The staging API unifies them: all queries accept optional `yearMin`/`yearMax` parameters. Omitting both returns the latest year.

### Staging API: Product Classification

| Production API | Staging API |
|---|---|
| `productClass: HS` (ambiguous) | `productClass: HS92`, `HS12`, or `HS22` (explicit) |
| `productClass: SITC` (broken) | `productClass: SITC` (working) |
| `productLevel: fourDigit` (enum) | `productLevel: 4` (integer) |

### Impact on Eval Question Routing

With both APIs available:
- Questions needing **derived narrative metrics** (diversification grades, policy recs, structural transformation) → use the **production API** (`countryProfile`)
- Questions that hit **broken production endpoints** (CPY_P, groups, SITC, year ranges) → use the **staging API** (all working)
- Questions about **new capabilities** (6-digit products, HS22, group-level trade, data dictionary) → use the **staging API** (production doesn't support these)
- **Standard lookups** (country trade data, bilateral trade, product complexity) → prefer the **staging API** (more flexible, data through 2024), with production as fallback
- **Net effect:** The staging API handles ~60-70% of questions. The production API uniquely covers the ~12 narrative classification metrics. Together they provide comprehensive coverage.

### Staging API: Complete Query Type Reference

| Query | Arguments | Key Fields | Notes |
|---|---|---|---|
| `countryYear` | `countryId`, `yearMin`, `yearMax`, `productClass` | exportValue, importValue, gdp, gdppc, eci, eciRank, coi, coiRank, growthProj, population | Equivalent to production's countryYear + countryYearRange + allCountryYear + allCountryYearRange |
| `countryProductYear` | `countryId`, `productId`, `productClass`, `productLevel`, `yearMin`, `yearMax` | exportValue, importValue, exportRca, globalMarketShare, distance, cog, pci, normalizedPci, normalizedCog, normalizedDistance, productStatus, isNew | Equivalent to production's treeMap(CPY_C), treeMap(CPY_P), allCountryProductYear, newProducts |
| `countryCountryYear` | `countryId`, `partnerId`, `yearMin`, `yearMax` | exportValue, importValue, exportValueReported, importValueReported | Equivalent to production's treeMap(CCY_C) |
| `countryCountryProductYear` | `countryId`, `partnerId`, `productClass`, `productLevel`, `yearMin`, `yearMax` | exportValue, importValue | Equivalent to production's treeMap(CPY_C + partner) |
| `productYear` | `productId`, `productClass`, `productLevel`, `yearMin`, `yearMax` | pci, globalExportValue, complexityLevel | Equivalent to production's productYear + allProductYear + productYearRange |
| `productProduct` | `productClass`, `productLevel`, `yearMin`, `yearMax` | strength (proximity) | Equivalent to production's productSpace connections |
| `locationCountry` | (no required args) | id, iso3Code, iso2Code, nameEn, nameShortEn, incomeLevel, inRankings, inCp | Equivalent to production's allLocations + location |
| `locationGroup` | `groupType` | id, name, type, members, GDP/trade aggregates, CAGR fields | Equivalent to production's group + allGroups (broken on production) |
| `groupYear` | `groupType`, `yearMin`, `yearMax` | population, gdp, exportValue, importValue | Staging-only — group-level time series |
| `groupGroupProductYear` | `groupId`, `partnerGroupId`, ... | exportValue, importValue | Staging-only — group-to-group bilateral |
| `countryGroupProductYear` | `countryId`, `groupId`, ... | exportValue, importValue | Staging-only — country-to-group |
| `groupCountryProductYear` | `groupId`, `countryId`, ... | exportValue, importValue | Staging-only — group-to-country |
| `productHs92` | `productLevel` | id, code, nameEn, parent, topParent, greenProduct, naturalResource | Equivalent to production's allProducts(HS) |
| `productHs12` | `productLevel` | Same as above | Staging-only — explicit HS12 dictionary |
| `productHs22` | `productLevel` | Same as above | Staging-only — HS22 classification |
| `productSitc` | `productLevel` | Same as above | Equivalent to production's allProducts(SITC) |
| `downloadsTable` | (none) | 70 tables with full column definitions | Staging-only — data dictionary |
| `dataAvailability` | (none) | productClassification, yearMin, yearMax | Staging-only — year ranges |
| `dataFlags` | `countryId` | 21 boolean/numeric eligibility flags | Staging-only |
| `conversionWeights` | source/target class, productLevel | Maps product codes across classifications | Staging-only |
| `conversionPath` | source/target class | Step-by-step conversion path | Staging-only |
| `countryYearThresholds` | `countryId`, year | Descriptive statistics for complexity variables | Staging-only |

---

## 4. Core Architectural Idea: Two-Tool ReAct Agent

### Why dynamic agent routing, not a static router

Many real questions need BOTH data sources. Consider:

> "What is Kenya's ECI and how does it compare to other East African countries?"

- "Kenya's ECI" → GraphQL (`countryYear(countryId: 404)`) — fast, deterministic
- "compare to other East African countries" → SQL (needs regional group tables with cross-country comparison) OR GraphQL (`locationGroup` + multiple `countryYear` calls — but rate-limited to 1-2/sec)

A static router must pick one path. A dynamic agent can call GraphQL for the first part, then SQL for the second, and synthesize the results.

### The approach: give the agent two tools

The agent keeps its existing ReAct loop but gains access to **two tools** instead of one. The agent itself decides which tool to call for each sub-question based on its understanding of what each tool is good at. The agent system prompt describes the capabilities and limitations of each tool.

This is the simplest possible change to the existing architecture: we add a second tool and update the routing function to dispatch to the appropriate pipeline based on which tool was called.

### How it works at a high level

```
Agent receives question
  → Agent thinks: "I need Kenya's export data (GraphQL has this)"
  → Agent calls atlas_graphql(query_type="country_year", country="Kenya", year=2022)
  → GraphQL pipeline runs (~300ms), returns result + Atlas link
  → Agent thinks: "Now I need East African comparison (SQL is better for this)"
  → Agent calls atlas_sql(question="Compare Kenya's ECI to other East African countries")
  → SQL pipeline runs (~5s), returns result
  → Agent synthesizes both results into final answer with Atlas link
```

---

## 5. The Two Tools

### Tool 1: `atlas_graphql` — Structured GraphQL lookup

This tool has a **structured Pydantic schema** with explicit parameters. The agent fills in the parameters as part of its tool call, making the entire GraphQL pipeline deterministic (zero LLM calls after the agent's decision).

```python
class AtlasGraphQLInput(BaseModel):
    """Query the Atlas of Economic Complexity GraphQL API for structured data lookups.

    Use this tool for:
    - Country-level metrics for a specific year or year range (GDP, exports, imports,
      ECI, COI, growth projection, population)
    - Export baskets (what products does country X export, with RCA, PCI, distance,
      market share)
    - Trade partners (who does country X trade with, aggregate by partner)
    - Bilateral trade (what does country A export to country B, by product)
    - Product-level global data (PCI, global export value)
    - "Which countries export product X?" (with RCA, market share per country)
    - Country-to-group or group-to-country trade flows
    - Group-level aggregates (continental trade, regional GDP)
    - Product classification lookups and crosswalks

    Do NOT use this tool for:
    - Complex analytical queries requiring JOINs, CTEs, or window functions
    - Custom derived metrics or calculations not in the API
    - Questions requiring more than ~5 API calls (use SQL instead for efficiency)
    - Diversification grades, policy recommendations, structural transformation
      status (these are NOT available in the API)

    IMPORTANT: The API is rate-limited to 120 requests per minute (2 req/sec).
    Prefer broader queries over many narrow ones. Request only the fields you need.
    """
    query_type: Literal[
        "country_year",                # Country metrics (GDP, exports, ECI, COI, growth proj)
        "country_product_year",        # Country's export basket with RCA, PCI, distance, market share
        "product_exporters",           # Which countries export a product (CPY_P equivalent)
        "country_country_year",        # Trade partners (aggregate bilateral)
        "country_country_product_year",# Bilateral trade by product
        "product_year",                # Product-level global data (PCI, global value)
        "product_product",             # Product proximity/relatedness
        "location_country",            # Country metadata (ISO codes, names, eligibility)
        "location_group",              # Country groups (continent, trade bloc, income level)
        "group_year",                  # Group-level time series (GDP, trade, population)
        "country_group_product_year",  # Country-to-group trade
        "group_country_product_year",  # Group-to-country trade
        "product_dictionary",          # Product classification lookup (HS92, HS12, HS22, SITC)
        "conversion_weights",          # Product code crosswalk between classifications
        "data_availability",           # Year ranges by classification
        "data_flags",                  # Country data eligibility flags
        "downloads_table",             # Full data dictionary (70 tables with column definitions)
    ] = Field(description="The type of GraphQL query to execute")

    country: Optional[str] = Field(
        default=None,
        description="Country name or ISO3 code (e.g., 'Kenya', 'KEN', 'USA')"
    )
    partner_country: Optional[str] = Field(
        default=None,
        description="Partner country for bilateral trade queries"
    )
    product: Optional[str] = Field(
        default=None,
        description="Product name or HS code (e.g., 'crude oil', '2709', 'cars')"
    )
    year: Optional[int] = Field(
        default=None,
        description="Year (1962-2024 depending on classification). If omitted, returns latest."
    )
    min_year: Optional[int] = Field(
        default=None,
        description="Start year for range queries"
    )
    max_year: Optional[int] = Field(
        default=None,
        description="End year for range queries"
    )
    product_class: Literal["HS92", "HS12", "HS22", "SITC"] = Field(
        default="HS92",
        description="Product classification system"
    )
    product_level: Literal[1, 2, 4, 6] = Field(
        default=4,
        description="Product aggregation level (1=section, 2=two-digit, 4=four-digit, 6=six-digit)"
    )
    group_type: Optional[str] = Field(
        default=None,
        description="Group type for group queries (continent, subregion, trade, wdi_income_level, etc.)"
    )
```

**Key design choice:** The agent passes human-readable country/product names (e.g., "Kenya", "crude oil"), and the GraphQL pipeline resolves them to integer IDs (`countryId: 404`, `productId: 910`) using cached lookup tables. The agent never needs to know internal IDs.

### Tool 2: `atlas_sql` — Natural language SQL query

This tool keeps the existing interface: a natural language question that goes through the full SQL generation pipeline.

```python
class AtlasSQLInput(BaseModel):
    """Generate and execute SQL queries on the Atlas trade database.

    Use this tool for:
    - Complex analytical queries requiring JOINs, CTEs, window functions
    - Custom derived metrics (custom CAGR, weighted averages, market share changes)
    - Questions that would require many GraphQL API calls (>5)
    - Multi-step analytical queries
    - Anything the GraphQL tool cannot handle
    """
    question: str = Field(
        description="A natural language question about international trade data"
    )
```

### How the agent chooses

The agent is an LLM with access to both tool schemas. The tool descriptions (docstrings) explain when to use each. The agent's system prompt reinforces this with concrete guidance. The LLM's native tool-calling capability selects the appropriate tool.

This is the standard LangGraph/LangChain pattern: `llm.bind_tools([atlas_graphql_schema, atlas_sql_schema])`.

---

## 6. Graph Structure

### Detailed node and edge diagram

```
                              ┌────────────────────────────────────────────────────┐
                              │          GRAPHQL PIPELINE (~300-500ms)              │
                              │          Zero LLM calls, fully deterministic        │
                              │                                                    │
                              │  resolve_entities → build_graphql_query             │
                              │  → execute_graphql → format_graphql_results         │
                          ┌───┤                                                    │
                          │   └────────────────────────────────────────────────────┘
                          │
START → agent → [route] ──┤
                          │
                          │   ┌────────────────────────────────────────────────────┐
                          │   │            SQL PIPELINE (~3-8s)                     │
                          │   │            2-3 LLM calls                            │
                          └───┤                                                    │
                              │  extract_sql_question → extract_products            │
                              │  → lookup_codes → get_table_info → generate_sql     │
                              │  → validate_sql → [valid?] → execute_sql            │
                              │  → format_sql_results                               │
                              └────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
                                               (both paths) → agent → ... → END
```

### Routing function

```python
def route_after_agent(state: AtlasAgentState) -> str:
    last_msg = state["messages"][-1]

    if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
        return END

    if state.get("queries_executed", 0) >= max_uses:
        return "max_queries_exceeded"

    tool_name = last_msg.tool_calls[0]["name"]
    if tool_name == "atlas_graphql":
        return "resolve_entities"
    elif tool_name == "atlas_sql":
        return "extract_sql_question"

    return END
```

### Complete edge definitions

```python
builder.add_edge(START, "agent")

builder.add_conditional_edges("agent", route_after_agent, {
    "resolve_entities": "resolve_entities",
    "extract_sql_question": "extract_sql_question",
    "max_queries_exceeded": "max_queries_exceeded",
    END: END,
})

# GraphQL pipeline (linear, 4 nodes)
builder.add_edge("resolve_entities", "build_graphql_query")
builder.add_edge("build_graphql_query", "execute_graphql")
builder.add_edge("execute_graphql", "format_graphql_results")
builder.add_edge("format_graphql_results", "agent")

# SQL pipeline (existing, 8 nodes with one conditional)
builder.add_edge("extract_sql_question", "extract_products")
builder.add_edge("extract_products", "lookup_codes")
builder.add_edge("lookup_codes", "get_table_info")
builder.add_edge("get_table_info", "generate_sql")
builder.add_edge("generate_sql", "validate_sql")
builder.add_conditional_edges("validate_sql", route_after_validation, {
    "execute_sql": "execute_sql",
    "format_sql_results": "format_sql_results",
})
builder.add_edge("execute_sql", "format_sql_results")
builder.add_edge("format_sql_results", "agent")

builder.add_edge("max_queries_exceeded", "agent")
```

### Node count

- **Existing nodes (10):** agent, extract_sql_question, extract_products, lookup_codes, get_table_info, generate_sql, validate_sql, execute_sql, format_sql_results, max_queries_exceeded
- **New nodes (4):** resolve_entities, build_graphql_query, execute_graphql, format_graphql_results
- **Total: 14 nodes**

---

## 7. GraphQL Pipeline Design

The GraphQL pipeline is a 4-node linear chain. All nodes are deterministic (zero LLM calls). The entire pipeline runs in ~300-500ms.

### Node 1: `resolve_entities`

Maps human-readable country/product names from the tool call args to integer IDs used by the staging API.

**Entity resolver implementation:** At startup, the GraphQL client fetches `locationCountry` and `productHs92(productLevel: 4)` to build in-memory lookup tables:

- **Country lookup:** Matches by ISO3 code (exact), ISO2 code (exact), short name (fuzzy), long name (fuzzy). Returns integer `countryId` (e.g., `404` for Kenya).
- **Product lookup:** Matches by HS code (exact), short name (fuzzy). Returns integer `productId` (e.g., `910` for crude oil / code 2709).
- **Fuzzy matching:** Case-insensitive, handles common variants (e.g., "South Korea" → "Korea, Republic of").

### Node 2: `build_graphql_query`

Constructs the GraphQL query string from the resolved parameters. Each `query_type` maps to a template.

Example for `country_year`:
```graphql
{
  countryYear(countryId: 404, yearMin: 2022, yearMax: 2022, productClass: HS92) {
    countryId year
    exportValue importValue
    population gdp gdppc gdpConst gdppcConst
    eci eciRank eciFixed
    coi coiRank
    growthProj
  }
}
```

Example for `country_product_year` (export basket):
```graphql
{
  countryProductYear(countryId: 404, productClass: HS92, productLevel: 4,
                     yearMin: 2022, yearMax: 2022) {
    productId year
    exportValue importValue
    exportRca globalMarketShare
    distance cog pci
    normalizedPci normalizedCog normalizedDistance
    productStatus isNew
  }
}
```

Example for `product_exporters` (which countries export product X):
```graphql
{
  countryProductYear(productId: 910, productClass: HS92, productLevel: 4,
                     yearMin: 2022, yearMax: 2022) {
    countryId year
    exportValue exportRca globalMarketShare
  }
}
```

### Node 3: `execute_graphql`

Makes the async HTTP POST to `http://staging.atlas.growthlab-dev.com/api/graphql`.

**Rate limiting:** The execute node must enforce the ≤ 120 req/min (2 req/sec) limit per the Atlas `llms.txt`. Implementation:
```python
class RateLimitedGraphQLClient:
    def __init__(self, endpoint: str, max_requests_per_second: float = 2.0):
        self.endpoint = endpoint
        self.min_interval = 1.0 / max_requests_per_second
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()
        self._headers = {
            "Content-Type": "application/json",
            "User-Agent": "ask-atlas/1.0",  # Required by Atlas llms.txt
        }

    async def execute(self, query: str, variables: dict = None) -> dict:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_request_time = time.monotonic()

        async with httpx.AsyncClient(headers=self._headers) as client:
            response = await client.post(
                self.endpoint,
                json={"query": query, "variables": variables or {}},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()
```

### Node 4: `format_graphql_results`

Formats the raw JSON response into a readable string for the agent, generates the Atlas link, and creates a `ToolMessage`. Also enriches the response by joining product/country names from cached lookup tables (since the API returns IDs, not names).

---

## 8. SQL Pipeline (Existing, Minor Changes)

The existing SQL pipeline is preserved almost entirely. Changes:

1. **Rename** `extract_tool_question` → `extract_sql_question` and `format_results` → `format_sql_results`
2. **`extract_sql_question`** now extracts from `atlas_sql` tool calls instead of `query_tool`
3. **All other nodes** are completely unchanged

---

## 9. Agent System Prompt Design

### Proposed system prompt structure

```
You are Ask-Atlas — an expert agent designed to answer complex questions about
international trade data. You have access to two tools:

## Tool 1: atlas_graphql — Atlas API Lookup (fast, structured data)

Use this tool for structured data lookups from the Atlas of Economic Complexity API.
This tool is FAST (~300ms, no SQL generation needed) and returns data directly from
the Atlas database.

Good for:
- Country-level metrics: GDP, exports, imports, ECI, COI, growth projection, population
- Export baskets: what products does a country export (with RCA, PCI, distance, market share)
- Trade partners: who does a country trade with
- Bilateral trade: what does country A export to country B, by product
- "Which countries export product X?" with RCA and market share per country
- Product-level global data: PCI, global export value
- Group-level trade: continental, regional, trade bloc aggregates
- Country-to-group and group-to-country trade flows
- Product classification lookups and crosswalks (HS92, HS12, HS22, SITC)
- Time series (specify min_year and max_year)
- Data through 2024, including 6-digit product granularity

NOT available via this tool (use atlas_sql instead):
- Diversification grades, policy recommendations, structural transformation status
- Complex multi-step calculations, custom CAGR, weighted averages
- Questions requiring many API calls (>5) — SQL is more efficient

IMPORTANT: The API allows at most 120 requests per minute (2 req/sec). Prefer broader
queries (e.g., all products for a country) over many narrow ones.

## Tool 2: atlas_sql — SQL Query Generation (powerful, flexible)
... [same as before] ...

## Atlas Links

When the atlas_graphql tool returns results, it includes a link to the
relevant page on the Atlas of Economic Complexity website. ALWAYS include
these links in your response using markdown format.
```

---

## 10. Atlas Link Generation

Atlas links still work the same way — they're based on the Atlas website URL structure, not the API endpoint.

| Query Type | Atlas URL Pattern | Example |
|---|---|---|
| `country_year` | `/countries/{iso_numeric}/summary` | `/countries/404/summary` (Kenya) |
| `country_product_year` | `/countries/{iso_numeric}/export-basket` | Export basket visualization |
| `product_exporters` | `/products/{hs_code}` | Product page with exporters |
| `country_country_year` | `/countries/{iso_numeric}/partners` | Trade partners page |
| `country_country_product_year` | `/countries/{iso_numeric}/partners?partner={partner}` | Bilateral trade |
| `product_year` | `/products/{hs_code}` | Product details page |
| `location_group` | `/rankings/country` | Rankings page |
| `group_year` | `/rankings/country` | Rankings page |

The Atlas website URL uses `atlas.hks.harvard.edu` (the production site), even though the API uses the staging endpoint.

---

## 11. State Schema Changes

```python
class AtlasAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    queries_executed: int
    last_error: str
    retry_count: int

    # GraphQL pipeline state (NEW)
    graphql_params: Optional[dict]           # Parsed tool call args
    graphql_resolved: Optional[dict]         # Params with resolved integer IDs
    graphql_query: Optional[str]             # The GraphQL query string
    graphql_raw_response: Optional[dict]     # Raw JSON response
    graphql_error: Optional[str]             # Error message if query failed
    atlas_link: Optional[str]               # Generated Atlas website URL

    # SQL pipeline state (existing, unchanged)
    pipeline_question: str
    pipeline_products: Optional[SchemasAndProductsFound]
    pipeline_codes: str
    pipeline_table_info: str
    pipeline_sql: str
    pipeline_result: str
    pipeline_result_columns: list[str]
    pipeline_result_rows: list[list]
    pipeline_execution_time_ms: int

    # User overrides (existing, unchanged)
    override_schema: Optional[str]
    override_direction: Optional[str]
    override_mode: Optional[str]
```

---

## 12. Streaming and Frontend

### New SSE events for the GraphQL pipeline

| SSE Event | When | Data |
|---|---|---|
| `tool_call` | Agent calls `atlas_graphql` | `{"tool_name": "atlas_graphql", "args": {...}}` |
| `node_start` | Each GraphQL pipeline node begins | `{"node": "...", "label": "..."}` |
| `pipeline_state` | GraphQL complete | `{"stage": "execute_graphql", "graphql_query": "...", "atlas_link": "..."}` |
| `tool_output` | GraphQL result returned to agent | `{"content": "...", "atlas_link": "..."}` |

### Dynamic pipeline stepper

**GraphQL sequence:** Resolving entities → Building API query → Querying Atlas API → Formatting results

**SQL sequence (existing):** Extracting question → Identifying products → Looking up codes → Loading metadata → Generating SQL → Validating SQL → Executing query → Formatting results

---

## 13. Caching Architecture

Caching is especially important given the rate limit of 120 req/min (2 req/sec).

### Level 1: Entity lookup tables (warm at startup, refresh daily)

- **`locationCountry`** → country name/ISO → integer countryId mapping (~252 entries)
- **`productHs92(productLevel: 4)`** → HS code/name → integer productId mapping (~1,248 entries)
- Stored in memory. Built at application startup (2 API calls). Refreshed on a 24-hour timer.

### Level 2: Query response cache (TTL-based)

- **`countryYear`** responses — TTL 1 hour (keyed by countryId+year+productClass)
- **`countryProductYear`** responses — TTL 1 hour (keyed by countryId+productClass+productLevel+year)
- **`locationGroup`** — TTL 24 hours
- Use `cachetools.TTLCache` with configurable max size.

### Level 3: Rate limiter

- Global rate limiter: max 2 requests/second (shared across all concurrent users)
- Implementation: `asyncio.Lock` + minimum delay between requests
- Cached responses bypass the rate limiter entirely

---

## 14. Error Handling and Fallback

When the GraphQL pipeline fails, the error is returned to the agent as a `ToolMessage`. The agent naturally decides what to do:

```
Agent: calls atlas_graphql(query_type="country_year", country="Kenya", year=2022)
GraphQL: → HTTP error or empty result
ToolMessage: "The Atlas API returned an error. You may want to try atlas_sql."
Agent: → decides to retry with atlas_sql
```

| Error | ToolMessage | Agent likely action |
|---|---|---|
| Country not found | "Could not resolve country: 'Wakanda'" | Inform user |
| Network timeout | "API request timed out" | Retry or fall back to SQL |
| Empty result | "No data found for Kenya in 1960" | Inform user about data limits |
| Rate limited | "API rate limit reached, please wait" | Wait and retry, or use SQL |

---

## 15. Worked Examples

### Example 1: Simple question (GraphQL only)

**User:** "What are Kenya's total exports in 2022?"

```
Turn 1: Agent → atlas_graphql(query_type="country_year", country="Kenya", year=2022)
GraphQL pipeline (~300ms):
  resolve: "Kenya" → countryId=404
  build: countryYear(countryId:404, yearMin:2022, yearMax:2022, productClass:HS92)
  execute: HTTP POST → {exportValue: 15180082242, importValue: 28809922029, ...}
  format: "Kenya 2022: exports $15.2B, imports $28.8B, ECI -0.457, ..."
         + link: https://atlas.hks.harvard.edu/countries/404/summary

Turn 2: Agent synthesizes with Atlas link → END
```

**Total: ~1s. LLM calls: 2. API calls: 1. SQL calls: 0.**

### Example 2: "Which countries export the most tea?" (GraphQL — broken on production API, works on staging)

```
Turn 1: Agent → atlas_graphql(query_type="product_exporters",
         product="tea", product_class="HS92", product_level=4, year=2022)
GraphQL pipeline (~400ms):
  resolve: "tea" → productId=727
  build: countryProductYear(productId:727, productClass:HS92, productLevel:4,
         yearMin:2022, yearMax:2022) → requesting countryId, exportValue, exportRca, globalMarketShare
  execute: → 226 countries returned
  format: top exporters by value + Atlas link

Turn 2: Agent synthesizes → END
```

**This requires the staging API (CPY_P is broken on production). It's a single GraphQL call on the staging API.**

### Example 3: Mixed GraphQL + SQL

**User:** "What is Kenya's ECI and how does it compare to other East African countries?"

```
Turn 1: Agent → atlas_graphql(query_type="country_year", country="Kenya", year=2022)
  → Returns ECI, COI, growth projection + Atlas link

Turn 2: Agent → atlas_sql(question="Compare ECI values for Kenya, Tanzania, Uganda,
         Rwanda, Ethiopia, Burundi in the most recent year")
  → SQL pipeline generates and executes cross-country comparison

Turn 3: Agent synthesizes both results with Atlas link → END
```

### Example 4: Group-level trade (new capability)

**User:** "What are the total exports of African countries?"

```
Turn 1: Agent → atlas_graphql(query_type="group_year",
         group_type="continent", year=2022)
  → Returns GDP, population, trade for all continents
  → Agent extracts Africa's row

Turn 2: Agent synthesizes → END
```

**This requires the staging API (groups are broken on production). It's a single GraphQL call on the staging API.**

---

## 16. Design Decisions and Alternatives Considered

### Decision 1: Two-tool agent vs. static router

**Chosen: Two-tool agent.** The agent decides which tool to call per sub-question.

*Alternative: Static router node.* Rejected because many questions need BOTH data sources, and the agent naturally decomposes questions as part of its ReAct reasoning.

### Decision 2: Structured vs. natural-language GraphQL tool input

**Chosen: Structured Pydantic schema** for the GraphQL tool.

*Alternative: Natural language question with internal LLM classification.* Rejected because the whole point of the GraphQL path is to be fast — adding an LLM call defeats the purpose.

### Decision 3: Rate limiting strategy

**Chosen: Global rate limiter at 2 req/sec** (per Atlas `llms.txt`: ≤ 120 req/min) with aggressive caching.

This means:
- The agent should prefer fewer, broader queries (e.g., get all products for a country in one call, then filter client-side)
- Cached responses bypass the rate limiter
- For questions requiring many GraphQL calls (>5), the agent should use SQL instead
- The system prompt explicitly tells the agent about the rate limit

### Decision 4: Query limits

**Chosen: Single `queries_executed` counter for both tools, raised to ~10.** GraphQL queries are fast but rate-limited; SQL queries are slow but unlimited. A combined counter of ~10 gives room for mixed workflows.

### Decision 5: Atlas links for SQL answers

**Chosen: Atlas links only for GraphQL answers (Phase 1).** For GraphQL queries, the link is deterministic (query type → page type). For SQL queries, the mapping is fuzzy. Phase 2 can add SQL link generation.

---

## 17. Implementation Phases

### Phase 1: GraphQL Client Module

**New file: `src/graphql_client.py`**

- `RateLimitedGraphQLClient` — async httpx client with 2 req/sec throttle + `User-Agent: ask-atlas/1.0` header
- `EntityResolver` — country/product name → integer ID lookup tables
- Query builders for all 17 query types
- Response parsers and name enrichment (join IDs with cached names)
- TTL cache for responses
- Atlas link generation function

**New file: `src/tests/test_graphql_client.py`** — unit tests with mocked HTTP

### Phase 2: GraphQL Pipeline Nodes + Graph Update

**Modify: `src/generate_query.py`** — add GraphQL tool schema, 4 new nodes, update routing, update agent prompt

**Modify: `src/state.py`** — add GraphQL pipeline state fields

**New file: `src/tests/test_graphql_pipeline.py`** — pipeline and routing tests

### Phase 3: Streaming + Frontend

**Modify: `src/text_to_sql.py`** — handle new GraphQL pipeline streaming events

**Modify: frontend** — dynamic pipeline stepper, Atlas link display

### Phase 4: Evaluation + Tuning

**Modify: `evaluation/`** — routing metadata on eval questions, track tool usage

---

## 18. What This Means for Atlas Decoupling

With the GraphQL path in place, the system can operate without any database:
- **GraphQL-routable questions (~50-60%)** — work perfectly
- **SQL-routable questions** — agent informs user that complex analysis requires the database

The staging API's expanded capabilities (groups, SITC, 6-digit, CPY_P) mean that MORE questions can be handled without SQL, even though the derived narrative metrics require falling back to the production API.

By generating Atlas links for every GraphQL-answered question, users are funneled to the Atlas website for verification and exploration. The Atlas team sees this tool as driving traffic to their site.

---

## Appendix A: Files to Create/Modify

| File | Action | Description |
|---|---|---|
| `src/graphql_client.py` | **CREATE** | Rate-limited async client, entity resolver, query builders, caching, Atlas links |
| `src/generate_query.py` | **MODIFY** | Add GraphQL tool schema, 4 nodes, routing, agent prompt |
| `src/state.py` | **MODIFY** | Add GraphQL pipeline state fields |
| `src/text_to_sql.py` | **MODIFY** | Handle GraphQL pipeline streaming events |
| `src/api.py` | **MODIFY** | Pass GraphQL client to agent factory |
| `src/tests/test_graphql_client.py` | **CREATE** | Unit tests for GraphQL client |
| `src/tests/test_graphql_pipeline.py` | **CREATE** | Pipeline and routing tests |
| `evaluation/eval_questions.json` | **MODIFY** | Add routing metadata, update for staging API capabilities |
| `evaluation/run_eval.py` | **MODIFY** | Track tool usage per question |
| `frontend/src/hooks/use-chat-stream.ts` | **MODIFY** | Handle new tool types and pipeline sequences |
| `frontend/src/components/chat/pipeline-stepper.tsx` | **MODIFY** | Dynamic pipeline display |
