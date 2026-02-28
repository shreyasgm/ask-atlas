# Backend Architecture: Hybrid GraphQL + SQL

> **Date:** 2026-02-25
> **Status:** Architecture reference (implemented)
> **Related:** GitHub issue #42, `docs/hybrid_backend_analysis.md`; issues #50, #54 (documentation tool)

---


## Table of Contents

1. [Context](#1-context)
2. [System Modes](#2-system-modes)
3. [End-to-End Workflow](#3-end-to-end-workflow)
4. [Graph Topology](#4-graph-topology)
5. [GraphQL Pipeline Detail](#5-graphql-pipeline-detail)
6. [Rate Limit Circuit Breaker](#6-rate-limit-circuit-breaker)
7. [Verification Workflow](#7-verification-workflow)
8. [Atlas Link Generation](#8-atlas-link-generation)
9. [State Schema](#9-state-schema)
10. [File Structure](#10-file-structure)
11. [LLM Prompts Inventory](#11-llm-prompts-inventory)
12. [Implementation Phases](#12-implementation-phases)
13. [Evaluation Strategy](#13-evaluation-strategy)
    - 13.1 [Tier 1 — Unit Tests](#131-tier-1--unit-tests-no-llm-no-db)
    - 13.2 [Tier 2 — Component Evaluation](#132-tier-2--component-evaluation-real-llm-no-llm-as-judge)
    - 13.3 [Tier 3 — Trajectory Evaluation](#133-tier-3--trajectory-evaluation-new)
    - 13.4 [Tier 4 — End-to-End](#134-tier-4--end-to-end-existing-eval-system-extended)
    - 13.5 [Manual E2E Verification](#135-manual-ee-verification)
    - 13.6 [Evaluation Datasets & Collection](#136-evaluation-datasets--collection)
14. [Documentation Tool (docs_tool)](#14-documentation-tool-docs_tool)

---

## 1. Context

GitHub issue #42 adds GraphQL API access alongside the existing SQL pipeline. The Atlas GraphQL APIs provide ~12 derived metrics (policy recommendations, diversification grades, growth projections) that don't exist in the SQL database, plus faster lookups for common questions. Official API documentation is published by the Growth Lab at [github.com/harvard-growth-lab/api-docs](https://github.com/harvard-growth-lab/api-docs/blob/main/atlas.md); our consolidated reference (official docs + live introspection) is in `evaluation/graphql_api_official_docs.md`.

The file organization, graph construction, and module boundaries were designed from scratch for the dual-tool system. The existing SQL pipeline node functions are preserved; the graph topology, agent node, streaming engine, and file structure are new.

---

## 2. System Modes

The system supports three configurable modes:

| Mode | Tool Binding | System Prompt | When to Use |
|------|-------------|---------------|-------------|
| **`auto`** (default) | Dynamic: checks `GraphQLBudgetTracker`. If budget > 5 → dual-tool; else → SQL-only | Switches dynamically | Production default. Graceful degradation under load. |
| **`graphql_sql`** | Always dual-tool (both tools bound) | Extended prompt with GraphQL + SQL | Development, testing, or when GraphQL is reliable |
| **`sql_only`** | Only `query_tool` bound | Existing `AGENT_PREFIX` unchanged | Fallback, debugging, or when GraphQL APIs are down |

**Configuration:** A new `AgentMode` enum in `src/config.py`:

```python
class AgentMode(str, Enum):
    AUTO = "auto"
    GRAPHQL_SQL = "graphql_sql"
    SQL_ONLY = "sql_only"
```

Set via `model_config.py` (default: `"auto"`) and overridable per-request via the `/api/chat/stream` endpoint (query param or request body field). The per-request override allows the frontend to force a specific mode for debugging.

---

## 3. End-to-End Workflow

```
╔═══════════════════════════════════════════════════════════════════════════╗
║                         USER QUESTION                                    ║
║  "What is Kenya's diversification grade and how does its coffee          ║
║   export compare to Ethiopia's?"                                         ║
╚════════════════════════════════╤══════════════════════════════════════════╝
                                 │
                                 ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     MODE RESOLUTION                                    │
│                                                                        │
│  config.agent_mode (or per-request override)                           │
│                                                                        │
│  ┌──────────┐    ┌──────────────┐    ┌────────────┐                    │
│  │ sql_only │    │ auto         │    │graphql_sql │                    │
│  └────┬─────┘    └──────┬───────┘    └─────┬──────┘                    │
│       │                 │                   │                           │
│       │          budget_tracker              │                          │
│       │          .is_available()             │                          │
│       │          ┌────┴────┐                │                           │
│       │          │yes   no │                │                           │
│       │          │     │   │                │                           │
│       ▼          ▼     ▼   │                ▼                           │
│  SQL-only    Dual-tool  SQL-only       Dual-tool                       │
│  agent       agent      agent          agent                           │
└──────┬──────────┬──────────┬──────────────┬────────────────────────────┘
       │          │          │              │
       ▼          ▼          ▼              ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        AGENT (LLM)                                     │
│                                                                        │
│  SQL-only mode:                                                        │
│  • System prompt = existing AGENT_PREFIX (unchanged)                   │
│  • Tools = [query_tool]                                                │
│  • Behavior = identical to current production system                   │
│                                                                        │
│  Dual-tool mode:                                                       │
│  • System prompt = extended (SQL + GraphQL descriptions + budget       │
│    status + verification guidance)                                     │
│  • Tools = [query_tool, atlas_graphql]                                 │
│  • Agent decomposes question into sub-queries, routes each:            │
│    - "Kenya's diversification grade" → atlas_graphql (derived metric)  │
│    - "Coffee export comparison" → query_tool or atlas_graphql          │
│                                                                        │
│  Calls: atlas_graphql(question="What is Kenya's diversification        │
│          grade?")                                                       │
└────────────────────────┬───────────────────────────────────────────────┘
                         │
        ┌────────────────┴────────────────────────────┐
        │                                              │
        ▼                                              ▼
   atlas_graphql tool                            query_tool
   (natural language in)                    (natural language in)
        │                                              │
        ▼                                              ▼
╔═══════════════════════╗                 ╔═══════════════════════╗
║  GRAPHQL PIPELINE     ║                 ║  SQL PIPELINE         ║
║                       ║                 ║                       ║
║  Can REJECT the query ║                 ║  Preserved from       ║
║  if it doesn't fit    ║                 ║  current system       ║
║  any GraphQL API type ║                 ║                       ║
╚═══════════╤═══════════╝                 ╚═══════════╤═══════════╝
            │                                         │
            ▼                                         ▼
   Result + Atlas links                       Result (SQL rows)
   (or rejection note)                                │
            │                                         │
            └─────────────┬───────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        AGENT reviews results                           │
│                                                                        │
│  • Plausible? → Formulate answer, include Atlas links (if available)   │
│  • Implausible? → Call the OTHER tool to verify                        │
│  • GraphQL rejected? → Fall back to query_tool (SQL)                   │
│  • Need more info? → Call another tool (same or different)             │
│  • Done? → Return final answer to user                                 │
└────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
╔═══════════════════════════════════════════════════════════════════════════╗
║  FINAL RESPONSE TO USER                                                  ║
║                                                                          ║
║  [Agent's markdown text answer]                                          ║
║                                                                          ║
║  ──────────────────────────────                                          ║
║  View on Atlas:                                           (structured)   ║
║  [Kenya — Country Profile]  [Kenya — Export Basket]       (not LLM       ║
║  [Kenya vs Ethiopia — Coffee exports]                      generated)    ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## 4. Graph Topology

One compiled graph handles all modes. The agent node dynamically adjusts tool binding and system prompt based on the resolved mode. GraphQL pipeline nodes exist in the graph but are never reached in SQL-only mode (the agent can't call `atlas_graphql` without it being bound).

```
START
  │
  ▼
[agent_node]  ◄───────────────────────────────────────────────────────────┐
  │                                                                        │
  ▼                                                                        │
route_after_agent ──┬─────────────────┬──────────────┬──────────────┐      │
  │                 │                 │              │              │      │
  ▼                 ▼                 ▼              ▼              ▼      │
 END          max_queries       SQL PIPELINE   GRAPHQL PIPE   DOCS PIPE   │
(no tool_     _exceeded         (query_tool)   (atlas_graphql) (docs_tool) │
 calls)         │                 │              │              │          │
                │   ┌─────────────┘              │              │          │
                │   │                            │              │          │
                │   ▼                            ▼              ▼          │
                │  [extract_sql_       [extract_graphql    [extract_docs   │
                │   _question]          _question]          _question]    │
                │   │                        │                  │          │
                │   ▼                        ▼                  ▼          │
                │  [extract_products] [classify_query]   [select_and    │
                │   │                        │            _synthesize]  │
                │   ▼                   ┌────┴────┐            │          │
                │  [lookup_codes]       │ ROUTE   │            ▼          │
                │   │                  │reject?  │       [format_docs     │
                │   ▼                  └────┬────┘        _results]       │
                │  [get_table_info]          │                  │          │
                │   │                       │                   │          │
                │   ▼              [extract_entities]            │          │
                │  [generate_sql]           │                   │          │
                │   │                  [resolve_ids +           │          │
                │   │                   generate_atlas_links]   │          │
                │   ▼                       │                   │          │
                │  [validate_sql]  [build_and_execute           │          │
                │   │               _graphql]                   │          │
                │   ▼                       │                   │          │
                │  route_after_             │                   │          │
                │   validation              │                   │          │
                │   ├→ [execute_            │                   │          │
                │   │   _sql]               │                   │          │
                │   │     │                 │                   │          │
                │   │     │                 │                   │          │
                │   ▼     ▼    [format_graphql_results]         │          │
                │  [format_sql_results]     │                   │          │
                │         │                 │                   │          │
                └─────────┴─────────────────┴───────────────────┘──────────┘
```

### Key Design Decisions

1. **Flat graph, no subgraphs.** Subgraphs would cleanly isolate pipeline state, but they are incompatible with our streaming architecture. Specifically:
   - Our streaming uses `stream_mode=["messages", "updates"]` (dual mode). There is a [known LangGraph issue](https://github.com/langchain-ai/langgraph/issues/5932) where streaming multiple modes simultaneously with `subgraphs=True` breaks — exactly our pattern.
   - Even without that bug, `graph.stream(..., subgraphs=True)` yields `(namespace, data)` tuples where namespace is a tuple path like `("parent_node:abc123", "child_node:def456")`, not flat strings. Our `_extract_pipeline_state()` function dispatches on flat node name strings and would need non-trivial refactoring.
   - At ~17 nodes post-rewrite (agent + 8 SQL + 6 GraphQL + route nodes), we're in the "manageable with good prefixing" range. Subgraphs are recommended for >30 nodes.
   - If the graph grows beyond 25+ nodes in the future, revisit this decision.

   Each pipeline's state fields are prefixed (`pipeline_sql_*`, `graphql_*`) as a substitute for subgraph state isolation.

2. **Atlas link generation is combined with `resolve_ids`, not a separate parallel node.** The original design had `generate_atlas_links` as a separate graph node running in parallel with `build_and_execute_graphql` via a LangGraph fan-out/fan-in pattern. This was abandoned for two reasons:
   - **LangGraph superstep rollback:** In a fan-out, all branches must complete before the graph advances. If one branch raises an exception, the entire superstep rolls back — including the successful branch's state writes. This means a failure in `build_and_execute_graphql` would also discard `generate_atlas_links`'s output, causing both branches to re-execute from the previous node. Robust error handling can mitigate this, but it adds complexity for no real benefit.
   - **Negligible latency benefit:** Link generation is fully deterministic and takes microseconds. Running it in parallel with the ~100-500ms GraphQL API call saves no perceptible latency. Combining it with `resolve_ids` (which already has all the data link generation needs — resolved params and query_type) is simpler and equally fast.

   The `resolve_ids` node now calls `generate_atlas_links()` inline after resolving entity IDs and before formatting IDs for the target API (link generation needs the canonical integer ID form, not the API-specific prefixed form). This keeps the pipeline strictly sequential:
   ```python
   builder.add_edge("resolve_ids", "build_and_execute_graphql")
   builder.add_edge("build_and_execute_graphql", "format_graphql_results")
   ```

3. **Dynamic tool binding in agent_node.** The agent node resolves the effective mode (auto → check budget, or forced mode) and adjusts:
   - Which tools to bind
   - Which system prompt to use
   - What budget status line to include (if dual-tool)

4. **SQL pipeline is preserved.** The SQL pipeline node functions (`extract_products`, `lookup_codes`, `get_table_info`, `generate_sql`, `validate_sql`, `execute_sql`, `format_sql_results`) are carried over from the current system with minimal changes. The "system looks like it does now" requirement for SQL-only mode is satisfied because these functions are unchanged and the agent prompt is unchanged.

5. **Classification and entity extraction are separate LLM calls.** The `classify_query` → `extract_entities` split keeps each call focused:
   - Classification is a routing decision; extraction is entity work. Combining them overloads the LLM with two unrelated tasks and risks diluting both.
   - The extraction schema can be tailored per `query_type` (e.g., `lookback_years` only matters for `country_lookback`, `group_type` only for `explore_group`). The prompt can note which fields are relevant for the classified type.
   - Early rejection at classification saves an LLM call — if the query is rejected, `extract_entities` never runs.
   - Sequential, not parallel — extraction depends on the classified `query_type`.

---

## 5. GraphQL Pipeline Detail

> **Official API reference:** The Harvard Growth Lab publishes official documentation for the Explore API at [github.com/harvard-growth-lab/api-docs — atlas.md](https://github.com/harvard-growth-lab/api-docs/blob/main/atlas.md). A GraphiQL interactive schema explorer is also available by navigating to `https://atlas.hks.harvard.edu/api/graphql` in a browser (use the "Docs" menu in the top-right to browse all queries, types, and field descriptions). Our internal reference consolidating the official docs with live introspection data is in `evaluation/graphql_api_official_docs.md`.

**Structured output conventions (all LLM structured output schemas in the pipeline):**
1. **Description constants** for Literal fields — defined as module-level string constants with `- value : explanation` format, referenced via `Field(description=CONSTANT_NAME)`. The LLM sees the full explanation in the JSON schema.
2. **Reasoning field first** — a `reasoning: str` field placed before classification/selection fields to trigger chain-of-thought prompting.
3. **`with_structured_output(Model, include_raw=True)`** — LangChain's provider-agnostic constrained generation. `include_raw=True` captures the raw LLM response for debugging. No hardcoded `method` parameter.

Canonical invocation pattern (used by all structured output nodes):
```python
structured_llm = model.with_structured_output(Schema, include_raw=True)
result = await structured_llm.ainvoke(messages)
# result = {"raw": AIMessage(...), "parsed": Schema(...), "parsing_error": None}
```

```
┌──────────────────────────────────────────────────────────────────────┐
│                      GRAPHQL PIPELINE                                 │
│                                                                       │
│  ┌──────────────────────────┐                                         │
│  │ 1. extract_graphql_      │  Extract question string from           │
│  │    question              │  tool_call args (same pattern as SQL)   │
│  └────────────┬─────────────┘                                         │
│               │                                                       │
│               ▼                                                       │
│  ┌──────────────────────────┐                                         │
│  │ 2. classify_query        │  LLM call (lightweight model):         │
│  │    (LLM node)            │  • Classify → query_type OR "reject"   │
│  │                          │  • Pick → which API (Explore vs        │
│  │                          │    Country Pages)                      │
│  │                          │  Uses structured output (Pydantic)     │
│  │                          │  with chain-of-thought reasoning       │
│  └────────────┬─────────────┘                                         │
│               │                                                       │
│          ┌────┴────┐                                                  │
│          │ ROUTE   │  If query_type == "reject":                      │
│          │         │  → skip to format_graphql_results                │
│          │         │    with rejection ToolMessage                    │
│          └────┬────┘                                                  │
│               │ (not rejected)                                        │
│               ▼                                                       │
│  ┌──────────────────────────┐                                         │
│  │ 2b. extract_entities     │  LLM call (lightweight model):         │
│  │    (LLM node)            │  • Extract → country, year, product,   │
│  │                          │    partner, lookback_years, etc.       │
│  │                          │  • Initial ID guesses (LLM best-guess  │
│  │                          │    ISO alpha-3 codes, HS/SITC codes)   │
│  │                          │  Uses structured output (Pydantic)     │
│  │                          │  Prompt tailored to classified         │
│  │                          │  query_type                            │
│  └────────────┬─────────────┘                                         │
│               │                                                       │
│               ▼                                                       │
│  ┌──────────────────────────┐                                         │
│  │ 3. resolve_ids           │  Dual-source ID resolution             │
│  │    (lookup + LLM select  │  (mirrors SQL product resolution):     │
│  │    + generate links)     │                                         │
│  │                          │                                         │
│  │  A. Verify standard      │  Check if LLM's ISO alpha-3 /         │
│  │     codes against cached │  HS/SITC code guesses exist in the    │
│  │     lookup tables        │  lookup tables (verified suggestions)  │
│  │                          │                                         │
│  │  B. Search lookup tables │  Text-search country names, product    │
│  │     by name/description  │  names in cached catalogs              │
│  │                          │  (catalog suggestions)                 │
│  │                          │                                         │
│  │  C. LLM selects best    │  Present both sources to metadata      │
│  │     (lightweight model)   │  model. LLM picks final IDs based     │
│  │                          │  on question context.                  │
│  │                          │                                         │
│  │  D. Generate Atlas links │  Deterministic URL generation from     │
│  │     (deterministic)      │  resolved params + query_type.         │
│  │                          │  Called inline before ID format        │
│  │                          │  adaptation.                           │
│  └────────────┬─────────────┘                                         │
│               │                                                       │
│               ▼                                                       │
│  ┌──────────────────────────┐                                         │
│  │ 4. build_and_execute     │  Build query string from template      │
│  │    _graphql              │  → HTTP POST to Atlas API              │
│  │                          │  → Parse response                      │
│  │                          │  → Consume budget token on success     │
│  └────────────┬─────────────┘                                         │
│               │                                                       │
│               ▼                                                       │
│  ┌──────────────────────────┐                                         │
│  │ 5. format_graphql_       │  • Format API response as readable     │
│  │    results               │    text for the agent                   │
│  │                          │  • Attach Atlas links to state          │
│  │                          │    (only if API succeeded)              │
│  │                          │  • Build ToolMessage → back to agent    │
│  │                          │  • If rejected: return rejection note   │
│  └──────────────────────────┘                                         │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

### Node 1: `extract_graphql_question`

Identical pattern to `extract_tool_question` in the SQL pipeline. Extracts the `question` string from `tool_calls[0]["args"]`.

**State reset:** This node resets ALL `graphql_*` state fields at the start of each tool call cycle, mirroring how the SQL pipeline's `_turn_input()` / `extract_tool_question` starts a clean cycle. This prevents cross-turn leakage:
```python
# Reset GraphQL pipeline state at cycle start
graphql_classification=None, graphql_entity_extraction=None,
graphql_resolved_params=None, graphql_query=None,
graphql_api_target=None, graphql_raw_response=None, graphql_execution_time_ms=0,
graphql_atlas_links=[]
```

### Node 2: `classify_query`

Uses the **lightweight model** with structured output (see conventions above). This node classifies the query type and can **reject** the query if it doesn't fit any GraphQL API type. The schema is small and focused — classification only, no entity extraction.

```python
# --- Description constants (defined at module level) ---

QUERY_TYPE_DESCRIPTION = """
The Atlas GraphQL query type that best answers the user's question. Choose exactly one:
- reject       : Query doesn't fit any GraphQL API type — use when the question requires
                  custom SQL aggregation, multi-table joins, or data not available via the Atlas APIs.
- country_profile : Country overview including GDP, population, ECI, top exports,
                    diversification grade, peer comparisons (countryProfile API).
- country_lookback : Growth dynamics over a lookback period — how a country's exports
                     and complexity have changed (countryLookback API).
- new_products  : Products a country has started exporting recently (newProductsCountry API).
- treemap_products : What products does a country export in a given year — breakdown
                     by product (countryProductYear API).
- treemap_partners : Where does a country export to — breakdown by trading partner
                     (countryCountryYear API).
- treemap_bilateral : What products does country A export to country B — bilateral
                      product breakdown (countryCountryProductYear API).
- overtime_products : How have a country's product exports changed over time — time
                      series by product (countryProductYear API).
- overtime_partners : How have a country's trading partners changed over time — time
                      series by partner (countryCountryYear API).
- marketshare   : A country's share of global exports for a product over time
                  (countryProductYear + productYear APIs).
- product_space : Product space network — proximity and relatedness of exported products
                  (countryProductYear + productProduct APIs).
- feasibility   : Growth opportunity scatter — products plotted by complexity vs.
                  distance/feasibility (countryProductYear + productYear APIs).
- feasibility_table : Growth opportunity table — same data as feasibility in tabular form.
- country_year  : Country aggregate data by year — GDP, ECI, total trade values (countryYear API).
- product_info  : Global product-level data — trade value, PCI, number of exporters (productYear API).
- explore_bilateral : Bilateral trade data between two countries (countryCountryProductYear API).
- explore_group : Regional or group-level trade data — continents, income groups,
                  trade blocs (groupYear, groupGroupProductYear APIs).
- global_datum  : Global-level questions not tied to a specific country.
- explore_data_availability : Questions about data coverage — which years, products,
                              or countries have data available (dataAvailability API).

Routing guidance:
- For time-series questions ('how has X changed since Y'), prefer overtime_* or marketshare.
- For growth opportunity / diversification questions, prefer feasibility or feasibility_table.
- For 'what does country X export' snapshot questions, prefer treemap_products.
- For country overview / profile questions, prefer country_profile.
""".strip()

API_TARGET_DESCRIPTION = """
Which Atlas API endpoint to query. Choose one:
- explore        : The Explore API at /api/graphql — provides raw trade data, bilateral flows,
                   product relatedness, time series, and feasibility/opportunity data. Used by
                   treemap_*, overtime_*, marketshare, product_space, feasibility*, country_year,
                   product_info, explore_bilateral, explore_group, global_datum, and
                   explore_data_availability query types.
- country_pages  : The Country Pages API at /api/countries/graphql — provides derived analytical
                   profiles including countryProfile (46 fields), countryLookback (growth dynamics),
                   newProductsCountry, peer comparisons, and policy recommendations. Used by
                   country_profile, country_lookback, and new_products query types.
""".strip()


# --- Schema ---

class GraphQLQueryClassification(BaseModel):
    """Structured output for GraphQL query classification."""

    reasoning: str = Field(
        description="One sentence explaining which query type best fits this question and why.",
        max_length=300,
    )
    query_type: Literal[
        "reject",
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
    ] = Field(description=QUERY_TYPE_DESCRIPTION)
    rejection_reason: str | None = Field(
        default=None,
        description="Why this query can't be answered by the GraphQL API. Required when query_type is 'reject'.",
    )
    api_target: Literal["explore", "country_pages"] | None = Field(
        default=None, description=API_TARGET_DESCRIPTION
    )
```

The classification prompt describes all available query types and their use cases. The per-value descriptions in `QUERY_TYPE_DESCRIPTION` are embedded in the JSON schema, so the LLM sees the full explanation for each option during constrained generation. The prompt tells the LLM to pick the best match and to choose `"reject"` with a reason when no query type is suitable.

**Rejection routing:** After `classify_query`, a conditional edge checks `query_type`. If `"reject"`, the pipeline skips directly to `format_graphql_results`, which returns a ToolMessage to the agent: *"The atlas_graphql tool cannot answer this type of question. Reason: {rejection_reason}. Please use the query_tool (SQL) instead."* The agent then naturally falls back to `query_tool`.

### Node 2b: `extract_entities`

Runs only if classification accepted (not rejected). Uses the **lightweight model** with structured output. The prompt includes the `query_type` from classification to focus extraction — the LLM knows which fields are relevant for the classified type.

```python
PRODUCT_LEVEL_DESCRIPTION = """
Product aggregation level. Choose one:
- section    : Broadest grouping (~20 sectors like 'Agriculture', 'Machinery'). Best for high-level overviews.
- twoDigit   : HS 2-digit chapters (~97 categories like 'Coffee, tea, spices'). Good for sector-level analysis.
- fourDigit  : HS 4-digit headings (~1200 products like 'Coffee, not roasted'). Default and most commonly used.
- sixDigit   : Most detailed level (~5000 products). Only available in the Explore API, not Country Pages.
""".strip()

PRODUCT_CLASS_DESCRIPTION = """
Product classification system. Choose one:
- HS92  : Harmonized System 1992 revision (default). Data available 1995-2024. Most commonly used.
- HS12  : Harmonized System 2012 revision. Data available 2012-2024.
- HS22  : Harmonized System 2022 revision. Data available 2022-2024. Only available in the Explore API (not Country Pages or SQL pipeline).
- SITC  : Standard International Trade Classification. Data available 1962-2024. Use for long historical time series.
""".strip()

GROUP_TYPE_DESCRIPTION = """
Group type for regional/group queries (explore_group query type). Choose one:
- continent       : Continental grouping (e.g., Africa, Asia, Europe).
- region          : Sub-continental regions.
- subregion       : Finer sub-regional groupings.
- trade           : Trade blocs (e.g., EU, NAFTA, ASEAN).
- wdi_income_level : World Bank income groups (high, upper_middle, lower_middle, low).
- wdi_region      : World Bank regional classifications.
- political       : Political groupings.
- world           : The entire world as a single group.
""".strip()


class GraphQLEntityExtraction(BaseModel):
    """Structured output for entity extraction, given a classified query type."""

    reasoning: str = Field(
        description="One sentence explaining what entities you identified in the question and any ambiguities.",
        max_length=300,
    )
    country: str | None = Field(
        default=None, description="Country name mentioned in the question."
    )
    country_code_guess: str | None = Field(
        default=None,
        description="Best-guess ISO 3166-1 alpha-3 country code (e.g., 'KEN' for Kenya, 'USA' for United States, 'BRA' for Brazil).",
    )
    partner_country: str | None = Field(
        default=None, description="Trade partner country, if bilateral query."
    )
    partner_code_guess: str | None = Field(
        default=None,
        description="Best-guess ISO 3166-1 alpha-3 code for the partner country (e.g., 'ETH' for Ethiopia).",
    )
    product: str | None = Field(
        default=None, description="Product name mentioned in the question."
    )
    product_code_guess: str | None = Field(
        default=None,
        description=(
            "For goods: best-guess HS or SITC numeric code (e.g., '0901' for coffee, '8542' for "
            "electronic integrated circuits). For services: the service category name exactly as "
            "listed in the Atlas service categories (e.g., 'Travel & tourism', 'Transport'). "
            "Always provide standard codes, not internal Atlas IDs."
        ),
    )
    year: int | None = Field(default=None, description="Single year, if the question specifies one.")
    min_year: int | None = Field(default=None, description="Start year for time-series queries.")
    max_year: int | None = Field(default=None, description="End year for time-series queries.")
    product_level: Literal[
        "section", "twoDigit", "fourDigit", "sixDigit"
    ] | None = Field(default="fourDigit", description=PRODUCT_LEVEL_DESCRIPTION)
    lookback_years: Literal[3, 5, 10, 15] | None = Field(
        default=None,
        description="Lookback period in years for Country Pages growth dynamics (country_lookback query type).",
    )
    product_class: Literal[
        "HS92", "HS12", "HS22", "SITC"
    ] | None = Field(default=None, description=PRODUCT_CLASS_DESCRIPTION)
    group_type: str | None = Field(default=None, description=GROUP_TYPE_DESCRIPTION)
```

**Key design choices:**
- **`country_code_guess`** → ISO alpha-3 (not ISO numeric "404") — matches the SQL pipeline pattern. LLMs reliably know ISO alpha-3 codes from training data.
- **`product_code_guess`** → HS/SITC codes for goods, service category name for services. The LLM never guesses internal Atlas IDs.
- **Extraction prompt** includes the classified `query_type` and notes which fields are relevant for that type (e.g., `lookback_years` only for `country_lookback`, `group_type` only for `explore_group`).

**Services catalog injection (conditional):** When the question involves services (detected by keywords like "services", "tourism", "IT services", or by the classified `query_type` involving services), the extraction prompt includes the full list of Atlas service categories fetched from the services catalog cache (`productHs92(servicesClass: unilateral)` query). The catalog is small (~12-15 categories) and fits easily in a prompt, but is only injected conditionally to save tokens for the majority of goods-only queries.

The entity extraction prompt takes the classified `query_type` as input and extracts entities from the question. It uses per-value descriptions from the description constants for all Literal fields.

### Node 3: `resolve_ids`

**Dual-source resolution with LLM selection**, mirroring the SQL pipeline's product resolution pattern.

**Reference pattern from current system** (`src/product_and_schema_lookup.py`):
1. `extract_products` — LLM guesses product codes from the question
2. `get_candidate_codes()` — Verifies LLM guesses against DB AND searches DB by name → produces `ProductSearchResult` with `llm_suggestions` + `db_suggestions`
3. `select_final_codes()` — LLM is presented both sources and picks the best match in context

**The GraphQL pipeline mirrors this for both country IDs and product IDs:**

**Step A — Verify standard codes against lookup tables:**
- Country: Take `country_code_guess` (ISO alpha-3, e.g., "KEN") from extraction → look it up in the cached country catalog (from Explore API's `locationCountry` query) by `iso3Code` field → get the entry with internal numeric ID (e.g., 404) and official name/metadata. If not found, return empty.
- Product (goods): Take `product_code_guess` (HS/SITC code, e.g., "0901") → look it up in the cached product catalog (from Explore API's `productHs92` query) by `code` field → get the entry with internal product ID (e.g., 726). If not found, return empty.
- Product (services): Take `product_code_guess` (service category name, e.g., "Travel & tourism") → match against the services catalog by `nameShortEn` → get the internal product ID.

The LLM never guesses internal IDs. It guesses standard codes that it knows from training data, and `resolve_ids` translates those to internal IDs via lookup tables.

**Step B — Search lookup tables by name/description:**
- Text-search `country` name in the country catalog (~250 entries). Fuzzy/substring match. Return top candidates with names/metadata.
- Text-search `product` name in the product catalog (~1200 HS4 entries). Fuzzy/substring match. Return top candidates.

**Step C — LLM selects best IDs:**
- Present both sources (verified LLM guesses + catalog search results) to the lightweight model.
- LLM picks the single best country ID and product ID based on the original question context.
- If no good match exists, the LLM can exclude that entity from the final resolution.

The selection prompt presents the candidate IDs from both sources and asks the LLM to pick the best match.

**Caching:** The country and product catalogs are fetched once at startup via the Explore API (`locationCountry`, `productHs92`) and cached in `CacheRegistry` with a 24-hour TTL. These are small datasets and change infrequently.

**Important: Product IDs are internal, not HS codes.** The URL format is `product=product-HS92-{internal_id}` where the internal ID (e.g., 726 for Coffee) is NOT the HS code (0901). The mapping comes from the `productHs92` catalog query. This means:
- `resolve_ids` must resolve products to **internal catalog IDs**, not HS codes
- The product catalog cache must index by both HS code (for verifying LLM guesses) and by name (for text search)
- The `generate_atlas_links()` function (called inline within `resolve_ids`) needs the internal product ID to construct correct URLs

`resolve_ids` translates standard codes (ISO alpha-3, HS/SITC codes, service category names) into internal Atlas IDs, generates Atlas links, then formats those IDs for the target API.

**Step D — Generate Atlas links (deterministic, inline):**

After IDs are resolved but BEFORE formatting for the target API, `resolve_ids` calls `generate_atlas_links(query_type, resolved_params)` from `src/atlas_links.py`. This ordering matters because link generation needs the canonical integer ID form (e.g., `country_id: 404`), not the API-specific prefixed form (e.g., `"location-404"`). The generated links are written to `graphql_atlas_links` in the state update. See the detailed [Atlas Link Generation](#8-atlas-link-generation) section.

Link generation is deterministic and takes microseconds — no external calls. If it fails unexpectedly, the error is caught and `graphql_atlas_links` is set to `[]` (empty). This never blocks the rest of the pipeline.

**ID format adaptation:** After link generation, the node formats IDs for the target API using a centralized transformation map:

| Parameter | Explore API (`/api/graphql`) | Country Pages API (`/api/countries/graphql`) |
|-----------|------------------------------|----------------------------------------------|
| Country ID | `countryId: 404` (integer) | `location: "location-404"` (prefixed string) |
| Product ID | `productId: 726` (integer) | `product: "product-HS-726"` (prefixed string) |
| Year (single) | `yearMin: 2024, yearMax: 2024` | `year: 2024` |
| Year (range) | `yearMin: 1995, yearMax: 2024` | `minYear: 1995, maxYear: 2024` |
| Product level | `2, 4, 6` (integers) | `"section", "twoDigit", "fourDigit"` (strings) |
| Product class | `"HS92", "HS12", "HS22", "SITC"` | `"HS", "SITC"` |

**Note on country IDs:** The numeric country IDs (e.g., 404 for Kenya) correspond to M49 codes as designated by the UN (which coincide with ISO 3166-1 numeric codes for most countries). The official API documentation references M49, not ISO 3166-1 numeric. See [UN M49](https://unstats.un.org/unsd/methodology/m49/).

**Note:** HS22 is only available in the Explore API — it is not supported by the Country Pages API or the SQL pipeline. HS22 data coverage is limited to 2022-2024. If a user requests HS22 with a Country Pages query type, the pipeline should fall back to HS92 with a resolution note.

This transformation map should be implemented as a single function in `resolve_ids` rather than scattered across nodes.

### Node 4: `build_and_execute_graphql`

Constructs the GraphQL query string from a template + resolved params. Sends it via `AtlasGraphQLClient` (httpx). Parses the response. Consumes one rate limit budget token via `budget_tracker.consume()` (see [consume-on-success semantics](#consume-on-success) under [Rate Limit Circuit Breaker](#6-rate-limit-circuit-breaker)). If budget is exhausted at this point (race condition — budget was available when agent called the tool but depleted by concurrent requests), writes an error to `graphql_raw_response`.

**Error-safety requirement:** This node should catch all errors and write them to state rather than raising, so that `format_graphql_results` can return an informative error ToolMessage to the agent:

```python
# In build_and_execute_graphql:
try:
    response = await client.execute(query, variables)
    return {"graphql_raw_response": response, ...}
except httpx.HTTPStatusError as e:
    return {"graphql_raw_response": {"error": f"HTTP {e.response.status_code}", "detail": str(e)}, ...}
except Exception as e:
    return {"graphql_raw_response": {"error": "unexpected_error", "detail": str(e)}, ...}
```

### Node 5: `format_graphql_results`

Receives the API response from Node 4 and the Atlas links (already generated in Node 3 `resolve_ids`). Combines them into a ToolMessage:
- If API succeeded: formats response as readable text + attaches links to state
- If API failed (`graphql_raw_response` contains `"error"` key): returns error ToolMessage to agent, discards links
- If pipeline was rejected at classification: returns rejection message (links were never generated)

### Error Recovery Strategy

All pipeline nodes must be non-fatal — they must never raise exceptions that would break the graph execution. Each node has an explicit error-to-state mapping:

| Node | Error Scenario | Behavior |
|------|---------------|----------|
| `classify_query` | LLM API call fails (timeout, 500, parsing error) | Treat as rejection: write `query_type="reject"`, `rejection_reason="Classification failed: {error}"` to state. Pipeline skips to `format_graphql_results` which returns error ToolMessage. |
| `extract_entities` | LLM API call fails (timeout, 500, parsing error) | Write error to state, skip to `format_graphql_results` which returns error ToolMessage: "Entity extraction failed: {error}". |
| `resolve_ids` | No matching country/product found in catalogs | Write error to `graphql_resolved_params`: `{"error": "Could not resolve entities: no matching country/product found"}`. `format_graphql_results` returns error ToolMessage. |
| `resolve_ids` | Service category not found in catalog | Write resolution note ("Service category '{name}' not found — using best available match") + use closest match from services catalog. If no match at all, treat as error. |
| `resolve_ids` | LLM selection call fails | Use best catalog match without LLM selection, or treat as error if no candidates exist. |
| `resolve_ids` | Atlas link generation fails (unexpected error in `generate_atlas_links()`) | Catch exception, log warning, set `graphql_atlas_links=[]`. ID resolution result is unaffected — pipeline continues. |
| `build_and_execute_graphql` | HTTP error (4xx, 5xx, timeout) | Write error dict to `graphql_raw_response` (never raise). See Node 4 above. |
| `build_and_execute_graphql` | Budget exhausted (race condition) | Write `{"error": "budget_exhausted"}` to `graphql_raw_response`. |

In all error cases, the agent receives a ToolMessage explaining what went wrong. The agent can then fall back to `query_tool` (SQL) or inform the user.

### RetryPolicy for LLM and HTTP Nodes

LangGraph supports attaching `RetryPolicy` to nodes for automatic retry on transient failures. This is valuable for nodes that make external calls:

```python
from langgraph.pregel import RetryPolicy

builder.add_node("classify_query", classify_node,
    retry=RetryPolicy(max_attempts=3, backoff_factor=1.5, retry_on=Exception))
builder.add_node("extract_entities", extract_entities_node,
    retry=RetryPolicy(max_attempts=3, backoff_factor=1.5, retry_on=Exception))
builder.add_node("build_and_execute_graphql", execute_graphql_node,
    retry=RetryPolicy(max_attempts=3, backoff_factor=2.0, retry_on=Exception))
```

**Which nodes get RetryPolicy:**
- `classify_query` — LLM call, subject to transient API errors. 3 attempts with 1.5x backoff.
- `extract_entities` — LLM call, subject to transient API errors. 3 attempts with 1.5x backoff.
- `build_and_execute_graphql` — HTTP call, subject to transient network errors. 3 attempts with 2.0x backoff.
- `resolve_ids` — Contains an LLM call (Step C) but also catalog lookups. Retry the whole node is acceptable since Steps A+B are idempotent.
- Other nodes (extract, format) — pure functions, no retry needed. Atlas link generation is deterministic and runs inline within `resolve_ids`, so it benefits from `resolve_ids`' retry policy.

**Note:** RetryPolicy retries before the node's error handling catches the exception. For `build_and_execute_graphql`, this means transient HTTP errors get retried automatically, and only persistent failures reach the `except` block that writes to state.

---

## 6. Rate Limit Circuit Breaker

### Official API Usage Warning

The Harvard Growth Lab's [official API documentation](https://github.com/harvard-growth-lab/api-docs/blob/main/atlas.md) includes an explicit warning that is directly relevant to this project:

> "The Atlas API is specifically designed to support functionality for the Atlas of Economic Complexity. Public access is provided as a courtesy to users who may find it useful, but it is always possible that specific endpoints or data values may change suddenly and without warning. **The Atlas API is best used to access data for stand-alone economic analysis, not to support other software applications.**"

This means our project's reliance on the GraphQL API carries inherent risk: endpoints may change without notice, and the Growth Lab does not guarantee stability for software integrations. This is a known and accepted risk. The mitigation is architectural: the three-mode system (auto/graphql_sql/sql_only) ensures the system can always fall back to the SQL pipeline if the API changes or becomes unavailable. The circuit breaker and budget tracker (below) handle transient failures; the `sql_only` mode handles prolonged API disruption. If the API introduces breaking changes, the GraphQL pipeline can be disabled via configuration while we adapt.

### Budget Tracker Mechanism

A process-global `GraphQLBudgetTracker` implements a sliding-window counter:

```python
class GraphQLBudgetTracker:
    MAX_REQUESTS = 100       # Leave 20/min headroom from 120 limit
    WINDOW_SECONDS = 60.0

    def is_available(self) -> bool: ...
    def remaining(self) -> int: ...
    def consume(self) -> bool: ...   # Atomic check-and-decrement
```

Uses `collections.deque` of monotonic timestamps + `asyncio.Lock` for thread-safety across concurrent requests.

<a id="consume-on-success"></a>
**Consume-on-success semantics:** Budget tokens are consumed AFTER a successful HTTP response (2xx), not before the HTTP call. This prevents the API being down from quickly exhausting the budget and unnecessarily degrading to SQL-only mode. The flow is:
1. `budget_tracker.is_available()` → check if budget exists (pre-flight, no consumption)
2. Make HTTP call
3. If 2xx response → `budget_tracker.consume()` (record the successful request)
4. If HTTP error → do NOT consume (failed requests don't burn budget)

### Circuit Breaker for API Health

The budget tracker answers "are we under the rate limit?" (capacity), but doesn't address "is the API actually working?" (health). A lightweight circuit breaker in `AtlasGraphQLClient` complements the budget tracker:

```python
class CircuitBreaker:
    FAILURE_THRESHOLD = 5     # Consecutive failures to trip
    RECOVERY_TIMEOUT = 30.0   # Seconds before half-open

    def record_success(self) -> None: ...
    def record_failure(self) -> None: ...
    def is_open(self) -> bool: ...  # True = API is down, fast-fail
```

**States:**
- **CLOSED** (normal): All requests pass through. Track consecutive failure count.
- **OPEN** (tripped after 5 consecutive failures): All requests fast-fail for 30 seconds. `is_open()` returns `True`.
- **HALF-OPEN** (after 30s recovery timeout): Allow ONE request through. If it succeeds → CLOSED. If it fails → OPEN again.

**Integration with mode resolution:** The `agent_node` checks both:
```python
graphql_available = budget_tracker.is_available() and not circuit_breaker.is_open()
```

This means the system degrades to SQL-only when either the rate limit is reached OR the API is unhealthy — without wasting budget on a broken API.

### Three-Layer Integration

**Layer 1: Mode resolution** (before each agent turn). The `agent_node` resolves the effective mode (see §2 for mode semantics, §4 Key Design Decision #3 for dynamic tool binding). Budget status injected into the dual-tool prompt:
- `> 20` remaining → "GraphQL APIs are available."
- `5–20` remaining → "GraphQL rate limit is running low. Prefer SQL for non-essential queries."

**Layer 2: Hard gate in execution** (race condition safety). `build_and_execute_graphql` checks `budget_tracker.is_available()` before the HTTP call. If exhausted, writes `{"error": "budget_exhausted"}` to `graphql_raw_response`.

**Layer 3: Circuit breaker feedback.** After each HTTP call in `build_and_execute_graphql`:
- 2xx → `circuit_breaker.record_success()` + `budget_tracker.consume()`
- Error → `circuit_breaker.record_failure()` (no budget consumption)

---

## 7. Verification Workflow

The agent naturally decides to call the other tool when results seem implausible. No special mechanism is needed — the ReAct loop already supports multi-turn tool calls.

**Example flow:**

```
Agent → atlas_graphql("What is Kenya's total exports in 2024?")
GraphQL returns: exportValue = $16.2B

Agent thinks: "That seems plausible for Kenya. Moving on."
Agent → Final answer with $16.2B

--- OR ---

Agent → atlas_graphql("What is Kenya's total exports in 2024?")
GraphQL returns: exportValue = $1.62 (clearly wrong — missing scale factor)

Agent thinks: "This seems too low. Let me verify via SQL."
Agent → query_tool("What is Kenya's total export value in 2024?")
SQL returns: export_value = 16,200,000,000

Agent → "Kenya's total exports in 2024 were approximately $16.2 billion.
         (Verified across both data sources.)"
```

Verification guidance added to the extended agent prompt:

```
**Trust & Verification:**
- If a result from either tool seems implausible (e.g., unexpectedly zero,
  wrong order of magnitude, or contradicts known facts), you may verify
  by querying the other data source.
- When you verify, briefly note it: "I verified this via [SQL/GraphQL]
  and the results are consistent" or flag any discrepancy.
- Verification is optional — use it when your confidence is low.
```

Verification queries appear in the pipeline stepper like any other query. The agent's text briefly mentions the verification. The user can see both queries ran but isn't overwhelmed.

---

## 8. Atlas Link Generation

> **Reference documentation:** The markdown documents in `evaluation/` contain authoritative reference material for the link generation implementation. These should be consulted during implementation:
> - `evaluation/atlas_explore_pages_exploration.md` — Complete Explore page URL parameter reference, API→URL mapping
> - `evaluation/explore_page_collection_guide.md` — Product ID↔HS code mappings, working API queries
> - `evaluation/atlas_country_pages_exploration.md` — All 12 country page subpage URL patterns
> - `evaluation/country_page_collection_guide.md` — Country page data point catalog

### Architecture

Atlas links are **structured output** — deterministically generated by the `resolve_ids` node (inline, via `generate_atlas_links()` from `src/atlas_links.py`), not by the LLM. They appear below the agent's response as clickable citation-style pills. **Links are always generated when a deterministic query_type→URL mapping exists.** Rather than suppressing links based on a numeric confidence score, entity resolution quality is surfaced transparently via `resolution_notes`.

### Deterministic URL Construction

URL generation is fully deterministic — a lookup-table function from `(query_type, resolved_params)` → URL string. The reverse mapping from the Explore Pages documentation defines the exact parameter format:

```
Base: https://atlas.hks.harvard.edu/explore/{vizType}
Required: ?year={yearMax}&exporter=country-{countryId}
Optional: &importer=country-{partnerCountryId}       (if bilateral)
          &product=product-{classification}-{productId} (if product-specific)
          &startYear={yearMin}&endYear={yearMax}      (if time series)
          &productLevel={productLevel}                (if feasibility/table)
          &view=markets                               (if locations mode)
```

**Note:** Product IDs in URLs use the internal numeric format (`product-HS92-726` for Coffee), NOT the HS code (0901). The mapping comes from the product catalog — see Node 3 `resolve_ids`.

### Product Classification in URLs

The `product` URL parameter supports 4 classification-specific prefixes, each with a different internal ID space and year range:

| Classification | URL Prefix | Year Range | Example (Coffee) |
|----------------|-----------|------------|-------------------|
| HS92 (default) | `product-HS92-{id}` | 1995–2024 | `product-HS92-726` |
| HS12 | `product-HS12-{id}` | 2012–2024 | `product-HS12-725` |
| HS22 | `product-HS22-{id}` | 2022–2024 | `product-HS22-727` |
| SITC | `product-SITC-{id}` | 1962–2024 | `product-SITC-726` |

The numeric ID part is classification-specific — the **same product** (e.g., Coffee/0901) has **different internal numeric IDs** across classifications. The `resolve_ids` node must track the active product classification and use the corresponding catalog (`productHs92`, `productHs12`, `productHs22`, `productSitc`). Default classification is HS92 (matching the Atlas site default).

The `exporter` and `importer` URL parameters use `country-{numericId}` format (same across all classifications). Country IDs use prefixed format in API responses (`country-404`) but bare integers in API arguments (`countryId: 404`).

### Entity Resolution Transparency

Instead of a numeric confidence score that gates link emission, each link carries a `resolution_notes: list[str]` field that transparently surfaces how entities were resolved.

- `resolution_notes` is **empty** when resolution was clean and unambiguous.
- `resolution_notes` is **populated** with human-readable notes when entities were ambiguously resolved, e.g.:
  - `"Country 'Turkey' resolved to Türkiye (792) — multiple name variants matched"`
  - `"Product 'chips' resolved to Electronic integrated circuits (8542) — ambiguous term"`
  - `"Year not specified in question — defaulted to 2024"`

These notes originate in the `resolve_ids` node (Node 3, §5), which writes them to the resolved params. The `generate_atlas_links()` call within `resolve_ids` passes them through to `AtlasLink.resolution_notes`. They flow through to the SSE event and are available for the frontend to display (e.g., as a subtle tooltip on the link pill).

### URL Rules by Query Type

**Country Pages (base URL: `https://atlas.hks.harvard.edu/countries/{m49_id}`):**

> **Note:** Country IDs correspond to M49 codes as designated by the UN (which coincide with ISO 3166-1 numeric codes for most countries). See [UN M49](https://unstats.un.org/unsd/methodology/m49/).

| Query Type | Resolved Params | Atlas URL |
|---|---|---|
| `country_profile` | country_id | `/countries/{id}` |
| `country_lookback` | country_id | `/countries/{id}/growth-dynamics` |
| `new_products` | country_id | `/countries/{id}/new-products` |
| `country_profile` (exports context) | country_id | `/countries/{id}/export-basket` |
| `country_profile` (complexity context) | country_id | `/countries/{id}/export-complexity` |
| `country_year` | country_id | `/countries/{id}` |

**Explore Pages — Treemap (base URL: `https://atlas.hks.harvard.edu/explore/treemap`):**

| Query Type | Resolved Params | Atlas URL |
|---|---|---|
| `treemap_products` | country_id + year | `?year={Y}&exporter=country-{id}` |
| `treemap_partners` | country_id + year | `?year={Y}&exporter=country-{id}&view=markets` |
| `treemap_bilateral` | country_id + partner_id + year | `?year={Y}&exporter=country-{id}&importer=country-{pid}` |
| `product_info` | product_id + year | `?year={Y}&product=product-{cls}-{pid}` |
| `explore_bilateral` | country_id + partner_id + year | `?year={Y}&exporter=country-{id}&importer=country-{pid}` |
| `explore_group` | group_id + year | `?year={Y}&exporter=group-{gid}` (only if group entity resolved) |

**Explore Pages — Time Series:**

| Query Type | Resolved Params | Atlas URL |
|---|---|---|
| `overtime_products` | country_id + year range | `/explore/overtime?year={maxY}&startYear={minY}&endYear={maxY}&exporter=country-{id}` |
| `overtime_partners` | country_id + year range | `/explore/overtime?year={maxY}&startYear={minY}&endYear={maxY}&exporter=country-{id}&view=markets` |
| `marketshare` | country_id + year range | `/explore/marketshare?year={maxY}&startYear={minY}&endYear={maxY}&exporter=country-{id}` |

**Explore Pages — Network & Opportunity:**

| Query Type | Resolved Params | Atlas URL |
|---|---|---|
| `product_space` | country_id + year | `/explore/productspace?year={Y}&exporter=country-{id}` |
| `feasibility` | country_id + year | `/explore/feasibility?year={Y}&exporter=country-{id}` |
| `feasibility_table` | country_id + year + product_level | `/explore/feasibility/table?year={Y}&exporter=country-{id}&productLevel={lvl}` |

**No Link Generated (no mapping exists):**

| Query Type | Reason |
|---|---|
| `global_datum` | No Atlas page exists for global-level questions |
| `explore_data_availability` | Informational query; no corresponding Atlas page |

### Frontier Country Handling

Country Pages subpages `growth-opportunities` and `product-table` are **unavailable for highest-complexity "frontier" countries** (USA, Germany, Japan, etc.). The link generator maintains a hardcoded list of frontier countries.

**Fallback strategy:** For frontier countries, instead of falling back to the narrative `/countries/{id}/strategic-approach` page (which provides no actionable data), fall back to the **Explore feasibility page** — which is available for ALL countries:
- `growth-opportunities` → `/explore/feasibility?year={Y}&exporter=country-{id}` (scatter view)
- `product-table` → `/explore/feasibility/table?year={Y}&exporter=country-{id}&productLevel=4` (table view)

This gives users the same underlying data (complexity, distance, opportunity gain) in a different visualization, rather than a purely narrative page.

### Multiple Links Per Query

Some query types generate multiple links (primary + supplementary):

| Query Type | Primary Link | Supplementary Link(s) |
|-----------|-------------|----------------------|
| `treemap_products` | `/explore/treemap?...` | `/countries/{id}/export-basket` (Country Page) |
| `country_lookback` | `/countries/{id}/growth-dynamics` | `/explore/overtime?...` (visual time series) |
| `feasibility` | `/explore/feasibility?...` | `/explore/feasibility/table?...` (table view) |
| `product_space` | `/explore/productspace?...` | `/countries/{id}/export-complexity` (Country Page) |
| `overtime_products` | `/explore/overtime?...` | `/explore/treemap?...` (snapshot for latest year) |

### Standalone Implementation

`src/atlas_links.py` is a standalone Python module with pure, deterministic functions — no LLM calls, no HTTP, no graph dependencies.

- **Core function:** `generate_atlas_links(query_type, resolved_params) → list[AtlasLink]`
- **Helper functions** for each URL pattern (country page URLs, explore page URLs)
- **Product classification registry** mapping `ProductClass` → URL prefix format
- Independently testable via `pytest` — see §13.1 test matrix for detailed assertions

### Data Structure

```python
@dataclass
class AtlasLink:
    url: str
    label: str
    link_type: Literal["country_page", "explore_page"]
    resolution_notes: list[str]  # Empty = clean resolution; non-empty = ambiguity notes
```

### SSE Emission

Atlas links are emitted as part of the `pipeline_state` event for `format_graphql_results`:

```json
{
  "event": "pipeline_state",
  "data": {
    "stage": "format_graphql_results",
    "atlas_links": [
      {
        "url": "https://atlas.hks.harvard.edu/countries/404",
        "label": "Kenya — Country Profile",
        "link_type": "country_page",
        "resolution_notes": []
      },
      {
        "url": "https://atlas.hks.harvard.edu/explore/treemap?year=2024&exporter=country-404",
        "label": "Kenya — Export Basket (2024)",
        "link_type": "explore_page",
        "resolution_notes": ["Year not specified in question — defaulted to 2024"]
      }
    ],
    "query_index": 1
  }
}
```

Atlas links are also persisted in `turn_summaries` for history restoration.

### Entity Resolution Transparency in Both Pipelines

Entity resolution transparency is a cross-cutting concern — both the GraphQL and SQL pipelines resolve entities (countries, products) but currently neither surfaces ambiguity to the user.

**GraphQL pipeline (§5, Node 3 `resolve_ids`):**
- Already does dual-source resolution. Add `resolution_notes: list[str]` to the output dict.
- Notes flow into `AtlasLink.resolution_notes` via the `generate_atlas_links()` call within `resolve_ids`.
- Notes also appear in the SSE `pipeline_state` event for `resolve_ids`.

**SQL pipeline (existing `product_and_schema_lookup.py`):**
- Currently `select_final_codes()` silently drops ambiguous products — no signal reaches the user or frontend.
- Add a `resolution_notes` field to the product resolution output.
- Capture: products dropped due to ambiguity, products with close-match alternatives.
- Surface in a new field in the SSE `pipeline_state` event for `extract_products` / `lookup_codes`.
- This is a small extension, not a rewrite of the product resolution logic.

---

## 9. State Schema

Extended `AtlasAgentState` (`src/state.py`). All fields use last-write-wins semantics unless annotated with a reducer function.

```python
class AtlasAgentState(TypedDict):
    """State carried through each node of the Atlas agent graph."""

    # === Shared (all pipelines) ===
    messages: Annotated[list[BaseMessage], add_messages]    # Chat history (reducer: append + dedup by ID)
    queries_executed: int                                   # SQL/GraphQL queries this turn; docs_tool does NOT increment
    last_error: str                                         # Most recent error message, "" if none
    turn_summaries: Annotated[list[dict], add_turn_summaries]  # Per-turn pipeline summaries (reducer: append)

    # === Trade overrides (shared, None = auto-detect) ===
    override_schema: Optional[str]       # Classification schema override, e.g. "hs92", "sitc"
    override_direction: Optional[str]    # Trade direction override, e.g. "export", "import"
    override_mode: Optional[str]         # Trade mode override, e.g. "goods", "services"

    # === SQL pipeline state (reset by extract_tool_question at cycle start) ===
    pipeline_question: str               # Question extracted from query_tool tool_call args
    pipeline_context: str                # Optional context from agent (docs findings, reasoning); "" if omitted
    pipeline_products: Optional[SchemasAndProductsFound]  # Product/schema extraction results from extract_products
    pipeline_codes: str                  # Formatted product codes string for the SQL generation prompt
    pipeline_table_info: str             # Table DDL/column descriptions for identified schemas
    pipeline_sql: str                    # Generated (and validated) SQL query string
    pipeline_result: str                 # Formatted query result string for the agent
    pipeline_result_columns: list[str]   # Column names from the last executed query
    pipeline_result_rows: list[list]     # Row data from the last executed query (for frontend tables)
    pipeline_execution_time_ms: int      # SQL execution time in milliseconds

    # === GraphQL pipeline state (reset by extract_graphql_question at cycle start) ===
    graphql_question: str                # Question extracted from atlas_graphql tool_call args
    graphql_context: str                 # Optional context from agent; "" if omitted
    graphql_classification: Optional[dict]   # GraphQLQueryClassification output as dict (see Node 2, §5)
    graphql_entity_extraction: Optional[dict]  # GraphQLEntityExtraction output as dict (see Node 2b, §5)
    graphql_resolved_params: Optional[dict]  # Final resolved IDs + API-formatted params (see Node 3, §5)
    graphql_query: Optional[str]         # Constructed GraphQL query string sent to the API
    graphql_api_target: Optional[str]    # "explore" (/api/graphql) or "country_pages" (/api/countries/graphql)
    graphql_raw_response: Optional[dict] # Raw API response, or {"error": ..., "detail": ...} on failure
    graphql_execution_time_ms: int       # API call duration in milliseconds
    graphql_atlas_links: list[dict]      # AtlasLink objects as dicts: [{url, label, link_type, resolution_notes}]

    # === SQL pipeline resolution transparency ===
    pipeline_resolution_notes: list[str]  # Entity resolution notes from SQL product/schema lookup; empty = clean

    # === Docs pipeline state (reset by extract_docs_question at cycle start) ===
    docs_question: str                   # Question extracted from docs_tool tool_call args
    docs_context: str                    # Broader user query / reasoning for why docs are needed; "" if omitted
    docs_selected_files: list[str]       # Document filenames selected by the LLM (Step A of Node 2, §14.3)
    docs_result: str                     # Synthesized documentation response from the LLM (Step C of Node 2, §14.3)
```

**Reset semantics:** Each pipeline's state fields are reset to defaults at the start of every tool call cycle (by the respective `extract_*` node) to prevent cross-turn leakage.

**Context fields:** Default to `""` when the agent omits context. See §14.5 for per-node usage.

**`dict` fields:** GraphQL state fields use `dict` (not Pydantic models) for reliable LangGraph state serialization. Pydantic models (§5) are used for structured LLM output parsing, then converted to `dict` before writing to state.

---

## 10. File Structure

### New Files

| File | Purpose | Est. Lines |
|------|---------|-----------|
| `src/graph.py` | Graph construction: `build_atlas_graph()`, all node wiring, edge definitions, compile. Single entry point for the graph. | ~200 |
| `src/agent_node.py` | Agent node function, dynamic tool binding, mode resolution, system prompt assembly. | ~150 |
| `src/graphql_pipeline.py` | All GraphQL pipeline nodes: `extract_graphql_question`, `classify_query`, `extract_entities`, `resolve_ids` (includes inline Atlas link generation), `build_and_execute_graphql`, `format_graphql_results`. Two Pydantic schemas (`GraphQLQueryClassification`, `GraphQLEntityExtraction`) with description constants. Classification, extraction, and ID selection LLM chains. | ~700 |
| `src/graphql_client.py` | `AtlasGraphQLClient` (httpx), `GraphQLBudgetTracker`, `CircuitBreaker`, GraphQL query template builders. | ~350 |
| `src/atlas_links.py` | `generate_atlas_links()` helper function (called inline from `resolve_ids`, not a graph node). Deterministic URL builders, product classification registry, query-type→link dispatch, `AtlasLink` dataclass, frontier country list. | ~200 |
| `src/docs_pipeline.py` | Docs pipeline nodes: `extract_docs_question`, `select_and_synthesize`, `format_docs_results`. `DocsToolInput` schema. Document manifest loader. Selection and synthesis LLM chains. | ~250 |

### Documentation Files

| File | Purpose | Est. Size |
|------|---------|-----------|
| `src/docs/metrics_glossary.md` | Comprehensive metric definitions (issue #50 content) | ~200 lines |
| `src/docs/trade_methodology.md` | Atlas data methodology, research paper summary (issue #50 content) | ~400 lines |
| `src/docs/country_page_reproduction.md` | SQL patterns for Atlas country page visualizations (issue #50 content) | ~300 lines |
| `src/docs/data_coverage.md` | Year availability, services vs goods, known gaps (issue #50 content) | ~150 lines |

### Modified Files

| File | Changes |
|------|---------|
| `src/state.py` | Add GraphQL pipeline state fields, docs pipeline state fields, and context fields (`pipeline_context`, `graphql_context`, `docs_context`) for structured question/context separation. |
| `src/config.py` | Add `AgentMode` enum, `agent_mode` setting (default: `"auto"`). Rename `query_model`/`metadata_model` to `frontier_model`/`lightweight_model`. Add `prompt_model_assignments` setting and `get_prompt_model()` helper for per-prompt model resolution. |
| `model_config.py` | Add `AGENT_MODE = "auto"` default. Rename `QUERY_MODEL`/`METADATA_MODEL` to `FRONTIER_MODEL`/`LIGHTWEIGHT_MODEL`. Add `PROMPT_MODEL_ASSIGNMENTS` dict mapping each prompt key to `"frontier"` or `"lightweight"`. |
| `src/sql_pipeline.py` | **Renamed from `src/generate_query.py`**. Remove graph construction (moved to `graph.py`). Remove agent node (moved to `agent_node.py`). Keep all SQL pipeline node functions + SQL prompts. Minor refactors for clean imports. |
| `src/streaming.py` | **Renamed from `src/text_to_sql.py`**. Update `PIPELINE_NODES`/`NODE_LABELS` to include GraphQL + docs nodes (see complete map below). Extend `_extract_pipeline_state` for GraphQL and docs events. Extend turn summary builder for atlas_links. |
| `src/api.py` | Track GraphQL events + atlas_links in SSE generator. Add `mode` parameter to `/api/chat/stream`. Persist atlas_links in turn summaries. |
| `src/cache.py` | Add country lookup cache (24h TTL), product ID cache (24h TTL), services catalog cache (24h TTL, full list of service category names and IDs from `productHs92(servicesClass: unilateral)` query), GraphQL response cache (optional). **Lazy fetching with warm-on-first-request:** Catalogs are NOT fetched at server startup (avoids cold-start delays and startup failures if the API is down). Instead, the first `resolve_ids` call triggers the fetch and caches the result. Subsequent calls use the cache. Consider adding a background task in `create_async` to warm the cache shortly after the first request. |
| `src/product_and_schema_lookup.py` | No changes to internals. Used by SQL pipeline as-is. |

### Test Files

| File | Purpose | Est. Lines |
|------|---------|-----------|
| `src/tests/test_atlas_links.py` | URL generation for all query types, all 4 product classification formats, frontier fallback, resolution_notes propagation, edge cases | ~250 |
| `src/tests/test_graphql_client.py` | Budget tracker (sliding window, concurrent access, edge cases), mocked HTTP calls | ~200 |
| `src/tests/test_graphql_pipeline.py` | All 6 nodes with mocked LLM + HTTP. Classification + extraction split. Rejection path (skips extraction). Dual-source ID resolution with standard codes + inline link generation. Services catalog injection. | ~450 |
| `src/tests/test_graph.py` | Graph construction for all 3 modes. Verify SQL-only mode produces identical behavior to current system. Verify dual-tool routing. | ~200 |
| `src/tests/test_agent_node.py` | Dynamic tool binding, mode resolution, prompt assembly | ~150 |
| `src/tests/test_docs_pipeline.py` | All docs pipeline nodes with mocked LLM + test docs. Selection, synthesis, fallback paths. | ~300 |

### Updated `NODE_LABELS` for Streaming

The complete node label map for `src/streaming.py`, covering all three pipelines:

```python
NODE_LABELS = {
    # SQL pipeline (unchanged)
    "extract_tool_question": "Extracting question",
    "extract_products": "Identifying products",
    "lookup_codes": "Looking up product codes",
    "get_table_info": "Loading table metadata",
    "generate_sql": "Generating SQL query",
    "validate_sql": "Validating SQL",
    "execute_sql": "Executing query",
    "format_results": "Formatting results",
    "max_queries_exceeded": "Query limit reached",
    # GraphQL pipeline
    "extract_graphql_question": "Extracting question",
    "classify_query": "Classifying query type",
    "extract_entities": "Extracting entities",
    "resolve_ids": "Resolving entity IDs",
    "build_and_execute_graphql": "Querying Atlas API",
    "format_graphql_results": "Formatting results",
    # Docs pipeline
    "extract_docs_question": "Extracting question",
    "select_and_synthesize": "Consulting documentation",
    "format_docs_results": "Preparing documentation",
}
```

---

## 11. LLM Prompts Inventory

> **Note:** All LLM prompts were user-vetted before implementation.

### Model Types

The system defines two model types:

- **Frontier model** — the most capable model, used for tasks requiring complex reasoning (e.g., agent orchestration, SQL generation). Configured via `FRONTIER_MODEL` / `FRONTIER_MODEL_PROVIDER` in `model_config.py`.
- **Lightweight model** — a faster, cheaper model, used for structured extraction and classification tasks. Configured via `LIGHTWEIGHT_MODEL` / `LIGHTWEIGHT_MODEL_PROVIDER` in `model_config.py`.

Each prompt in the system is assigned one of these two model types. The assignment is configurable per-prompt via `PROMPT_MODEL_ASSIGNMENTS` in `model_config.py`, so any prompt can be switched between frontier and lightweight without code changes.

### Per-Prompt Model Assignment

**Configuration in `model_config.py`:**

```python
# --- Frontier model (complex reasoning, agent orchestration) ---
FRONTIER_MODEL = "gpt-5.2"
FRONTIER_MODEL_PROVIDER = "openai"

# --- Lightweight model (extraction, classification, selection) ---
LIGHTWEIGHT_MODEL = "gpt-5-mini"
LIGHTWEIGHT_MODEL_PROVIDER = "openai"

# --- Per-prompt model assignment ---
# Maps each prompt to "frontier" or "lightweight".
# Override individual entries to experiment with model routing.
PROMPT_MODEL_ASSIGNMENTS = {
    "agent_system_prompt":          "frontier",      # Prompt 1 & 2
    "graphql_classification":      "lightweight",   # Prompt 3
    "graphql_entity_extraction":   "lightweight",   # Prompt 3b
    "id_resolution_selection":     "lightweight",   # Prompt 4
    "sql_generation":              "frontier",      # Prompt 5
    "product_extraction":          "lightweight",   # Prompt 6
    "product_code_selection":      "lightweight",   # Prompt 7
    "document_selection":          "lightweight",   # Prompt 8
    "documentation_synthesis":     "lightweight",   # Prompt 9
}
```

At runtime, each node resolves its LLM by looking up its prompt key in `PROMPT_MODEL_ASSIGNMENTS` and instantiating the corresponding model type (frontier or lightweight). This is handled by a helper function in `src/config.py`:

```python
def get_prompt_model(prompt_key: str) -> BaseChatModel:
    """Get the LLM instance for a specific prompt.

    Looks up the model type assignment for the given prompt key
    and returns the corresponding frontier or lightweight model.
    """
    settings = get_settings()
    assignment = settings.prompt_model_assignments[prompt_key]
    if assignment == "frontier":
        return create_llm(settings.frontier_model, settings.frontier_model_provider)
    else:
        return create_llm(settings.lightweight_model, settings.lightweight_model_provider)
```

This means any prompt can be promoted to the frontier model (e.g., if classification accuracy needs improvement, set `"graphql_classification": "frontier"`) or demoted to the lightweight model (e.g., if SQL generation works well enough with a cheaper model, set `"sql_generation": "lightweight"`) — all via configuration, no code changes.

### Prompts Inventory

All LLM prompts that require user vetting:

| # | Prompt | File | Default Model | Purpose |
|---|--------|------|---------------|---------|
| 1 | **Agent system prompt (dual-tool)** | `src/agent_node.py` | frontier | Extends existing `AGENT_PREFIX` with GraphQL tool description, budget status, verification guidance |
| 2 | **Agent system prompt (SQL-only)** | `src/agent_node.py` | frontier | Existing `AGENT_PREFIX` — unchanged, used in SQL-only mode |
| 3 | **GraphQL classification prompt** | `src/graphql_pipeline.py` | lightweight | Describes all query types with per-value descriptions from `QUERY_TYPE_DESCRIPTION` constant; LLM classifies + rejects. Uses `with_structured_output(GraphQLQueryClassification, include_raw=True)` |
| 3b | **GraphQL entity extraction prompt** | `src/graphql_pipeline.py` | lightweight | Given classified `query_type`, extracts entities (country, product, year, etc.); includes services catalog when relevant. Uses `with_structured_output(GraphQLEntityExtraction, include_raw=True)` |
| 4 | **ID resolution selection prompt** | `src/graphql_pipeline.py` | lightweight | Presents LLM-guessed + catalog-searched candidates, asks LLM to pick best country/product IDs |
| 5 | **SQL generation prompt** | `src/sql_pipeline.py` | frontier | Existing SQL generation prompt — unchanged, carried over |
| 6 | **Product extraction prompt** | `src/product_and_schema_lookup.py` | lightweight | Existing product extraction — unchanged |
| 7 | **Product code selection prompt** | `src/product_and_schema_lookup.py` | lightweight | Existing code selection — unchanged |
| 8 | **Document selection prompt** | `src/docs_pipeline.py` | lightweight | Presents document manifest + user question; LLM selects which docs to load |
| 9 | **Documentation synthesis prompt** | `src/docs_pipeline.py` | lightweight | Reads selected documents + question; synthesizes comprehensive, liberal response including related concepts |

Prompts 1 and 2 share the key `"agent_system_prompt"` — they use the same model type regardless of mode. The SQL-only prompt is a subset of the dual-tool prompt, not a different model assignment.

Prompts 1, 3, 3b, 4, 8, and 9 are **new** and must be drafted + vetted. Prompts 2, 5, 6, 7 are **existing** and unchanged. The agent system prompt (Prompt 1) must also be extended with `docs_tool` description and context-passing guidance.

**SQL pipeline services enhancement:** Prompt #6 (product extraction, `src/product_and_schema_lookup.py`) should be enhanced to inject the services catalog when the question involves services. Currently the SQL pipeline tells the LLM about `services_unilateral`/`services_bilateral` schemas but does NOT present actual service category names — the LLM must guess them. Injecting the catalog (fetched from the DB classification tables: `classification.product_services_unilateral`) when services are detected would improve service product resolution in both pipelines. This is a minor enhancement; flag as requiring user vetting since it modifies an existing prompt.

---

## 12. Implementation Phases

**Phase 1: Infrastructure** — `src/atlas_links.py` (deterministic URL builders, product classification registry, frontier list), `src/graphql_client.py` (budget tracker + HTTP client + query template builders), cache extensions. Pure functions and HTTP, no graph changes. Full unit tests. Also add `AgentMode` enum to `src/config.py`, rename model settings from `query_model`/`metadata_model` to `frontier_model`/`lightweight_model`, and add per-prompt model assignment configuration (`PROMPT_MODEL_ASSIGNMENTS` in `model_config.py`, `get_prompt_model()` helper in `src/config.py`).

**Phase 2: GraphQL Pipeline Nodes** — `src/graphql_pipeline.py` with all nodes. `GraphQLQueryClassification` and `GraphQLEntityExtraction` Pydantic schemas with description constants, reasoning fields, and reject support. Dual-source ID resolution (verify standard codes + search + LLM select). Query template builders. Services catalog caching and conditional injection. Classification, extraction, and selection LLM prompts (drafted → user-vetted). SQL pipeline services enhancement (inject service category names into Prompt #6 when services detected). Unit tests with mocked LLM + HTTP.

**Phase 3: Graph Rewrite** — `src/graph.py` (full graph construction with both pipelines, conditional routing, sequential GraphQL pipeline). `src/agent_node.py` (dynamic tool binding, mode resolution, prompt assembly). Rename `generate_query.py` → `sql_pipeline.py` (extract SQL nodes, remove graph construction). Test graph wiring — verify SQL-only mode produces identical behavior to current system, verify dual-tool routing, verify rejection path.

**Phase 4: Streaming** — Rename `text_to_sql.py` → `streaming.py`. Update pipeline node registry for GraphQL nodes. Extend `_extract_pipeline_state` for GraphQL events. Extend turn summary builder for atlas_links. Update `src/api.py` for mode parameter and GraphQL event tracking.

**Phase 5: Agent Prompt & Eval** — System prompt drafting (requires user vetting per CLAUDE.md). Evaluation questions for GraphQL routing accuracy, rejection accuracy, link generation correctness. Integration tests with real GraphQL APIs.

### Documentation Tool Phases

The docs tool depends on the graph rewrite (Phase 3) for multi-tool graph infrastructure. It can be implemented alongside or after the main redesign phases:

| Backend Redesign Phase | Docs Tool Work |
|----------------------|----------------|
| Phase 1: Infrastructure | — |
| Phase 2: GraphQL Pipeline Nodes | — |
| Phase 3: Graph Rewrite | Docs Phase A can start (pipeline nodes, standalone) |
| Phase 4: Streaming | Wire docs pipeline streaming |
| Phase 5: Agent Prompt & Eval | Docs Phase B (graph integration) + Docs Phase C (eval) |

**Docs Phase 0: Documentation Content** (issue #50, can proceed in parallel with all engineering) — Write the 4 initial markdown documentation files. Content creation, not engineering.

**Docs Phase A: Docs Pipeline Nodes** — Create `src/docs_pipeline.py` with all 3 nodes. Create document manifest loader (scans directory, extracts descriptions). Draft selection and synthesis LLM prompts (require user vetting). Tests in `src/tests/test_docs_pipeline.py` with mocked LLM. Standalone — not wired into graph yet.

**Docs Phase B: Graph Integration** — Add docs pipeline nodes to `build_atlas_graph()`. Extend `route_after_agent` for `docs_tool`. Add `_docs_tool_schema` to agent tool binding. Add context-passing guidance to agent system prompt (requires user vetting). Update streaming node labels. Add `docs_*` fields to state.

**Docs Phase C: Evaluation** — Add methodology questions to eval set. Test docs_tool routing, context passing, query budget isolation, progressive disclosure, synthesis quality.

---

## 13. Evaluation Strategy

**TDD throughout:** Write tests first, then implement. `PYTHONPATH=$(pwd) pytest -m "not db and not integration and not eval"` after every change.

### 13.1 Tier 1 — Unit Tests (no LLM, no DB)

Mocked LLM + mocked HTTP. Run with `pytest -m "not db and not integration and not eval"`. Target: <30 seconds.

#### GraphQL Pipeline Per-Node Tests

| Node | Test Strategy | Key Assertions |
|------|--------------|----------------|
| `extract_graphql_question` | Direct invocation with mock state | Extracts question from `tool_calls[0]["args"]`; resets all `graphql_*` state fields to defaults |
| `classify_query` | Direct invocation with mocked LLM | Correct `GraphQLQueryClassification` Pydantic model returned; schema has reasoning + query_type + rejection_reason + api_target (4 fields); `reasoning` field populated before `query_type`; rejection produces `query_type="reject"` with reason; all 19 query_types covered (test each); per-value descriptions from `QUERY_TYPE_DESCRIPTION` constant visible in JSON schema; `include_raw=True` captures raw LLM response |
| `extract_entities` | Direct invocation with mocked LLM (given known `query_type`) | Correct `GraphQLEntityExtraction` Pydantic model returned; `reasoning` field populated; country codes are ISO alpha-3 (not ISO numeric); product codes are HS/SITC for goods (not internal IDs); services queries include catalog in prompt; `product_class` is constrained `Literal["HS92", "HS12", "HS22", "SITC"]` (not `str`); field relevance noted per query_type (e.g., `lookback_years` only for `country_lookback`); description constants used for `product_level`, `product_class`, `group_type` |
| `resolve_ids` | Direct invocation with mocked catalog + mocked LLM | Correct final internal IDs (not HS codes); translates ISO alpha-3 → internal numeric ID via `iso3Code` field; translates HS/SITC codes → internal product ID via `code` field; translates service category name → internal product ID via `nameShortEn`; handles missing entities gracefully; adapts ID format per API target (integers for Explore, prefixed strings for Country Pages); product catalog indexed by both HS code and name; generates Atlas links inline (correct URLs for all visualization types + country pages; all 4 product classification URL formats; resolution_notes propagation; frontier country fallback; no links for global_datum/data_availability; link generation failure does not block ID resolution) |
| `build_and_execute_graphql` | Direct invocation with mocked HTTP (httpx) | Correct GraphQL query constructed from template; budget consumed on success only; errors caught — node should not raise; error dict written to `graphql_raw_response` on failure; circuit breaker feedback (success/failure recorded) |
| `format_graphql_results` | Direct invocation with both success/failure states | Correct ToolMessage content on success; links attached to state on success; error ToolMessage on API failure; rejection message on classify reject; links discarded on API error |

#### Docs Pipeline Per-Node Tests

| Node | Test Strategy | Key Assertions |
|------|--------------|----------------|
| `extract_docs_question` | Direct invocation with mock state | Extracts question from `tool_calls[0]["args"]`; resets all `docs_*` state fields |
| `select_and_synthesize` | Direct invocation with mocked LLM + test docs | Selection LLM picks correct docs; handles single and multiple doc selection; synthesis is comprehensive (includes related concepts); fallback to all docs when selection fails; fallback to raw docs when synthesis fails; error handling never raises |
| `format_docs_results` | Direct invocation with success/failure states | Correct ToolMessage content; does NOT increment `queries_executed`; handles parallel `tool_calls` gracefully |

#### Infrastructure Unit Tests

| Component | What to Test | Key Assertions |
|-----------|-------------|----------------|
| Budget tracker | Sliding window, concurrent access, edge cases | Window expiry; consume-on-success semantics; `is_available()` returns False at limit; thread-safety under concurrent access |
| Circuit breaker | State transitions, recovery timeout | CLOSED→OPEN after 5 failures; OPEN→HALF-OPEN after 30s; HALF-OPEN→CLOSED on success; HALF-OPEN→OPEN on failure |
| Route functions | All state combinations → verify next-node selection | Correct routing for: reject → format_graphql_results; success → resolve_ids; SQL vs GraphQL dispatch; max_queries_exceeded |

#### Graph-Level Test Scenarios

| Scenario | Setup | Key Assertions |
|----------|-------|----------------|
| SQL-only mode regression | `AgentMode.SQL_ONLY` + `FakeToolCallingModel` | Identical event stream to current system; `atlas_graphql` never appears in tool bindings; all SQL pipeline nodes fire in correct order |
| Dual-tool happy path | `AgentMode.GRAPHQL_SQL` + `FakeToolCallingModel` that calls `atlas_graphql` | Full GraphQL pipeline fires: extract → classify → extract_entities → resolve (+ inline link generation) → execute → format → agent gets ToolMessage with data + links |
| Rejection → SQL fallback | `FakeToolCallingModel` that calls `atlas_graphql`, then `query_tool` after rejection | `classify_query` returns `query_type="reject"` → pipeline skips to `format_graphql_results` (bypasses `extract_entities` and `resolve_ids`) → agent receives rejection ToolMessage → agent calls `query_tool` → SQL pipeline fires normally |
| Extraction failure after successful classification | Mock `classify_query` success + mock `extract_entities` LLM failure | `classify_query` returns valid `query_type` → `extract_entities` fails → error ToolMessage returned to agent → agent can retry or fall back to SQL |
| Services product resolution | Question about services (e.g., "Kenya's tourism exports") | Services catalog presented in `extract_entities` prompt → `product_code_guess` = "Travel & tourism" → `resolve_ids` matches against services catalog by `nameShortEn` → internal service product ID resolved |
| Budget exhaustion in auto mode | `budget_tracker.remaining() <= 5` | `agent_node` binds only `query_tool`; system prompt is existing `AGENT_PREFIX` (unchanged); behavior identical to SQL-only |
| Circuit breaker tripped | `circuit_breaker.is_open() == True` | Same as budget exhaustion — agent sees only SQL tool |
| GraphQL API error handling | Mock HTTP 500 in `build_and_execute_graphql` | Node writes error to state (doesn't raise); `format_graphql_results` returns error ToolMessage; links (generated in `resolve_ids`) are discarded |
| Concurrent budget access | Multiple simultaneous graph invocations | Budget tracker correctly counts across concurrent requests; no race conditions with `asyncio.Lock`; consume-on-success prevents failed requests from burning budget |
| Docs tool happy path | `FakeToolCallingModel` calls `docs_tool` | Full pipeline fires: extract → select_and_synthesize → format → agent gets ToolMessage with documentation |
| Docs + SQL in sequence | `FakeToolCallingModel` calls `docs_tool` then `query_tool` | Docs pipeline returns documentation; SQL pipeline fires normally; `queries_executed` only incremented by SQL, not docs |
| Docs tool does not affect query budget | Call `docs_tool` 5 times, then `query_tool` | `queries_executed == 1` (only SQL counted) |
| Context passing | Agent calls `docs_tool` then `query_tool` with `context` field populated | Verify `pipeline_context` reaches `generate_sql`; verify `extract_products` uses only `pipeline_question` (not context) |

### 13.2 Tier 2 — Component Evaluation (real LLM, no LLM-as-judge)

Run with `pytest -m "integration"`. Real LLM calls against curated test sets with known correct answers. No judge needed — compare predicted vs. expected values.

**Classification accuracy eval set:**
- 50–100 questions with ground truth `query_type` labels
- Run each through `classify_query` with real LLM
- Measure: accuracy (% correct query_type), rejection precision (% of rejects that should be rejected), rejection recall (% of unsuitable questions correctly rejected)
- Threshold: >90% accuracy, >85% rejection precision, >80% rejection recall

**ID resolution accuracy eval set:**
- 50 questions with ground truth country/product internal IDs
- Run each through `resolve_ids` with real LLM + real catalogs
- Measure: exact match on final country ID, exact match on final product ID
- Threshold: >95% country accuracy, >90% product accuracy

**SQL execution accuracy (existing system improvement):**
- For existing 246 SQL questions: run generated SQL AND reference SQL against real DB
- Compare result sets (not just text comparison) — captures semantic equivalence
- This catches cases where different SQL produces the same correct result

### 13.3 Tier 3 — Trajectory Evaluation (new)

Verify the agent uses the RIGHT tool, not just gets the right answer. Uses LangChain's `agentevals` library.

**Tool sequence annotation:**
- For each of the 246 existing eval questions, annotate **expected tool sequence**:
  - `"sql_only"` — should use `query_tool`
  - `"graphql_only"` — should use `atlas_graphql`
  - `"graphql_then_sql_fallback"` — should try `atlas_graphql`, get rejected, then use `query_tool`
  - `"either"` — either tool is acceptable
- Use `trajectory_llm_as_judge` from `agentevals` to verify the agent's tool call sequence matches expectations
- Key metric: **Tool selection accuracy** — did the agent call the right tool for the question type?

**New GraphQL-specific trajectory tests:**
- Questions that should route to `atlas_graphql`: country profiles, growth dynamics, diversification, new products, explore visualizations
- Questions that should be rejected by GraphQL and fall back to SQL: complex multi-table joins, ad-hoc aggregations, questions about specific HS codes at 6-digit level
- Budget exhaustion scenario: verify auto mode degrades to SQL-only when budget <= 5

### 13.4 Tier 4 — End-to-End (existing eval system, extended)

Extends the existing 246-question eval (`evaluation/`) with new categories and dimensions.

**New question categories (GraphQL-appropriate):**

| Category | Example Questions | Expected Tool |
|----------|-----------------|---------------|
| `country_profile` | "Tell me about Kenya's economy" | `atlas_graphql` |
| `growth_dynamics` | "How has Kenya changed in the last decade?" | `atlas_graphql` |
| `diversification` | "What is Kenya's diversification grade?" | `atlas_graphql` |
| `new_products` | "What new products does Kenya export?" | `atlas_graphql` |
| `explore_treemap` | "What did Kenya export in 2024?" | `atlas_graphql` |
| `explore_overtime` | "How have Kenya's exports changed since 1995?" | `atlas_graphql` |
| `explore_feasibility` | "What are Kenya's growth opportunities?" | `atlas_graphql` |
| `explore_marketshare` | "What is Kenya's global market share in coffee?" | `atlas_graphql` |

**New judge dimension:**
- Add **data source appropriateness** (weight: 0.15) to the existing 4-dimension rubric
- Criteria: Did the answer come from the most suitable data source? (e.g., GraphQL for derived metrics, SQL for custom aggregations)
- Adjust existing dimension weights: factual_correctness (0.30), data_accuracy (0.25), completeness (0.15), reasoning_quality (0.15), data_source_appropriateness (0.15)

### 13.5 Manual E2E Verification

- Ask "What is Kenya's diversification grade?" → verify GraphQL routing + Atlas link
- Ask "Compare Brazil and India coffee exports" → verify agent uses both tools
- Ask "What is the average temperature in Nairobi?" → verify classification rejects
- Ask "How have Kenya's exports changed since 1995?" → verify overtime link generated
- Ask "What are Kenya's growth opportunities?" → verify feasibility link generated
- Set mode to `sql_only` → verify system behaves identically to current production
- Exhaust budget artificially → verify auto mode degrades to SQL-only
- Trip circuit breaker (mock 5 consecutive failures) → verify fast-fail to SQL-only
- Verify frontier country (USA) gets Explore feasibility link (not `growth-opportunities`)
- Verify ambiguous query shows resolution_notes on atlas links

### 13.6 Evaluation Datasets & Collection

Detailed collection workflows, ground truth policies, curation tooling, and collection phases are in [`docs/evaluation_data_collection.md`](evaluation_data_collection.md).

#### Ground Truth Source Hierarchy

| Ground Truth Source | Quality Tier | Metadata `source_method` |
|---|---|---|
| **Browser-verified data** (navigating Atlas website) | **Highest** — authoritative source of truth | `"browser_country_page"` or `"browser_explore_page"` |
| **GraphQL API responses** (direct API queries) | **Medium** — adds variety, lower confidence | `"graphql_api"` |
| **SQL pipeline output** (forced SQL route) | **Consistency check only** | `"sql_cross_check"` |
| **LLM-generated expectations** | **N/A** (structural, not answer quality) | `"llm_generated"` |

**Key rules:** Ground truth must be independent of the system being evaluated. GraphQL API responses cannot serve as ground truth for the GraphQL pipeline. Every question records its `source_method` for filtering by quality tier.

#### Judge Extensions

**New scoring dimension — `data_source_appropriateness` (weight 0.15):**

| Dimension | Weight |
|---|---|
| `factual_correctness` | 0.30 |
| `data_accuracy` | 0.25 |
| `completeness` | 0.15 |
| `reasoning_quality` | 0.15 |
| `data_source_appropriateness` | 0.15 |

**`TrajectoryVerdict` judge mode:** Deterministic comparison of `tools_called` against `expected_tool` from question metadata. Purely programmatic — no LLM needed.

#### Evaluation Deliverables

| File | Purpose |
|---|---|
| `evaluation/classification_eval.json` | Classification ground truth (~60 questions) |
| `evaluation/entity_extraction_eval.json` | Entity extraction ground truth (~30 questions) |
| `evaluation/id_resolution_eval.json` | ID resolution ground truth (~45 questions) |
| `evaluation/graphql_eval_collection_guide.md` | Collection guide for GraphQL-specific e2e questions |
| `evaluation/annotate_tool_expectations.py` | Rule-based + LLM tool routing annotation |
| `evaluation/generate_classification_eval.py` | Classification eval set generation |
| `evaluation/analyze_coverage_gaps.py` | Coverage gap analysis and reporting |
| `evaluation/refresh_ground_truth.py` | Ground truth refresh pipeline |
| `evaluation/judge.py` (updated) | `TrajectoryVerdict` mode, `data_source_appropriateness` dimension |
| `evaluation/run_agent_evals.py` (updated) | Capture tool call sequences from graph state |

---

## 14. Documentation Tool (docs_tool)

> **Related issues:** #50 (write technical docs — content prerequisite), #54 (technical info injection — this design supersedes/extends the earlier analysis in `docs/technical_info_injection_design.md`)

### 14.1 Motivation

Complex queries require understanding of what metrics mean, how they're computed, and how Atlas data differs from raw Comtrade. Currently, ~13 metric definitions live statically in `AGENT_PREFIX` (~400 tokens, always injected). These are broad but miss critical nuances (time comparability, normalized variants, storage patterns). More importantly, the agent has no access to deeper methodology documentation that would help it reason about complex questions.

The docs_tool solves this by providing on-demand access to technical documentation without polluting the main agent context or the SQL/GraphQL pipeline prompts.

### 14.2 Architecture

#### What the Docs Tool Is

A third agent tool alongside `query_tool` (SQL) and `atlas_graphql` (GraphQL). When the agent calls `docs_tool(question="...")`, the question routes through a short pipeline that:
1. Uses an LLM to select which documentation files are relevant (progressive disclosure)
2. Loads the selected files
3. Uses an LLM to synthesize a focused, comprehensive response
4. Returns the synthesized response as a ToolMessage

The docs_tool does **NOT** count against the query budget (`queries_executed`). It's a knowledge lookup, not a data query.

#### Why Not Give Sub-Pipelines Direct Docs Access

The SQL and GraphQL pipelines are deterministic linear chains. Making them agentic (with their own docs-fetching loops) would:
- Contradict the flat-graph architecture described under [Graph Topology](#4-graph-topology)
- Break the streaming infrastructure, which relies on known pipeline topology (`PIPELINE_NODES`, `NODE_LABELS`)
- Add significant complexity for marginal benefit

Instead, the agent handles documentation at the reasoning layer and passes context down to sub-queries via the structured `context` field (see [Context Passing to Sub-Queries](#145-context-passing-to-sub-queries) below).

#### Progressive Disclosure (Inspired by Agent Skills Pattern)

The Anthropic Agent Skills pattern uses three-tier progressive disclosure to manage context:
- **Tier 1** (~50-200 tokens): Only name + description always present
- **Tier 2** (~1000-5000 tokens): Full content loaded only when relevant
- **Tier 3** (unlimited): Supporting files loaded on demand

Our docs_tool implements this same principle:
- **Tier 1:** The agent sees only the `docs_tool` description in its tool list (~50 tokens). It knows documentation exists but no content is loaded.
- **Tier 2:** When the agent calls docs_tool, an LLM reads short descriptions of each available document (~100 tokens per doc) and selects which ones are relevant. Only selected documents are loaded.
- **Tier 3:** The selected documents (~500-5000 tokens each) are loaded and synthesized into a focused response.

This prevents loading all documentation into context for every query. A simple trade question never touches documentation. A methodology question loads only the relevant doc(s).

### 14.3 Docs Pipeline Detail

#### Pipeline Nodes (3 nodes)

```
[extract_docs_question]         Extract question + context from tool_call args; reset docs_* state
         |
[select_and_synthesize]         LLM selects docs → load → LLM synthesizes response (using both question + context)
         |
[format_docs_results]           Build ToolMessage → back to agent
```

#### Node 1: `extract_docs_question`

Same pattern as `extract_tool_question` (SQL) and `extract_graphql_question` (GraphQL). Extracts the `question` and `context` strings from `tool_calls[0]["args"]` into `docs_question` and `docs_context` respectively. Resets all `docs_*` state fields. If `context` is not provided in the tool call args, `docs_context` defaults to empty string.

#### Node 2: `select_and_synthesize`

Two-step LLM process using the lightweight model:

**Step A — Document Selection (LLM call #1):**

Present the question + context + a document manifest to the lightweight model. The manifest contains a short description of each available document (~100 tokens per doc). The LLM selects which document(s) to read. Both `docs_question` and `docs_context` are provided — the question drives selection, while the context (the broader user query) may reveal additional relevant documents. For example, a question of "What is PCI?" with context "The user wants to analyze semiconductor exports for middle-income countries" might prompt selection of both `metrics_glossary.md` and `data_coverage.md`.

The manifest is built automatically from the documentation directory — each markdown file has a brief header/description that serves as its manifest entry. Adding a new document requires only creating the markdown file with a descriptive header. No regex patterns, no keyword lists, no YAML configuration to maintain.

This is the progressive disclosure step — the LLM sees lightweight descriptions (not full content) and decides what to load. For a small doc set (3-5 docs), the selection prompt is ~300-500 tokens total.

**Step B — Load Selected Documents (file I/O):**

Read the selected markdown files from disk. Each file is loaded in full.

**Step C — Synthesis (LLM call #2):**

Present the loaded documentation + `docs_question` + `docs_context` to the lightweight model. The LLM synthesizes a response. When context is provided, the synthesis can be tailored to the broader user intent — e.g., emphasizing time comparability caveats when the context reveals a multi-year analysis.

The synthesis prompt should instruct the LLM to be **liberal and comprehensive** in its response:
- If the user asks about one metric (e.g., PCI), also include brief definitions of closely related metrics (e.g., ECI, COG, distance) and how they relate
- If the user asks about one technical aspect, include descriptions of related technical aspects
- The goal is to provide enough context that the agent receiving this response can formulate comprehensive, well-informed sub-queries and answers
- The response should be thorough but focused — not a dump of the entire document, but not narrowly scoped to just the exact question either

**Error handling:** If the selection LLM call fails, fall back to loading all documents. If the synthesis LLM call fails, return the raw concatenated documentation. The pipeline must never raise — it should always return a ToolMessage (same error-safety principle as the GraphQL pipeline).

**RetryPolicy:** `RetryPolicy(max_attempts=2, backoff_factor=1.5)` for LLM calls (same pattern as `classify_query` / `extract_entities` in the GraphQL pipeline).

#### Node 3: `format_docs_results`

Creates a `ToolMessage` with the synthesized response and routes back to the agent. Does NOT increment `queries_executed`.

### 14.4 Tool Schemas: Question/Context Separation

All three tools use a structured `question` + `context` separation. The `context` field is optional (defaults to empty string) and carries background information that helps the pipeline produce better results without polluting the question that drives core pipeline logic.

**Why separate fields instead of a single formatted string:**
- **Node-level control.** Each node chooses which inputs to use. `extract_products` (SQL) operates on the question only — context might mention product names or table names that aren't the query target. `generate_sql` sees both, since context provides column guidance and metric caveats that improve SQL quality.
- **No parsing required.** Nodes don't need to parse `## Query` / `## Additional Context` sections from a single string. The separation is structural.
- **Clean optionality.** When the agent has no context to provide, the field is simply empty — no need to format a structured markdown template.

#### docs_tool

```python
class DocsToolInput(BaseModel):
    question: str = Field(
        description="A question about economic complexity methodology, metric definitions, "
        "data sources, or how to reproduce Atlas visualizations."
    )
    context: str = Field(
        default="",
        description="The broader user query or reasoning for why this documentation is needed. "
        "Helps the documentation tool tailor its response to the actual use case."
    )

@tool("docs_tool", args_schema=DocsToolInput)
def _docs_tool_schema(question: str, context: str = "") -> str:
    """Retrieves technical documentation about economic complexity metrics,
    trade data methodology, and Atlas visualization reproduction.

    Use this tool when you need to:
    - Understand how a metric is computed or what it means in detail
    - Learn about data sources, cleaning methodology, or limitations
    - Get SQL patterns for reproducing Atlas country page visualizations
    - Understand data coverage and availability

    Do NOT use this tool for actual data queries — use query_tool or
    atlas_graphql for those. This tool does not count against your query limit."""
    raise NotImplementedError("Schema-only tool; execution routes through graph nodes.")
```

#### query_tool and atlas_graphql (updated schemas)

The existing `query_tool` and `atlas_graphql` tool schemas should also gain an optional `context` field:

```python
class QueryToolInput(BaseModel):
    question: str = Field(description="The data query question.")
    context: str = Field(
        default="",
        description="Additional technical context (e.g., metric definitions, column guidance, "
        "data caveats) that may help answer the query accurately."
    )

class AtlasGraphQLInput(BaseModel):
    question: str = Field(description="The data query question.")
    context: str = Field(
        default="",
        description="Additional technical context that may help classify the query "
        "and interpret the results."
    )
```

The extract nodes (`extract_tool_question`, `extract_graphql_question`) write `context` from `tool_calls[0]["args"]` into `pipeline_context` and `graphql_context` respectively. If the agent omits the context argument, it defaults to empty string.

### 14.5 Context Passing to Sub-Queries

When the agent calls `docs_tool` and learns technical context, it should pass that context to subsequent tool calls via the structured `context` parameter. The separation of `question` and `context` is structural — not a formatting convention in a single string.

#### How the Agent Populates Context

After calling `docs_tool` and receiving a synthesized response, the agent passes relevant excerpts from that response into the `context` field of subsequent `query_tool` or `atlas_graphql` calls:

```python
# Agent calls docs_tool first
docs_tool(
    question="What is PCI and how does it relate to complexity profiles?",
    context="The user asked: For middle-income countries, analyze PCI of semiconductor "
            "exports 2012-2015 and their complexity profiles."
)
# → Returns comprehensive docs response

# Agent then calls query_tool with context from docs response
query_tool(
    question="What are the PCI values for semiconductor products 2012-2015?",
    context="PCI (Product Complexity Index) measures the diversity and sophistication "
            "of productive know-how required to produce a product. It is defined at the "
            "product-year level and stored in country_product_year tables. PCI values "
            "are NOT comparable across years — only within-year rankings are meaningful. "
            "Use the export_pci column."
)
```

The agent can also populate `context` from its own reasoning (not just docs_tool results) — e.g., passing prior query results or decomposition notes to help a sub-query.

#### How Each Pipeline Uses Context

The `question` and `context` flow through each pipeline differently:

**SQL pipeline:**

| Node | Uses `pipeline_question` | Uses `pipeline_context` |
|------|-------------------------|------------------------|
| `extract_products` | Yes — detects schemas, products, classification systems | **No** — context may mention product names or table names as metadata guidance, not as query targets |
| `lookup_codes` | Yes — matches products | **No** — same reasoning |
| `get_table_info` | Yes — selects relevant schemas | **No** — schema selection should be driven by the query |
| `generate_sql` | Yes — the query to translate | **Yes** — metric definitions, column names, time comparability caveats, table guidance all improve SQL quality |
| `validate_sql` | No (uses generated SQL) | No |
| `execute_sql` | No (uses validated SQL) | No |
| `format_results` | No (uses execution results) | No |

**GraphQL pipeline:**

| Node | Uses `graphql_question` | Uses `graphql_context` |
|------|------------------------|----------------------|
| `classify_query` | Yes — drives classification | **Yes** — context may disambiguate query type (e.g., "this involves feasibility metrics" helps route to `feasibility`) |
| `extract_entities` | Yes — drives entity extraction | **Yes** — context may help identify entities (e.g., "looking at the coffee sector" helps extract product) |
| `resolve_ids` (+ inline `generate_atlas_links()`) | Yes — entity names to resolve | **No** — context won't contain entity IDs |
| `build_and_execute_graphql` | No (uses resolved params) | No |
| `format_graphql_results` | No (uses API results) | No |

**Docs pipeline:**

| Node | Uses `docs_question` | Uses `docs_context` |
|------|---------------------|-------------------|
| `select_and_synthesize` (selection step) | Yes — primary driver for document selection | **Yes** — broader user intent may reveal additional relevant documents |
| `select_and_synthesize` (synthesis step) | Yes — the question to answer | **Yes** — tailors synthesis to the actual use case rather than giving a generic response |
| `format_docs_results` | No (uses synthesis result) | No |

The agent system prompt should include guidance on when and how to populate the `context` field in tool calls. This is part of the triple-tool agent prompt that must be drafted and vetted.

### 14.6 Routing Logic

The `route_after_agent` function from the [Graph Topology](#4-graph-topology) section, extended for three tools:

```python
def route_after_agent(state: AtlasAgentState) -> str:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        if state.get("queries_executed", 0) >= max_uses:
            return "max_queries_exceeded"
        tool_name = last_msg.tool_calls[0]["name"]
        if tool_name == "query_tool":
            return "extract_tool_question"       # SQL pipeline
        elif tool_name == "atlas_graphql":
            return "extract_graphql_question"     # GraphQL pipeline
        elif tool_name == "docs_tool":
            return "extract_docs_question"        # Docs pipeline
    return END
```

### 14.7 Documentation Files

#### Initial Document Set

| File | Content | Est. Size |
|------|---------|-----------|
| `metrics_glossary.md` | Definitions of all economic complexity metrics (ECI, PCI, RCA, COI, COG, distance, proximity, diversity, ubiquity, normalized variants, complexity_enum, product_status). For each: formal definition, definition level, time comparability, common misinterpretations. | ~800-1200 words |
| `trade_methodology.md` | How the Atlas of Economic Complexity computes trade metrics. Data cleaning beyond Comtrade, BACI reconciliation, mirror flows, re-export handling, valuation (CIF/FOB). Summary of the Growth Lab research paper. | ~1500-3000 words |
| `country_page_reproduction.md` | SQL patterns for reproducing Atlas country page visualizations: export basket, growth dynamics, new products, complexity profile, product space. Exact table/column references. | ~1000-2000 words |
| `data_coverage.md` | Year availability per schema, services vs goods data differences, known data gaps, latest available year conventions. | ~500-800 words |

#### Extensibility

Adding a new document requires only:
1. Write a `.md` file with a brief description in its header (first paragraph or YAML frontmatter)
2. Place it in the documentation directory
3. No code changes, no YAML config updates, no regex patterns to define

The document manifest is built dynamically by scanning the directory and extracting each file's header/description. The LLM decides relevance at runtime based on these descriptions and the user's question.

#### Content Prerequisite

The actual documentation content is issue #50 (Write economic complexity technical documentation). The documentation files must be written before the docs_tool can be useful. The engineering work (pipeline nodes, graph integration) can proceed in parallel with content creation.

### 14.8 Existing Metric Definitions: Keep As-Is

#### Current State in SQL Pipeline

The `AGENT_PREFIX` system prompt (`src/generate_query.py:708-782`) already contains ~13 metric definitions covering:
- **Pre-calculated metrics:** RCA, diversity, ubiquity, product proximity, distance, ECI, PCI, COI, COG
- **Calculable metrics:** Market share, new products, product space
- **Policy guidance:** How to use distance (feasibility), PCI (attractiveness), and COG (strategic value) for diversification analysis

The SQL generation chain prompt (`src/generate_query.py:121-172`) also lists pre-calculated metrics with usage guidance.

#### Assessment: These Provide Real Value

The existing definitions serve a useful purpose — they give the SQL pipeline a **broad overview** of the full universe of complexity metrics. This allows the pipeline to:
- Recognize metric terminology in user questions
- Suggest alternative analyses (e.g., if a user asks about complexity, the agent knows ECI, PCI, COI, and COG are all available)
- Generate SQL that uses the correct column names

#### What's Missing (Nuances)

Per `docs/technical_info_injection_design.md`, the existing definitions miss:
- **Time comparability:** ECI and PCI are NOT comparable across years (computed independently per year)
- **Normalized variants:** `normalized_pci`, `normalized_cog`, etc. columns are undocumented
- **Storage redundancy:** PCI is defined at product-year level but stored in country-product-year tables
- **Presentation guidance:** How to present metric results to users

#### Decision: Keep Existing + Docs Tool Fills the Gap

Rather than adding a YAML metric catalog for targeted injection (which adds engineering complexity and maintenance burden), the gap is filled by:
1. **Keeping the existing broad definitions** in `AGENT_PREFIX` — they provide the SQL pipeline with a comprehensive overview
2. **The docs_tool** handles detailed metric nuances — when the agent encounters a metric-heavy query, it calls docs_tool to get detailed definitions including time comparability, column guidance, and storage patterns
3. **Context passing** delivers the detailed context to sub-queries via the structured `context` field

This avoids introducing a separate YAML catalog mechanism while still addressing the nuance gap. The `metrics_glossary.md` document (accessed via docs_tool) contains all the detailed nuances that the static AGENT_PREFIX definitions lack.

**Future consideration:** If evaluation reveals that SQL queries for metric-heavy questions are consistently wrong because the SQL generation LLM lacks specific column/table guidance, a YAML catalog for targeted injection can be added later as an optimization. But the docs_tool + context passing approach should be tried first.

#### GraphQL Pipeline: No Metric Injection Needed

The GraphQL pipeline does NOT need separate metric catalog injection:
- **`classify_query`** classifies query types (country_profile, feasibility, etc.) — the classification prompt will describe what each query type means (via the `QUERY_TYPE_DESCRIPTION` constant), which inherently includes enough metric context for routing decisions
- **`resolve_ids`** matches country/product names to IDs + generates Atlas links inline — no metric knowledge needed
- **`build_and_execute_graphql`** constructs queries from templates — deterministic, no metric knowledge needed

The GraphQL pipeline's classification prompt (LLM Prompt #3) will be drafted with appropriate query type descriptions during implementation. This is a prompt authoring task, not an injection mechanism task.

### 14.9 Progressive Disclosure: Skills Pattern Analysis

We adopt the three-tier progressive disclosure principle from the Anthropic Agent Skills pattern (tool description → doc descriptions → full docs). The docs_tool runs its own LLM calls in isolation — documentation content never pollutes the SQL generation prompt or conversation history. We don't adopt the SKILL.md format itself since our docs are consumed by pipeline code, not injected into the agent prompt. We also use explicit tool calls rather than implicit skill matching.

### 14.10 Example Query Control Paths

#### Path 1: Simple trade query — no docs involvement
**"What were India's banana exports in 2015?"**
```
Agent → query_tool(question="India's banana exports 2015")
  → pipeline_question = "India's banana exports 2015"
  → pipeline_context = ""  (agent provided no context)
  → SQL pipeline: normal flow, existing metric definitions in AGENT_PREFIX available
Agent → answers with trade data
```
Docs involvement: **None.** Zero overhead. Tool description (~50 tokens) is the only cost. `pipeline_context` is empty — no extra tokens in any node.

#### Path 2: Methodology question — docs_tool provides the answer
**"How does the Atlas handle discrepancies between importer and exporter trade data?"**
```
Agent → recognizes this as methodology, not data
Agent → docs_tool(
    question="How does Atlas handle importer/exporter discrepancies?",
    context="The user wants to understand data methodology."
  )
  → docs_question = "How does Atlas handle importer/exporter discrepancies?"
  → docs_context = "The user wants to understand data methodology."
  → select_and_synthesize:
    → Selection LLM reads doc manifest + question + context → picks trade_methodology.md
    → Loads trade_methodology.md
    → Synthesis LLM extracts BACI reconciliation explanation
      (liberally includes related info: mirror flows, CIF/FOB, data quality)
  → Returns focused ToolMessage
Agent → answers from documentation (no SQL needed, no query budget spent)
```

#### Path 3: Complex multi-part query — docs_tool + context passing
**"For middle-income countries, analyze PCI of semiconductor exports 2012-2015 and their complexity profiles"**
```
Agent → calls docs_tool(
    question="What is PCI? What constitutes a complexity profile?
      How are these metrics related?",
    context="The user asked: For middle-income countries, analyze PCI of
      semiconductor exports 2012-2015 and their complexity profiles."
  )
  → select_and_synthesize:
    → Selection LLM → picks metrics_glossary.md
      (context reveals multi-year analysis → may also pick data_coverage.md)
    → Synthesis LLM returns comprehensive response, tailored to the
      broader context (emphasizes time comparability, country-product
      level metrics, and multi-year caveats):
      "PCI measures product sophistication, defined at product-year level,
       NOT comparable across years. A complexity profile includes ECI
       (country complexity), COI (proximity to complex products),
       COG (potential gain from developing a product), and diversity.
       COG and distance together indicate diversification feasibility..."

Agent → decomposes into sub-queries with context from docs response:

  Sub-query 1: query_tool(
    question="What semiconductor products did middle-income countries
      export 2012-2015?",
    context="Use hs92 classification. Middle-income defined by World Bank."
  )

  Sub-query 2: query_tool(
    question="What are the PCI values for semiconductor products 2012-2015?",
    context="PCI is defined at product-year level. It is stored in
      country_product_year tables but values are the same for a given
      product-year regardless of country. PCI values are NOT comparable
      across years. Use export_pci column."
  )
  → extract_products sees only question → detects "semiconductor products"
  → generate_sql sees both question + context → uses export_pci column,
    avoids cross-year comparison

  Sub-query 3: query_tool(
    question="What are the ECI, COI, COG, and diversity values for the
      top semiconductor-exporting countries 2012-2015?",
    context="ECI and COI are country-year level (in country_year table).
      COG is country-product-year level. Diversity counts products with
      RCA >= 1. These metrics are NOT comparable across years."
  )

Agent → synthesizes comprehensive analysis from all results
```

This is the key control path that demonstrates the full architecture:
1. **Agent pre-fetches docs** (free, no query budget) with `context` describing the broader user query
2. **Synthesis is liberal and tailored** — uses context to emphasize aspects relevant to the actual use case
3. **Context passes to sub-queries as structured fields** — `generate_sql` sees metric guidance; `extract_products` sees only the clean question
4. **Existing AGENT_PREFIX definitions** provide the broad metric overview that helps the agent reason about decomposition

### 14.11 Relationship to `docs/technical_info_injection_design.md`

This design supersedes the YAML metric catalog recommendation in `docs/technical_info_injection_design.md` (issue #54). The multi-tool agent makes a tool-based approach more natural than conditional injection. The YAML catalog remains a valid future optimization if docs_tool + context passing proves insufficient for metric-heavy SQL queries.
