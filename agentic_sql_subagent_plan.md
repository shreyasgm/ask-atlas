# Agentic SQL Sub-Agent: Implementation Plan

> **Date:** 2026-03-05
> **Status:** Implementation plan
> **Branch:** `feat/agentic-sql-subagent` (to be created)
> **Scope:** SQL pipeline only (Phase 1). GraphQL pipeline unchanged.

---

## Table of Contents

1. [Epistemic Analysis](#1-epistemic-analysis)
2. [Architecture: Hybrid Deterministic + Agentic](#2-architecture-hybrid-deterministic--agentic)
3. [The Sub-Agent's Job: SQL Expert, Not Tool Dispatcher](#3-the-sub-agents-job-sql-expert-not-tool-dispatcher)
3A. [Epistemic Map: What Each Node Knows, Decides, and Passes Forward](#3a-epistemic-map-what-each-node-knows-decides-and-passes-forward)
4. [Tool Design: Harnesses and Information Sources](#4-tool-design-harnesses-and-information-sources)
5. [Multi-Step Query Chaining Strategy](#5-multi-step-query-chaining-strategy)
6. [Sub-Agent State and Reasoning Loop](#6-sub-agent-state-and-reasoning-loop)
7. [Integration with Parent Graph](#7-integration-with-parent-graph)
8. [Streaming Strategy](#8-streaming-strategy)
9. [Implementation Steps](#9-implementation-steps)
10. [Testing Strategy](#10-testing-strategy)
11. [Risks and Mitigations](#11-risks-and-mitigations)
12. [Decisions Log: Alternatives Considered and Rejected](#12-decisions-log-alternatives-considered-and-rejected)

---

## 1. Epistemic Analysis

### What the current pipeline's LLM calls actually do

The SQL pipeline makes 4 LLM calls (minimum) per query — 1 for the orchestrator agent, plus 3 within the SQL pipeline itself. Each has a distinct character:

| # | Call | Model | Prompt size | Character | What it needs to know |
|---|------|-------|-------------|-----------|----------------------|
| 0 | Agent (orchestrator) | Frontier | System prompt + conversation history | Tool routing + reasoning | Which tool to call, how to phrase the question |
| 1 | Product extraction | Lightweight | ~290 lines | Classification + extraction | Schema options, product taxonomy, schema selection heuristics |
| 2 | Code selection | Lightweight | ~12 lines | Disambiguation | Candidate codes from DB, question context |
| 3 | SQL generation | Frontier | ~84 lines base + conditional blocks | Creative generation | DDL, product codes, domain rules, table selection guide, common mistakes, few-shot examples |
| 4 | SQL retry (if needed) | Frontier | Same as #3 + retry block | Blind error correction | Same as #3, plus previous SQL and error string |

### Where the knowledge lives — and where it should

The `SQL_GENERATION_PROMPT` (prompt_sql.py) is 84 lines of hard-won domain knowledge:
- Table selection guide (when to use `country_year` vs `country_product_year` vs bilateral tables)
- Column naming rules (`export_value` not `export_value_usd`, `product_code` not `product_id`)
- Metric definitions (RCA, ECI, PCI, COG, distance, CAGR formulas)
- Services vs goods table differences
- Product digit-level suffixes (`_1`, `_2`, `_4`, `_6`)
- Query planning steps (identify countries, products, time period, metrics)
- Common mistakes to avoid

This is the same knowledge needed for error recovery. When a query fails with "column 'export_value_usd' does not exist," the entity that fixes it needs to know that the column is actually called `export_value`. That knowledge is in the SQL generation prompt.

**But in the current pipeline, error recovery is epistemically impoverished.** The retry mechanism appends `SQL_RETRY_BLOCK` to the generation prompt:

```
**Retry — previous attempt failed:**
Failed SQL: {previous_sql}
Error: {error_message}
Fix the error and generate a corrected SQL query.
```

The LLM regenerates from scratch with a hint. It works sometimes — but it can't:
- Inspect the schema to verify column names
- Try a different classification schema (HS92 → SITC)
- Check whether it's using the right table suffix
- Reason about whether empty results mean a wrong query or genuinely missing data

### The core insight: who should generate SQL?

The previous version of this plan proposed a `generate_and_run_sql` tool — a tool that internally calls `create_query_generation_chain()` to generate SQL via a separate LLM call, then validates and executes. This is wrong.

**SQL generation and error recovery require the same knowledge.** If we make SQL generation a tool, we create an artificial information barrier:
- The *reasoning* agent sees the error but NOT the DDL, NOT the domain rules, NOT the few-shot examples
- It has to describe corrections in natural language ("try using export_value instead") and hope the generation tool interprets them correctly
- It needs an extra tool call (`explore_schema`) to learn things the generation prompt already contains

**The sub-agent should BE the SQL generator.** Its system prompt should contain the SQL domain knowledge. Its initial context should contain the DDL, codes, and few-shot examples. When it wants to write SQL, it writes SQL directly and calls a tool to validate + execute it. When execution fails, it already has all the context needed to reason about the fix — in the same conversation, with no information barrier.

This is the epistemic principle: **the entity that has the knowledge should make the decision.** Tools should be harnesses (validate, execute) and information sources (schema lookup, product re-extraction) — not creative capabilities.

### What changes vs the previous plan

| Aspect | Previous plan | Revised plan |
|--------|--------------|-------------|
| SQL generation | Tool (`generate_and_run_sql`) calls a separate LLM via `create_query_generation_chain()` | Agent generates SQL natively in its reasoning; passes SQL to `execute_sql` tool |
| LLM calls per attempt | 2 (reasoning + generation) | 1 (reasoning, which includes SQL generation) |
| Agent's system prompt | Strategy guidance only | Strategy guidance + SQL domain knowledge (from `SQL_GENERATION_PROMPT`) |
| Agent's initial context | Question + codes + table_info summary | Question + codes + full DDL + few-shot examples |
| Error recovery | Agent describes fix in natural language → generation tool interprets | Agent sees DDL + error + domain rules → writes corrected SQL directly |
| Tool count | 3 (`generate_and_run_sql`, `explore_schema`, `lookup_products`) | 3 (`execute_sql`, `explore_schema`, `lookup_products`) |

The tool count stays at 3, but the *character* of the primary tool changes fundamentally: from "do the creative work for me" to "run this SQL I wrote."

---

## 2. Architecture: Hybrid Deterministic + Agentic

### Where agency adds value — and where it doesn't

The pipeline has two epistemically distinct phases:

**Context gathering** (steps 1–4: extract question → extract products → lookup codes → get table info). The correct action at each step is always the same regardless of the question. Product extraction always happens. Code lookup always follows. Table info always follows that. Making an LLM "decide" to extract products is wasted reasoning: it adds latency, costs tokens, and introduces a chance that the model skips a step it should always take. **Keep this deterministic.**

**Query crafting** (steps 5–8: generate SQL → validate → execute → retry). This is where real uncertainty lives. The LLM must translate a natural language question into correct SQL. It often fails. The current retry mechanism is blind. **Make this agentic.**

### Current pipeline (flat, linear with retry loop)

```
agent → extract_tool_question → extract_products → lookup_codes → get_table_info
      → generate_sql → validate_sql ─┬→ execute_sql → format_results → agent
                                      └→ generate_sql (retry, max 2 attempts)
```

The parent graph also has:
- `tool_call_nudge` — anti-hallucination node that injects a message asking the agent to call a tool if it tries to respond without ever calling one.
- `execute_catalog_lookup` — budget-free tool node for the `lookup_catalog` tool, which resolves Atlas internal numeric IDs to human-readable names. Routes back to agent like `docs_tool`.
- `docs_tool` pipeline — `extract_docs_question → select_docs → synthesize_docs → format_docs_results`.
- GraphQL pipeline — `extract_graphql_question → plan_query → resolve_ids → build_and_execute_graphql → format_graphql_results`.

None of these are affected by this plan.

### New pipeline (deterministic setup → agentic loop)

```
agent → extract_tool_question → extract_products → lookup_codes → get_table_info
      → sql_query_agent → format_results → agent
         │
         │  (internally: a subgraph with ReAct loop)
         │
         ├─ reasoning_node (LLM generates SQL, picks tools, or finishes)
         │    ├→ execute_sql     ──→ reasoning_node
         │    ├→ explore_schema  ──→ reasoning_node
         │    ├→ lookup_products ──→ reasoning_node
         │    └→ [no tool call]  ──→ done
         │
         └─ returns: sql, result, columns, rows, execution_time, attempt_history
```

**What stays the same:**
- `extract_tool_question`, `extract_products`, `lookup_codes`, `get_table_info` — unchanged
- `format_results` — mostly unchanged (reads `pipeline_*` fields from parent state)
- Parent graph's agent node, GraphQL pipeline, docs pipeline — untouched

**What changes:**
- `generate_sql`, `validate_sql`, `execute_sql` nodes removed from parent graph
- `route_after_validation`, `route_after_execution` routing functions removed
- `retry_count` / `last_error` retry mechanism replaced by sub-agent reasoning
- New `sql_query_agent` wrapper node invokes the sub-agent subgraph
- New file `src/sql_subagent.py` contains the sub-agent

---

## 3. The Sub-Agent's Job: SQL Expert, Not Tool Dispatcher

The sub-agent is not a meta-reasoner that delegates to tools. It IS the SQL expert. Its system prompt contains the domain knowledge currently in `SQL_GENERATION_PROMPT`:

- Table selection guide (which table for which question pattern)
- Column naming conventions
- Metric definitions and formulas
- Services vs goods table differences
- Common mistakes to avoid
- Query planning methodology

Its initial context contains the per-query specifics:

- The user's question
- Product codes (from deterministic extraction)
- Full DDL for the identified tables (from deterministic setup)
- Few-shot examples (loaded from `queries.json` + SQL files)
- Any technical context passed by the orchestrator agent

The sub-agent writes SQL directly in its tool call to `execute_sql`. This is its primary creative act. The tools around it are harnesses and information sources — they handle the mechanical and exploratory work.

### Typical interaction flow

**Happy path (1 iteration):**
```
[Initial context: question + codes + DDL + few-shot examples]

Agent → writes SQL → calls execute_sql(sql="SELECT ...")
Tool  → validates (pass) → executes → returns "Success. 15 rows: ..."
Agent → [no more tool calls] → done
```

**Recovery path (2-3 iterations):**
```
[Initial context: question + codes + DDL + few-shot examples]

Agent → writes SQL → calls execute_sql(sql="SELECT ... export_value_usd ...")
Tool  → validates (pass) → executes → returns "Error: column 'export_value_usd' does not exist"

Agent → [sees error, sees DDL in context showing 'export_value'] →
        writes corrected SQL → calls execute_sql(sql="SELECT ... export_value ...")
Tool  → validates (pass) → executes → returns "Success. 15 rows: ..."
Agent → [no more tool calls] → done
```

Note: in the recovery path, the agent doesn't need to call `explore_schema` because the DDL is already in its initial context. It can see the correct column name. This is the advantage of the agent being the SQL expert — it has all the information it needs to self-correct without extra round-trips.

**Schema exploration path (when initial DDL isn't enough):**
```
Agent → writes SQL → calls execute_sql(sql="SELECT ... FROM hs92.country_year ...")
Tool  → validates → "Error: table 'hs92.country_year' does not exist"

Agent → "Let me check what tables are available in hs92" →
        calls explore_schema(query="List tables in the hs92 schema")
Tool  → returns "hs92.country_year_1, hs92.country_product_year_4, ..."

Agent → writes corrected SQL with right table name → calls execute_sql(...)
Tool  → "Success. ..."
```

**Multi-step CTE path (complex analytical questions):**
```
[Initial context: "Which of the top 10 coffee exporters in 2020 also have ECI > 0.5?"]

Agent → [plans approach: 1. find top coffee exporters, 2. join with ECI data, 3. filter]
        writes CTE-based SQL → calls execute_sql(sql="""
          WITH top_coffee AS (
            SELECT location_code, SUM(export_value) as total
            FROM hs92.country_product_year_4
            WHERE product_code = '0901' AND year = 2020
            GROUP BY location_code ORDER BY total DESC LIMIT 10
          )
          SELECT tc.location_code, tc.total, cy.eci
          FROM top_coffee tc
          JOIN hs92.country_year cy ON tc.location_code = cy.location_code
          WHERE cy.year = 2020 AND cy.eci > 0.5
        """)
Tool  → "Success. 6 rows: ..."
Agent → [no more tool calls] → done
```

**Exploratory multi-query path (when the agent needs to inspect data before deciding):**
```
Agent → "Let me check what years are available" →
        calls execute_sql(sql="SELECT DISTINCT year FROM hs92.country_year ORDER BY year DESC LIMIT 5")
Tool  → "Success. 5 rows: (2022,), (2021,), (2020,), ..."

Agent → [sees latest year is 2022, writes final analytical query using 2022] →
        calls execute_sql(sql="SELECT ... WHERE year = 2022 ...")
Tool  → "Success. 15 rows: ..."
Agent → done
```

Note: the exploratory path is legitimate — the agent genuinely didn't know what years existed. But it should not run separate queries just to build up results incrementally when CTEs would work.

**Product re-extraction path (rare, ~5-10% of cases):**
```
Agent → writes SQL → calls execute_sql(...)
Tool  → "Success. 0 rows returned."

Agent → "Empty results for HS92 coffee codes. Maybe services data is needed." →
        calls lookup_products(instruction="Re-extract including services schemas")
Tool  → returns new codes + updated DDL for services tables

Agent → writes new SQL using services tables → calls execute_sql(...)
Tool  → "Success. 8 rows: ..."
```

---

## 3A. Epistemic Map: What Each Node Knows, Decides, and Passes Forward

The current prompts have been carefully crafted to match each node's epistemic position — what it needs to know to do its job, and nothing more. The new architecture must maintain this discipline. This section maps the information flow through every node in the new pipeline, specifying what knowledge each node holds, what decisions it makes, and what the sub-agent's prompt needs to contain (and why).

### Information flow: progressive enrichment

The pipeline progressively transforms a natural-language question into structured query results. Each node adds one layer of epistemic enrichment:

```
User question (natural language)
    │
    ▼
[extract_tool_question] ── pure extraction ──────────────────────────────
    │  outputs: question string, optional context
    ▼
[extract_products] ── classification + extraction (lightweight LLM) ────
    │  adds: which schemas, which products (guessed), which countries
    ▼
[lookup_codes] ── verification bridge (DB + lightweight LLM) ───────────
    │  adds: verified product codes (DB-confirmed)
    ▼
[get_table_info] ── schema retrieval (pure I/O) ────────────────────────
    │  adds: full DDL for relevant tables
    ▼
[sql_query_agent] ── creative SQL + execution (frontier LLM, multi-turn)
    │  adds: SQL, results, timing
    ▼
[format_results] ── packaging (pure function) ──────────────────────────
    │  outputs: ToolMessage for orchestrator agent
```

Each node's prompt/logic is tuned to the exact information available at that stage. A node never needs information from a later stage, and it never receives unnecessary information from an earlier stage.

### Node: `extract_tool_question` (unchanged)

| Aspect | Detail |
|--------|--------|
| **Knows** | Raw `tool_calls[0]["args"]` from the agent's last message |
| **Decides** | Nothing — pure extraction |
| **Outputs** | `pipeline_question` (str), `pipeline_context` (str) |
| **Model** | None — deterministic |

The `context` field is a deliberate information channel: the orchestrator agent can pass technical context (e.g., metric definitions learned from `docs_tool`) to inform SQL generation, without the SQL pipeline needing access to the full conversation history. This separation is important — the sub-agent should focus on the SQL task, not parse conversation turns.

### Node: `extract_products` (unchanged)

| Aspect | Detail |
|--------|--------|
| **Knows** | The question; `PRODUCT_EXTRACTION_PROMPT` (schema decision tree, product ID heuristics, country detection, group detection rules) |
| **Decides** | (1) Classification schemas, (2) product mentions with best-guess codes, (3) whether DB verification needed, (4) whether group tables needed, (5) countries mentioned |
| **Doesn't know** | Whether guessed product codes actually exist in DB; table structures; SQL syntax |
| **Outputs** | `pipeline_products: SchemasAndProductsFound` |
| **Model** | Lightweight, structured output |

This is **hypothesis generation, not verification**. The LLM guesses product codes from training data (e.g., "coffee → 0901"). These are initial hypotheses confirmed or corrected by the next node. Override handling (`override_schema`, `override_mode`) is deterministic post-processing on the LLM output.

**Prompt design principle:** The extraction prompt needs the schema decision tree (when goods vs services vs both) and product identification heuristics, but NOT table DDL or SQL syntax. Its epistemic scope is *"what kind of data are we looking for?"* not *"how do we query it?"*

### Node: `lookup_codes` (unchanged)

| Aspect | Detail |
|--------|--------|
| **Knows** | Extracted products (from `extract_products`), actual DB product classification tables, `PRODUCT_CODE_SELECTION_PROMPT`, the user question |
| **Decides** | Final verified product codes — the codes SQL generation will use |
| **Doesn't know** | Table structures; how SQL queries work |
| **Outputs** | `pipeline_codes` (formatted string) |
| **Model** | Lightweight (for code selection step only) |

This is the **verification bridge** between LLM intuition and database reality. Two-step process:
1. **`get_candidate_codes()`**: Dual-source query — verify LLM code guesses against DB AND search DB by product name. Produces `llm_suggestions` + `db_suggestions`.
2. **`select_final_codes()`**: Lightweight LLM picks the best match from both sources, in context of the user question.

Without this step, SQL generation might use product codes that don't exist in the DB. The code selection prompt (`PRODUCT_CODE_SELECTION_PROMPT`) is minimal — just 4 lines — because it operates on a narrow, well-defined task: "given these candidate codes and the question, pick the best ones."

### Node: `get_table_info` (unchanged)

| Aspect | Detail |
|--------|--------|
| **Knows** | Identified schemas (from `extract_products`), table descriptions JSON, `SQLDatabaseWithSchemas` instance |
| **Decides** | Nothing — pure I/O |
| **Outputs** | `pipeline_table_info` (DDL string with table structures + descriptions) |
| **Model** | None — deterministic |

Retrieves DDL for all relevant tables: data tables (e.g., `hs92.country_product_year_4`) and classification lookup tables (e.g., `classification.location_country`, `classification.product_hs92`). Includes classification tables needed for JOINs. The quality of the sub-agent's SQL generation depends directly on the completeness of this DDL.

### Node: `sql_query_agent` (NEW — replaces generate_sql + validate_sql + execute_sql + retry)

This is the core architectural change. The sub-agent's epistemic position differs fundamentally from the current `generate_sql` node because it operates in a **multi-turn loop with tool access**.

#### Three layers of knowledge

**Layer 1 — System prompt (static domain knowledge):**

The sub-agent's system prompt contains knowledge that applies to EVERY query, regardless of the specific question. This is the domain knowledge currently in `SQL_GENERATION_PROMPT`, restructured for an agentic context:

| Knowledge block | Source in current code | Why in system prompt |
|-----------------|----------------------|---------------------|
| Table selection guide | `SQL_GENERATION_PROMPT` (lines on `country_year` vs `country_product_year_N`, etc.) | Sub-agent must know which table pattern fits which question type to write correct SQL |
| Column naming rules | `SQL_GENERATION_PROMPT` ("Never filter on `product_id`/`country_id`... always use `product_code`/`iso3_code`") | Wrong column names are the #1 error type. Must always be available, not just on retry |
| Metric definitions and formulas | `SQL_GENERATION_PROMPT` (RCA, ECI, PCI, COG, distance, CAGR, market share, new products, growth opportunities) | Sub-agent needs to know how to compute these. Formulas, not data |
| Services vs goods differences | `SQL_GENERATION_PROMPT` ("Services tables have different schemas... Combine via UNION ALL, never JOIN") + `SQL_GROUP_TABLES_BLOCK` | When to use UNION ALL, service table suffix rules (_1 vs _2), schema naming |
| Schema year coverage | `SQL_GENERATION_PROMPT` ("hs12 data starts from 2012, hs92 from 1995, sitc from 1962") | Determines which schema to use for a given time range |
| LIMIT rules | `SQL_GENERATION_PROMPT` ("LIMIT {top_k} unless... For enumeration queries... do NOT apply LIMIT") | Prevent over-truncation or missing it entirely |
| Common mistakes | `SQL_GENERATION_PROMPT` (Common Mistakes to Avoid section) | Preventive guidance that should be in-context for every attempt |

Plus **new agentic guidance** not in the current prompt:

| Knowledge block | Why it's new |
|-----------------|-------------|
| Tool usage strategy | When to use each tool: `execute_sql` first, `explore_schema` on schema gaps, `lookup_products` on suspected wrong codes. Priority order prevents wasteful exploration |
| CTE guidance | Multi-step queries should use CTEs, not separate `execute_sql` calls. See [Section 5](#5-multi-step-query-chaining-strategy) |
| Error recovery patterns | What different error types mean and how to fix them. The current retry has none of this — the sub-agent needs explicit patterns (column not found → check DDL; table not found → check schema + suffix; empty → reconsider codes/schema/period) |
| Empty-result reasoning | Don't give up on 0 rows. Consider alternative codes, schemas, time periods. This is the sub-agent's most important new capability vs. the current blind retry |
| Stopping criteria | When to stop iterating and what to output. Without this, the sub-agent may over-explore or fail to terminate |
| Output contract | Results are returned via state fields; the sub-agent's final text is NOT shown to the user. Without this, the sub-agent may try to "summarize" results in prose |

**Layer 2 — Initial context (per-query specifics, in the first HumanMessage):**

Information that changes for every query — the specific question, codes, and DDL. Mirrors what `create_query_generation_chain()` currently receives as parameters:

| Context element | Source | Why in initial context (not system prompt) |
|----------------|--------|-------------------------------------------|
| User question | `extract_tool_question` | Changes every query |
| Technical context | Agent's tool_call `context` arg | Optional; may contain `docs_tool` findings |
| Verified product codes | `lookup_codes` output | Question-specific |
| Table DDL | `get_table_info` output | Schema-specific; varies by identified schemas |
| Few-shot examples | `queries.json` + SQL files | Could be in system prompt, but placed here to keep the system prompt focused on rules. Also allows future relevance-filtering |
| Active overrides | Parent state (`override_direction`, `override_mode`) | Per-conversation constraints |

**Layer 3 — Tool-accessible knowledge (on demand):**

| Tool | Knowledge it provides | When needed |
|------|----------------------|-------------|
| `execute_sql` | Whether SQL is valid; whether it runs; what the DB returns | Every query — this is the sub-agent's primary action |
| `explore_schema` | DDL for tables NOT in initial context; actual data values (sample rows); available schemas and tables | When initial DDL is insufficient or when the sub-agent needs to verify what data looks like (e.g., is `location_code` "USA" or "us" or 840?) |
| `lookup_products` | Alternative product codes and schemas; updated DDL for newly identified schemas | When initial product extraction was wrong (~5-10% of queries) |

#### Intentional information barriers

These are deliberate design choices — things the sub-agent does NOT know:

| Not known | Why excluded |
|-----------|-------------|
| Orchestrator agent's conversation history | Keeps context window focused on the SQL task. The `context` field is the deliberate, curated channel for prior knowledge |
| GraphQL pipeline existence | Irrelevant to SQL task; would waste attention and tokens |
| Docs pipeline existence | The agent does documentation lookup before calling `query_tool`; the sub-agent doesn't need to know this |
| Query budget system | Budget is managed by the parent graph (`format_results` increments `queries_executed`). The sub-agent only cares about getting the SQL right |
| How `format_results` works | The sub-agent outputs structured state fields; packaging is someone else's job |

#### Epistemic advantage over the current retry mechanism

The current retry mechanism (`SQL_RETRY_BLOCK`) appends "Failed SQL: ... Error: ..." and regenerates from scratch. The sub-agent improves on this in five specific ways:

1. **DDL always in context.** When `column 'export_value_usd' does not exist`, the sub-agent can immediately see (in the DDL from its initial context) that the column is actually `export_value`. No extra tool call needed. The current retry has the DDL too, but the sub-agent also has the full reasoning chain of what it tried and why.

2. **Schema exploration available.** When `table 'hs92.country_year' does not exist`, the sub-agent can call `explore_schema("List tables in hs92")` to discover the correct table name. The current retry can only guess.

3. **Product re-extraction.** When results are empty and the sub-agent suspects wrong product codes, it can call `lookup_products("Try SITC instead of HS92")`. The current retry regenerates with the same wrong codes.

4. **Cumulative reasoning.** The sub-agent's conversation history preserves the full chain: attempt 1 → error → reasoning → attempt 2. It doesn't regenerate from scratch; it builds on what it learned.

5. **Diagnostic exploration.** The sub-agent can run exploratory queries (e.g., `SELECT DISTINCT year FROM ... ORDER BY year DESC LIMIT 5`) to understand the data before writing the final analytical query.

#### How the sub-agent prompt differs from SQL_GENERATION_PROMPT

The current `SQL_GENERATION_PROMPT` is designed for **single-shot generation**: *"Based on your analysis, generate a SQL query... Just return the SQL query, nothing else."* The sub-agent prompt serves a fundamentally different purpose: **multi-turn SQL expert with tool access**.

| Aspect | SQL_GENERATION_PROMPT (current) | Sub-agent system prompt (new) |
|--------|--------------------------------|-------------------------------|
| Mode | Single-shot: one prompt → one SQL string | Multi-turn: reason → act → observe → repeat |
| Output | Raw SQL text (parsed by `StrOutputParser` + strip) | Tool calls (`execute_sql`, `explore_schema`, `lookup_products`) |
| Error handling | `SQL_RETRY_BLOCK` appended on retry (blind, no new info) | Natural: sees error in conversation, reasons about fix with DDL in context |
| Planning | Implicit ("1. Identify main elements, 2. Select tables, ...") | Explicit: "Plan first for complex queries. Use CTEs for multi-step logic." |
| Context persistence | None — regenerates from scratch on retry | Full: conversation history preserves all reasoning and results across attempts |
| DDL + codes | In the prompt template (via `{table_info}`, `{codes}`) | In the initial HumanMessage (same content, different delivery vehicle) |
| Few-shot examples | Via `FewShotPromptTemplate` (interleaved in prompt) | In the initial HumanMessage (as formatted text) |

**What migrates directly:** ALL domain knowledge (table selection, column naming, metrics, common mistakes, schema coverage, LIMIT rules, services handling, group table guidance). The words may be restructured; the knowledge does not change.

**What is removed:** "Just return the SQL query, nothing else" (replaced by tool-calling). `SQL_RETRY_BLOCK` (replaced by natural conversation). `FewShotPromptTemplate` mechanics (replaced by plain text in HumanMessage).

**What is added:** Tool usage strategy, CTE guidance, error recovery patterns, empty-result reasoning, stopping criteria, output contract.

### Node: `format_results` (mostly unchanged)

| Aspect | Detail |
|--------|--------|
| **Knows** | Parent state: agent's tool_calls (for ToolMessage IDs), `last_error`, `pipeline_result`, `pipeline_sql`, columns, rows, timing, products, codes, question |
| **Decides** | Whether to return error message or success content; ToolMessage formatting |
| **Doesn't know** | Sub-agent's internal reasoning; how many iterations the sub-agent ran |
| **Outputs** | `messages` (ToolMessages), `queries_executed` (+1), `sql_call_history` snapshot |
| **Model** | None — deterministic |

The wrapper node (`sql_query_agent_node`) maps the sub-agent's final state to the parent's `pipeline_*` fields. `format_results` reads those fields and produces ToolMessages — exactly as it does today. The sub-agent's internal reasoning trace is invisible to `format_results`. This is intentional: `format_results` is the boundary between the SQL pipeline and the orchestrator agent, and its contract shouldn't change.

### Conditional block migration: what goes where

The current `build_sql_generation_prefix()` conditionally appends several blocks to the SQL generation prompt. In the new architecture, each block migrates to a specific location:

| Current block | Condition | New location | Rationale |
|---------------|-----------|-------------|-----------|
| `SQL_GENERATION_PROMPT` (base) | Always | Sub-agent system prompt | Static domain knowledge — same for every query |
| `SQL_CODES_BLOCK` | When product codes found | Initial HumanMessage (under "Product codes identified:") | Per-query data |
| `SQL_DIRECTION_BLOCK` | When `override_direction` set | Initial HumanMessage (under override section) + system prompt constraint rule | Per-conversation override |
| `SQL_MODE_BLOCK` | When `override_mode` set | Initial HumanMessage (under override section) + system prompt constraint rule | Per-conversation override |
| `SQL_GROUP_TABLES_BLOCK` | When `requires_group_tables` | Sub-agent system prompt (always present) | The current conditional is an optimization to save tokens. For the sub-agent, group table guidance should always be available — it's 30 lines and the sub-agent may discover it needs group tables after an initial attempt (e.g., user asks about "Africa's exports" and the initial extraction didn't flag `requires_group_tables`) |
| `SQL_CONTEXT_BLOCK` | When agent passes context | Initial HumanMessage (under "Technical context:") | Per-query data from the orchestrator |
| `SQL_RETRY_BLOCK` | On retry after failure | **Removed entirely.** Error recovery is handled naturally via conversation history | The sub-agent sees its previous SQL + error in its own message history |

**Key decision: `SQL_GROUP_TABLES_BLOCK` becomes always-present.** In the current pipeline, this block is conditional because the extraction LLM detects group queries and sets `requires_group_tables`. But extraction isn't perfect — sometimes a question about "Sub-Saharan Africa" isn't flagged. With the block always in the system prompt, the sub-agent can use group table patterns even if the extraction missed the flag. The cost is ~30 lines of prompt (~600 tokens) — acceptable given the sub-agent already has a large system prompt.

### `explore_schema`: epistemic design detail

The `explore_schema` tool serves as the sub-agent's **schema encyclopedia**. It must support four distinct epistemic needs, all through a single natural-language `query` parameter:

| Need | Example query | What it returns | When used |
|------|---------------|-----------------|-----------|
| "What schemas exist?" | "What schemas are available?" | List of schemas (hs92, hs12, sitc, services_unilateral, services_bilateral) with year coverage | Sub-agent suspects it needs a different classification |
| "What tables are in schema X?" | "List tables in the hs92 schema" | Table names with descriptions | Sub-agent got "table not found" and needs to discover correct name + suffix |
| "What columns does table X have?" | "Show columns in hs92.country_product_year_4" | Full DDL for the table | Sub-agent needs DDL for a table not in initial context |
| "What do values look like?" | "Show 5 sample rows from hs92.country_product_year_4" | Sample rows with headers | Sub-agent needs to understand data format (is `location_code` "USA" or "us" or 840?) |

**Implementation:** `explore_schema` uses `SQLDatabaseWithSchemas` methods for DDL and `information_schema` queries or raw SQL for listings and samples. It makes NO LLM calls — it's a mechanical tool. The natural-language `query` parameter is interpreted by simple pattern matching or keyword detection, not by an LLM. This keeps the tool fast and deterministic.

**Token budget:** Sample rows are limited to 5 rows. DDL for a single table is typically 500-1500 tokens. Schema listings are small. The tool should cap its output at ~3000 tokens to avoid bloating the sub-agent's context.

### `lookup_products`: epistemic design detail

The `lookup_products` tool is the sub-agent's escape hatch when the deterministic product extraction got it wrong. Its epistemic flow:

1. Sub-agent provides an `instruction` string describing what to do differently
2. The instruction is prepended to the original question as guidance for the extraction LLM: *"[Instruction: Try SITC classification instead of HS92] Original question: What did Kenya export in 2020?"*
3. Tool runs the full extraction pipeline: re-run `extract_products` → `get_candidate_codes` → `select_final_codes`
4. Tool fetches DDL for any newly identified schemas via `get_table_info_for_schemas`
5. Returns: new product codes + updated DDL + summary of what changed

**Why prepend-to-question (not prompt modification):** The extraction LLM (`PRODUCT_EXTRACTION_PROMPT`) already handles questions about schemas and products. Prepending the instruction naturally guides it without requiring a separate code path for "re-extraction mode." The instruction acts as a hint, not a command — the extraction LLM still applies its full decision tree.

**What the sub-agent needs to know to use this tool well:** The system prompt should explain when different schemas apply (HS92 for historical data pre-2012, HS12 as default, SITC for longest time series back to 1962, services_* for non-goods trade). This lets the sub-agent formulate useful instructions like "Try SITC — the question asks about trade in the 1970s" rather than vague ones like "try again."

**Cost:** This tool is expensive — 2 LLM calls (extraction + code selection) + DB queries + DDL fetch. The system prompt must make clear this is a last resort, not a first action.

---

## 4. Tool Design: Harnesses and Information Sources

The sub-agent has exactly 3 tools. The design principle: **tools handle mechanics, the agent handles creativity.** SQL generation is a creative act; validation, execution, schema lookup, and product extraction are mechanical.

### Tool 1: `execute_sql`

**What the agent provides:** A `sql` string — the complete SQL query it has written.

**What the tool does internally:**
1. Runs `validate_sql()` on the SQL — automatic, cannot be skipped
2. If validation fails: returns validation errors + the SQL (no execution attempted)
3. If validation passes: executes against the database
4. Returns: formatted results (success) or error message with full context

**What `validate_sql()` checks (from `sql_validation.py`):**
- Empty / whitespace-only SQL — reject
- Syntax parse via sqlglot — catch `ParseError`
- Write-operation blocking — reject DML/DDL (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, REVOKE)
- `SELECT *` detection — warn but allow
- Leading LIKE wildcard — warn but allow

**Intentionally NOT checked by validation:** Table existence, schema mismatch, and column existence are left to the database — it produces clearer error messages, and pre-checking them requires fragile DDL parsing that produces false positives.

**Why validation is bundled:** Validation is a deterministic safety check (syntax, write-blocking). There is zero epistemic value in the agent "deciding" whether to validate. It should always happen. Bundling it into `execute_sql` makes it automatic.

**Interface:**
```python
execute_sql(sql: str) -> str
"""Validate and execute a SQL query against the Atlas trade database.

Returns the query results if successful. If the SQL fails validation
(syntax errors, unknown tables/columns) or execution (database errors),
returns a detailed error message. Validation is automatic.

Always returns the SQL that was attempted, whether it succeeded or failed."""
```

**Result formatting (see also [Section 5](#5-multi-step-query-chaining-strategy)):**
- Column headers always included in output
- Row count always reported (e.g., `"62 rows returned"`)
- Results truncated beyond 50 rows: first 20 rows shown, remainder summarized
- On 0 rows: explicit `"0 rows returned"` with a hint to check product codes, table suffix, or time period

### Tool 2: `explore_schema`

**What the agent provides:** A `query` string describing what schema information it wants.

**What the tool does internally:**
- Lists tables in a schema, or returns DDL for a specific table
- Read-only, no LLM calls, fast

**When the agent uses this:**
- When it needs DDL for tables NOT in its initial context (e.g., it realizes it needs a different schema's tables)
- When the initial DDL is ambiguous and it wants to verify specific column details
- Most queries won't need this — the initial DDL from the deterministic phase is usually sufficient

**Interface:**
```python
explore_schema(query: str) -> str
"""Explore the Atlas database schema. Returns table listings, DDL,
column names, descriptions, or sample data.

Examples:
- "List tables in the hs92 schema"
- "Show columns in hs92.country_country_product_year_4"
- "What schemas are available?"
- "Show 5 sample rows from hs92.country_product_year_4"
"""
```

**Data sampling:** `explore_schema` also supports returning a small number of sample rows from a table (e.g., 5 rows). This helps the agent understand what values look like in practice — e.g., whether `location_code` is `"USA"` or `"us"` or `840`. This is similar to [ReFoRCE's column exploration](https://arxiv.org/abs/2502.00675) capability. Folded into `explore_schema` rather than a separate tool to keep tool count at 3.

### Tool 3: `lookup_products`

**What the agent provides:** An `instruction` string describing what to look for differently.

**What the tool does internally:**
1. Runs the full product extraction pipeline (extract → candidate lookup → code selection)
2. Fetches updated DDL for any newly identified schemas
3. Updates sub-agent state with new codes + table_info

**When the agent uses this:**
- Wrong classification schema (HS92 has no services → try services tables)
- Ambiguous product ("chips" → electronic vs food)
- Empty results suggest wrong product codes
- ~5-10% of queries; the deterministic extraction is correct for the rest

**Interface:**
```python
lookup_products(instruction: str) -> str
"""Re-extract product codes and classification schemas from the question.
Returns new product codes, identified schemas, and updated table DDL.

Use when:
- Results are empty and you suspect wrong product codes
- The wrong classification schema was identified
- You need services tables but only have goods tables (or vice versa)

Examples:
- "Try SITC classification instead of HS92"
- "Look for electronic chips, not food products"
- "Include services schemas — the question asks about tourism"
"""
```

### Why only 3 tools

LLM tool-selection accuracy degrades with tool count. 3 tools maps to 3 distinct cognitive actions:
1. "Run this SQL I wrote" (`execute_sql`)
2. "Show me more about the schema" (`explore_schema`)
3. "Reconsider what products I'm looking for" (`lookup_products`)

These are easy to distinguish. An agent will rarely confuse "I want to run SQL" with "I want to look up product codes." Adding more fine-grained tools (separate validate, separate list-tables vs get-DDL) would fragment the decision space without adding value.

---

## 5. Multi-Step Query Chaining Strategy

A critical question for an agentic SQL system: how does the agent handle complex questions that require combining results from multiple logical steps? For example: "Which of the top 10 coffee exporters in 2020 also have an ECI above 0.5?" This requires (1) finding top coffee exporters, then (2) filtering by ECI — two logical sub-queries whose results must be combined.

### The problem

The current plan gives the agent `execute_sql` as a tool it can call multiple times. But each call is independent — the agent can't `JOIN` the results of query 1 into query 2. Without a chaining mechanism, the agent must either:
- Compose the entire multi-step logic in a single query (hard without guidance)
- Run query 1, eyeball the results in its context window, then hardcode values into query 2 (brittle, doesn't scale past ~10 rows)

### Chosen approach: CTEs + state-based reasoning

We use two complementary mechanisms, neither of which requires infrastructure changes:

**Mechanism 1: CTEs (Common Table Expressions) within a single query.** The agent composes a single SQL statement using `WITH` clauses to express multi-step logic. Each CTE is a named sub-query whose results feed into subsequent CTEs or the final `SELECT`. Example:

```sql
WITH top_coffee_exporters AS (
  SELECT location_code, SUM(export_value) as total_exports
  FROM hs92.country_product_year_4
  WHERE product_code = '0901' AND year = 2020
  GROUP BY location_code
  ORDER BY total_exports DESC
  LIMIT 10
),
exporter_eci AS (
  SELECT cy.location_code, cy.eci, tce.total_exports
  FROM hs92.country_year cy
  JOIN top_coffee_exporters tce ON cy.location_code = tce.location_code
  WHERE cy.year = 2020
)
SELECT * FROM exporter_eci WHERE eci > 0.5 ORDER BY total_exports DESC;
```

This is the approach recommended by the top-performing text-to-SQL systems in the literature. SQL-of-Thought, AGENTIQL, and Chain-of-Query all decompose complex questions into sub-problems at the planning stage, then compose a single CTE-based query rather than chaining separate queries.

**Mechanism 2: State-based reasoning across iterations.** When the agent runs a query and gets results, those results appear in its conversation history (the `messages` list). If the first query reveals something unexpected — e.g., a table has different columns than expected, or the result set suggests a different analytical approach — the agent can write a completely new query informed by what it learned. It doesn't literally `JOIN` against in-memory data, but it can incorporate the insights.

### What this means for the prompt

The `SQL_SUBAGENT_PROMPT` must include explicit query planning and CTE guidance:

```
## Query Planning

For complex questions involving multiple dimensions or multi-step logic:

1. **Plan first.** Before writing SQL, outline your approach:
   - What sub-questions need answering?
   - What tables and joins are needed for each?
   - Can the sub-questions be expressed as CTEs in a single query?

2. **Use CTEs for multi-step queries.** Common Table Expressions (WITH clauses)
   let you break complex logic into named, readable steps within a single query.
   This is preferred over running multiple separate queries. Each CTE can
   reference previous CTEs.

3. **Reserve multiple execute_sql calls for genuine exploration**, not for
   building up results incrementally. Valid reasons for multiple calls:
   - First query returned an error and you're correcting it
   - First query returned empty results and you're trying a different approach
   - You need to check what values exist in a column before filtering on it
```

### What this means for `execute_sql` tool output

The tool must return results in a way that supports state-based reasoning without blowing up the context window:

- **Truncation:** If a result exceeds 50 rows, return the first 20 rows plus a summary: `"... (42 more rows, 62 total). Showing first 20."` The agent has enough to validate correctness without consuming excessive tokens.
- **Column names always included:** The result string always starts with column headers so the agent can verify it got the right columns.
- **Row count always included:** Even on success, always report `"N rows returned"` so the agent can reason about empty or unexpectedly large results.

### Alternatives considered and rejected

See [Section 12: Decisions Log](#12-decisions-log-alternatives-considered-and-rejected) for the full analysis of temporary tables, Python sandboxes, and other approaches we evaluated and chose not to pursue.

---

## 6. Sub-Agent State and Reasoning Loop

### Sub-Agent State Schema

```python
class SQLSubAgentState(TypedDict):
    """Internal state for the SQL sub-agent's reasoning loop."""
    # Context (populated before loop starts, from deterministic phase)
    question: str
    context: str              # optional technical context from orchestrator
    products: Optional[SchemasAndProductsFound]
    codes: str                # formatted product codes string
    table_info: str           # DDL + descriptions for identified schemas
    override_direction: Optional[str]   # e.g. "exports", "imports"
    override_mode: Optional[str]        # e.g. "goods", "services"

    # ReAct conversation (sub-agent's internal reasoning trace)
    messages: Annotated[list[BaseMessage], add_messages]

    # Working state (updated by tool nodes)
    sql: str                  # most recent successfully executed SQL
    result: str               # formatted result string
    result_columns: list[str]
    result_rows: list[list]
    execution_time_ms: int
    last_error: str
    iteration_count: int      # safety counter

    # Accumulator
    attempt_history: Annotated[list[dict], operator.add]
```

### Reasoning Node

```python
async def reasoning_node(state: SQLSubAgentState) -> dict:
    """Sub-agent LLM: generates SQL and decides on tools."""
    if state.get("iteration_count", 0) >= MAX_ITERATIONS:
        return {"messages": [AIMessage(content="Reached maximum attempts.")]}

    # This is the frontier model — the same one used for SQL generation today.
    # It generates SQL natively (via tool call args) and reasons about errors.
    model = frontier_llm.bind_tools(tool_schemas)
    response = await model.ainvoke(
        [SystemMessage(content=SQL_SUBAGENT_PROMPT)] + state["messages"]
    )
    return {
        "messages": [response],
        "iteration_count": state.get("iteration_count", 0) + 1,
    }
```

### Initial Context Message

The sub-agent starts with a single `HumanMessage` containing everything from the deterministic phase:

```python
initial_message = HumanMessage(content=f"""Answer this question by writing a SQL query:

{question}

{f"Technical context: {context}" if context else ""}

Product codes identified:
{codes if codes else "No specific product codes identified."}

{_format_overrides(override_direction, override_mode)}

Table schemas (DDL) — these are the tables available to query:
{table_info}

Reference examples (question → SQL):
{formatted_few_shot_examples}

Write a SQL query to answer the question, then call execute_sql to run it.""")
```

**Trade override handling:** The current `generate_sql_node` passes `override_direction` and `override_mode` from parent state to `create_query_generation_chain()`, which injects constraints into the SQL generation prompt (e.g., adding `WHERE trade_direction = 'exports'` guidance). The sub-agent must receive these overrides too. They are passed in the initial context message (shown above) and/or included in the sub-agent's system prompt as constraint rules. The `override_schema` is already handled by `extract_products_node` in the deterministic phase (it forces the classification schema), so it doesn't need separate handling in the sub-agent.

**Few-shot example formatting:** Currently, `create_query_generation_chain()` uses LangChain's `FewShotPromptTemplate` to format examples as `"User question: {question}\nSQL query: {query}"` pairs. For the sub-agent, these same examples are formatted as plain text in the initial `HumanMessage` rather than via a template. The examples are loaded from `queries.json` + SQL files in `src/example_queries/` — same source, different formatting.
```

The system prompt (`SQL_SUBAGENT_PROMPT`) contains static domain knowledge — table selection rules, column naming conventions, metric definitions, common mistakes, plus new agentic guidance (tool strategy, error recovery patterns, stopping criteria). The initial context message contains per-query specifics — DDL, codes, examples, overrides. This mirrors the current `create_query_generation_chain()` structure: prefix (domain rules) + few-shot examples + question, but now the agent retains the full context across iterations.

See [Section 3A](#3a-epistemic-map-what-each-node-knows-decides-and-passes-forward) for the detailed epistemic analysis of what knowledge lives in the system prompt vs. initial context vs. tools, and why. See [Section 9, Step 6](#step-6-prompt-engineering) for the detailed prompt structure specification.

### Max Iterations

`MAX_ITERATIONS = 5` — allows 1 initial attempt + up to 4 corrections.

Current pipeline: 2 total attempts (original + 1 blind retry). The sub-agent gets more because each attempt is informed — it reasons about what failed and why.

The `recursion_limit` on the subgraph is 25 (separate from parent's 150).

---

## 7. Integration with Parent Graph

### Wrapper Node

```python
async def sql_query_agent_node(state: AtlasAgentState) -> dict:
    """Invoke the SQL sub-agent and map results back to parent state."""
    sub_input = {
        "question": state["pipeline_question"],
        "context": state.get("pipeline_context", ""),
        "products": state.get("pipeline_products"),
        "codes": state.get("pipeline_codes", ""),
        "table_info": state.get("pipeline_table_info", ""),
        "override_direction": state.get("override_direction"),
        "override_mode": state.get("override_mode"),
        "messages": [],
        "sql": "",
        "result": "",
        "result_columns": [],
        "result_rows": [],
        "execution_time_ms": 0,
        "last_error": "",
        "iteration_count": 0,
        "attempt_history": [],
    }

    result = await sql_subagent.ainvoke(
        sub_input,
        config={"recursion_limit": 25},
    )

    return {
        "pipeline_sql": result.get("sql", ""),
        "pipeline_result": result.get("result", ""),
        "pipeline_result_columns": result.get("result_columns", []),
        "pipeline_result_rows": result.get("result_rows", []),
        "pipeline_execution_time_ms": result.get("execution_time_ms", 0),
        "last_error": result.get("last_error", ""),
        "retry_count": 0,  # no longer used; clear stale state
        "pipeline_sql_history": result.get("attempt_history", []),
        "token_usage": result.get("token_usage", []),
        "step_timing": result.get("step_timing", []),
    }
```

### Query Budget Accounting

`queries_executed` is a shared budget counter incremented by both `format_results_node` (SQL pipeline) and `format_graphql_results` (GraphQL pipeline). The parent agent's `route_after_agent` checks `queries_executed >= max_uses` to gate further tool calls. This is unaffected by the sub-agent change — `format_results_node` still increments it, and the sub-agent's internal iterations do NOT count against this budget (they are sub-agent tool calls, not parent-level tool calls).

### Parent Graph Changes

In `graph.py`, the SQL pipeline wiring changes from:

```python
# BEFORE: 3 nodes, 7 edges, 2 conditional routing functions
builder.add_node("generate_sql", partial(generate_sql_node, ...))
builder.add_node("validate_sql", partial(validate_sql_node, ...))
builder.add_node("execute_sql", partial(execute_sql_node, ...))
builder.add_edge("get_table_info", "generate_sql")
builder.add_edge("generate_sql", "validate_sql")
builder.add_conditional_edges("validate_sql", route_after_validation, {...})
builder.add_conditional_edges("execute_sql", route_after_execution, {...})
```

To:

```python
# AFTER: 1 node, 2 edges, no conditional routing
builder.add_node("sql_query_agent", partial(sql_query_agent_node, ...))
builder.add_edge("get_table_info", "sql_query_agent")
builder.add_edge("sql_query_agent", "format_results")
```

`route_after_validation` and `route_after_execution` are deleted. The `generate_sql_node`, `validate_sql_node`, `execute_sql_node` functions in `sql_pipeline.py` remain — `execute_sql`'s DB execution logic is reused by the sub-agent's `execute_sql` tool node; `validate_sql` is called inside that same tool node.

### format_results Compatibility

`format_results_node` reads from parent state:
- `messages[-1]` — to get `tool_calls` and create `ToolMessage` responses
- `last_error`, `pipeline_result` — to determine success vs error content
- `pipeline_sql`, `pipeline_result_columns`, `pipeline_result_rows`, `pipeline_execution_time_ms` — for the `sql_call_history` snapshot
- `pipeline_products`, `pipeline_codes`, `pipeline_question` — also for the `sql_call_history` snapshot

`format_results_node` writes:
- `messages` — `ToolMessage` responses for each `tool_call`
- `queries_executed` — incremented by 1 (shared budget with GraphQL pipeline)
- `sql_call_history` — per-call snapshot (an `Annotated` accumulator, distinct from `pipeline_sql_history`)

The wrapper node writes `pipeline_sql`, `pipeline_result`, `pipeline_result_columns`, `pipeline_result_rows`, `pipeline_execution_time_ms`, and `last_error`. The fields `pipeline_products`, `pipeline_codes`, and `pipeline_question` are set by earlier deterministic nodes and persist in parent state — the wrapper does not need to re-set them. `format_results_node` works unchanged.

**Note:** `pipeline_sql_history` (tracks per-stage SQL versions: generated → validated → execution_error) and `sql_call_history` (tracks per-call snapshots with question/products/codes/result) are two different accumulator fields. The wrapper maps the sub-agent's `attempt_history` to `pipeline_sql_history`. The `sql_call_history` snapshot is built by `format_results_node` from the `pipeline_*` fields the wrapper sets.

---

## 8. Streaming Strategy

### Phase 1: Coarse-grained (ship first)

Update **two** constants:
- `PIPELINE_NODES` (frozenset) in `sql_pipeline.py`: remove `generate_sql`, `validate_sql`, `execute_sql`; add `sql_query_agent`.
- `PIPELINE_SEQUENCE` (ordered list) in `streaming.py` (line ~70): replace `generate_sql`, `validate_sql`, `execute_sql` with `sql_query_agent`. This list drives the frontend's pipeline stepper progress bar and `node_start`/`pipeline_state` SSE events.

The streaming system sees one node (`sql_query_agent`) where it previously saw three. The frontend pipeline stepper shows:

```
✓ Identifying products and schemas
✓ Looking up product codes
✓ Retrieving table schemas
⟳ Generating and executing SQL query...    ← was 3 separate steps
✓ Formatting results
```

Deterministic nodes and `format_results` still emit their individual events unchanged.

### Phase 1b: Fine-grained via callback (future, not in initial scope)

Pass a callback to the sub-agent that emits SSE events from inside tool nodes. This restores step-by-step granularity. Requires changes to the streaming layer.

---

## 9. Implementation Steps

### Step 0: Create branch and baseline eval

```bash
git checkout -b feat/agentic-sql-subagent
PYTHONPATH=$(pwd) pytest -m "eval" -v > eval_baseline_$(date +%Y%m%d).txt 2>&1
```

### Step 1: Write tests (red)

New file: `src/tests/test_sql_subagent.py`

| Test | Verifies |
|------|----------|
| `test_reasoning_routes_to_correct_tool` | Mock LLM returns tool_call for each tool → correct node reached |
| `test_reasoning_stops_on_no_tool_call` | No tool_calls → routes to done |
| `test_max_iterations_enforced` | Agent stops after MAX_ITERATIONS |
| `test_execute_sql_happy_path` | Valid SQL → validates → executes → returns result |
| `test_execute_sql_validation_error` | Invalid SQL → returns error, no execution |
| `test_execute_sql_execution_error` | Valid SQL, DB error → returns error with SQL |
| `test_explore_schema_returns_ddl` | Returns DDL for requested tables |
| `test_lookup_products_updates_state` | Re-extracts products, updates codes + table_info |
| `test_wrapper_maps_state_correctly` | Sub-agent result → correct parent state fields |
| `test_full_happy_path` | End-to-end: agent writes SQL, executes, succeeds |
| `test_full_recovery_path` | First attempt fails, agent corrects, second succeeds |
| `test_result_truncation` | Results >50 rows are truncated to first 20 with summary |
| `test_explore_schema_sample_rows` | `explore_schema` returns sample rows when requested |
| `test_zero_rows_hint` | 0-row result includes diagnostic hint about product codes/table suffix |

### Step 2: Build sub-agent (`src/sql_subagent.py`) (green)

New file:
1. `SQLSubAgentState` TypedDict
2. `SQL_SUBAGENT_PROMPT` constant — domain knowledge from `SQL_GENERATION_PROMPT` + tool strategy guidance + stopping criteria (**requires approval before finalizing**)
3. Tool node functions: `execute_sql_tool_node()`, `explore_schema_node()`, `lookup_products_node()`
4. `reasoning_node()` — frontier LLM with bound tools
5. `route_after_reasoning()` — dispatch to tool or done
6. `build_sql_subagent()` factory
7. `sql_query_agent_node()` wrapper for parent graph

Reuses from existing code (imported, not duplicated):
- `validate_sql()` from `sql_validation.py` (note: `sql_validation.py` only exports `validate_sql` and `ValidationResult` — it does NOT have `build_schema_from_ddl()` or `extract_table_names_from_ddl()`. The `explore_schema` tool will need to query schema information directly via `SQLDatabaseWithSchemas` methods or raw SQL against `information_schema`)
- `get_table_info_for_schemas()`, `get_tables_in_schemas()` from `sql_pipeline.py`
- `ProductAndSchemaLookup`, `format_product_codes_for_prompt()` from `product_and_schema_lookup.py`
- `async_execute_with_retry()`, `execute_with_retry()` from `error_handling.py`
- `SQLDatabaseWithSchemas` from `sql_multiple_schemas.py` (for `explore_schema` tool's DDL and table listing)

### Step 3: Integrate into parent graph

Edit `src/graph.py`:
- Import `build_sql_subagent`, `sql_query_agent_node`
- Remove `generate_sql`, `validate_sql`, `execute_sql` node registrations
- Remove `route_after_validation`, `route_after_execution`
- Add `sql_query_agent` node, wire edges

Edit `src/sql_pipeline.py`:
- Update `PIPELINE_NODES` frozenset
- Keep all existing functions (imported by sub-agent)

### Step 4: Update streaming

Edit `src/streaming.py`:
- Update `PIPELINE_SEQUENCE` (line ~70): replace `"generate_sql"`, `"validate_sql"`, `"execute_sql"` with `"sql_query_agent"`
- Add `sql_query_agent` to node-name → label mapping
- Handle new node in `_extract_pipeline_state()`
- Verify `queries_executed` tracking still works (it is incremented by `format_results_node`, which is unchanged)

### Step 5: Update existing tests

Edit `src/tests/test_graph_wiring.py`:
- Shorter SQL pipeline path assertions
- Remove `route_after_validation` / `route_after_execution` tests

### Step 6: Prompt engineering

Write `SQL_SUBAGENT_PROMPT`. The prompt is organized into six sections, each serving a distinct epistemic function. See [Section 3A](#3a-epistemic-map-what-each-node-knows-decides-and-passes-forward) for the full analysis of why each piece of knowledge lives where it does.

**Requires explicit approval before finalizing.** The sections below define what the prompt must contain and why; the exact wording is deferred to implementation.

#### Prompt Section 1: Role and Objective

Establishes the sub-agent's identity and hard constraints.

- "You are a SQL expert for the Atlas trade database."
- Your job: write and execute SQL to answer the user's question about international trade.
- "You MUST call `execute_sql` to run your SQL. Never answer without executing a query."
- "Your query results are returned to the parent system — your final text message is NOT shown to the user. Focus on getting the SQL right, not on prose summaries."

#### Prompt Section 2: Domain Knowledge (migrated from `SQL_GENERATION_PROMPT`)

This is the core knowledge from the current `SQL_GENERATION_PROMPT` and its conditional blocks, restructured for the agentic context. All content migrates; none is invented. Subsections:

- **Table selection guide:** When to use `country_year` (aggregates) vs `country_product_year_N` (product-level) vs `country_country_year` (bilateral aggregate) vs `country_country_product_year_N` (bilateral by product). Table suffixes `_1`, `_2`, `_4`, `_6` indicate product digit level.
- **Column naming conventions:** `export_value` not `export_value_usd`. Filter on `product_code` and `iso3_code`, NEVER on `product_id` or `country_id` (internal join-only IDs). Use raw column names (`distance`, `cog`, `export_rca`), not "normalized" variants.
- **Metric definitions and formulas:** Pre-calculated metrics (RCA, diversity, ubiquity, ECI, PCI, COI, COG, distance, proximity) — use directly, do not recompute. Calculable metrics: growth opportunities (RCA < 1, sort by COG DESC, filter distance < 10th percentile), market share (country product exports / global product exports × 100%), new products (RCA < 1 → RCA ≥ 1 year-over-year), CAGR (POWER formula, default 5-year window, do NOT use lookback tables).
- **Services vs goods:** Different schemas (`services_unilateral`, `services_bilateral` vs `hs92`, `hs12`, `sitc`). Combine via UNION ALL, never JOIN. Service table suffixes: `_1` is aggregate only (`product_code='services'`); `_2`, `_4`, `_6` all contain the same 5 categories — always use `_2` for disaggregated service queries.
- **Schema year coverage:** hs12 from 2012, hs92 from 1995, sitc from 1962, services from 1980. Use appropriate schema for time range requested. Default to latest available year ({sql_max_year}) when not specified.
- **LIMIT rules:** Apply `LIMIT {top_k}` for most queries. Do NOT apply LIMIT for enumeration queries ("list all", "which countries belong to", "how many", "members of").
- **Common mistakes to avoid:** Never filter on `product_id`/`country_id`; services combine via UNION ALL, never JOIN; "total exports" without qualification requires BOTH goods and services tables.
- **Group / regional aggregate patterns:** Always present (not conditional — see [Section 3A, conditional block migration](#conditional-block-migration-what-goes-where)). How to use `classification.location_group_member` to aggregate across member countries. Available groups with exact `group_name` and `group_type` values. Example SQL. "Aggregate first, compute second" rule for derived metrics over groups. Do NOT use `group_group_product_year` tables.

#### Prompt Section 3: Query Planning and CTE Strategy

Guides the sub-agent's approach to complex, multi-step questions. See [Section 5](#5-multi-step-query-chaining-strategy) for the full rationale.

- "For complex questions involving multiple dimensions, **plan before writing SQL:**"
  1. What sub-questions need answering?
  2. What tables and joins are needed for each?
  3. Can the sub-questions be expressed as CTEs in a single query?
- "Use Common Table Expressions (WITH clauses) for multi-step analytical queries. Each CTE can reference previous CTEs. This is **preferred over running multiple separate queries.**"
- "Reserve multiple `execute_sql` calls for genuine exploration, not for building up results incrementally. Valid reasons for multiple calls: first query returned an error; first query returned empty results and you're trying a different approach; you need to check what values exist in a column before filtering on it."

#### Prompt Section 4: Tool Usage Strategy

Establishes priority order and prevents wasteful exploration. This section has no equivalent in the current prompt — it's entirely new.

- "**Always write SQL and call `execute_sql` first.** This is your primary action. Don't explore the schema or re-extract products before you've tried running a query."
- "**On error:** Examine the error message alongside the DDL in your initial context. Most errors (wrong column name, wrong table name) are fixable by reading the DDL. Fix the SQL yourself and call `execute_sql` again."
- "**Use `explore_schema` only when the DDL in your context doesn't have what you need** — e.g., you realize you need a different schema's tables, or you want to see sample data values to understand the format of a column."
- "**Use `lookup_products` only when you suspect the initial product extraction was wrong** — e.g., empty results for a product that should have data, or you need services tables but only got goods tables. This tool is expensive (multiple LLM calls). Use it as a last resort."
- "Don't over-explore. Most queries succeed on the first or second `execute_sql` call."

#### Prompt Section 5: Error Recovery Patterns

Explicit patterns for the most common failure modes. This replaces the current `SQL_RETRY_BLOCK`'s generic "Fix the error" with actionable guidance:

- **Column not found** → "Check the DDL in your context for the correct column name. Common fix: `export_value` not `export_value_usd`. Use raw column names, not 'normalized' variants."
- **Table not found** → "Check the schema name and table suffix (`_1`, `_2`, `_4`, `_6`). The suffix must match the product digit level. Call `explore_schema` to list available tables in the schema if needed."
- **Empty results (0 rows)** → "Don't give up immediately. Consider: (1) Wrong product codes? Call `lookup_products` to re-extract. (2) Wrong table suffix? A 4-digit product code needs `_4` tables. (3) Wrong time period? Check schema year coverage. (4) Wrong classification schema? HS12 starts from 2012 — try HS92 for earlier years. (5) Genuinely no data? Call `explore_schema` to sample the table and confirm before concluding. If the data truly doesn't exist, report that clearly."
- **Validation error (syntax)** → "Fix the syntax. Check for unbalanced quotes, missing commas, reserved words used as identifiers."
- **Database execution error** → "Read the Postgres error message carefully. Common issues: ambiguous column reference (qualify with table alias), division by zero (use NULLIF), type mismatch (explicit CAST)."

#### Prompt Section 6: Stopping Criteria

Prevents over-iteration and defines the output contract.

- "When `execute_sql` returns rows that answer the question, **STOP.** Do not run additional queries to 'verify' or 'improve' the result."
- "If after multiple attempts you cannot get results, **STOP.** Report what you tried, what errors you encountered, and your best assessment of why the data isn't available."
- "Do NOT keep trying if the data genuinely doesn't exist. Sometimes the correct answer is 'this data is not available in the database.'"
- "Your job is to get the SQL right and return results. The parent agent handles interpreting and formatting the results for the user."

### Step 7: Run tests and eval

```bash
PYTHONPATH=$(pwd) pytest -m "not db and not integration and not eval" -v
PYTHONPATH=$(pwd) pytest -m "integration" -n 10 --dist loadscope -v
PYTHONPATH=$(pwd) pytest -m "eval" -v > eval_after_subagent_$(date +%Y%m%d).txt 2>&1
```

---

## 10. Testing Strategy

### Unit tests (mock LLM + mock DB)

Test the sub-agent's internal routing, tool nodes, and wrapper independently. Mock the frontier LLM to return predetermined tool_calls. Mock the DB to return predetermined results or errors. Verify state flows correctly through the subgraph.

### Integration tests (real LLM, mock or real DB)

Test that the sub-agent actually recovers from errors and handles complex queries:
- Column name wrong → agent sees DDL in context → corrects without extra tool calls
- Table doesn't exist → agent calls explore_schema → writes correct SQL
- Empty results → agent calls lookup_products → tries different schema
- Multi-step analytical question → agent uses CTEs rather than multiple separate queries
- Large result set → agent receives truncated output and can still reason correctly

### Eval (before/after)

Run the full eval suite before and after. Key metrics:
- **Accuracy**: expect improvement on queries that currently fail due to blind retry
- **Latency**: expect ~0-500ms increase on happy path (1 extra LLM "reasoning" step is now unified with SQL generation, so may be net neutral); larger increase on recovery paths (more iterations)
- **Token usage**: may decrease on happy path (1 LLM call instead of separate reasoning + generation); increases on recovery paths

---

## 11. Risks and Mitigations

### Risk 1: Large system prompt + initial context

The sub-agent's system prompt (~SQL_GENERATION_PROMPT size) + initial context (DDL + codes + few-shot examples) may be 8-12K tokens. This is comparable to what the current `create_query_generation_chain()` already sends (DDL + examples + prompt), so it should be fine. Monitor context size in eval.

### Risk 2: Agent doesn't call execute_sql

The agent might reason about the SQL without actually running it, producing a "theoretical" answer.

**Mitigation:** System prompt explicitly says "You MUST call execute_sql to test your SQL. Never answer without executing." The initial context message ends with "call execute_sql to run it."

### Risk 3: Agent over-explores (wastes iterations)

Agent calls explore_schema and lookup_products repeatedly without writing SQL.

**Mitigation:** System prompt: "Always write SQL and call execute_sql first. Only use other tools after a failure." MAX_ITERATIONS = 5 caps total attempts.

### Risk 4: Streaming UX regression

Frontend shows 1 step instead of 3 during query crafting.

**Mitigation:** Phase 1 coarse-grained events are acceptable. Phase 1b adds callback-based granular events.

### Risk 5: State mapping bugs

`format_results` reads stale or missing `pipeline_*` fields.

**Mitigation:** Wrapper node explicitly maps every field. Unit tests verify the mapping.

### Risk 6: Prompt engineering iteration needed

First version of `SQL_SUBAGENT_PROMPT` may not produce optimal behavior.

**Mitigation:** Expected. The eval suite is the feedback mechanism. Start simple, iterate based on observed failures.

### Risk 7: Agent writes multiple queries instead of CTEs

The agent may default to running 3-4 separate queries and trying to reason about their combined results in-context, rather than composing a single CTE-based query. This wastes iterations, bloats the context window, and produces worse results (the agent can't literally JOIN in-memory data).

**Mitigation:** Prompt explicitly guides toward CTEs for multi-step logic (see [Section 5](#5-multi-step-query-chaining-strategy)). Include CTE examples in the few-shot examples loaded into initial context. Monitor in eval — if the agent frequently runs >2 queries for single questions, strengthen the CTE guidance.

### Risk 8: Large result sets bloat context

If `execute_sql` returns 500+ rows, subsequent LLM calls become slow and expensive.

**Mitigation:** Result truncation (see [Section 5](#5-multi-step-query-chaining-strategy)): show first 20 rows + summary for results exceeding 50 rows. The agent has enough to validate correctness without consuming excessive tokens.

---

## 12. Decisions Log: Alternatives Considered and Rejected

This section documents significant design alternatives that were evaluated during planning. Each entry explains what was considered, why it was rejected, and under what future conditions it might be reconsidered.

### Decision 1: Query chaining via CTEs + state (chosen) vs. temporary tables (rejected) vs. Python sandbox (rejected)

**Context:** The agent needs to handle complex questions that require combining results from multiple logical steps (e.g., "top exporters of X that also have high ECI").

**Option A: CTEs + state-based reasoning (CHOSEN)**
The agent writes single SQL statements with `WITH` clauses for multi-step logic. For genuine exploration (checking available values before writing the final query), it runs separate queries and reasons about prior results in its conversation history.

- Zero infrastructure change — works with existing per-query connection model
- Matches what top text-to-SQL systems do (SQL-of-Thought, AGENTIQL, CoQ, ReFoRCE all compose single CTE-based queries)
- No security surface expansion (database stays read-only)
- Limitation: agent can't iteratively build up and JOIN large intermediate result sets across separate queries

**Option B: Temporary tables via persistent DB session (REJECTED)**
Hold a single PostgreSQL connection open for the sub-agent's lifetime. The agent creates `CREATE TEMP TABLE ... AS SELECT ...`, then queries that temp table in later calls. Temp tables are session-scoped in PostgreSQL — they vanish when the connection closes.

- Would require changing connection lifecycle: currently each `execute_sql` gets a fresh connection from the pool (`async with async_engine.connect()`)
- Conflicts with `postgresql_readonly` execution option (temp tables require write privileges)
- Holding connections longer impacts pool sizing (~20 concurrent users × held connections = pool exhaustion risk)
- Statement timeout management becomes more complex with long-lived connections
- Security surface expansion: agent can now write to the database, even if only temp tables
- **Marginal benefit:** CTEs handle 95%+ of multi-step cases without temp tables. The remaining cases (very large intermediate results) are rare for our analytical query patterns.

*Reconsider if:* Users routinely ask questions requiring intermediate results with 1000+ rows that must be JOINed — CTEs would work but temp tables would be more efficient. Would require a dedicated connection pool with write access and careful session lifecycle management.

**Option C: Python code execution sandbox (REJECTED)**
Give the agent a sandboxed Python environment where it can run SQL via a function call, manipulate results with pandas/numpy, and chain arbitrary logic.

- The literature shows code-as-action outperforms tool-calling by ~20% success rate (smolagents CodeAgent benchmarks) and uses 30% fewer steps
- Vercel's approach (bash + SQL in sandbox) achieved 100% success rate vs 80% with tools, 3.5x faster
- Cloudflare saw 98.7% token reduction with code-based invocation

However, rejected for Phase 1 because:
- **Our complexity is in getting SQL right, not in post-processing.** The Atlas database has unusual column names, table suffixes, and schema conventions — that's where queries fail. Python post-processing doesn't help with this.
- **Significant infrastructure burden:** Requires Docker/E2B/Modal sandbox, security hardening, deployment pipeline changes
- **Overkill for current use case:** Users ask analytical questions about trade data. The answers are SQL query results, not computed transformations.
- **Adds latency:** Sandbox startup, code execution, result serialization — all add time to the critical path

*Reconsider if:* Users start asking for complex transformations (pivot tables, statistical analysis, visualizations, growth rate calculations across multiple result sets) that are awkward to express in pure SQL. At that point, a Python sandbox becomes the right tool. This would be a Phase 2 or Phase 3 addition.

### Decision 2: Data sampling folded into `explore_schema` (chosen) vs. separate `sample_table` tool (rejected)

**Context:** The agent sometimes needs to see actual data values to write correct SQL (e.g., is the country code `"USA"`, `"us"`, or `840`?). This is similar to ReFoRCE's "column exploration" capability.

**Chosen:** Add sample-rows support to `explore_schema` (e.g., `explore_schema("Show 5 sample rows from hs92.country_product_year_4")`). This keeps tool count at 3.

**Rejected:** A separate `sample_table(table_name, limit=5)` tool. Would increase tool count to 4. LLM tool-selection accuracy degrades with more tools, and the cognitive distinction between "explore schema" and "sample data" is blurry — both answer "what does this table look like?"

### Decision 3: Result truncation at 50 rows (chosen) vs. full results (rejected) vs. summary-only (rejected)

**Context:** When `execute_sql` returns results, those results enter the agent's conversation history. Large results bloat the context window and slow subsequent LLM calls.

**Chosen: Truncate at 50 rows** — show first 20 rows + row count summary for larger results. Balances agent's ability to validate correctness against context window cost.

**Rejected: Full results** — a 500-row result would consume ~10K+ tokens in the conversation. With up to 5 iterations, that's 50K tokens of result data alone. Unacceptable.

**Rejected: Summary-only** (just row count + column names, no data) — the agent needs to see actual values to catch semantic errors (e.g., "these are import values, not export values" or "these country codes look wrong").

### Decision 4: Query planning in the prompt (chosen) vs. separate planning tool/node (rejected)

**Context:** Research (SQL-of-Thought, AGENTIQL, MAC-SQL) shows that planning before writing SQL significantly improves accuracy. The question is whether planning should be a separate tool, a separate LangGraph node, or just prompt guidance.

**Chosen: Prompt guidance** — the system prompt instructs the agent to plan before writing complex queries. No separate tool or node. The plan appears in the agent's reasoning (visible in the message trace) but doesn't require an extra LLM call or tool dispatch.

**Rejected: Separate planning tool** — would add a 4th tool, increase round-trips, and fragment the agent's reasoning. The agent already has all the information it needs (DDL, codes, domain rules) to plan internally.

**Rejected: Separate planning node in the subgraph** — would force every query through a planning step, adding latency to simple queries that don't need it. The agent should plan when the question is complex, not always.

### References

Key papers and articles that informed these decisions:

- [SQL-of-Thought: Multi-agentic Text-to-SQL with Guided Error Correction](https://arxiv.org/html/2509.00581v2) — staged reasoning with query plans before SQL generation
- [Chain-of-Query: Multi-Agent SQL Collaboration](https://arxiv.org/abs/2508.15809) — clause-by-clause CTE-based query construction
- [AGENTIQL: Multi-Expert Text-to-SQL](https://arxiv.org/abs/2510.10661) — planner + executor merging strategy
- [ReFoRCE: Text-to-SQL with Self-Refinement and Column Exploration](https://arxiv.org/abs/2502.00675) — iterative column exploration, consensus enforcement
- [Agentic Text-to-SQL Systems (Emergent Mind survey)](https://www.emergentmind.com/topics/agentic-text-to-sql-systems) — taxonomy of agent architectures
- [The Arc of Agent Action: Code vs Tools (Victor Dibia)](https://newsletter.victordibia.com/p/the-arc-of-agent-action-from-code) — code-as-action vs tool-calling tradeoffs, sandbox commoditization
- [Smolagents Text-to-SQL with Error Correction (HuggingFace)](https://huggingface.co/learn/cookbook/agent_text_to_sql) — CodeAgent approach to SQL
- [LangGraph SQL Agent Tutorial](https://docs.langchain.com/oss/python/langgraph/sql-agent) — LangGraph-native patterns
- [Spatio-Temporal Agentic Text-to-SQL](https://arxiv.org/abs/2510.25997) — ReAct loop for SQL + visualization
- [MAC-SQL: Multi-Agent Collaborative Framework](https://aclanthology.org/2025.coling-main.36.pdf) — decomposer + selector + refiner pattern
