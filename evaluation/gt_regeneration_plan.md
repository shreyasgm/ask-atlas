# Plan: Regenerate Ground Truth for HS12 + Remediate Missing GT + Run Eval

*Created: 2026-02-28*

## Context

The agent's default product classification was changed from HS92 to HS12 (commit `56528b4`). All existing ground truths were captured using HS92 data. Running evals with the HS12 agent against HS92 ground truth would produce false failures on product-specific questions. Ground truth must be regenerated before we can get a clean before/after comparison.

Additionally, the evaluation strategy and collection guides (`evaluation/evaluation_strategy.md`, `country_page_collection_guide.md`, `explore_page_collection_guide.md`) recommend remediating the 56 original questions (IDs 3-60) that lack ground truth entirely.

### Critical API Constraint

The Atlas has **two separate GraphQL APIs** with different classification support:

| API | Endpoint | Product Classes | ID Format |
|-----|----------|----------------|-----------|
| **Explore API** | `/api/graphql` | `HS92`, `HS12`, `HS22`, `SITC` | Integer (`countryId: 404`) |
| **Country Pages API** | `/api/countries/graphql` | `HS`, `SITC` only (HS = HS92) | String (`location: "location-404"`) |

**Consequence:** Country Page GT (109 questions, IDs 61-169) does NOT need regeneration — the Country Pages API only supports HS92, which is what the existing GT already uses. Only Explore Page GT needs HS12 regeneration.

### Completed: HS92 Classification Annotations & Agent Prompt Fixes

The following safeguards have been implemented to ensure the agent uses HS92 when querying Country Pages:

1. **Question text annotations** (`eval_questions.json`): 97 Country Page questions (IDs 61-169, excluding 10 exempt + 2 already mentioning HS92) now end with an explicit classification instruction:
   - **37 questions** (goods-only metrics: ECI, COI, diversification, PCI, strategic approach, growth projection): `"Use the HS 1992 classification."`
   - **60 questions** (generic "products"/"sectors", services, export baskets/totals): `"Use the HS 1992 classification for goods and also use the services schema."`
   - **8 remediation questions** (IDs 32, 33, 37, 38, 41, 43, 44, 50) also annotated with the same scheme.

2. **Metadata fields** (`eval_questions.json`): All 109 CP questions + 8 remediation questions now carry `expected_api_target: "country_pages"` and `expected_classification: "HS92"`.

   **Why track this in metadata?** The metadata serves a single purpose: it tells the judge pipeline to inject the classification note into the judging prompt. Without it, the judge has no way to know that product name mismatches (e.g., "Petroleum oils, crude" in HS92 vs "Crude petroleum" in HS12) are an expected consequence of the HS92 constraint, not agent errors. The metadata is read-only at eval time — it doesn't affect the agent, only the judge. If this proves unnecessary after a few eval runs (i.e., the question text annotations alone are sufficient to steer the agent), the metadata fields and judge classification note can be removed with no downstream impact.

3. **Agent prompt fixes** (`src/graphql_pipeline.py`):
   - `PRODUCT_CLASS_DESCRIPTION`: Updated to show HS12 as default for Explore API; added note that Country Pages only supports HS (=HS92).
   - `API_TARGET_DESCRIPTION`: Added classification constraint note to `country_pages` description.
   - 6 Explore API builder functions: Default changed from `"HS92"` to `"HS12"` (`_build_country_product_year`, `_build_country_country_product_year`, `_build_product_table`, `_build_treemap_cpy`, `_build_treemap_ccpy`, `_build_feasibility_cpy`). `_build_growth_opportunities` kept at `"HS"` (targets Country Pages).

4. **Judge pipeline** (`evaluation/run_eval.py`, `evaluation/judge.py`):
   - `_load_questions_meta()` now loads `expected_api_target` and `expected_classification`.
   - `judge_answer()` accepts optional `classification_note` parameter.
   - When `expected_classification` is present, the judge receives a note explaining that GT was collected from HS92 data and product name discrepancies may stem from classification differences.
   - Verdict includes `classification_note_applied: true` flag when active.

### Rate Limits & Headers

