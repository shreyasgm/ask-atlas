# Ask-Atlas Comprehensive Evaluation Strategy

> Consolidates and extends: `docs/backend_redesign_analysis.md` Section 13, GitHub issues [#51](https://github.com/growth-lab/ask-atlas/issues/51), [#89](https://github.com/growth-lab/ask-atlas/issues/89), [#90](https://github.com/growth-lab/ask-atlas/issues/90).
>
> **Scope:** Strategy document only — no code changes. Written at implementation-ready detail so a coding agent can pick up each phase without further design work.

---

## Table of Contents

1. [Current State Assessment](#1-current-state-assessment)
2. [Five-Tier Evaluation Pyramid](#2-five-tier-evaluation-pyramid)
3. [Ground Truth Source Hierarchy](#3-ground-truth-source-hierarchy)
4. [Browser Automation Strategy](#4-browser-automation-strategy)
5. [Observability and Production Monitoring](#5-observability-and-production-monitoring)
6. [Dataset Collection Plan](#6-dataset-collection-plan)
7. [Eval Run Costs and Cadence](#7-eval-run-costs-and-cadence)
8. [Metrics and Dashboards](#8-metrics-and-dashboards)
9. [Phased Implementation Roadmap](#9-phased-implementation-roadmap)
10. [File Inventory](#10-file-inventory)
11. [Risk Mitigation](#11-risk-mitigation)
12. [Modern Eval Research to Incorporate](#12-modern-eval-research-to-incorporate)

---

## 1. Current State Assessment

### 1.1 Existing Infrastructure

**Question corpus:**

- `evaluation/eval_questions.json` — 246 questions across 28 categories
- Sources: 60 original SQL questions, 109 country-page questions, 77 explore-page questions
- Ground truth in `evaluation/results/{qid}/ground_truth/results.json` — 190 questions with verified data, 56 original SQL questions without

**Evaluation pipeline:**

| File | Role |
|------|------|
| `evaluation/run_eval.py` | Top-level orchestrator: agent run -> judge -> report -> history |
| `evaluation/run_agent_evals.py` | Runs agent on questions with concurrency control; extracts `pipeline_sql` from `AtlasAgentState` |
| `evaluation/judge.py` | 3 judge modes: `ground_truth` (4-dim rubric), `refusal`, `plausibility` |
| `evaluation/report.py` | Aggregates scores by category/difficulty; generates JSON + Markdown reports |
| `evaluation/compare_runs.py` | Diffs two eval runs for regression detection |
| `evaluation/execution_accuracy.py` | SQL execution accuracy comparison |

**Judge dimensions (current):**

| Dimension | Weight |
|-----------|--------|
| `factual_correctness` | 0.35 |
| `data_accuracy` | 0.30 |
| `completeness` | 0.20 |
| `reasoning_quality` | 0.15 |

Verdicts: pass (>=3.5), partial (>=2.5), fail (<2.5) on a 1-5 scale.

**Ground truth collection scripts:**

| Script | API Endpoint | Questions Generated |
|--------|-------------|-------------------|
| `evaluation/collect_country_page_data.py` | `https://atlas.hks.harvard.edu/api/countries/graphql` | IDs 61-169 (8 countries, 11 categories) |
| `evaluation/collect_explore_page_data.py` | `https://atlas.hks.harvard.edu/api/graphql` | IDs 170-246 (product metadata, bilateral trade, time series, regional) |

**Smoke test:** `src/tests/test_eval_integration.py` — 10 questions with `@pytest.mark.eval` marker, real LLM + DB + LLM-as-judge.

**E2E observability:** `scripts/e2e_observability_test.py` — 5 test cases with real-time node tracing, tool call tracking, colored output. Not integrated into the eval pipeline.

**Run history:** `evaluation/runs/history.jsonl` — 21 runs logged. Most recent baseline: avg_score 2.4/5.0, 20% pass rate (5-question smoke, 2026-02-26).

**Reference documentation:**

| Document | Content |
|----------|---------|
| `evaluation/atlas_country_pages_exploration.md` | 12 subpages, 73 data points, DOM structure, two GraphQL APIs |
| `evaluation/atlas_explore_pages_exploration.md` | 7 visualization types, 62 data points, 27 GraphQL queries |
| `evaluation/country_page_collection_guide.md` | Step-by-step browser collection for country pages (8 countries, 10 categories) |
| `evaluation/explore_page_collection_guide.md` | Step-by-step browser collection for explore pages |
| `evaluation/graphql_api_official_docs.md` | Official Explore API documentation |

### 1.2 Planned but Not Built

From GitHub issues #51, #89, #90 and `docs/backend_redesign_analysis.md` Section 13:

**Issue #89 — Evaluation Dataset Collection:**
- Tool sequence annotations (`expected_tool`) for all 246 questions
- Classification eval set (~60 questions)
- Entity extraction eval set (~30 questions)
- ID resolution eval set (~40 questions)

**Issue #90 — Judge Extensions & Trajectory Testing:**
- `TrajectoryVerdict` judge mode (deterministic tool sequence comparison)
- `data_source_appropriateness` scoring dimension (weight 0.15)
- Tool call sequence capture in `run_agent_evals.py`
- Rebalanced dimension weights

**Issue #51 — Data Collection Strategy Overhaul:**
- Invert source priority: Explore API as primary, Country Pages for unique narrative only
- Browser-verified ground truth collection (highest trust tier)
- Ground truth source hierarchy enforcement with `source_method` metadata
- `refresh_ground_truth.py` with dry-run + changelog
- Remediate 56 original SQL questions missing ground truth

### 1.3 Missing Entirely

These capabilities are not described in any existing plan and are defined for the first time in this document:

- **Browser automation workflow** using `claude-in-chrome` MCP for ground truth collection (Section 4)
- **Production observability** — structured request traces, metrics endpoint (Section 5)
- **Continuous evaluation loop** — production failures feeding back into eval datasets (Section 5.3)
- **Cost model** for eval runs with mitigation strategies (Section 7)
- **Data staleness management** — refresh pipeline, changelog, review cadence (Section 4.4)
- **Trend reporting** — historical progress tracking from `runs/history.jsonl` (Section 8)

---

## 2. Five-Tier Evaluation Pyramid

The evaluation system is organized into five tiers, ordered by speed and cost. Lower tiers run more frequently and catch issues earlier; higher tiers provide deeper signal at higher cost.

| Tier | Time | LLM Cost | Signal | Frequency |
|------|------|----------|--------|-----------|
| 1: Unit tests | <30s | $0 | Structural correctness | Every commit |
| 2: Component eval | ~2-3 min | ~$0.50-1.00 | Per-node accuracy (classification, extraction, ID resolution) | Every PR |
| 3: Trajectory eval | ~1 min | $0 (deterministic) | Tool routing correctness | Every PR |
| 4: End-to-end eval | ~5 min (smoke) / ~30-60 min (full) | ~$0.66 (smoke) / ~$32-38 (full) | Answer quality + judge scoring | Weekly + pre-release |
| 5: Production monitoring | Continuous | Marginal | Real-world failure detection | Always-on |

### Tier 1: Unit Tests (no LLM, no DB)

Mocked LLM + mocked HTTP. Run with `PYTHONPATH=$(pwd) pytest -m "not db and not integration and not eval"`. Target: <30 seconds.

**What to test:**

| Component | Key Assertions |
|-----------|---------------|
| GraphQL classification parsing | Correct `GraphQLQueryClassification` model for all query_types; rejection works; reasoning field populated |
| GraphQL entity extraction parsing | Correct `GraphQLEntityExtraction` model; ISO alpha-3 country codes; HS/SITC product codes (not internal IDs); `product_class` constrained to `Literal` values |
| ID resolution logic | Correct final IDs; handles missing entities; adapts ID format per API target (Explore vs Country Pages) |
| Atlas link generation | Correct URLs for all 7 Explore visualization types + 12 Country Page subpages; frontier fallback; resolution_notes propagation |
| Budget tracker | Window expiry; consume-on-success semantics; `is_available()` at limit; thread-safety |
| Circuit breaker | CLOSED->OPEN after 5 failures; OPEN->HALF-OPEN after 30s; HALF-OPEN->CLOSED on success |
| Route functions | Correct next-node selection for all state combinations |

**Evaluation dimension:** Structural correctness. These tests verify that the system's internal logic is wired correctly, independent of LLM quality.

### Tier 2: Component Evaluation (real LLM, no LLM-as-judge)

Real LLM calls against curated test sets with known correct answers. No judge needed — compare predicted vs. expected values directly.

**Classification accuracy:**
- Input: ~60 questions from `evaluation/classification_eval.json`
- Run each through `classify_query` with real LLM
- Metrics: accuracy (% correct `query_type`), rejection precision, rejection recall
- Thresholds: >90% accuracy, >85% rejection precision, >80% rejection recall

**Entity extraction accuracy:**
- Input: ~30 questions from `evaluation/entity_extraction_eval.json`
- Run each through `extract_entities` given known `query_type`
- Metrics: exact match on country, product, year fields
- Thresholds: >90% country accuracy, >85% product accuracy

**ID resolution accuracy:**
- Input: ~40 questions from `evaluation/id_resolution_eval.json`
- Run each through `resolve_ids` with real LLM + real catalogs
- Metrics: exact match on internal country ID, exact match on internal product ID
- Thresholds: >95% country accuracy, >90% product accuracy

**Evaluation dimension:** Per-node accuracy. Tests individual pipeline nodes in isolation, catching regressions in classification, extraction, or resolution before they compound into end-to-end failures.

### Tier 3: Trajectory Evaluation (deterministic)

Verify the agent uses the **right tool**, not just gets the right answer. No LLM cost — purely programmatic comparison.

**Mechanism:**
1. Each question in `eval_questions.json` has an `expected_tool` annotation: `sql_only`, `graphql_only`, `graphql_preferred`, `either`, or `refusal`
2. After running the agent, extract the tool call sequence from `AtlasAgentState` message history
3. Compare actual tool sequence against `expected_tool`
4. Verdict: `pass` (correct tool), `fail` (wrong tool), `acceptable` (`either` annotation and any valid tool used)

**Key metric:** Tool selection accuracy — did the agent call the right tool for the question type?

**Additional trajectory tests:**
- Budget exhaustion: verify auto mode degrades to SQL-only when budget <= 5
- Circuit breaker trip: verify fast-fail to SQL-only after 5 consecutive GraphQL failures
- `sql_only` mode: verify system behavior is identical to pre-GraphQL production

**Evaluation dimension:** Tool routing correctness. A correct answer from the wrong tool is a latent bug — it means the system is wasting API budget or missing richer data sources.

### Tier 4: End-to-End Evaluation (existing system, extended)

The existing 246-question eval (`evaluation/run_eval.py`), extended with new categories, dimensions, and ground truth.

**Current pipeline:** `run_eval.py` -> `run_agent_evals.py` (agent) -> `judge.py` (LLM judge) -> `report.py` (aggregation)

**Extensions:**
- New GraphQL-appropriate question categories (see Section 6, Phase 3)
- New `data_source_appropriateness` judge dimension (weight 0.15)
- Rebalanced dimension weights (see Section 6, Phase 2)
- Tool call sequence captured alongside answer text
- Atlas link verification as part of ground truth comparison

**Smoke test (5 questions, ~$0.66):** Question IDs [1, 6, 25, 97, 195] — curated to span categories and difficulties. Run after any backend prompt or pipeline change.

**Full eval (246+ questions, ~$32-38):** Weekly during active development, or before any release.

**Evaluation dimension:** Answer quality. The LLM judge scores the agent's final answer against verified ground truth across multiple quality axes.

### Tier 5: Production Monitoring (continuous)

Real-world request tracing and failure detection. See Section 5 for full design.

**Key signals:**
- Request latency (p50, p95, p99)
- Error rate by pipeline (SQL, GraphQL, Docs)
- Tool selection distribution
- GraphQL budget utilization
- Circuit breaker trips

**Evaluation dimension:** Operational reliability. Catches failure modes that curated eval sets miss — novel question patterns, data staleness, infrastructure issues.

---

## 3. Ground Truth Source Hierarchy

Ground truth must be independent of the system being evaluated. LLMs are the system under test — using LLM-generated answers as ground truth is circular. The browser (the rendered Atlas website) is the authoritative source of truth.

| Source | Trust Tier | Use For | `source_method` Value |
|--------|-----------|---------|----------------------|
| Browser-verified data (navigating Atlas website) | **Highest** | All pipeline ground truth | `browser_country_page`, `browser_explore_page` |
| GraphQL API responses (direct API queries) | **Medium** — adds variety but lower confidence (API queries formed by LLM may be incorrect) | SQL pipeline cross-validation; convenience cross-check for browser-collected data | `graphql_api` |
| SQL pipeline output (forced SQL route) | **Consistency check only** — not validation | Cross-pipeline consistency measurement | `sql_cross_check` |
| LLM-generated expectations | **Structural only** — not answer quality | Tool routing labels, classification labels, trajectory expectations | `llm_generated` |

### Key Rules

1. **GraphQL API responses cannot serve as ground truth for the GraphQL pipeline.** The tool would be validating itself — the same API call the pipeline makes cannot also be the expected answer. For GraphQL pipeline evaluation, ground truth comes from the browser.

2. **SQL pipeline output cannot validate GraphQL pipeline correctness** — only measure consistency. The GraphQL pipeline is expected to be higher quality for its target question types. SQL cross-checks measure divergence between pipelines, not correctness of either.

3. **Every question-answer pair must record its `source_method`** in metadata so evaluations can be filtered by ground truth quality tier. This is enforced in the `results.json` schema:

```json
{
  "question_id": "247",
  "execution_timestamp": "2026-03-15T10:30:00Z",
  "source": "graphql_eval",
  "source_method": "browser_explore_page",
  "atlas_url": "https://atlas.hks.harvard.edu/explore/treemap?...",
  "expected_tool": "graphql_only",
  "expected_query_type": "treemap_products",
  "results": {
    "data": [{"product": "Travel & tourism", "value": 3496641908.95, "share": "21.6%"}]
  }
}
```

4. **Source priority for new ground truth collection** (from #51): Explore API as primary source for batch coverage; browser verification for highest-trust data; Country Pages API only for data points unique to that API (narrative text, growth projections, derived metrics not in Explore API).

---

## 4. Browser Automation Strategy

This section is new — not covered in the existing plans. It defines the workflow for collecting and refreshing ground truth by navigating the Atlas website in a browser.

### 4.1 Data Point Classification by Extraction Method

The Atlas website renders data through three distinct mechanisms. The extraction method determines how ground truth is collected for each data point.

| Extraction Method | Description | Examples | Collection Approach |
|-------------------|-------------|----------|-------------------|
| **DOM-extractable** | Standard HTML elements readable via `document.querySelector` | Country profile stat cards (GDP, population, rankings), feasibility table (`<table>` element), list items, text blocks | Navigate to page, wait for JS rendering, extract text from DOM elements |
| **Canvas/visualization-based** | Rendered in `<canvas>` elements; values only visible in tooltips on hover | Treemaps (export composition), scatter plots (feasibility graph), product space network, time-series charts | **Preferred:** Query underlying GraphQL API for raw data. **Fallback:** Mouse interaction + screenshot for visible values |
| **Narrative/derived text** | Generated narrative text from Atlas backend, not available via API | Growth dynamics descriptions, strategic approach text, summary page narratives | Must be read from rendered page — no API alternative |

**Page-by-page extraction classification:**

| Atlas Page / Component | Extraction Method | DOM Selector / API Query | Notes |
|----------------------|-------------------|-------------------------|-------|
| Country profile stat cards | DOM | `.stat-card`, `.profile-header` region | GDP, population, income classification, ECI rank, COI rank |
| Country profile text blocks | Narrative | Rendered `<p>` / `<div>` text | Growth projection narrative, economic overview |
| Export treemap (country page) | Canvas + API | `treemapProducts` query on Country Pages API | Values only in tooltips; use API data instead |
| Export treemap (explore page) | Canvas + API | `treemapExporterProducts` / `treemapExporterPartners` on Explore API | Same — prefer API, screenshot as fallback |
| Growth dynamics chart | Canvas + API | Country Pages API `countryProfile` fields | Chart is canvas-rendered; API provides underlying data |
| Feasibility scatter plot | Canvas + API | Explore API `productFeasibilityExporter` | Hover tooltips on canvas; API returns full dataset |
| Feasibility table | DOM | `<table>` element on feasibility page | **Only DOM-accessible HTML table** on country pages |
| Product space network | Canvas + API | Explore API `productProduct` (relatedness edges) | Network layout is canvas; API provides edge data |
| Over-time line chart | Canvas + API | Explore API `treemapExporterProducts` with year range | Time series values in API; chart in canvas |
| Market share stacked area | Canvas + API | Explore API `marketshareExporterProducts` | Same pattern — API for data, canvas for viz |
| New products list | DOM + API | Country Pages API `newProducts` | Some data in DOM list items; full data in API |
| Diversification grade cards | DOM | Country page diversification subpage | Grade, percentile, peer comparison |
| Strategic approach quadrant | Narrative | Country page product-space subpage | Text description of strategic approach |

### 4.2 Browser Collection Workflow

**Orchestrator script:** `evaluation/browser_collect_ground_truth.py`

**Input:** A manifest file listing question IDs, expected Atlas URLs, and extraction methods:

```json
[
  {
    "question_id": 61,
    "atlas_url": "https://atlas.hks.harvard.edu/countries/404/growth-dynamics",
    "extraction_method": "dom",
    "extractor": "country_profile",
    "data_points": ["gdp_per_capita", "population", "income_classification"]
  },
  {
    "question_id": 170,
    "atlas_url": "https://atlas.hks.harvard.edu/explore/treemap?...",
    "extraction_method": "api_preferred",
    "extractor": "treemap_verify",
    "api_query": "treemapExporterProducts",
    "data_points": ["top_products", "export_values"]
  }
]
```

**Workflow per question:**

1. **Navigate** to the Atlas URL. Atlas is a SPA — wait for JavaScript rendering to complete.
   - Wait indicator: the page's loading spinners disappear; key DOM elements are present.
   - Typical wait: 3-5 seconds for initial render, up to 10 seconds for visualization-heavy pages.
   - The existing collection guides (`country_page_collection_guide.md`, `explore_page_collection_guide.md`) document specific wait times and element locations per page type.

2. **Extract data** using the appropriate method:
   - **DOM extraction:** Query specific CSS selectors, extract text content, parse numbers.
   - **API extraction:** Call the underlying GraphQL API with known parameters, receive structured JSON.
   - **Screenshot extraction (fallback):** For canvas-based visualizations where API data is insufficient, take a screenshot and log it for manual verification.

3. **Write result** to `evaluation/results/{qid}/ground_truth/results.json` with full metadata including `source_method`, `execution_timestamp`, and `atlas_url`.

4. **Validate** — compare extracted data against any existing ground truth for the same question; log discrepancies.

**Per-page-type extraction modules:**

| Module | Page Type | Method |
|--------|-----------|--------|
| `evaluation/browser_extractors/country_profile.py` | Country profile stat cards + text | DOM selectors for stat values; text extraction for narratives |
| `evaluation/browser_extractors/feasibility_table.py` | Feasibility HTML table | DOM `<table>` parsing — rows, columns, cell values |
| `evaluation/browser_extractors/treemap_verify.py` | Treemap visualizations | API query + optional screenshot comparison |

Each extractor encapsulates page-specific DOM selectors and extraction logic. The orchestrator dispatches to the appropriate extractor based on the manifest's `extractor` field.

**Implementation notes for the coding agent:**
- The `claude-in-chrome` MCP tools provide `navigate`, `javascript_tool` (for DOM extraction), `get_page_text`, and `get_screenshot` capabilities.
- The existing collection guide documents are the specification — they describe URL structures, element locations, interactive elements, and wait conditions in detail.
- Rate limit Atlas requests to <=120 req/min; include User-Agent header.
- Atlas uses M49 country codes prefixed with `location-` in Country Pages API and plain numeric IDs in Explore API.

### 4.3 Ground Truth Spot-Check Workflow

**Script:** `evaluation/spot_check_graphql.py`

**Purpose:** For a configurable subset of questions, run the question through the Ask-Atlas GraphQL pipeline to get the agent's answer, then navigate to the expected Atlas URL and extract the actual data from the page, and compare the two.

**Workflow:**

1. Select a random or configured subset of questions (default: 10% of questions with ground truth).
2. For each question:
   a. Run the question through the agent (GraphQL mode) to get the agent's answer.
   b. Navigate to the `atlas_url` from `eval_questions.json` and extract data from the page.
   c. Compare agent answer vs. browser data — check key values, rankings, percentages.
   d. Record match/mismatch with discrepancy details.
3. Output: `evaluation/spot_check_report.json`

```json
{
  "timestamp": "2026-03-15T10:30:00Z",
  "questions_checked": 19,
  "matches": 15,
  "mismatches": 4,
  "details": [
    {
      "question_id": 65,
      "question_text": "What is Kenya's GDP per capita?",
      "agent_answer": "$2,274",
      "browser_data": "$2,350",
      "match": false,
      "discrepancy": "Value differs — likely data update since ground truth was collected"
    }
  ]
}
```

**Use cases:**
- Validate that ground truth is still current (detect staleness)
- Validate that the GraphQL pipeline returns data consistent with the browser
- Identify questions where the agent systematically diverges from displayed values

### 4.4 Ground Truth Refresh Workflow

**Script:** `evaluation/refresh_ground_truth.py` (maps to #51)

**Purpose:** Re-collect ground truth data and detect changes from data updates.

**Workflow:**

1. **API refresh (batch):** Re-run the existing collection scripts (`collect_country_page_data.py`, `collect_explore_page_data.py`) against the live Atlas GraphQL APIs. Diff new results against stored `results.json` files.

2. **Dry-run mode (default):** Show what would change without overwriting:
   ```
   Q61: GDP per capita changed from $2,274 to $2,350
   Q75: ECI rank changed from 93 to 91
   Q170: Coffee PCI changed from -0.82 to -0.79
   ```

3. **Commit mode (`--commit`):** Overwrite `results.json` files with new data, writing the old values to `evaluation/ground_truth_changelog.jsonl`:
   ```json
   {"timestamp": "2026-06-15T10:00:00Z", "question_id": 61, "field": "gdp_per_capita", "old": 2274, "new": 2350, "source": "api_refresh"}
   ```

4. **Browser-only questions:** Flag questions whose ground truth can only be refreshed via browser (narrative text, derived metrics not in API) for manual re-collection. Output a list of flagged question IDs.

**Review cadence:**

| Frequency | Activity |
|-----------|----------|
| Quarterly | Run `refresh_ground_truth.py --dry-run`, review changelog, commit if appropriate |
| On known data update | Same — Atlas trade data updates annually; derived metrics update periodically |
| Monthly | Run spot-check workflow (Section 4.3) on 10% sample |
| Weekly | Review production failures (Section 5.3), flag questions with stale data |

---

## 5. Observability and Production Monitoring

### 5.1 Custom Trace Collector

**File:** `evaluation/trace_collector.py`

**Purpose:** Extract structured execution traces from `AtlasAgentState` after each agent run.

**`ExecutionTrace` dataclass:**

```python
@dataclass
class ExecutionTrace:
    question_id: str
    tool_calls: list[str]               # Ordered list of tool names called
    node_sequence: list[str]            # LangGraph node execution order
    node_traces: list[NodeTrace]        # Per-node timing and details
    graphql_classification: dict | None  # GraphQLQueryClassification output
    graphql_entities: dict | None        # GraphQLEntityExtraction output
    graphql_resolved_params: dict | None # Resolved internal IDs
    atlas_links: list[dict]             # Generated Atlas links
    pipeline_sql: str | None            # SQL query if SQL pipeline used
    total_duration_ms: int
    error: str | None

@dataclass
class NodeTrace:
    node_name: str
    start_time: float
    end_time: float
    duration_ms: int
```

**Extraction:** The data is already in `AtlasAgentState` — the trace collector reads it from `graph.aget_state(config)` after the agent completes. Specifically:
- `tool_calls` — extracted from `messages` (scan for `ToolMessage` entries)
- `graphql_classification` — from `state["graphql_classification"]`
- `graphql_entities` — from `state["graphql_entity_extraction"]`
- `graphql_resolved_params` — from `state["graphql_resolved_params"]`
- `atlas_links` — from `state["graphql_atlas_links"]`
- `pipeline_sql` — from `state["pipeline_sql"]`
- `node_sequence` — from checkpoint metadata (LangGraph records node transitions)

**Integration with `run_agent_evals.py`:** After each question, extract `ExecutionTrace` and save alongside the existing `result.json`. This feeds both the `TrajectoryVerdict` judge (Tier 3) and the `data_source_appropriateness` dimension (Tier 4).

**Note on LangSmith:** [LangSmith](https://smith.langchain.com/) is the hosted evaluation/tracing platform for LangGraph. It provides auto-traces, intermediate step inspection, annotation queues, and dataset management. However, we are deliberately not adopting it for two reasons: (1) it is expensive at our expected usage volume, and (2) hitting free-tier limits causes errors that break the entire agent pipeline — an unacceptable failure mode. Our custom trace collector provides the signals we need (tool call sequences, node timing, classification/extraction outputs) without external dependencies or cost risk.

### 5.2 Production Request Traces

**New table:** `request_traces` in the App DB (`CHECKPOINT_DB_URL`)

```sql
CREATE TABLE request_traces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id TEXT NOT NULL,
    question TEXT NOT NULL,
    tool_calls JSONB,           -- ["query_tool", "atlas_graphql"]
    node_sequence JSONB,        -- ["classify", "extract", "resolve_ids", ...]
    pipeline_used TEXT,         -- "sql", "graphql", "docs", "mixed"
    latency_ms INTEGER,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_request_traces_created_at ON request_traces (created_at);
```

**Structured logging in `src/api.py`:**
- Log every request with: question text, tool_calls, node_sequence, latency_ms, error, thread_id
- Use Python `logging` module with JSON formatter for structured log aggregation
- No PII in logs — question text is acceptable (it's user-generated trade queries, not personal data)

**`/api/metrics` endpoint:**
- Returns aggregated metrics for monitoring integration:
  - Request count (last 1h, 24h, 7d)
  - Latency percentiles (p50, p95, p99)
  - Error rate
  - Pipeline distribution (% SQL, % GraphQL, % Docs)
  - GraphQL budget utilization
- Lightweight — queries `request_traces` with time-windowed aggregation

### 5.3 Production Failure Feedback Loop

A closed loop that turns production failures into evaluation improvements:

**Weekly review process:**

1. Query `request_traces` for errors and slow responses (latency > p95):
   ```sql
   SELECT question, error, tool_calls, latency_ms
   FROM request_traces
   WHERE error IS NOT NULL OR latency_ms > (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FROM request_traces WHERE created_at > NOW() - INTERVAL '7 days')
   ORDER BY created_at DESC;
   ```

2. Categorize each failure:
   - **`eval_candidate`** — Novel question type or failure mode not covered by existing eval set. Add to `eval_questions.json`, collect ground truth, verify failure reproduces.
   - **`known_limitation`** — Question type the system is known not to handle (out of scope, unsupported data). Document but don't add to eval set.
   - **`infrastructure`** — Timeout, API error, DB connection issue. Fix operationally, not via eval.

3. For `eval_candidate` failures:
   - Add question to `eval_questions.json` with appropriate category and difficulty
   - Collect browser ground truth using the workflow from Section 4.2
   - Run the question through the eval pipeline to verify the failure reproduces
   - Track as a regression target in future eval runs

**Cadence integration (see Section 4.4):** The weekly failure review feeds into the monthly full-eval cycle and the quarterly ground truth refresh.

---

## 6. Dataset Collection Plan

Maps directly to GitHub issues #89, #90, #51. Each phase produces specific artifacts with defined formats.

### Phase 0 — Tool Annotations (immediate, #89)

**Script:** `evaluation/annotate_tool_expectations.py`

**Output:** `evaluation/tool_annotations.json` — tool routing annotation for all 246 questions.

**Three-step workflow:**

1. **Rule-based pre-annotation (~195 questions, deterministic):**
   - `source: "atlas_country_page"` -> `expected_tool: "graphql_only"` (109 questions)
   - `source: "atlas_explore_page"` -> `expected_tool: "graphql_only"` (77 questions)
   - `category: "out_of_scope"` or `"data_boundaries"` -> `expected_tool: "refusal"` (8 questions)
   - `category: "edge_cases"` -> `expected_tool: "refusal"` (8 questions, review individually)

2. **LLM-assisted annotation (~48 ambiguous original SQL questions):** Prompt the LLM with the question text + category + list of GraphQL query types -> classify which tool is most appropriate. Values: `sql_only`, `graphql_only`, `graphql_preferred`, `either`.

3. **Human review:** Review all LLM annotations; spot-check 10% of rule-based ones.

**Annotation values:** `sql_only`, `graphql_only`, `graphql_preferred`, `either`, `refusal`

**Effort:** ~4 hours

### Phase 0b — Coverage Gap Analysis (immediate, #89)

**Script:** `evaluation/analyze_coverage_gaps.py`

**Output:** `evaluation/coverage_report.json` + `evaluation/coverage_report.md`

**Analysis:**
- Cross-reference all 28 categories x question counts — identify categories with <3 questions
- Cross-reference GraphQL `query_type` values x eval questions — identify under-represented types
- Difficulty distribution check — ensure balanced easy/medium/hard across categories
- Source distribution — identify which source types lack ground truth

**Current distribution (28 categories):**

| Category | Count | Has GT? |
|----------|-------|---------|
| Total Export Values | 3 | No (original) |
| Data Availability Boundaries | 4 | No (original) |
| Out-of-Scope Refusals | 4 | No (original) |
| Frontier Edge Cases (Country Page) | 4 | Yes |
| Growth Opportunities (Country Page) | 4 | Yes |
| Export Diversification Strategies | 5 | No (original) |
| *(25 more categories with 6-22 questions each)* | ... | ... |

**Effort:** ~2 hours

### Phase 1 — Component Eval Datasets (during GraphQL implementation, #89)

**Dependency:** `GraphQLQueryClassification` schema finalized (needed for valid `query_type` labels).

#### Classification Eval Set

**File:** `evaluation/classification_eval.json` (~60 questions)
**Generator:** `evaluation/generate_classification_eval.py`

**Hybrid generation:**
- **Extract from existing questions (~30):** Many existing `cp_*`/`explore_*` questions map to a known `query_type` — the collection scripts reveal the mapping. Extract and label them.
- **LLM-generated for gaps (~18):** For under-represented query types (`overtime_partners`, `marketshare`, `global_datum`, `explore_data_availability`), generate new questions. Human verifies each.
- **Rejection cases (~12):** Manually written questions that the classifier should reject — complex SQL-only analytical questions, out-of-scope questions, multi-step questions.

**Format:**
```json
{
  "id": "cls_001",
  "question": "What is the GDP per capita of Kenya?",
  "expected_query_type": "country_profile",
  "expected_api_target": "country_pages",
  "expected_country": "Kenya",
  "expected_product": null,
  "source_method": "extracted_from_eval",
  "notes": "Direct country profile lookup — extracted from Q63"
}
```

**Effort:** ~6 hours

#### Entity Extraction Eval Set

**File:** `evaluation/entity_extraction_eval.json` (~30 questions)

Since extraction depends on the classified type, each test case includes the `query_type` as input.

**Coverage per extraction field:**
- Country + country code (~15 questions, including aliases like "the US" -> "USA")
- Partner country (~5 bilateral questions)
- Product + product code (~10 questions, including services like "tourism" -> "Travel & tourism")
- Year / year range (~10 questions)
- Product level, product class, lookback years, group type (~5 questions each for non-default values)

**Format:**
```json
{
  "id": "ext_001",
  "question": "How have Kenya's coffee exports changed since 2010?",
  "input_query_type": "overtime_products",
  "expected_country": "Kenya",
  "expected_country_code": "KEN",
  "expected_product": "coffee",
  "expected_product_code": "0901",
  "expected_min_year": 2010,
  "expected_max_year": null,
  "expected_product_class": null,
  "source_method": "llm_generated",
  "notes": "Standard time-series extraction"
}
```

**Effort:** ~4 hours

#### ID Resolution Eval Set

**File:** `evaluation/id_resolution_eval.json` (~40 questions)

**Ground truth source:** The `locationCountry` and `productHs92` catalogs from the Explore API — these are lookup tables, not system outputs.

**Resolution tiers:**

| Tier | Count | Example | Challenge |
|------|-------|---------|-----------|
| Clean | 15 | "Kenya's RCA in Coffee" -> country_id=404, product_id=726 | Straightforward name -> ID lookup |
| Alias/synonym | 12 | "the US exports in semiconductors" -> country_id=840, product_id=3595 | Common name vs. official name, product synonyms |
| Ambiguous | 8 | "petroleum exports from Brazil" -> multiple valid product IDs | Multiple valid resolutions, requires disambiguation |
| Services | 5 | "Kenya's tourism service exports" -> match "Travel & tourism" service category | Service name matching against catalog; no HS code |
| Missing entity | 5 | "What does Narnia export?" -> null with error | Entity doesn't exist in Atlas, should fail gracefully |

**Effort:** ~6 hours

#### Component Eval Runner

**File:** `evaluation/run_component_evals.py`

**Purpose:** Run all three component eval sets (classification, extraction, ID resolution) and report accuracy metrics.

**Interface:**
```bash
# Run all component evals
uv run python evaluation/run_component_evals.py

# Run specific component
uv run python evaluation/run_component_evals.py --component classification
uv run python evaluation/run_component_evals.py --component extraction
uv run python evaluation/run_component_evals.py --component id_resolution
```

**Output:** JSON report with per-component accuracy, per-tier breakdown (for ID resolution), and failure details.

**Effort:** ~4 hours

### Phase 2 — Judge Extensions (during integration testing, #90)

**Dependency:** Phase 0 tool annotations + Phase 1 component datasets.

#### `TrajectoryVerdict` Judge Mode

Add to `evaluation/judge.py` — deterministic tool sequence comparison (no LLM cost).

**Logic:**
1. Extract tool names from `ExecutionTrace.tool_calls`
2. Look up `expected_tool` from question annotations
3. Compare:
   - `expected_tool: "graphql_only"` -> tool_calls must contain `atlas_graphql` and not `query_tool`
   - `expected_tool: "sql_only"` -> tool_calls must contain `query_tool` and not `atlas_graphql`
   - `expected_tool: "graphql_preferred"` -> pass if `atlas_graphql` used; acceptable if `query_tool` used as fallback
   - `expected_tool: "either"` -> pass if any valid tool used
   - `expected_tool: "refusal"` -> pass if no data tool called (appropriate refusal)
4. Return `TrajectoryVerdict`: `pass`, `fail`, or `acceptable`

**Effort:** ~3 hours (in Phase A)

#### `data_source_appropriateness` Dimension

New scoring dimension for the `ground_truth` judge mode.

**Criteria:** Did the answer come from the most suitable data source?
- 5 = Optimal source used (GraphQL for derived metrics, SQL for custom aggregations)
- 3 = Acceptable source used (correct answer, suboptimal tool)
- 1 = Completely wrong source used

**Rebalanced weight scheme:**

| Dimension | New Weight | Old Weight |
|-----------|-----------|-----------|
| `factual_correctness` | 0.30 | 0.35 |
| `data_accuracy` | 0.25 | 0.30 |
| `completeness` | 0.15 | 0.20 |
| `reasoning_quality` | 0.15 | 0.15 |
| `data_source_appropriateness` | **0.15** | — (new) |

Weights sum to 1.00. Verdict thresholds unchanged: pass >= 3.5, partial >= 2.5, fail < 2.5.

**Effort:** ~5 hours total (dimension + rebalance)

#### Tool Call Capture in `run_agent_evals.py`

Modify `run_single_question()` to extract and record alongside the existing result:
- `tool_calls` — ordered list of tool names from message history
- `graphql_classification` — from `state["graphql_classification"]`
- `graphql_entities` — from `state["graphql_entity_extraction"]`
- `atlas_links` — from `state["graphql_atlas_links"]`
- `node_sequence` — from checkpoint metadata

This data feeds both `TrajectoryVerdict` and `data_source_appropriateness`.

**Effort:** ~4 hours (in Phase A)

### Phase 3 — Browser-Verified Ground Truth (post-launch, #51)

**Dependency:** Phase 0 + GraphQL pipeline being functional.

#### Remediate 56 Original SQL Questions

The 56 original SQL questions (IDs 1-60, excluding those already covered) lack ground truth. For each:
1. Determine the appropriate Atlas page URL
2. Collect ground truth from the browser using Section 4.2 workflow
3. Tag with `source_method: "browser_country_page"` or `"browser_explore_page"`
4. Update `eval_questions.json` with `atlas_url` and `expected_tool`

**Effort:** ~12 hours

#### New GraphQL Eval Questions (IDs 247+)

~30-40 new questions specifically exercising GraphQL pipeline paths:

| Category | Questions | Expected Tool | Ground Truth Source |
|----------|-----------|---------------|-------------------|
| `gql_country_profile` | 4-6 | `graphql_only` | Browser: Atlas country pages |
| `gql_growth_dynamics` | 3-4 | `graphql_only` | Browser: growth-dynamics subpage |
| `gql_explore_treemap` | 4-5 | `graphql_only` | Browser: treemap visualization |
| `gql_explore_overtime` | 3-4 | `graphql_only` | Browser: overtime visualization |
| `gql_explore_feasibility` | 3-4 | `graphql_only` | Browser: feasibility page |
| `gql_cross_tool` | 4-5 | both tools | Browser + verified SQL |
| `gql_link_verification` | 4-5 | any | Browser: verify generated Atlas link URL matches expected page |

**Collection guide:** `evaluation/graphql_eval_collection_guide.md` — follows the same structure as existing guides (URL patterns, question templates, extraction methods, ground truth recording format).

**Effort:** ~10 hours

#### Refresh and Spot-Check Scripts

- `evaluation/refresh_ground_truth.py` — See Section 4.4. Effort: ~6 hours
- `evaluation/spot_check_graphql.py` — See Section 4.3. Effort: ~4 hours

---

## 7. Eval Run Costs and Cadence

Evaluations are run manually (not in CI/CD). Cost estimates assume GPT-5.2 for agent and GPT-5-mini for judge.

### Cost Per Run Type

| Run Type | Questions | Agent Cost | Judge Cost | Total |
|----------|-----------|-----------|-----------|-------|
| Smoke (5 questions) | 5 | ~$0.60 | ~$0.06 | **~$0.66** |
| Component eval (classification) | 60 | ~$0.36 (lightweight, classification only) | N/A (deterministic) | **~$0.36** |
| Component eval (extraction) | 30 | ~$0.18 | N/A (deterministic) | **~$0.18** |
| Component eval (ID resolution) | 40 | ~$0.24 | N/A (deterministic) | **~$0.24** |
| Trajectory eval (all questions) | 246 | ~$29.52 | $0 (deterministic) | **~$29.52** |
| Full eval (246 questions) | 246 | ~$29.52 | ~$2.95 | **~$32.47** |
| Full eval + new questions (~290) | 290 | ~$34.80 | ~$3.48 | **~$38.28** |

**Assumptions:** ~$0.12/question agent cost (based on observed token usage), ~$0.012/question judge cost (GPT-5-mini).

### Recommended Cadence

| Run Type | When | Why |
|----------|------|-----|
| Smoke eval (~$0.66) | After any backend prompt or pipeline change | Quick sanity check — catches obvious regressions |
| Component eval (~$0.78) | After changes to classification, extraction, or ID resolution nodes | Isolates per-node accuracy changes |
| Trajectory eval ($0) | As often as desired (free) | Verifies tool routing correctness after any agent change |
| Full eval (~$32-38) | Weekly during active development; before any release | Comprehensive quality assessment |
| Spot-check (Section 4.3) | Monthly | Detects ground truth staleness |

### Cost Mitigation Strategies

1. **Cache LLM responses for static inputs:** Component eval inputs (classification questions) don't change between runs. Cache the LLM's structured output and only re-run when the prompt or model changes.
2. **Use cheaper models for component evals:** Classification accuracy can be measured with a cheaper model (GPT-5-mini) if the goal is regression detection rather than absolute accuracy measurement.
3. **Smoke test as default gate:** Use 5-question smoke (~$0.66) for quick per-change validation; save full eval for weekly runs.
4. **Trajectory eval is free:** Deterministic comparison costs nothing after the agent run — run it alongside every eval.

---

## 8. Metrics and Dashboards

### Quality Metrics (from eval runs)

| Metric | Target | Source |
|--------|--------|--------|
| Overall pass rate | >60% | `evaluation/runs/history.jsonl` |
| Avg weighted score | >3.5/5.0 | `evaluation/runs/history.jsonl` |
| Classification accuracy | >90% | Component eval report |
| Entity extraction accuracy | >90% country, >85% product | Component eval report |
| ID resolution accuracy | >95% country, >90% product | Component eval report |
| Trajectory match rate | >95% | Trajectory eval report |
| Regression count (vs. previous run) | 0 | `evaluation/compare_runs.py` |

**Baseline (2026-02-26):** avg_score 2.4/5.0, pass_rate 20% (5-question smoke). This is the number to beat.

### Performance Metrics (from production)

| Metric | Target | Source |
|--------|--------|--------|
| Agent response latency p95 | <15s | `request_traces` table |
| GraphQL pipeline latency p95 | <5s | `request_traces` table |
| SQL pipeline latency p95 | <10s | `request_traces` table |
| Error rate | <5% | `request_traces` table |
| GraphQL budget utilization | <80% | `request_traces` table |
| Circuit breaker trips/week | 0 | `request_traces` table |

### Coverage Metrics (from dataset analysis)

| Metric | Target | Source |
|--------|--------|--------|
| Questions with ground truth | >90% (currently 77%) | `evaluation/coverage_report.json` |
| Categories with >=3 eval questions | 100% (28/28) | `evaluation/coverage_report.json` |
| Source method distribution: browser-verified | >30% | `evaluation/coverage_report.json` |
| GraphQL query_types with eval coverage | 100% | `evaluation/coverage_report.json` |

### Trend Reporting

**Script:** `evaluation/trend_report.py`

**Input:** `evaluation/runs/history.jsonl` — one-line JSON per run with timestamp, scores, agent/judge models.

**Output:** Markdown trend report showing:
- Pass rate over time (last N runs)
- Avg score over time
- Per-category pass rate trends
- Regressions detected (questions that moved from pass to fail)
- Run duration trends

Run: `uv run python evaluation/trend_report.py` (default: last 10 runs) or `uv run python evaluation/trend_report.py --runs 20`.

---

## 9. Phased Implementation Roadmap

Phases are ordered by dependency chains and signal-per-effort. No calendar dates — work items become available when their dependencies are satisfied.

### Phase A: Foundations

**Dependencies:** None — start immediately.
**Effort:** ~15 hours

| Work Item | Effort | Unlocks |
|-----------|--------|---------|
| Tool call capture in `run_agent_evals.py` — extract tool_calls, node_sequence, classification from `AtlasAgentState` | ~4h | Trajectory eval, data_source_appropriateness |
| `annotate_tool_expectations.py` — rule-based + LLM annotation of `expected_tool` for all 246 questions | ~4h | Trajectory eval |
| `analyze_coverage_gaps.py` — category balance, query type coverage, difficulty distribution | ~2h | Prioritized dataset collection |
| `TrajectoryVerdict` judge mode — deterministic tool sequence comparison in `judge.py` | ~3h | Free quality gate for every run |
| `trend_report.py` — historical progress tracking from `runs/history.jsonl` | ~2h | Visible progress tracking |

### Phase B: Component Eval Datasets

**Dependencies:** Phase A + `GraphQLQueryClassification` schema finalized.
**Effort:** ~20 hours

| Work Item | Effort |
|-----------|--------|
| Classification eval set: `classification_eval.json` (~60 questions) via `generate_classification_eval.py` | ~6h |
| Entity extraction eval set: `entity_extraction_eval.json` (~30 questions) | ~4h |
| ID resolution eval set: `id_resolution_eval.json` (~40 questions) | ~6h |
| Component eval runner: `run_component_evals.py` | ~4h |

### Phase C: Judge Extensions

**Dependencies:** Phase A tool annotations + Phase B component datasets.
**Effort:** ~5 hours

| Work Item | Effort |
|-----------|--------|
| `data_source_appropriateness` judge dimension (weight 0.15) | ~4h |
| Rebalance existing judge dimension weights | ~1h |

### Phase D: Browser-Verified Ground Truth

**Dependencies:** Phase A + GraphQL pipeline being functional.
**Effort:** ~40 hours (can run in parallel with Phase C)

| Work Item | Effort |
|-----------|--------|
| Browser extraction orchestrator + per-page-type extractors | ~8h |
| Remediate 56 original SQL questions missing ground truth | ~12h |
| New GraphQL-specific eval questions (IDs 247+, ~30-40 questions) | ~10h |
| `refresh_ground_truth.py` with dry-run + commit modes | ~6h |
| Spot-check workflow: `spot_check_graphql.py` | ~4h |

### Phase E: Production Monitoring

**Dependencies:** Independent — can run in parallel with any phase.
**Effort:** ~12 hours

| Work Item | Effort |
|-----------|--------|
| `request_traces` table in App DB | ~3h |
| Structured logging in `src/api.py` | ~4h |
| `/api/metrics` endpoint | ~3h |
| Production failure review SOP document | ~2h |

### Dependency Graph

```
Phase A (Foundations)
  |
  ├──> Phase B (Component Datasets)
  |      |
  |      └──> Phase C (Judge Extensions)
  |
  ├──> Phase D (Browser GT)   [can run in parallel with C]
  |
Phase E (Production Monitoring)   [independent — anytime]
```

**Total effort:** ~92 hours across all phases. Phases A and E can start immediately and run in parallel.

---

## 10. File Inventory

### New Files to Create

| File | Purpose | Phase |
|------|---------|-------|
| `evaluation/annotate_tool_expectations.py` | Tool routing annotation script (rule-based + LLM) | A |
| `evaluation/tool_annotations.json` | Annotation output — `expected_tool` for all 246 questions | A |
| `evaluation/analyze_coverage_gaps.py` | Coverage gap analysis across categories, query types, difficulty | A |
| `evaluation/coverage_report.json` | Coverage analysis output (machine-readable) | A |
| `evaluation/coverage_report.md` | Coverage analysis output (human-readable) | A |
| `evaluation/trace_collector.py` | `ExecutionTrace` dataclass and extraction from `AtlasAgentState` | A |
| `evaluation/trend_report.py` | History trend analysis from `runs/history.jsonl` | A |
| `evaluation/classification_eval.json` | Classification ground truth — question -> `query_type` label (~60 questions) | B |
| `evaluation/entity_extraction_eval.json` | Entity extraction ground truth — question + query_type -> entities (~30 questions) | B |
| `evaluation/id_resolution_eval.json` | ID resolution ground truth — question -> entity IDs (~40 questions) | B |
| `evaluation/run_component_evals.py` | Component eval runner (classification, extraction, ID resolution) | B |
| `evaluation/generate_classification_eval.py` | Classification eval set generator (extract + LLM + manual) | B |
| `evaluation/browser_collect_ground_truth.py` | Browser automation orchestrator for ground truth collection | D |
| `evaluation/browser_extractors/country_profile.py` | DOM extraction for country profile stat cards + narratives | D |
| `evaluation/browser_extractors/feasibility_table.py` | DOM extraction for the feasibility HTML table | D |
| `evaluation/browser_extractors/treemap_verify.py` | API query + screenshot verification for treemap visualizations | D |
| `evaluation/refresh_ground_truth.py` | Ground truth refresh pipeline (dry-run + commit + changelog) | D |
| `evaluation/spot_check_graphql.py` | Spot-check workflow — agent answer vs. browser data comparison | D |
| `evaluation/graphql_eval_collection_guide.md` | Collection guide for new GraphQL-specific eval questions | D |
| `evaluation/ground_truth_changelog.jsonl` | Append-only changelog of ground truth data changes | D |

### Existing Files to Modify

| File | Changes | Phase |
|------|---------|-------|
| `evaluation/run_agent_evals.py` | Capture `tool_calls`, `node_sequence`, `graphql_classification`, `graphql_entities`, `atlas_links` from agent state after each question | A |
| `evaluation/judge.py` | Add `TrajectoryVerdict` mode (A); add `data_source_appropriateness` dimension + rebalance weights (C) | A + C |
| `evaluation/run_eval.py` | Integrate trajectory eval into orchestration; pass tool call data to judge | A |
| `evaluation/report.py` | Add trajectory stats (match rate, tool distribution) and component eval summaries to reports | A |
| `evaluation/eval_questions.json` | Add `expected_tool` field to all 246 questions (A); add new GraphQL questions IDs 247+ (D) | A + D |
| `src/api.py` | Structured logging to `request_traces` table; `/api/metrics` endpoint | E |

---

## 11. Risk Mitigation

### LLM Judge Reliability

- **Calibration:** Before trusting judge scores at scale, calibrate against human judgments on 20 questions. Have a human score the same questions the judge scores, then measure agreement (Cohen's kappa or simple correlation).
- **Inter-run agreement:** Run the same eval twice with the same judge model. Scores should be consistent (within 0.3 points). If not, the judge prompt needs tightening.
- **Hedge with deterministic evaluation:** Use trajectory eval (deterministic, free) as the primary quality gate; reserve LLM-as-judge for answer quality assessment where deterministic comparison isn't possible.

### Browser Automation Fragility

- **Version-tag extraction scripts:** Atlas DOM structure may change. Pin extraction scripts to a known-working state; when extraction fails, the error message should identify which selector broke.
- **Screenshot fallback:** For canvas-based visualizations, always have a screenshot fallback. A screenshot can be manually inspected even if automated extraction fails.
- **Monthly spot-checks:** Run the spot-check workflow (Section 4.3) monthly to detect breakage early.
- **Graceful degradation:** If browser extraction fails for a question, fall back to API-based collection and flag the question for manual review. Don't block the entire refresh pipeline on one broken extractor.

### Cost Overruns

- **Smoke test as default:** $0.66 per run — use this for per-change validation. Full eval ($32-38) is weekly only.
- **Cache LLM responses:** For component evals with static inputs, cache structured outputs and skip re-running when only the eval harness changes (not the prompt or model).
- **Cheaper models for component evals:** Classification accuracy is a regression metric — a cheaper model suffices for regression detection.
- **Budget alerts:** Track cumulative eval spend in `runs/history.jsonl`; alert if weekly spend exceeds $100.

### Data Staleness

- **`refresh_ground_truth.py` with dry-run:** Always preview changes before overwriting. The changelog provides auditability.
- **Quarterly spot-checks:** Run browser spot-checks quarterly to detect Atlas data updates.
- **Staleness metadata:** Each ground truth file records `execution_timestamp`. A simple script can flag questions where ground truth is older than 6 months.
- **Known update schedule:** Atlas trade data updates annually (with ~2 year lag). Derived metrics (ECI, PCI, COI) update when the underlying data updates. Growth projections update independently.

---

## 12. Modern Eval Research to Incorporate

This section documents the evaluation patterns informing this strategy, with sources. The strategy doesn't adopt any framework wholesale — it selects the patterns that fit Ask-Atlas's needs.

### Multi-Dimensional Agent Evaluation

The industry has converged on evaluating agents across multiple axes — capability, robustness, tool correctness, safety, and efficiency — rather than just final answer quality. The five-tier pyramid (Section 2) maps to these dimensions:

| Dimension | Tier Coverage |
|-----------|--------------|
| **Capability** (can it answer correctly?) | Tier 2 (component), Tier 4 (end-to-end) |
| **Tool correctness** (does it use the right tools?) | Tier 3 (trajectory) |
| **Robustness** (does it handle edge cases?) | Tier 1 (unit tests for budget/circuit breaker), Tier 4 (edge case questions) |
| **Efficiency** (how fast? how much cost?) | Tier 5 (production latency), Tier 3 (budget utilization) |
| **Safety** (does it refuse appropriately?) | Tier 4 (refusal judge mode), Tier 3 (trajectory for refusal questions) |

Source: "Beyond Task Completion: Assessment Framework for Agentic AI" (arXiv 2512.12791).

### LLM-as-Judge Patterns

G-Eval (chain-of-thought before scoring, 1-5 scale) is the dominant pattern (~53% adoption in production LLM evaluation systems). The existing `judge.py` already follows this pattern:
- Chain-of-thought reasoning in the judge prompt ("First, analyze the factual accuracy...")
- 1-5 score per dimension with textual reasoning
- Weighted aggregation to a final verdict

**When to use deterministic evaluation vs. LLM-as-judge:**
- **Deterministic (Tier 3):** Tool sequence comparison, classification label matching, ID resolution exact match. These have objectively correct answers — no LLM judgment needed.
- **LLM-as-judge (Tier 4):** Answer quality assessment where the "correct" answer involves natural language comparison, rounding tolerance, completeness judgment. The judge evaluates whether the agent's prose answer conveys the same information as the ground truth data.

Source: Confident AI's LLM evaluation metrics guide; G-Eval (Liu et al., 2023).

### Trajectory Evaluation for Agents

LangChain's `agentevals` library supports extracting and scoring full execution paths (tool call sequences, node transitions, intermediate states). The core insight: a correct final answer from the wrong tool is a latent bug — it masks tool routing errors that will surface on harder questions.

`TrajectoryVerdict` is the Ask-Atlas implementation of this pattern. It's simpler than the full `agentevals` library (which uses LLM-as-judge for trajectory scoring) because our tool routing has clear expected values: each question maps to a specific expected tool. Deterministic comparison is both cheaper and more reliable for this use case.

Source: LangChain `agentevals` documentation; "Evaluating Multi-Step Agentic Workflows" (LangChain blog, 2025).

### Continuous Evaluation (Closed Loop)

The gold standard evaluation loop:

```
Offline eval on curated datasets (during development)
    ↓
Periodic full eval runs (weekly during active development)
    ↓
Online monitoring in production (always-on)
    ↓
Production failures fed back into eval datasets
    ↓
(loop back to offline eval)
```

This strategy implements each stage:
- **Offline eval:** Tiers 1-4, run manually during development
- **Periodic full eval:** Weekly full eval runs (Tier 4)
- **Online monitoring:** Production request traces + `/api/metrics` (Tier 5)
- **Failure feedback:** Weekly failure review -> categorize -> add eval candidates (Section 5.3)

The feedback loop ensures the eval set evolves with production usage patterns rather than stagnating on the original curated questions.

### LangSmith (Noted, Not Adopted)

[LangSmith](https://smith.langchain.com/) is the hosted evaluation/tracing platform for LangGraph. It provides:
- Auto-traces for every LangGraph node execution
- Intermediate step inspection (what each node produced)
- Annotation queues for human review of agent outputs
- Dataset management for eval sets
- Hosted eval runs with built-in judge support

**Why we're not using it:** LangSmith is expensive at our expected usage volume (~20 concurrent users, each triggering multi-node graph executions). More critically, hitting free-tier rate limits causes errors that propagate through the LangGraph execution — the agent fails for the end user because the tracing layer is throttled. This is an unacceptable failure mode for a production system.

**Our alternative:** A custom `ExecutionTrace` collector (Section 5.1) that extracts the same signals (tool calls, node timing, classification/extraction outputs) without external dependencies. The data is stored locally in eval run directories and the `request_traces` table, giving us full control over storage, querying, and cost.

### Cost-Conscious Evaluation

Token costs scale with eval dataset size x model cost. The cost analysis in Section 7 quantifies this for each run type. Key cost-control patterns adopted:
- **Tiered evaluation:** Cheaper tiers run more frequently; expensive tiers run less often
- **Deterministic evaluation where possible:** Trajectory eval ($0), component eval (real LLM but no judge), unit tests ($0)
- **Smoke test as default gate:** $0.66 per change vs. $32+ for full eval
- **Response caching:** For component evals with static inputs, cache LLM responses and skip re-running when only the eval harness changes
- **Model tiering:** Use cheaper models (GPT-5-mini) for component eval regression detection; save expensive models (GPT-5.2) for full answer quality assessment

---

*Document version: 2026-02-27. Consolidates `docs/backend_redesign_analysis.md` Section 13, GitHub issues #51, #89, #90.*
