# Backend Redesign Analysis: Hybrid GraphQL + SQL Architecture with Atlas Links

> **Date:** 2026-02-21
> **Status:** Analysis complete, pending review
> **Related:** GitHub issue #42, `evaluation/hybrid_backend_analysis.md`

---

## Table of Contents

1. [Current State Assessment](#1-current-state-assessment)
2. [Design Goals](#2-design-goals)
3. [Core Architectural Idea: Two-Tool ReAct Agent](#3-core-architectural-idea-two-tool-react-agent)
4. [The Two Tools](#4-the-two-tools)
5. [Graph Structure](#5-graph-structure)
6. [GraphQL Pipeline Design](#6-graphql-pipeline-design)
7. [SQL Pipeline (Existing, Minor Changes)](#7-sql-pipeline-existing-minor-changes)
8. [Agent System Prompt Design](#8-agent-system-prompt-design)
9. [Atlas Link Generation](#9-atlas-link-generation)
10. [State Schema Changes](#10-state-schema-changes)
11. [Streaming and Frontend](#11-streaming-and-frontend)
12. [Caching Architecture](#12-caching-architecture)
13. [Error Handling and Fallback](#13-error-handling-and-fallback)
14. [Worked Examples](#14-worked-examples)
15. [Design Decisions and Alternatives Considered](#15-design-decisions-and-alternatives-considered)
16. [Implementation Phases](#16-implementation-phases)
17. [What This Means for Atlas Decoupling](#17-what-this-means-for-atlas-decoupling)

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

- **~62% of eval questions** (37 of 60) can be answered by the GraphQL API alone or with richer data.
- These questions burn $0.01-0.05 in LLM tokens and take 3-8s, but could be answered in 200-500ms at zero LLM cost via a deterministic GraphQL call.
- **~25% of questions** (15 of 60) require SQL for complex analytical queries (cross-country comparisons, regional aggregations, custom time ranges, derived calculations).
- **~13% of questions** are out-of-scope or need no data access.
- The GraphQL API also provides **~12 derived metrics not in the database** (policy recommendations, diversification grades, structural transformation status, growth projections, etc.).

---

## 2. Design Goals

### A. Dual data source with dynamic routing

Rather than a static router that sends the entire question down one path (GraphQL OR SQL), the **agent itself** decides which tool to call for each sub-question. Complex questions get decomposed naturally, with some sub-questions going to GraphQL and others to SQL.

### B. Atlas website links for trust and verification

For GraphQL-answerable sub-questions, generate a link to the Atlas page where the user can verify the answer and explore further. This builds trust and drives traffic to the Atlas website.

### C. Complementarity with the Atlas of Economic Complexity

By referencing the Atlas website for simple data lookups, we position this tool as a complement to (not a competitor of) the Atlas, making it more acceptable to the Atlas software development team.

### D. Decoupling from Atlas DB for simple questions

For the ~62% of questions that GraphQL can handle, no database is needed at all. This enables a lightweight deployment mode and reduces infrastructure costs.

---

## 3. Core Architectural Idea: Two-Tool ReAct Agent

### Why dynamic agent routing, not a static router

The previous design proposed a dedicated `classify_query` router node that would send the entire question down one path. This has a fundamental limitation: **many real questions need BOTH data sources.** Consider:

> "What is Kenya's diversification grade and how does it compare to other East African countries?"

- "Kenya's diversification grade" → GraphQL (`countryProfile.diversificationGrade`), only available via GraphQL
- "compare to other East African countries" → SQL (needs regional group tables, cross-country comparison)

A static router must pick one path. A dynamic agent can call GraphQL for the first part, then SQL for the second, and synthesize the results.

### The approach: give the agent two tools

The agent keeps its existing ReAct loop but gains access to **two tools** instead of one. The agent itself decides which tool to call for each sub-question based on its understanding of what each tool is good at. The agent system prompt describes the capabilities and limitations of each tool, and the agent makes the routing decision as part of its natural reasoning.

This is the simplest possible change to the existing architecture: we add a second tool and update the routing function to dispatch to the appropriate pipeline based on which tool was called.

### How it works at a high level

```
Agent receives question
  → Agent thinks: "I need Kenya's diversification grade (GraphQL has this)"
  → Agent calls atlas_graphql(query_type="country_profile", country="Kenya")
  → GraphQL pipeline runs (~200ms), returns result + Atlas link
  → Agent thinks: "Now I need East African comparison (need SQL for regional data)"
  → Agent calls atlas_sql(question="Compare Kenya's diversity to other East African countries")
  → SQL pipeline runs (~5s), returns result
  → Agent synthesizes both results into final answer with Atlas link
```

---

## 4. The Two Tools

### Tool 1: `atlas_graphql` — Structured GraphQL lookup

This tool has a **structured Pydantic schema** with explicit parameters. The agent fills in the parameters as part of its tool call, making the entire GraphQL pipeline deterministic (zero LLM calls after the agent's initial decision).

```python
class AtlasGraphQLInput(BaseModel):
    """Query the Atlas of Economic Complexity GraphQL API for structured data lookups.

    Use this tool for:
    - Country profiles (GDP, population, exports, imports, ECI, COI, rankings)
    - Export baskets (top sectors/products for a country in a year)
    - Trade partners (export destinations, import origins)
    - Bilateral trade (what country A exports to country B)
    - Growth projections, diversification grades, policy recommendations
    - Pre-computed growth rates (CAGR at 3/5/10/15 year windows)
    - Time series of country-level metrics (exports, GDP, ECI over years)
    - RCA values and product space data
    - New products a country has started exporting

    Do NOT use this tool for:
    - Cross-country comparisons ("which countries export the most X?")
    - Regional aggregations ("total exports of African countries")
    - 6-digit product granularity
    - Product-level time series across many years
    - Custom derived metrics or complex analytical queries
    """
    query_type: Literal[
        "country_profile",           # 46-field country overview (GDP, trade, ECI, COI, growth, diversification, policy)
        "country_year",              # Country metrics for a specific year
        "country_year_range",        # Country metrics time series (min_year to max_year)
        "all_country_year",          # All countries for one year (good for rankings)
        "country_lookback",          # Pre-computed growth (CAGR, ECI change, etc.) over lookback period
        "country_product_lookback",  # Per-product growth metrics over lookback period
        "treemap_products",          # Country's export basket by product (with RCA, PCI, distance, market share)
        "treemap_partners",          # Country's trade partners
        "treemap_bilateral",         # What country A exports to country B, by product
        "product_space",             # Products with RCA values and connections
        "product_info",              # Product details (name, code, hierarchy)
        "product_year",              # Product-level global data (PCI, global export value)
        "all_product_year",          # All products for one year
        "new_products",              # New products a country started exporting
        "new_products_comparison",   # New products vs peer countries
        "global_datum",              # Global statistics (total trade, rank counts)
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
        description="Year for the query (1980-2024)"
    )
    min_year: Optional[int] = Field(
        default=None,
        description="Start year for range queries"
    )
    max_year: Optional[int] = Field(
        default=None,
        description="End year for range queries"
    )
    product_level: Literal["section", "twoDigit", "fourDigit"] = Field(
        default="fourDigit",
        description="Product aggregation level"
    )
    lookback_years: Optional[Literal[3, 5, 10, 15]] = Field(
        default=None,
        description="Lookback period for growth metrics"
    )
```

**Key design choice:** The agent passes human-readable country/product names (e.g., "Kenya", "crude oil"), and the GraphQL pipeline resolves them to internal IDs (`location-404`, `product-HS-910`) using cached lookup tables. The agent never needs to know GraphQL IDs.

### Tool 2: `atlas_sql` — Natural language SQL query

This tool keeps the existing interface: a natural language question that goes through the full SQL generation pipeline.

```python
class AtlasSQLInput(BaseModel):
    """Generate and execute SQL queries on the Atlas trade database.

    Use this tool for:
    - Cross-country comparisons ("which countries export the most X?")
    - Regional aggregations ("total exports of African countries")
    - Custom time ranges and product-level time series
    - Complex derived metrics (custom CAGR, weighted averages, market share changes)
    - 6-digit product granularity
    - Specific HS revision data (HS92 vs HS12)
    - Product proximity/relatedness analysis
    - Multi-step analytical queries requiring JOINs, CTEs, window functions
    - Anything the GraphQL tool cannot handle
    """
    question: str = Field(
        description="A natural language question about international trade data"
    )
```

### How the agent chooses

The agent is an LLM with access to both tool schemas. The tool descriptions (docstrings) explain when to use each. The agent's system prompt reinforces this with concrete guidance. The LLM's native tool-calling capability selects the appropriate tool.

This is the **standard LangGraph/LangChain pattern** for multi-tool agents: `llm.bind_tools([atlas_graphql_schema, atlas_sql_schema])`. The LLM sees both tools and picks the right one based on the question, just as it currently picks `query_tool` for every question.

---

## 5. Graph Structure

### Detailed node and edge diagram

```
                              ┌────────────────────────────────────────────────────┐
                              │          GRAPHQL PIPELINE (~200-500ms)              │
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

The routing function after the agent node inspects which tool was called:

```python
def route_after_agent(state: AtlasAgentState) -> str:
    last_msg = state["messages"][-1]

    # No tool calls → agent is done, end the graph
    if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
        return END

    # Check query limits
    if state.get("queries_executed", 0) >= max_uses:
        return "max_queries_exceeded"

    # Route based on which tool was called
    tool_name = last_msg.tool_calls[0]["name"]
    if tool_name == "atlas_graphql":
        return "resolve_entities"          # → GraphQL pipeline entry
    elif tool_name == "atlas_sql":
        return "extract_sql_question"      # → SQL pipeline entry (existing)

    return END
```

### Complete edge definitions

```python
# Entry
builder.add_edge(START, "agent")

# Agent routing (3-way conditional)
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
    "format_sql_results": "format_sql_results",  # validation failed, skip execution
})
builder.add_edge("execute_sql", "format_sql_results")
builder.add_edge("format_sql_results", "agent")

# Max queries exceeded
builder.add_edge("max_queries_exceeded", "agent")
```

### Node count

- **Existing nodes:** agent, extract_sql_question (renamed from extract_tool_question), extract_products, lookup_codes, get_table_info, generate_sql, validate_sql, execute_sql, format_sql_results (renamed from format_results), max_queries_exceeded — **10 nodes**
- **New nodes:** resolve_entities, build_graphql_query, execute_graphql, format_graphql_results — **4 nodes**
- **Total: 14 nodes**

---

## 6. GraphQL Pipeline Design

The GraphQL pipeline is a 4-node linear chain. All nodes are deterministic (zero LLM calls). The entire pipeline runs in ~200-500ms.

### Node 1: `resolve_entities`

Maps human-readable country/product names from the tool call args to GraphQL internal IDs.

**Input:** `state["graphql_params"]` (the parsed tool call args)
**Output:** `state["graphql_resolved"]` — same params but with `country_id`, `product_id`, `partner_id` fields added

```python
async def resolve_entities_node(state: AtlasAgentState, *, entity_resolver) -> dict:
    params = state["graphql_params"]
    resolved = {}

    if params.get("country"):
        resolved["country_id"] = entity_resolver.resolve_country(params["country"])
        # e.g., "Kenya" → "location-404"

    if params.get("partner_country"):
        resolved["partner_id"] = entity_resolver.resolve_country(params["partner_country"])

    if params.get("product"):
        resolved["product_id"] = entity_resolver.resolve_product(params["product"])
        # e.g., "crude oil" or "2709" → "product-HS-910"

    if not resolved.get("country_id") and params.get("country"):
        return {
            "graphql_error": f"Could not resolve country: {params['country']}",
            "last_error": f"Country not found: {params['country']}"
        }

    return {"graphql_resolved": {**params, **resolved}}
```

**Entity resolver implementation:** At startup, the GraphQL client fetches `allLocations(level: country)` and `allProducts(productClass: HS, productLevel: fourDigit)` to build in-memory lookup tables:

- **Country lookup:** Matches by ISO3 code (exact), ISO2 code (exact), short name (fuzzy), long name (fuzzy). Returns `location-{iso_numeric}` ID.
- **Product lookup:** Matches by HS code (exact), short name (fuzzy). Returns `product-HS-{internal_id}` ID.
- **Fuzzy matching:** Case-insensitive, handles common variants (e.g., "South Korea" → "Korea, Republic of").

### Node 2: `build_graphql_query`

Constructs the GraphQL query string from the resolved parameters. This is a pure function with no external dependencies.

**Input:** `state["graphql_resolved"]`
**Output:** `state["graphql_query"]` — the GraphQL query string

Each `query_type` maps to a template:

```python
def build_graphql_query(state: AtlasAgentState) -> dict:
    params = state["graphql_resolved"]
    query_type = params["query_type"]

    match query_type:
        case "country_profile":
            query = f'''{{
              countryProfile(location: "{params['country_id']}") {{
                location {{ shortName code }}
                latestGdpPerCapita {{ quantity year }}
                incomeClassification
                exportValue importValue exportValueRank
                latestEci latestEciRank
                latestCoi latestCoiRank coiClassification
                diversificationGrade
                growthProjectionClassification growthProjection growthProjectionRank
                policyRecommendation
                structuralTransformationStep
                diversity diversityRank
                marketShareMainSector {{ shortName code }}
                marketShareMainSectorDirection
                comparisonLocations {{ shortName }}
              }}
            }}'''

        case "treemap_products":
            year_arg = f', year: {params["year"]}' if params.get("year") else ""
            product_arg = f', product: "{params["product_id"]}"' if params.get("product_id") else ""
            partner_arg = f', partner: "{params["partner_id"]}"' if params.get("partner_id") else ""
            query = f'''{{
              treeMap(facet: CPY_C, productClass: HS{year_arg},
                      productLevel: {params.get("product_level", "fourDigit")},
                      locationLevel: country, location: "{params['country_id']}"{product_arg}{partner_arg}) {{
                ... on TreeMapProduct {{
                  product {{ shortName code topLevelParent {{ shortName }} productType }}
                  exportValue importValue rca pci distance
                  opportunityGain globalMarketShare
                }}
              }}
            }}'''

        # ... (templates for all 16 query types)

    return {"graphql_query": query}
```

### Node 3: `execute_graphql`

Makes the async HTTP POST to the Atlas GraphQL API.

**Input:** `state["graphql_query"]`
**Output:** `state["graphql_raw_response"]` or `state["graphql_error"]`

```python
async def execute_graphql_node(state: AtlasAgentState, *, graphql_client) -> dict:
    query = state["graphql_query"]
    try:
        response = await graphql_client.execute(query)
        if "errors" in response:
            return {
                "graphql_error": f"GraphQL error: {response['errors'][0]['message']}",
                "last_error": response["errors"][0]["message"],
            }
        return {"graphql_raw_response": response["data"]}
    except Exception as e:
        return {
            "graphql_error": f"GraphQL request failed: {str(e)}",
            "last_error": str(e),
        }
```

### Node 4: `format_graphql_results`

Formats the raw JSON response into a readable string for the agent, generates the Atlas link, and creates a `ToolMessage`.

**Input:** `state["graphql_raw_response"]` or `state["graphql_error"]`, `state["graphql_params"]`
**Output:** `state["messages"]` (appends ToolMessage), `state["queries_executed"]` (incremented), `state["atlas_link"]`

If GraphQL succeeded:
- Format the JSON response into a human-readable string
- Generate the Atlas website link (see section 9)
- Create a ToolMessage with the formatted result + Atlas link

If GraphQL failed:
- Create a ToolMessage with the error message
- The agent sees the error and can decide to retry with `atlas_sql`

---

## 7. SQL Pipeline (Existing, Minor Changes)

The existing SQL pipeline is preserved almost entirely. Changes:

1. **Rename** `extract_tool_question` → `extract_sql_question` and `format_results` → `format_sql_results` for clarity.

2. **`extract_sql_question`** now extracts from `atlas_sql` tool calls instead of `query_tool` tool calls. The logic is identical — pull the `question` arg from the tool call.

3. **`format_sql_results`** keeps existing behavior but now explicitly does NOT generate Atlas links (SQL answers typically don't map to a single Atlas page).

4. **All other nodes** (`extract_products`, `lookup_codes`, `get_table_info`, `generate_sql`, `validate_sql`, `execute_sql`) are completely unchanged.

---

## 8. Agent System Prompt Design

The agent system prompt is the critical piece that enables good routing decisions. It needs to:

1. Describe both tools and when to use each
2. Explain the agent can decompose complex questions into sub-questions
3. Instruct the agent to include Atlas links in its final response
4. Describe GraphQL capabilities and limitations concretely

### Proposed system prompt structure

```
You are Ask-Atlas — an expert agent designed to answer complex questions about
international trade data. You have access to two tools:

## Tool 1: atlas_graphql — Atlas API Lookup (fast, pre-computed data)

Use this tool for structured data lookups from the Atlas of Economic Complexity API.
This tool is FAST (no SQL generation needed) and provides pre-computed metrics that
are NOT available via SQL, including:
- Policy recommendations (ParsimoniousIndustrial, StrategicBets, LightTouch, TechFrontier)
- Diversification grades (A+ to D-)
- Growth projections and classifications (rapid, moderate, slow)
- Structural transformation status
- COI classifications (low, medium, high)
- Export growth classification (Troubling, Mixed, Static, Promising)
- Market share main sector and direction

Good for:
- Single-country profiles, metrics, and rankings
- Export baskets (what does country X export?)
- Trade partners (who does country X trade with?)
- Bilateral trade (what does country A export to country B?)
- Time series of country-level metrics
- RCA, distance, opportunity gain for a country's products
- Pre-computed CAGR (3, 5, 10, 15 year lookback periods)
- New products a country has started exporting

NOT good for (use atlas_sql instead):
- Cross-country comparisons ("which countries export the most X?")
- Regional aggregations ("total African exports")
- 6-digit product granularity
- Product-level time series across many years
- SITC classification data
- Custom derived calculations
- Complex multi-table analytical queries

## Tool 2: atlas_sql — SQL Query Generation (powerful, flexible)

Use this tool for complex analytical queries requiring SQL. Supports arbitrary JOINs,
aggregations, window functions, CTEs, and cross-country comparisons.

Good for:
- Cross-country comparisons and rankings
- Regional/group aggregations
- Custom time ranges and product-level time series
- Complex derived metrics
- 6-digit product granularity
- SITC and HS revision-specific data
- Product proximity analysis
- Any query that atlas_graphql cannot handle

## Workflow

1. Understand the user's question
2. For simple questions: call the appropriate single tool
3. For complex questions: break the question into sub-questions, call the
   appropriate tool for each sub-question, then synthesize the results
4. Prefer atlas_graphql when possible — it is faster and provides
   pre-computed metrics that SQL cannot
5. When atlas_graphql results include an Atlas link, include it in your
   response so users can verify the data on the Atlas website

## Atlas Links

When the atlas_graphql tool returns results, it includes a link to the
relevant page on the Atlas of Economic Complexity website
(atlas.hks.harvard.edu). ALWAYS include these links in your response
using markdown format, e.g.:
[View on Atlas of Economic Complexity](https://atlas.hks.harvard.edu/countries/404/summary)

This allows users to cross-check the data and explore further.

... [rest of existing prompt: data description, metrics, formatting rules] ...
```

---

## 9. Atlas Link Generation

For every successful GraphQL query, we generate a link to the corresponding Atlas website page. This is deterministic and based on the query type and resolved parameters.

### Atlas URL patterns

| Query Type | Atlas URL Pattern | Example |
|---|---|---|
| `country_profile` | `/countries/{iso_numeric}/summary` | `/countries/404/summary` (Kenya) |
| `country_year` | `/countries/{iso_numeric}/summary` | Same as profile |
| `country_year_range` | `/countries/{iso_numeric}/summary` | Same as profile |
| `all_country_year` | `/rankings/country` | Cross-country rankings |
| `country_lookback` | `/countries/{iso_numeric}/growth-dynamics` | Growth dynamics page |
| `country_product_lookback` | `/countries/{iso_numeric}/growth-dynamics` | Growth dynamics page |
| `treemap_products` | `/countries/{iso_numeric}/export-basket?year={year}` | Export basket visualization |
| `treemap_partners` | `/countries/{iso_numeric}/partners` | Trade partners page |
| `treemap_bilateral` | `/countries/{iso_numeric}/partners?partner={partner_iso}` | Bilateral trade |
| `product_space` | `/countries/{iso_numeric}/product-space` | Product space visualization |
| `product_info` | `/products/{hs_code}` | Product details page |
| `product_year` | `/products/{hs_code}` | Product details page |
| `new_products` | `/countries/{iso_numeric}/new-products` | New products page |
| `new_products_comparison` | `/countries/{iso_numeric}/new-products` | New products page |
| `global_datum` | `/rankings/country` | Global rankings |

### Implementation

```python
def generate_atlas_link(
    query_type: str,
    country_iso_numeric: int | None = None,
    product_hs_code: str | None = None,
    partner_iso_numeric: int | None = None,
    year: int | None = None,
) -> str | None:
    """Generate a link to the Atlas of Economic Complexity website."""
    base = "https://atlas.hks.harvard.edu"

    match query_type:
        case "country_profile" | "country_year" | "country_year_range":
            if country_iso_numeric:
                return f"{base}/countries/{country_iso_numeric}/summary"

        case "country_lookback" | "country_product_lookback":
            if country_iso_numeric:
                return f"{base}/countries/{country_iso_numeric}/growth-dynamics"

        case "treemap_products":
            if country_iso_numeric:
                url = f"{base}/countries/{country_iso_numeric}/export-basket"
                if year:
                    url += f"?year={year}"
                return url

        case "treemap_partners":
            if country_iso_numeric:
                return f"{base}/countries/{country_iso_numeric}/partners"

        case "treemap_bilateral":
            if country_iso_numeric:
                url = f"{base}/countries/{country_iso_numeric}/partners"
                if partner_iso_numeric:
                    url += f"?partner={partner_iso_numeric}"
                return url

        case "product_space":
            if country_iso_numeric:
                return f"{base}/countries/{country_iso_numeric}/product-space"

        case "product_info" | "product_year":
            if product_hs_code:
                return f"{base}/products/{product_hs_code}"

        case "new_products" | "new_products_comparison":
            if country_iso_numeric:
                return f"{base}/countries/{country_iso_numeric}/new-products"

        case "all_country_year" | "global_datum" | "all_product_year":
            return f"{base}/rankings/country"

    return None
```

### Where the link appears in the response

The Atlas link is included in the `ToolMessage` from `format_graphql_results`:

```
Kenya's total exports in 2022: $16.2 billion (rank: 90th)
ECI: -0.5268 (rank: 93rd)
Diversification grade: C
Growth projection: moderate
Policy recommendation: Parsimonious Industrial

📎 View on Atlas: https://atlas.hks.harvard.edu/countries/404/summary
```

The agent then includes this link in its final response to the user.

---

## 10. State Schema Changes

```python
class AtlasAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    queries_executed: int
    last_error: str
    retry_count: int

    # GraphQL pipeline state (NEW)
    graphql_params: Optional[dict]           # Parsed tool call args
    graphql_resolved: Optional[dict]         # Params with resolved entity IDs
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

**Note on `queries_executed`:** This counter now counts both GraphQL and SQL tool invocations. Since GraphQL queries are cheap and fast while SQL queries are expensive, we may want to raise the default `max_uses` from 3 to something like 8-10 to give the agent room for multiple GraphQL lookups + a few SQL queries per user question.

---

## 11. Streaming and Frontend

### New SSE events for the GraphQL pipeline

The frontend currently expects: `thread_id`, `agent_talk`, `tool_call`, `tool_output`, `node_start`, `pipeline_state`, `done`.

The GraphQL pipeline emits the same event types but with different node names:

| SSE Event | When | Data |
|---|---|---|
| `tool_call` | Agent calls `atlas_graphql` | `{"tool_name": "atlas_graphql", "args": {...}}` |
| `node_start` | `resolve_entities` begins | `{"node": "resolve_entities", "label": "Resolving entities"}` |
| `node_start` | `build_graphql_query` begins | `{"node": "build_graphql_query", "label": "Building API query"}` |
| `node_start` | `execute_graphql` begins | `{"node": "execute_graphql", "label": "Querying Atlas API"}` |
| `pipeline_state` | GraphQL complete | `{"stage": "execute_graphql", "graphql_query": "...", "atlas_link": "..."}` |
| `node_start` | `format_graphql_results` begins | `{"node": "format_graphql_results", "label": "Formatting results"}` |
| `tool_output` | GraphQL result returned to agent | `{"content": "...", "atlas_link": "..."}` |

### Dynamic pipeline stepper

The frontend's pipeline stepper shows different sequences based on which tool was called:

**GraphQL pipeline sequence:**
```
Resolving entities → Building API query → Querying Atlas API → Formatting results
```

**SQL pipeline sequence (existing):**
```
Extracting question → Identifying products → Looking up codes → Loading table metadata
→ Generating SQL → Validating SQL → Executing query → Formatting results
```

The `PIPELINE_SEQUENCE` constant becomes tool-specific:

```python
GRAPHQL_PIPELINE_SEQUENCE = [
    ("resolve_entities", "Resolving entities"),
    ("build_graphql_query", "Building API query"),
    ("execute_graphql", "Querying Atlas API"),
    ("format_graphql_results", "Formatting results"),
]

SQL_PIPELINE_SEQUENCE = [
    ("extract_sql_question", "Extracting question"),
    ("extract_products", "Identifying products"),
    ("lookup_codes", "Looking up product codes"),
    ("get_table_info", "Loading table metadata"),
    ("generate_sql", "Generating SQL query"),
    ("validate_sql", "Validating SQL"),
    ("execute_sql", "Executing query"),
    ("format_sql_results", "Formatting results"),
]
```

### Frontend changes

The `useChatStream` hook needs to:
1. Detect which tool was called from the `tool_call` event
2. Use the appropriate pipeline sequence for the stepper
3. Display Atlas links in the chat UI (clickable markdown links)
4. Handle multiple pipeline runs per user message (the agent may call GraphQL, then SQL, then GraphQL again)

---

## 12. Caching Architecture

### Level 1: Entity lookup tables (warm at startup, refresh daily)

- **`allLocations`** → country name/ISO → GraphQL ID mapping (~252 entries)
- **`allProducts`** → HS code/name → GraphQL product ID mapping (~1,248 at fourDigit)
- Stored in memory. Built at application startup. Refreshed on a 24-hour timer.
- This is critical for the `resolve_entities` node to work without any API calls.

### Level 2: GraphQL response cache (TTL-based)

- **`countryProfile`** responses — TTL 1 hour (data changes yearly)
- **`globalDatum`** — TTL 24 hours
- **`treeMap` responses** — TTL 1 hour (keyed by country+year+productLevel)
- **`allCountryYear`** — TTL 1 hour (keyed by year)
- Use an in-memory LRU cache (e.g., `cachetools.TTLCache`) with configurable max size.

This means the second question about Kenya in the same session returns instantly.

### Level 3: Atlas link is deterministic (no caching needed)

Atlas link generation is a pure function of query type + parameters. No caching needed.

---

## 13. Error Handling and Fallback

### GraphQL errors → agent decides to fall back

When the GraphQL pipeline fails, the error is returned to the agent as a `ToolMessage`. The agent then naturally decides what to do:

```
Agent: calls atlas_graphql(query_type="treemap_products", country="Kenya", ...)
GraphQL pipeline: → HTTP 500 error from Atlas API
ToolMessage: "The Atlas GraphQL API returned an error for this query. You may
             want to try the atlas_sql tool to get this data from the database."
Agent: thinks "GraphQL failed, let me try SQL"
Agent: calls atlas_sql(question="What are Kenya's top export products?")
SQL pipeline: → runs normally → returns results
```

This is more elegant than automatic fallback — the agent stays in control and can reason about why the failure happened.

### Specific error types

| Error | Response in ToolMessage | Agent likely action |
|---|---|---|
| Country not found | "Could not resolve country: 'Wakanda'" | Inform user, ask for clarification |
| Product not found | "Could not resolve product: 'unobtanium'" | Try SQL with natural language |
| Broken endpoint | "GraphQL API error: [message]" | Fall back to SQL |
| Network timeout | "GraphQL request timed out" | Retry or fall back to SQL |
| Empty result | "No data found for Kenya in 1960" | Inform user about data limits |

### SQL errors (unchanged)

The existing SQL error handling remains: validation errors skip execution, execution errors include retry logic, and all errors are reported to the agent via ToolMessage.

---

## 14. Worked Examples

### Example 1: Simple question (GraphQL only)

**User:** "What are Kenya's total exports?"

```
Turn 1: Agent thinks → "Simple country metric, GraphQL can handle this"
        Agent calls → atlas_graphql(query_type="country_profile", country="Kenya")

GraphQL pipeline:
  resolve_entities    → "Kenya" → "location-404"           (~1ms, cached)
  build_graphql_query → countryProfile query string         (~1ms)
  execute_graphql     → HTTP POST, get JSON response        (~300ms)
  format_graphql_results → formatted text + Atlas link      (~1ms)

ToolMessage:
  "Kenya export value: $16,200,000,000 (rank: 90th)
   Import value: $27,300,000,000
   ECI: -0.5268 (rank: 93)
   📎 https://atlas.hks.harvard.edu/countries/404/summary"

Turn 2: Agent synthesizes → "Kenya's total exports in the most recent year were
        approximately $16.2 billion, ranking 90th globally.
        [View on Atlas](https://atlas.hks.harvard.edu/countries/404/summary)"
        → END
```

**Total time: ~1s. LLM calls: 2 (agent turn 1 + agent turn 2). GraphQL calls: 1. SQL calls: 0.**

### Example 2: Simple question (SQL only)

**User:** "Which countries export the most crude oil?"

```
Turn 1: Agent thinks → "Cross-country comparison, need SQL"
        Agent calls → atlas_sql(question="Which countries export the most crude oil?")

SQL pipeline: (existing 8-node pipeline, ~5s)
  → extract_products → lookup_codes("crude oil" → hs92 code 2709)
  → generate_sql → validate → execute → format

ToolMessage: "Results: Saudi Arabia $161B, Russia $120B, Iraq $85B, ..."

Turn 2: Agent synthesizes → "The top crude oil exporters are: ..."
        → END
```

**Total time: ~7s. LLM calls: 4-5 (agent + extract + lookup + generate + agent). SQL calls: 1.**

### Example 3: Complex question (both tools)

**User:** "What is Kenya's diversification grade and how does it compare to other East African countries?"

```
Turn 1: Agent thinks → "Diversification grade is a pre-computed metric only
         in GraphQL. But regional comparison needs SQL. I'll get Kenya's
         grade first."
        Agent calls → atlas_graphql(query_type="country_profile", country="Kenya")

GraphQL pipeline: (~300ms)
  → resolve → build → execute → format
ToolMessage: "Kenya diversification grade: C, ECI: -0.5268, ..."
             + Atlas link

Turn 2: Agent thinks → "Got Kenya's grade (C). Now I need East African peers.
         GraphQL can't do regional queries, so I'll use SQL."
        Agent calls → atlas_sql(question="What are the ECI and diversity values
         for East African countries (Kenya, Tanzania, Uganda, Rwanda, Ethiopia,
         Burundi) in the most recent year?")

SQL pipeline: (~5s)
  → generates query joining country_year with location_country
  → executes → returns ECI/diversity for 6 countries

Turn 3: Agent synthesizes → "Kenya has a diversification grade of C.
         Compared to its East African neighbors:
         - Ethiopia: ECI -1.02 (less complex)
         - Tanzania: ECI -0.89
         - Kenya: ECI -0.53 (most complex in region)
         - Uganda: ECI -0.71
         - Rwanda: ECI -0.62
         Kenya is the most economically complex country in East Africa.
         [View Kenya on Atlas](https://atlas.hks.harvard.edu/countries/404/summary)"
        → END
```

**Total time: ~8s. GraphQL calls: 1. SQL calls: 1. Agent gets the best of both worlds.**

### Example 4: Complex question (multiple GraphQL calls)

**User:** "Compare the growth prospects of Vietnam and Indonesia."

```
Turn 1: Agent thinks → "Growth projections are pre-computed in GraphQL.
         I'll get both country profiles."
        Agent calls → atlas_graphql(query_type="country_profile", country="Vietnam")

GraphQL pipeline: (~300ms) → countryProfile for Vietnam
ToolMessage: "Vietnam: growth projection 3.8%, classification: rapid,
             diversification grade: B, ECI: 0.24 ..."

Turn 2: Agent calls → atlas_graphql(query_type="country_profile", country="Indonesia")

GraphQL pipeline: (~300ms) → countryProfile for Indonesia
ToolMessage: "Indonesia: growth projection 2.1%, classification: moderate,
             diversification grade: C, ECI: -0.12 ..."

Turn 3: Agent calls → atlas_graphql(query_type="country_lookback",
         country="Vietnam", lookback_years=5)

GraphQL pipeline: (~300ms) → 5-year lookback for Vietnam
ToolMessage: "Vietnam 5yr: export CAGR 2.2%, ECI change +0.15, ..."

Turn 4: Agent calls → atlas_graphql(query_type="country_lookback",
         country="Indonesia", lookback_years=5)

GraphQL pipeline: (~300ms) → 5-year lookback for Indonesia
ToolMessage: "Indonesia 5yr: export CAGR 1.8%, ECI change -0.03, ..."

Turn 5: Agent synthesizes comparison with Atlas links for both countries
        → END
```

**Total time: ~4s. All GraphQL, zero SQL, zero SQL-generation LLM calls.**

### Example 5: GraphQL fails, agent falls back to SQL

**User:** "What countries are the biggest exporters of cars?"

```
Turn 1: Agent thinks → "Cross-country product comparison. GraphQL can't do
         CPY_P (broken endpoint). I know this needs SQL."
        Agent calls → atlas_sql(question="Which countries export the most cars?")

SQL pipeline: → generates, validates, executes
ToolMessage: "Germany $147.6B, Japan $82.3B, ..."

Turn 2: Agent synthesizes → "The top car exporters are: ..."
        → END
```

The agent's system prompt tells it that cross-country product comparisons need SQL, so it doesn't even try GraphQL.

---

## 15. Design Decisions and Alternatives Considered

### Decision 1: Two-tool agent vs. static router

**Chosen: Two-tool agent.** The agent decides which tool to call per sub-question.

*Alternative considered: Static router node* — A dedicated `classify_query` node routes the entire question to either GraphQL or SQL. Rejected because:
- Many questions need BOTH data sources
- The agent naturally decomposes questions as part of its ReAct reasoning
- Adding a router is an extra LLM call for every question, even simple ones
- The agent already has the reasoning capability to make routing decisions

### Decision 2: Structured vs. natural-language GraphQL tool input

**Chosen: Structured Pydantic schema** for the GraphQL tool.

*Alternative considered: Natural language question* (like the SQL tool), with an internal LLM to classify and extract parameters. Rejected because:
- The whole point of the GraphQL path is to be FAST — adding an LLM call defeats the purpose
- The agent is already an LLM — it can fill in structured parameters as part of its tool call
- Structured input means the entire GraphQL pipeline is deterministic and testable
- The Pydantic schema serves as documentation for the agent (it sees the field descriptions)

### Decision 3: Separate format nodes vs. unified format node

**Chosen: Separate format nodes** (`format_graphql_results` and `format_sql_results`).

*Alternative considered: Unified format node* that handles both paths. Rejected because:
- The formatting logic is quite different (JSON response vs. SQL result rows/columns)
- Atlas link generation only applies to GraphQL results
- Separate nodes are easier to test independently
- The SSE streaming events are different for each path

### Decision 4: Query limits

**Chosen: Single `queries_executed` counter for both tools, with raised `max_uses`.**

*Alternative considered: Separate counters* (unlimited GraphQL, limited SQL). Rejected because:
- Simplicity — one counter is easier to reason about
- Even though GraphQL is cheap, unbounded API calls could cause issues
- Raising `max_uses` from 3 to ~10 gives plenty of room for mixed GraphQL+SQL workflows
- If needed, we can add separate counters later

### Decision 5: Atlas links for SQL answers

**Chosen: Atlas links only for GraphQL answers (Phase 1).**

*Alternative considered: Also generating Atlas links for SQL answers.* Deferred to Phase 2 because:
- For GraphQL queries, the link is deterministic (query type → page type, 1:1 mapping)
- For SQL queries, the mapping is fuzzy — a complex query might touch multiple countries/products
- Getting SQL links right requires extracting entities from the SQL question, which adds complexity
- Better to ship the GraphQL link feature first and iterate

---

## 16. Implementation Phases

### Phase 1: GraphQL Client Module

**New file: `src/graphql_client.py`**

- Async HTTP client (httpx) for `POST https://atlas.hks.harvard.edu/api/countries/graphql`
- `EntityResolver` class with country/product lookup tables
- Startup initialization: fetch `allLocations` + `allProducts`, build indexes
- Query builders for all 16 query types
- Response parsers
- TTL cache for responses
- Atlas link generation function

**New file: `src/tests/test_graphql_client.py`**

- Unit tests with mocked HTTP responses
- Entity resolution tests (name matching, ISO codes, fuzzy matching)
- Query builder tests (correct GraphQL syntax for each query type)
- Atlas link generation tests

### Phase 2: GraphQL Pipeline Nodes + Graph Update

**Modify: `src/generate_query.py`**

- Add `AtlasGraphQLInput` and `AtlasSQLInput` tool schemas
- Rename `_query_tool_schema` → `_atlas_sql_schema`, add `_atlas_graphql_schema`
- Add 4 new nodes: `resolve_entities`, `build_graphql_query`, `execute_graphql`, `format_graphql_results`
- Rename existing format nodes for clarity
- Update `route_after_agent` to inspect tool name and route to the correct pipeline
- Update `agent_node` to bind both tools: `llm.bind_tools([_atlas_graphql_schema, _atlas_sql_schema])`
- Add new edges for GraphQL pipeline

**Modify: `src/state.py`**

- Add GraphQL pipeline state fields

**Modify: `src/generate_query.py` (agent prompt)**

- Rewrite `AGENT_PREFIX` to describe both tools (see section 8)
- Add Atlas link inclusion instructions

**New file: `src/tests/test_graphql_pipeline.py`**

- Test each GraphQL pipeline node independently
- Test end-to-end GraphQL pipeline with mocked HTTP
- Test routing: agent calls GraphQL → correct pipeline runs

### Phase 3: Streaming + Frontend

**Modify: `src/text_to_sql.py`**

- Handle new GraphQL pipeline node events in `astream_agent_response`
- Emit correct SSE events for GraphQL pipeline
- Support dynamic pipeline sequence based on tool name
- Include `atlas_link` in relevant SSE events

**Modify: `frontend/src/hooks/use-chat-stream.ts`**

- Detect tool type from `tool_call` event
- Use appropriate pipeline sequence for stepper
- Handle Atlas links in `tool_output` events

**Modify: `frontend/src/components/chat/pipeline-stepper.tsx`**

- Support dynamic pipeline sequences (short GraphQL vs. long SQL)

**Modify: `frontend/src/components/chat/message-bubble.tsx` (or equivalent)**

- Render Atlas links as clickable elements in the chat UI

### Phase 4: Evaluation + Tuning

**Modify: `evaluation/eval_questions.json`**

- Add routing metadata: expected tool (`graphql`, `sql`, `both`, `none`) per question
- Add new questions for GraphQL-exclusive metrics (diversification grades, policy recs)

**Modify: `evaluation/run_eval.py`**

- Track which tool was used for each question
- Report routing accuracy (did the agent pick the right tool?)
- Report GraphQL vs. SQL usage statistics
- Compare answer quality between tools when both can answer

**New: Router accuracy evaluation**

- For each eval question, check if the agent's tool choice matches the expected routing
- Flag misroutes: agent used SQL when GraphQL would have been faster, or tried GraphQL for something SQL-only

---

## 17. What This Means for Atlas Decoupling

### Degraded mode: no database

With the GraphQL path in place, the system can operate without any database:

- **GraphQL-routable questions (~62%)** — work perfectly
- **SQL-routable questions (~25%)** — agent calls `atlas_sql`, gets an error, informs the user: "This question requires our analytical database, which is currently unavailable. For simpler questions about specific countries, I can still help."

This enables:
- **Lightweight deployment** (GraphQL only) as a demo or low-cost tier
- **Full deployment** (GraphQL + database) for complete capability
- **Graceful degradation** if the database is down

### Strategic positioning

By generating Atlas links for every GraphQL-answered question:
- Users are funneled to the Atlas website for verification and exploration
- The Atlas team sees this tool as **driving traffic** to their site
- Simple data lookups explicitly reference the authoritative source
- Complex analytical queries (SQL path) provide value that the Atlas website can't — this is where our tool uniquely adds value

This framing — "we handle the questions the Atlas website can't, and we send users TO the website for the ones it can" — makes the tool complementary rather than competitive.

---

## Appendix A: GraphQL Query Type Reference

Summary of the 16 supported query types, their GraphQL endpoints, and key fields:

| Query Type | GraphQL Endpoint | Key Fields | Status |
|---|---|---|---|
| `country_profile` | `countryProfile` | 46 fields: GDP, trade, ECI, COI, growth, diversification, policy | Working |
| `country_year` | `countryYear` | population, exportValue, importValue, gdp, eci, eciRank, coi | Working |
| `country_year_range` | `countryYearRange` | Time series arrays of the above | Working |
| `all_country_year` | `allCountryYear` | Same as country_year, for all 145 countries | Working |
| `country_lookback` | `countryLookback` | CAGR, ECI change, growth classification | Working |
| `country_product_lookback` | `countryProductLookback` | Per-product CAGR, growth | Working |
| `treemap_products` | `treeMap(CPY_C)` | exportValue, rca, pci, distance, opportunityGain, globalMarketShare | Working |
| `treemap_partners` | `treeMap(CCY_C)` | exportValue, importValue per partner country | Working |
| `treemap_bilateral` | `treeMap(CPY_C+partner)` | Bilateral trade by product | Working |
| `product_space` | `productSpace` | RCA, x/y coords, connections | Working |
| `product_info` | `product` | code, shortName, longName, parent, productType | Working |
| `product_year` | `productYear` | pci, globalExportValue, complexityLevel | Working |
| `all_product_year` | `allProductYear` | Same for all ~1,245 products | Working |
| `new_products` | `newProductsCountry` | newProducts list, count, export value | Working |
| `new_products_comparison` | `newProductsComparisonCountries` | Peer country comparison | Working |
| `global_datum` | `globalDatum` | Global trade totals, rank counts | Working |

### Known broken endpoints (NOT included as query types)

| Broken Endpoint | Why | Workaround |
|---|---|---|
| `treeMap(CPY_P)` | Null locations | SQL only |
| `productYearRange` | Missing positional arg | Loop `productYear` |
| `allCountryYearRange` | `'hs_coi'` error | Use single-country or single-year variants |
| `group` / `allGroups` | LocationGroup model missing | SQL only |
| `manyCountryProductYear` | Two overlapping bugs | SQL only |
| SITC treeMap | SITC not implemented | SQL only |

## Appendix B: LangGraph Implementation Details

### How `bind_tools` works with two tools

```python
# Current (single tool):
model_with_tools = llm.bind_tools([_query_tool_schema])

# New (two tools):
model_with_tools = llm.bind_tools([_atlas_graphql_schema, _atlas_sql_schema])
```

LangChain converts each tool's Pydantic schema into the provider's function-calling format. The LLM receives both tool descriptions and autonomously decides which to call based on the question. This is the standard LangGraph/LangChain pattern for multi-tool agents.

### How the tool call is extracted

The existing `extract_tool_question` function pulls `question` from `tool_calls[0]["args"]`. For GraphQL, we need to extract the full structured args:

```python
def extract_graphql_params(state: AtlasAgentState) -> dict:
    """Extract structured parameters from the atlas_graphql tool call."""
    last_msg = state["messages"][-1]
    tool_call = last_msg.tool_calls[0]
    return {"graphql_params": tool_call["args"]}

def extract_sql_question(state: AtlasAgentState) -> dict:
    """Extract the question from the atlas_sql tool call."""
    last_msg = state["messages"][-1]
    tool_call = last_msg.tool_calls[0]
    return {"pipeline_question": tool_call["args"]["question"]}
```

Note: `resolve_entities` is the entry point for the GraphQL pipeline, so `extract_graphql_params` is folded into it rather than being a separate node.

### Parallel tool calls

LLMs can make parallel tool calls (e.g., calling `atlas_graphql` for Kenya AND Vietnam in the same turn). Our graph processes one tool call at a time (the first in the list). If the agent makes multiple parallel calls, the second one would need to be handled in a subsequent turn. This is acceptable for now — the agent prompt can be tuned to prefer sequential calls for clarity.

If parallel tool call support becomes important later, LangGraph's `Send` API can fan out to process multiple calls concurrently:

```python
# Future enhancement: parallel tool call processing
def route_after_agent(state) -> list[Send] | str:
    last_msg = state["messages"][-1]
    if not last_msg.tool_calls:
        return END
    # Fan out: one Send per tool call
    return [
        Send("graphql_pipeline" if tc["name"] == "atlas_graphql" else "sql_pipeline",
             {"tool_call": tc})
        for tc in last_msg.tool_calls
    ]
```

## Appendix C: Files to Create/Modify

| File | Action | Description |
|---|---|---|
| `src/graphql_client.py` | **CREATE** | Async HTTP client, entity resolver, query builders, caching, Atlas link generator |
| `src/generate_query.py` | **MODIFY** | Add GraphQL tool schema, 4 new nodes, update routing, update agent prompt |
| `src/state.py` | **MODIFY** | Add GraphQL pipeline state fields |
| `src/text_to_sql.py` | **MODIFY** | Handle GraphQL pipeline streaming events |
| `src/api.py` | **MODIFY** | Minor: pass GraphQL client to agent factory |
| `src/tests/test_graphql_client.py` | **CREATE** | Unit tests for GraphQL client |
| `src/tests/test_graphql_pipeline.py` | **CREATE** | Tests for GraphQL pipeline nodes and routing |
| `src/tests/test_pipeline_nodes.py` | **MODIFY** | Rename SQL-specific tests |
| `src/tests/test_agent_trajectory.py` | **MODIFY** | Add trajectories testing tool routing |
| `evaluation/eval_questions.json` | **MODIFY** | Add routing metadata, new GraphQL-only questions |
| `evaluation/run_eval.py` | **MODIFY** | Track tool usage per question |
| `frontend/src/hooks/use-chat-stream.ts` | **MODIFY** | Handle new tool types and pipeline sequences |
| `frontend/src/components/chat/pipeline-stepper.tsx` | **MODIFY** | Dynamic pipeline display |