Per the [official API docs](https://github.com/harvard-growth-lab/api-docs/blob/main/atlas.md) (§ "Best Practices & Restrictions"):

> "The Atlas API currently enforces a **rate limit of 120 requests per minute**."

The official docs also recommend caching responses, batching fields into single queries, requesting only needed fields, and filtering within queries. For bulk data, they point to the [data downloads page](https://atlas.hks.harvard.edu/data-downloads).

- **Official rate limit**: 120 req/min (shared across both APIs from the same IP)
- **Working limit**: 2 req/s, 200 req/min (user-approved stretch beyond official limit)
- **Required User-Agent header**: `ask-atlas-gt` — so the Atlas software team can distinguish our automated collection from unknown bots
- All collection scripts must enforce these limits using `asyncio.Semaphore` or `aiolimiter`
- **No authentication required** — both APIs are publicly accessible

> **Usage warning** (from official docs): "The Atlas API is best used to access data for stand-alone economic analysis, not to support other software applications." The Growth Lab reserves the right to monitor and restrict access.

### Classification Safeguard: Questions Explicitly Requesting Other Schemas

Some questions explicitly ask for a specific classification (HS92, SITC, or services). These must NOT be blindly overridden to HS12 during GT collection:

- **Q244-Q246**: Explicitly ask about HS92 → collect GT with `productClass: HS92`
- **Q3, Q8, Q11-Q14**: Ask about services → collect GT with `servicesClass: unilateral` (classification N/A)
- **Q26 (CAGR 2010-2020)**: HS12 only has data from 2012 → may need SITC or HS92 for pre-2012 years
- **New classification-diversity questions (Step 2)**: Each specifies its schema explicitly

When extending collection scripts for remediation (Step 3), always check whether the question text explicitly mentions a classification and use that classification for GT, not the agent's default.

---

## Step 1: Update Explore Page Collection Script for HS12

**File**: `evaluation/collect_explore_page_data.py`

### 1a. Add `--product-class` CLI argument (default: `HS12`)
- All hardcoded `productClass: HS92` → use CLI param (8+ occurrences at lines 144, 162, 178, 197, 217, 232, 251, 266)
- Product ID format: `product-HS92-{id}` → `product-{CLASS}-{id}` (lines 340, 355, 399, 493, 948, 980, 1122, 1293)
- Product labels: `(XXXX HS92)` → `(XXXX {CLASS})` (lines 357, 1164, 1209, 1235, 1517, 1547, 1569)

### 1b. Exempt HS92-specific questions
Q244, Q245, Q246 explicitly ask about HS92 classification — hardcode these to `HS92` regardless of the CLI parameter.

### 1c. Add rate limiting and User-Agent header
- Add `User-Agent: ask-atlas-gt` header to all HTTP requests (so the Atlas team knows it's us)
- Add rate limiter: max 2 req/s, 200 req/min (stretching official 120 req/min limit per user approval)
- Use `asyncio.Semaphore` or `aiolimiter` for enforcement
- Both APIs share the rate limit from the same IP — budget queries across both scripts if running in parallel
- Follow official recommendations: batch fields into single queries, request only needed fields, filter within queries

### 1d. Update SQL ground truth queries (product-specific only)
- **Q2** (`evaluation/questions/2/queries/01.sql`): `hs92.country_product_year_4` → `hs12.country_product_year_4`; `classification.product_hs92` → `classification.product_hs12`
- **Q6** (`evaluation/questions/6/queries/01.sql`): Same changes as Q2
- **Q1** and **Q25**: These are country-level aggregates (`hs92.country_year`) — values are identical across classifications. No change needed.

### 1e. Add rate limiting to Country Page collection script
**File**: `evaluation/collect_country_page_data.py`
- Add `User-Agent: ask-atlas-gt` header to all HTTP requests
- Add same rate limiter as Explore script
- **Do NOT change `productClass: HS`** — the Country Pages API only supports `HS` (=HS92) and `SITC`

---

## Step 2: Add Classification-Diversity Questions (IDs 247+)

Currently only Q244-Q246 explicitly reference non-default classifications. Add ~6 new questions to test the agent's ability to handle explicit classification requests:

**HS92-specific (2 questions):**
- "Under HS 1992 classification, what is Brazil's Revealed Comparative Advantage in Coffee?"
  → GT via Explore API: `countryProductYear(productClass: HS92, countryId: 76, productId: 726)` → `exportRca`
- "What are India's top 3 export products using HS 1992 classification?"
  → GT via Explore API: `countryProductYear(productClass: HS92, countryId: 356)` → sort by `exportValue`, top 3

**SITC-specific (2 questions):**
- "Using SITC classification, what is Japan's total export value in 2020?"
  → GT via Explore API: `countryYear(countryId: 392, productClass: SITC, yearMin: 2020, yearMax: 2020)`
- "What is the SITC code for Coffee, and what is Kenya's RCA in it?"
  → GT via Explore API: `productSitc` catalog + `countryProductYear(productClass: SITC)` for Kenya/Coffee

**HS22-specific (1 question):**
- "What are the top exports for Germany in 2023 under the HS 2022 classification?"
  → GT via Explore API: `countryProductYear(productClass: HS22, countryId: 276, yearMin: 2023, yearMax: 2023)`

**Cross-classification (1 question):**
- "How does Brazil's number of products with RCA > 1 differ between HS 1992 and HS 2012?"
  → GT: Two Explore API calls with `productClass: HS92` and `productClass: HS12`, count where `exportRca > 1`

### Ground truth collection for these
Run targeted Explore API queries with the appropriate `productClass` for each. All 6 are API-only (no browser needed).

---

## Step 3: Remediate 56 Missing GT Questions (IDs 3-60)

These currently have NO ground truth and are judged on "plausibility" only. Organized into three tiers following the evaluation strategy.

### Tier A: Expected-behavior annotations (10 questions)

Refusal/boundary questions — add `expected_behavior` field to `eval_questions.json` and create minimal GT.

| Q | Question | Expected behavior |
|---|----------|------------------|
| 46 | Soviet Union 1990 exports | Explain: historical entity not in Atlas |
| 48 | Brazil 2030 exports | Refuse: future year not available |
| 53 | Capital of France | Refuse: off-topic |
| 54 | SQL injection attempt | Refuse: not a trade question |
| 55 | Nigeria protectionism advice | Refuse: policy opinion outside scope |
| 56 | Python scraping script | Refuse: coding task outside scope |
| 57 | US→China bilateral services | Explain: bilateral services by partner not available |
| 58 | UK exports 1960 | Handle: data may be limited (SITC starts 1962, HS starts 1995) |
| 59 | Taiwan vs Chinese Taipei | Explain: same entity under different names |
| 60 | Germany import services 2021 | Data GT from services API |

**Implementation**: Add `expected_behavior` field to these questions in `eval_questions.json`. Create `results.json` with the expected behavior description rather than data values.

### Tier B: Straightforward API-based GT (23 questions)

Single-country, single-metric lookups. Most use the Explore API; some use Country Pages API.

**Countries needed beyond current 8**: Japan (392), Canada (124), Chile (152), Peru (604), UK (826), France (250), Singapore (702), Thailand (764), Netherlands (528), Switzerland (756), South Africa (710), Vietnam (704), China (156), Tuvalu (798), Liechtenstein (438), South Sudan (728)

| Q | Summary | API Source | Classification |
|---|---------|-----------|----------------|
| 3 | Singapore service exports 2018 | Explore: `countryYear` + `servicesClass: unilateral` | N/A (services) |
| 4 | Germany automotive export % | Explore: `countryProductYear(productClass: HS12)` | HS12 (default) |
| 5 | Canada top 5 sectors 2021 | Explore: `countryProductYear(productClass: HS12)` | HS12 (default) |
| 7 | South Africa minerals share 2019 | Explore: `countryProductYear(productClass: HS12)` | HS12 (default) |
| 8 | France services share 2022 | Explore: services treemap | N/A (services) |
| 10 | Peru top 3 mineral products 2016 | Explore: `countryProductYear(productClass: HS12)` | HS12 (default) |
| 11 | UK top 3 service sectors 2019 | Explore: services treemap | N/A (services) |
| 12 | Switzerland services % 2022 | Explore: services share | N/A (services) |
| 13 | Thailand Travel services share 2022 | Explore: services detail | N/A (services) |
| 14 | Netherlands Transport services share 2017 | Explore: services detail | N/A (services) |
| 17 | Japan main export destinations 2021 | Explore: `countryCountryYear` | HS12 (default) |
| 22 | China electronics market share | Explore: `countryProductYear` / `productYear` | HS12 (default) |
| 24 | Germany automotive global market share | Explore: `countryProductYear` / `productYear` | HS12 (default) |
| 26 | Vietnam export CAGR 2010-2020 | Explore: `countryYear` series | HS12 (default) |
| 30 | Switzerland pharma growth 2010-2020 | Explore: `countryProductYear` series | HS12 (default) |
| 32 | Brazil ECI trend 15 years | Country Pages: `countryYearRange` | HS (=HS92, only option) |
| 34 | South Africa top 5 complex products | Explore: product PCI ranking | HS12 (default) |
| 45 | Tuvalu exports 2020 | Explore: `countryYear` (sparse data) | HS12 (default) |
| 47 | Germany→Germany self-exports | Explore: `countryCountryYear` (should be 0/n/a) | HS12 (default) |
| 49 | Products >$1T for single country 2022 | Explore: `productYear` filter | HS12 (default) |
| 50 | Liechtenstein exports + ECI | Explore + Country Pages | Mixed |
| 51 | Japan bananas RCA | Explore: `countryProductYear` | HS12 (default) |
| 52 | South Sudan top exports 2015 | Explore: `countryYear` (sparse data) | HS12 (default) |

**Note on classification**: Since the agent now defaults to HS12, GT should use HS12 for Explore API queries. For Country Pages API queries (like Q32), use `HS` (the only supported option). For services questions, classification is N/A. Eight questions targeting Country Pages (Q32, Q33, Q37, Q38, Q41, Q43, Q44, Q50) have already been annotated with explicit HS92 classification instructions in their question text and carry `expected_api_target`/`expected_classification` metadata.

### Tier C: Complex analytical GT (23 questions)

Multi-step, comparative, or trend-based. Collect the key quantitative anchors from APIs even if full narrative can't be machine-verified.

| Q | Summary | Approach |
|---|---------|---------|
| 9 | Chile minerals vs services share | Two Explore API calls, compare shares |
| 15 | Ireland ICT vs Travel service growth | Explore overtime API, two service sectors |
| 16 | Spain service trends over decade | Explore overtime API, multiple years |
| 18 | Australia exports to SE Asia % | Explore bilateral data, sum region |
| 19 | Kenya trade balance top 10 partners | Multiple Explore bilateral calls |
| 20 | Singapore re-exports analysis | Complex — may need expected_behavior |
| 21 | Australia iron ore market share Asia vs global | Explore market share by region |
| 23 | South Korea semiconductor decade | Explore overtime market share |
| 27 | Netherlands vs Sweden export growth | Two-country comparison |
| 28 | Russia mineral fuels share change | Explore overtime sectoral |
| 29 | Turkey sector growth contributions | Explore sectoral overtime |
| 31 | Colombia global market share decade | Explore overtime market share |
| 33 | Vietnam complexity vs regional peers | Multi-country ECI (Country Pages for ECI) |
| 35 | Indonesia high-complexity share trend | Explore overtime product complexity |
| 36 | Poland high-tech exports 20 years | Explore overtime product-level |
| 37 | Egypt COI vs regional competitors | Multi-country COI (Country Pages) |
| 38 | South Korea complexity industries | Country Pages + Explore |
| 39 | Malaysia complexity vs global avg | Global comparison |
| 40 | Mexico diversification sectors decade | Multi-step |
| 41 | Portugal Product Space opportunities | Country Pages product space |
| 42 | Argentina diversification risks | Analytical, partial GT |
| 43 | Ghana diversification potential | Country Pages + Explore |
| 44 | Kazakhstan resource vs complexity | Country Pages + Explore |

**Approach**: Collect key quantitative data points (specific ECI values, product rankings, growth rates, share percentages) from APIs. This enables `ground_truth` judging on core facts while allowing `plausibility` judging on interpretation. Some questions (Q20 re-exports) may ultimately need `expected_behavior` annotations rather than data GT.

### Ground Truth Collection: API vs Browser

Per the collection guides, approximately **85% of data points** can be collected via direct GraphQL API calls, with the remaining **~15% requiring browser extraction** for client-rendered narrative text.

**Rule of thumb:** Numbers/ranks/enums → API. Narrative sentences → Browser.

#### API-Sourced Data Points (Layer 1)

The existing collection scripts (`collect_country_page_data.py`, `collect_explore_page_data.py`) handle these. All API-collected GT carries `source: "atlas_country_page"` or `source: "atlas_explore_page"` in the `results.json` metadata.

#### Browser-Sourced Data Points (Layer 2)

The Atlas Country Pages site is a **JavaScript SPA** (React). All 12 "subpages" are sections rendered on a single page load — subpage URLs function as anchor links, not separate page loads. This means ~15-20 GraphQL requests fire on initial page load. The data is there via API for numbers, but certain narrative text is generated client-side and requires browser extraction.

**Browser-required data points** (from `atlas_country_pages_exploration.md` and `country_page_collection_guide.md`):

| Data Point | Subpage | Example Value | Why Browser-Only |
|-----------|---------|---------------|-----------------|
| GDP per capita 5-year growth average | `/countries/{id}` (intro) | "averaged 2.6% over the past five years" | Client-generated narrative text |
| Regional growth comparison | `/countries/{id}` (intro) | "above"/"below" regional average | Client-generated narrative |
| Complexity-income relationship | `/countries/{id}` (intro) | "slightly more complex than expected" | Client-generated narrative |
| Growth speed descriptor | `/countries/{id}` (intro) | "slowly", "moderately" | Client-generated enum rendering |
| Export growth rate (5-year) | `/countries/{id}/export-basket` | "3.2% annual average" | Text section on export-basket page |
| Non-oil export growth rate | `/countries/{id}/export-basket` | "4.1% annual average" | Text section on export-basket page |
| Export growth pattern | `/countries/{id}/growth-dynamics` | "troubling", "promising", "static" | Client-generated narrative |
| Sectors driving export growth | `/countries/{id}/growth-dynamics` | "largest contribution from low complexity products" | Client-generated narrative |
| Structural transformation status | `/countries/{id}/market-share` | "started the process of structural transformation" | Client-generated narrative |
| Market share growth drivers | `/countries/{id}/market-share` | "export growth driven by expanding market share" | Client-generated narrative |
| ECI rank change text | `/countries/{id}` (intro) | "improving 3 positions" | Client-generated from lookback data |
| Complexity trend driver | `/countries/{id}` (intro) | "driven by diversifying its exports" | Client-generated narrative |
| Strategic approach description | `/countries/{id}/strategic-approach` | Full paragraph describing the approach | Client-generated narrative |
| Sectors by complexity level | `/countries/{id}/export-complexity` | "largest exports in low complexity" | Client-generated narrative |
| High-potential sectors | `/countries/{id}/product-table` | Lists of sectors from text | Client-generated narrative |

**Browser extraction workflow** (using Claude in Chrome):
1. Navigate to `https://atlas.hks.harvard.edu/countries/{m49_code}/{subpage}`
2. Wait **~4-5 seconds** for JavaScript rendering (the site loads all data via GraphQL on initial page load)
3. Use `get_page_text` to extract the full page text
4. Locate the specific narrative section and record the relevant text
5. For **interactive elements** (tooltips, hover data, treemap hover): use mouse actions to hover over specific elements, then capture the tooltip content
6. **Treemaps are `<canvas>` elements** — product data inside treemaps is NOT accessible via DOM queries; use the GraphQL API instead
7. Store the result in `results.json` with `source: "atlas_country_page"` and the relevant `atlas_url`

**Explore page browser data points** are fewer (most Explore data is fully API-accessible), but tooltips on canvas-rendered visualizations (treemaps, scatter plots, product space) require hovering over interactive elements.

**Existing GT examples** (showing what browser-collected data looks like in the system):
- Q139 (GDP growth average): `{"metric": "GDP per capita growth (5-year average)", "value": "2.6%"}`
- Q155 (Export growth pattern): `{"metric": "Export growth pattern", "value": "troubling", "description": "Spain has seen a troubling pattern..."}`
- Q166 (Strategic approach): `{"metric": "Strategic approach description", "approach": "Light Touch Approach", "description": "Kenya's existing knowhow affords..."}`

---

## Step 4: Regenerate Existing Ground Truth

### 4a. Explore Page GT — HS12 (74 questions, IDs 170-243)
```bash
PYTHONPATH=$(pwd) uv run python evaluation/collect_explore_page_data.py --product-class HS12
```
- Regenerates all product-specific GT with HS12 classification
- Rate limited at 2 req/s, 200 req/min
- User-Agent: `ask-atlas-gt`

### 4b. Explore Page GT — HS92 (3 questions, IDs 244-246)
```bash
PYTHONPATH=$(pwd) uv run python evaluation/collect_explore_page_data.py --product-class HS92 --questions 244 245 246
```
These explicitly ask about HS92, so they stay as HS92.

### 4c. Country Page GT — No regeneration needed (109 questions, IDs 61-169)
The Country Pages API only supports `HS` (=HS92). The existing GT already uses HS92 data. The agent will need to use HS92 when querying the Country Pages API regardless of its default, because HS12 is not available there. **No GT action needed.**

**Already completed:** Question text annotations, metadata fields, agent prompt fixes, and judge classification notes have been implemented as additional safeguards (see "Completed: HS92 Classification Annotations" above).

### 4d. SQL GT — Product-specific only (2 questions: Q2, Q6)
```bash
docker compose -f docker-compose.test.yml up -d --wait
# Execute updated SQL files for Q2 and Q6
```
Q1 and Q25 are country aggregates (identical across classifications) — no change needed.

### 4e. New classification-diversity questions (6 questions, IDs 247-252)
Run targeted API queries per Step 2.

### 4f. Missing GT remediation (56 questions, IDs 3-60)

**Collection strategy by layer** (following the collection guides):

| Layer | Coverage | Method | Questions |
|-------|----------|--------|-----------|
| **API script** | ~85% of data points | Extend `collect_explore_page_data.py` with new countries | Tier B (23) + quantitative anchors from Tier C (23) |
| **Browser extraction** | ~15% of data points | Claude in Chrome for client-rendered narrative text | Tier C questions needing narrative: Q33 (complexity comparison text), Q37 (COI comparison text), Q38 (complexity industries text), Q41 (product space opportunities text), Q43 (diversification text), Q44 (complexity text). Also Q20 (re-export narrative) if collectible. |
| **expected_behavior** | Refusal questions | Manual annotation in `eval_questions.json` | Tier A (10) |

**Note:** Many Tier C questions need *both* layers — API for quantitative anchors (ECI values, rankings, growth rates) and browser for narrative descriptions. Record both in the same `results.json`, prioritizing the quantitative data since the judge weights factual correctness highest.

**New countries to add to collection scripts** (16 countries beyond existing 8):

| Country | M49 ID | Needed for |
|---------|--------|-----------|
| Japan | 392 | Q17, Q51 |
| Canada | 124 | Q5 |
| Chile | 152 | Q9 |
| Peru | 604 | Q10 |
| UK | 826 | Q11 |
| France | 250 | Q8 |
| Singapore | 702 | Q3, Q20 |
| Thailand | 764 | Q13 |
| Netherlands | 528 | Q14, Q27 |
| Switzerland | 756 | Q12, Q30 |
| South Africa | 710 | Q7, Q34 |
| Vietnam | 704 | Q26, Q33 |
| China | 156 | Q22 |
| Tuvalu | 798 | Q45 |
| Liechtenstein | 438 | Q50 |
| South Sudan | 728 | Q52 |

Additional countries for Tier C: Ireland (372), Australia (36), South Korea (410), Russia (643), Turkey (792, already included), Colombia (170), Indonesia (360), Egypt (818), Poland (616), Malaysia (458), Mexico (484), Portugal (620), Argentina (32), Ghana (288), Kazakhstan (398).

---

## Step 5: Run Eval and Compare

### Questions to run (55 total)

**32 previously failed questions** (measure improvement):
Q1, Q57, Q85, Q86, Q93, Q94, Q97, Q103, Q113, Q127, Q128, Q129, Q130, Q131, Q133, Q135, Q139, Q147, Q168, Q170, Q171, Q185, Q186, Q208, Q210, Q213, Q217, Q224, Q225, Q226, Q230, Q238

**17 stratified regression checks** (previously passed — diverse categories):
Q2, Q4, Q17, Q25, Q32, Q53, Q58, Q61, Q75, Q98, Q101, Q107, Q121, Q190, Q195, Q214, Q240

**6 new classification-diversity questions** (new baseline):
Q247-Q252

### Command
```bash
PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py \
  --questions 1 2 4 17 25 32 53 57 58 61 75 85 86 93 94 97 98 101 103 107 113 121 127 128 129 130 131 133 135 139 147 168 170 171 185 186 190 195 208 210 213 214 217 224 225 226 230 238 240 247 248 249 250 251 252
```

### After the eval completes
1. Compare per-question verdicts against `20260227T151832Z` baseline run
2. Compute: how many of the 32 failures flipped to pass/partial
3. Compute: how many of the 17 regression checks stayed pass
4. Report category-level and overall pass rate delta
5. Note new classification-diversity question results as baseline

---

## Execution Order Summary

| Phase | What | Effort | Blocking? | Status |
|-------|------|--------|-----------|--------|
| **Pre** | HS92 annotations, metadata, agent prompts, judge pipeline | ~2h | No — prerequisite safeguard | **Done** |
| **1a-c** | Update Explore collection script (HS12 param, rate limiting, User-Agent) | ~1.5h | Yes — blocks GT regeneration | Pending |
| **1d** | Update SQL queries (Q2, Q6 only) | ~15min | Yes — blocks SQL GT | Pending |
| **1e** | Add rate limiting to Country Page script | ~30min | No | Pending |
| **2** | Write 6 new classification-diversity questions + collect GT | ~1h | No — can do in parallel | Pending |
| **3 Tier A** | Add expected_behavior for 10 refusal Qs | ~30min | No | Pending |
| **3 Tier B** | Collect GT for 23 straightforward Qs (extend scripts for new countries) | ~2h | No | Pending |
| **3 Tier C** | Collect GT for 23 complex Qs (API + browser) | ~4-6h | No — can do incrementally | Pending |
| **4a-b** | Regenerate Explore Page GT (77 Qs) | ~30min (script run) | Yes — blocks eval | Pending |
| **4d** | Re-run SQL GT (Q2, Q6) | ~15min | Yes — blocks eval | Pending |
| **5** | Run 55-question eval + compare | ~60min (script run) | — | Pending |

**Critical path:** Steps 1a-c → 4a-b → 5. Everything else can run in parallel or be deferred.

---

## Previous Eval Results Summary (for reference)

**Baseline:** 54.1% pass rate (40/74), avg score 3.17/5.0

**Root causes of 32 failures:**
- Services data blind spot: Q1, Q85, Q86, Q94, Q208, Q210 (6)
- Data recency / routing: Q170, Q171, Q185, Q186, Q213, Q217, Q230 (7)
- Country page-only metrics: Q93, Q97, Q103, Q133, Q135, Q139, Q147 (7)
- Growth opportunities: Q127, Q128, Q129, Q130, Q131, Q168, Q224, Q225, Q226 (9)
- Other: Q57 (bilateral services refusal), Q238 (EU group), Q113 (error) (3)

**Improvements since then:** Services support, year-gap routing, anti-hallucination (tool_call_nudge), missing GraphQL fields (newProductCount, policyRecommendation), group/regional support, natural resource PCI documentation.

**Projected after:** Conservative ~67% pass, Optimistic ~76% pass.

---

## Files Already Modified (completed)

| File | Change | Status |
|------|--------|--------|
| `evaluation/eval_questions.json` | HS92 classification annotations (97 CP + 8 remediation questions); `expected_api_target` + `expected_classification` metadata (117 questions) | Done |
| `evaluation/run_eval.py` | Thread `expected_api_target`, `expected_classification` through `_load_questions_meta()`; build `classification_note` and pass to `judge_answer()` | Done |
| `evaluation/judge.py` | Accept optional `classification_note` param; inject into GT judging system prompt; flag `classification_note_applied` in verdict | Done |
| `src/graphql_pipeline.py` | Fix `PRODUCT_CLASS_DESCRIPTION` (HS12 default note, CP constraint); fix `API_TARGET_DESCRIPTION` (CP constraint); change 6 Explore builders from HS92 → HS12 default | Done |

## Files Still to Modify

| File | Change |
|------|--------|
| `evaluation/collect_explore_page_data.py` | Add `--product-class` param (default HS12), rate limiting, `User-Agent: ask-atlas-gt` |
| `evaluation/collect_country_page_data.py` | Add rate limiting and `User-Agent: ask-atlas-gt` only (keep `productClass: HS`) |
| `evaluation/questions/2/queries/01.sql` | `hs92.*` → `hs12.*` tables |
| `evaluation/questions/6/queries/01.sql` | `hs92.*` → `hs12.*` tables |
| `evaluation/eval_questions.json` | Add Q247-Q252; add `expected_behavior` for 10 refusal Qs (Tier A) |
| `evaluation/results/{id}/ground_truth/results.json` | Regenerated for Explore Page Qs (170-246) + new Qs |

---

## Reference: Data Extraction Sources

The following documents describe the full set of extractable data points and how they map to API queries vs browser interactions. Consult these when implementing collection for new questions.

| Document | Location | What It Covers |
|----------|----------|---------------|
| `atlas_country_pages_exploration.md` | `evaluation/` | 12 country page subpages, 62 extractable data points, GraphQL→website component mappings, tooltip fields, interactive elements |
| `atlas_explore_pages_exploration.md` | `evaluation/` | 7 Explore visualization types, URL parameters, tooltip data, Products vs Locations modes, 27 unique data points not on country pages |
| `graphql_api_official_docs.md` | `evaluation/` | Official API docs + introspection: 27 Explore API queries, complete type schemas (40 types), enum values, rate limits, example queries |
| `graphql_api_guide.md` | `src/docs/` | When to use each API, ID format differences, Country Pages API catalog (25 queries, `countryProfile` 46 fields, `countryLookback` 13 fields), response size tiers, broken queries |
| `country_page_collection_guide.md` | `evaluation/` | Question templates by category, extraction methods (API vs Browser), country assignment matrix, GT recording format |
| `explore_page_collection_guide.md` | `evaluation/` | Question templates for Explore-unique data, country×product pairing matrix, deduplication with country pages |

### Key Data Architecture Facts (from the exploration docs)

- **Country Pages are a single-page SPA**: All 12 subpages load on one page visit (~15-20 GraphQL requests to `/api/countries/graphql`). Subpage URLs are anchor links.
- **Treemaps are `<canvas>` elements**: Product data inside treemaps is NOT DOM-accessible. Always use the API (`treeMap(facet: CPY_C)` or `countryProductYear`) for treemap data.
- **Explore page default classification is HS 1992**: The Explore website defaults to "HS 1992" in the settings panel for all visualization types (treemap, overtime, marketshare, productspace, feasibility).
- **Country Pages show "UN COMTRADE (HS 1992) and the IMF's WEO data"** at the bottom of the introduction section — confirming HS92 as the classification.
- **Country Pages product IDs** use format `product-HS-{id}` (e.g., `product-HS-726`), while **Explore API** uses bare integers (`productId: 726`).
- **Frontier countries** (USA, Germany) do not have growth opportunities or product table visualizations on their country pages ("Visualization not available for highest complexity countries").
- **No server-side sort or limit**: Both APIs return all matching items. The client must sort and truncate for "top N" queries.

---

## Verification

1. **After updating the Explore script**: Run for 2-3 test questions to verify HS12 data looks correct and rate limiting works. Confirm `User-Agent: ask-atlas-gt` header is sent.
2. **After regenerating Explore GT**: Use Claude in Chrome to browse the Atlas Explore page with HS12 selected in Settings and compare displayed values (tooltip hover data) against API-collected GT for 5-10 questions.
3. **After collecting browser GT**: For narrative data points, use Claude in Chrome to visit the relevant country page subpage, wait ~5s for JS rendering, and compare the extracted text against the GT `description` field.
4. **Classification cross-check**: Verify that questions explicitly asking about HS92 (Q244-Q246), SITC, or services were NOT overridden to HS12 in GT collection. Verify Q26 (Vietnam CAGR 2010-2020) handles the pre-2012 HS12 data gap correctly.
5. **After running eval**: Compare per-question verdicts against `20260227T151832Z` baseline run.
6. **Regression check**: Verify the 17 regression questions still pass.
7. **Classification-diversity**: Verify new Q247-Q252 get reasonable verdicts.
8. **Metadata check**: Run `python3 -c "import json; ..."` to verify all expected questions have `expected_api_target` and `expected_classification` fields.
