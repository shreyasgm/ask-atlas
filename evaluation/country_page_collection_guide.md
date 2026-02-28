# Atlas Country Page — Ground Truth Collection Guide

This guide describes how to systematically collect ground truth Q&A pairs from the Atlas of Economic Complexity country pages and integrate them into the eval system.

---

## Table of Contents

1. [Quick Reference & Prerequisites](#1-quick-reference--prerequisites)
2. [Country Selection & Assignment Matrix](#2-country-selection--assignment-matrix)
3. [Question Templates by Category](#3-question-templates-by-category)
4. [Ground Truth Recording Format](#4-ground-truth-recording-format)
5. [Integration with the Eval System](#5-integration-with-the-eval-system)
6. [Batch Workflow](#6-batch-workflow)
7. [Scale & Time Estimate](#7-scale--time-estimate)

---

## 1. Quick Reference & Prerequisites

> **Full technical reference**: See `atlas_country_pages_exploration.md` for complete
> URL structure (12 subpages), GraphQL API details (25 query types, type schemas,
> enum values), and sample queries with verified responses.

**Essential quick-reference for collection:**

- **Country Pages API endpoint**: `POST https://atlas.hks.harvard.edu/api/countries/graphql`
- **ID format**: `"location-{m49_code}"` (e.g., `"location-404"` for Kenya)
- **Rate limit**: ≤ 120 req/min, include `User-Agent` header
- **No authentication required**
- **Product classification: `HS` and `SITC` only.** The Country Pages API `ProductClass` enum has exactly two values: `HS` (equivalent to HS92) and `SITC`. There is no `HS12` or `HS22` — passing either will return a GraphQL validation error. All product data from this API is HS 1992 data.

### Technical Notes

- **The site is a JavaScript SPA** (React). Static HTTP fetches won't work for page content, but the GraphQL API provides ~85% of data points without browser automation.
- **For browser-based extraction**: Wait ~4-5 s after navigation for JS rendering.
- **Treemaps are `<canvas>` elements** — use the API instead of DOM queries for treemap data.
- **Subpages 10 and 11** (`growth-opportunities`, `product-table`) are unavailable for frontier countries (e.g., USA, Germany).

### URL Structure (summary)

- **Base URL**: `https://atlas.hks.harvard.edu/countries/{m49_code}`
- **Country IDs**: M49 codes — e.g., 840 = USA, 404 = Kenya, 724 = Spain. **Total**: 145 countries.
- **12 subpages** per country (see `atlas_country_pages_exploration.md` § "Page-by-Page Exploration" for full table)
- **Explore API** (separate): `POST https://atlas.hks.harvard.edu/api/graphql` — uses integer IDs (`countryId: 404`), covers bilateral trade, 6-digit products, groups. See `atlas_explore_pages_exploration.md`.

### Data Points Available via API vs Browser

| Source | Data Points | % of Total | Examples |
|--------|------------|-----------|---------|
| **GraphQL API** | ~50-56 of 62 | ~85% | GDP, ECI, exports, treemap products, diversity grade, COI, growth projection, rankings |
| **Browser only** | ~6-12 of 62 | ~15% | Client-rendered narrative text: growth pattern descriptions, structural transformation text, strategic approach descriptions, complexity-income relationship text, comparison to regional averages |

**Rule of thumb:** Numbers/ranks/enums → API. Narrative sentences → Browser.

---

## 2. Country Selection & Assignment Matrix

### Selected Countries (8)

| Country | ISO ID | Income Level | Complexity Tier | Role |
|---------|--------|-------------|-----------------|------|
| USA | 840 | High-income | Frontier | Frontier edge cases (viz unavailable) |
| Germany | 276 | High-income | Frontier | Second frontier for diversity |
| Spain | 724 | High-income | Moderate-high | High-income, non-frontier |
| Turkiye | 792 | Upper-middle-income | Moderate | Middle-income complexity |
| Brazil | 76 | Upper-middle-income | Moderate-low | Large developing economy |
| India | 356 | Lower-middle-income | Moderate | Large, diverse emerging economy |
| Kenya | 404 | Lower-middle-income | Low | Lower-income, light-touch strategy |
| Ethiopia | 231 | Low-income | Low | Low-income, strategic bets |

### Deduplication Rules

Each data point type should use **1-2 countries**, not all 8. This avoids redundant "same question, different country" patterns while ensuring every data point is tested.

- **Spread countries across categories.** No single country should dominate.
- **Use frontier countries (USA, Germany) specifically** for data points that differ for high-complexity countries (e.g., "growth opportunities not available") and for high-value stat comparisons.
- **Use developing countries (Kenya, Ethiopia) specifically** for data points only available to non-frontier countries (e.g., product opportunities table, growth opportunities scatter).
- **Use middle-income countries (Turkiye, Brazil, Spain, India)** for the bulk of standard data points.

### Country-to-Category Assignment

This matrix assigns primary countries to each question category. Follow it to maintain diversity.

| Category | Primary Countries | Rationale |
|----------|------------------|-----------|
| Country Profile Overview | Kenya (404), Spain (724) | Contrast low-income vs high-income |
| Total Export Values | Brazil (76), Germany (276) | Large exporters with different profiles |
| Sectoral Export Composition | India (356), USA (840) | Diverse vs frontier economy treemaps |
| Trade Partners & Market Position | Turkiye (792), Ethiopia (231) | Mid-size and small economy trade patterns |
| Growth & Performance | Spain (724), India (356) | Different growth dynamics |
| Economic Complexity | Brazil (76), Turkiye (792) | Mid-range complexity, interesting rank changes |
| Diversification Strategies | Kenya (404), Ethiopia (231) | Developing countries with diversification needs |
| Growth Opportunities (non-frontier) | Kenya (404), India (356) | Light-touch and parsimonious strategies |
| Frontier Edge Cases | USA (840), Germany (276) | Verify "viz not available" behavior |
| Summary Stats | Turkiye (792), Brazil (76) | Cross-check summary against detail pages |

---

## 3. Question Templates by Category

Each template below specifies:
- **Question template** with `{country}` placeholder
- **Subpage URL** where the answer is found
- **Extraction method** — prefixed with **API** (GraphQL query, see `atlas_country_pages_exploration.md`) or **Browser** (requires page rendering)
- **Category** and **difficulty** for the question metadata

Data point numbers (DP#) reference the catalog in `evaluation/atlas_country_pages_exploration.md`.

---

### 3.1 Country Profile Overview

**Subpage**: `/countries/{id}` (main page)
**Assigned countries**: Kenya (404), Spain (724)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 1 | 1 | What is the GDP per capita of {country}? | **API**: `countryProfile.latestGdpPerCapita { quantity year }` | easy |
| 2 | 2 | What is the GDP per capita (PPP) of {country}? | **API**: `countryProfile.latestGdpPerCapitaPpp { quantity year }` | easy |
| 3 | 3 | What is the GDP per capita rank of {country}? | **API**: `countryProfile.latestGdpPerCapitaRank { quantity year }` → "{quantity}th of 145" | easy |
| 4 | 4 | What income classification does {country} have on the Atlas? | **API**: `countryProfile.incomeClassification` (enum: `LowerMiddle`, etc.) | easy |
| 5 | 5 | What is the population of {country}? | **API**: `countryProfile.latestPopulation { quantity year }` | easy |
| 6 | 6 | What is the average GDP per capita growth rate of {country} over the past five years? | **Browser**: Read introductory text for 5-year average (client-generated narrative) | medium |
| 7 | 7 | How does {country}'s GDP per capita growth compare to its regional average? | **Browser**: Read text for "above"/"below" regional average (client-generated narrative) | medium |
| 8 | 11 | What is the projected GDP per capita growth rate of {country} over the next decade? | **API**: `countryProfile.growthProjection` (decimal, e.g. 0.03383 = 3.4%) | easy |
| 9 | 12 | What is {country}'s growth projection rank? | **API**: `countryProfile.growthProjectionRank` → "{value}th of 145" | easy |
| 10 | 13 | Is {country} more or less complex than expected for its income level? | **Browser**: Read text for complexity-income relationship (client-generated narrative) | medium |
| 11 | 14 | How fast is {country} projected to grow according to the Atlas? | **Browser**: Read text for speed descriptor (e.g., "slowly", "moderately") — client-generated | easy |

---

### 3.2 Total Export Values

**Subpage**: `/countries/{id}/export-basket`
**Assigned countries**: Brazil (76), Germany (276)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 12 | 15 | What is the total value of exports for {country} according to the Atlas country page? | **API**: `countryProfile.exportValue` (raw number, format as USD) | easy |
| 13 | 16 | What is {country}'s exporter rank? | **API**: `countryProfile.exportValueRank` → "{value}th of 145" | easy |
| 14 | 17 | What is {country}'s current account balance? | **API**: `countryProfile.currentAccount { quantity year }` (negative = deficit) | easy |
| 15 | 18 | What is the 5-year average export growth rate for {country}? | **Browser**: Read text section for export growth rate (client-generated narrative) | medium |
| 16 | 19 | What is the non-oil export growth rate for {country}? | **Browser**: Read text section for non-oil export growth (client-generated narrative) | medium |
| 17 | 20 | What is the total value of imports for {country} according to the Atlas? | **API**: `countryProfile.importValue` (raw number, format as USD) | easy |
| 18 | 21 | Does {country} have a trade surplus or trade deficit? | **API**: Derive from `countryProfile.exportValue` vs `countryProfile.importValue` | easy |

---

### 3.3 Sectoral Export Composition

**Subpage**: `/countries/{id}/export-basket`
**Assigned countries**: India (356), USA (840)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 19 | 24 | What is the top product in {country}'s export basket and what share does it represent? | **API**: `treeMap(facet: CPY_C, ...)` → sort by `exportValue`, compute share from total | easy |
| 20 | 24 | What are the top 3 products in {country}'s export basket by share? | **API**: `treeMap(facet: CPY_C, ...)` → top 3 by `exportValue`, compute shares | medium |
| 21 | 25 | What is the gross export value of {top_product} from {country}? | **API**: `treeMap(facet: CPY_C, ...) { ... on TreeMapProduct { product { shortName } exportValue } }` | medium |
| 22 | 26 | What is the HS92 code for {top_product} exported by {country}? | **API**: `treeMap(facet: CPY_C, ...) { ... on TreeMapProduct { product { code } } }` | hard |

---

### 3.4 Trade Partners & Market Position

**Subpage**: `/countries/{id}/export-basket` (text section) and `/countries/{id}/market-share`
**Assigned countries**: Turkiye (792), Ethiopia (231)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 23 | 22 | What are the top 3 export destination countries for {country}? | **API**: `treeMap(facet: CCY_C, ...)` → top 3 partner locations by trade value | medium |
| 24 | 23 | What are the top 3 import origin countries for {country}? | **API**: `treeMap(facet: CCY_C, ...)` with import parameters → top 3 by value | medium |
| 25 | 36 | In which sector does {country} have the largest global market share? | **API**: `countryProfile.marketShareMainSector { shortName code }` | easy |
| 26 | 37 | What is {country}'s total share of global trade in its largest sector? | **API**: `countryProfile.marketShareMainSectorPositiveGrowth` or `countryYearRange` | easy |
| 27 | 38 | What is {country}'s global market share in {sector} as of the latest year? | **API**: `countryYearRange(...)` → filter by sector, read latest year value | medium |
| 28 | 40 | Has {country} completed its structural transformation according to the Atlas? | **Browser**: Read text description on market-share page (client-generated narrative) | medium |
| 29 | 42 | Is {country}'s export growth driven by expanding global market share or by concentrating in a growing sector? | **Browser**: Read text description on market-share page (client-generated narrative) | hard |

---

### 3.5 Growth & Performance

**Subpage**: `/countries/{id}/growth-dynamics` and `/countries/{id}/market-share`
**Assigned countries**: Spain (724), India (356)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 30 | 31 | What is the export growth rate (CAGR) for {product} from {country}? | **API**: `allCountryProductYear(...)` → compute CAGR from time series values | hard |
| 31 | 32 | What is {country}'s ECI value according to the growth dynamics chart? | **API**: `countryProfile.latestEci` (Float) | medium |
| 32 | 33 | How would you describe {country}'s export growth pattern? | **Browser**: Read text on growth-dynamics page (e.g., "static", "promising") — client-generated | medium |
| 33 | 34 | Which sectors or products are driving {country}'s export growth? | **Browser**: Read text description on growth-dynamics page (client-generated narrative) | medium |
| 34 | 35 | What is the gross country export value of {product} from {country}? | **API**: `treeMap(facet: CPY_C, ...) { ... on TreeMapProduct { product { shortName } exportValue } }` | hard |
| 35 | 39 | How has {country}'s market share in {sector} changed from 1996 to the latest year? | **API**: `countryYearRange(...)` → read sector values at 1996 and latest year | hard |
| 36 | 41 | Which sectors are driving {country}'s export growth according to the market share page? | **Browser**: Read text description on market-share page (client-generated narrative) | medium |

---

### 3.6 Economic Complexity

**Subpage**: `/countries/{id}` (main page) and `/countries/{id}/export-complexity`
**Assigned countries**: Brazil (76), Turkiye (792)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 37 | 8 | What is {country}'s Economic Complexity Index (ECI) ranking? | **API**: `countryProfile.latestEciRank` → "{value}th of 145" | easy |
| 38 | 9 | How has {country}'s ECI ranking changed over the past decade? | **Browser**: Read text on main page (e.g., "worsening 7 positions") — client-generated narrative | medium |
| 39 | 10 | What is driving {country}'s complexity trend? | **Browser**: Read text on main page for complexity trend driver — client-generated narrative | medium |
| 40 | 27 | What is {country}'s ECI ranking according to the export complexity page? | **API**: `countryProfile.latestEciRank` (same field, different page) | easy |
| 41 | 28 | How many positions has {country}'s ECI rank changed over 10 years? | **API**: `countryYearRange(...)` → compute rank difference over 10 years | medium |
| 42 | 29 | What is the Product Complexity Index (PCI) of {product} exported by {country}? | **API**: `treeMap(facet: CPY_C, ..., mergePci: true) { ... on TreeMapProduct { pci } }` | hard |
| 43 | 30 | Which sectors contain {country}'s largest exports by complexity level? | **Browser**: Read text on export-complexity page (client-generated narrative) | medium |

---

### 3.7 Diversification Strategies

**Subpage**: `/countries/{id}/new-products`
**Assigned countries**: Kenya (404), Ethiopia (231)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 44 | 43 | What is {country}'s economic diversification grade? | **API**: `countryProfile.diversificationGrade` (letter: "A", "B", etc.) | easy |
| 45 | 44 | What is {country}'s diversity rank? | **API**: `countryProfile.diversityRank` → "{value}th of 145" | easy |
| 46 | 45 | How has {country}'s diversity rank changed over the past 15 years? | **API**: `countryYearRange(...)` → compute rank difference over 15 years | medium |
| 47 | 46 | How many new products has {country} started exporting in the last 15 years? | **API**: `newProductsCountry(...)` → count of new products | easy |
| 48 | 47 | What is the per-capita income contribution of {country}'s new products? | **API**: `countryProfile.newProductExportValuePerCapita` | medium |
| 49 | 48 | What is the total value of {country}'s new export products? | **API**: `countryProfile.newProductExportValue` | medium |
| 50 | 49 | What share of {country}'s export basket is made up of new products? | **API**: Derive from `countryProfile.newProductExportValue / countryProfile.exportValue` | medium |
| 51 | 50 | How does {country}'s new product count compare to peer countries? | **API**: `newProductsComparisonCountries(...)` → comparison table data | hard |

---

### 3.8 Product Space & Strategic Approach

**Subpage**: `/countries/{id}/paths` and `/countries/{id}/strategic-approach`
**Assigned countries**: Kenya (404), Turkiye (792)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 52 | 51 | How many products does {country} export with a revealed comparative advantage (RCA > 1)? | **API**: `countryProfile.diversity` (count of products with RCA > 1) | easy |
| 53 | 52 | What is {country}'s Complexity Outlook Index rank? | **API**: `countryProfile.latestCoiRank` → "{value}th of 145" | easy |
| 54 | 53 | What strategic approach does the Atlas recommend for {country}? | **API**: `countryProfile.coiClassification` (enum: quadrant label) | easy |
| 55 | 54 | What does the Atlas's recommended strategic approach for {country} entail? | **Browser**: Read text description on strategic-approach page (client-generated narrative) | medium |

---

### 3.9 Growth Opportunities (Non-Frontier Countries Only)

**Subpage**: `/countries/{id}/growth-opportunities` and `/countries/{id}/product-table`
**Assigned countries**: Kenya (404), India (356)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 56 | 55 | What are the top product opportunities for {country} according to the Atlas? | **API**: `productSpace(productClass: HS, year: 2024, location: "location-{id}")` → filter by feasibility/opportunity gain | hard |
| 57 | 56 | What product selection strategy is shown by default for {country}'s growth opportunities? | **API**: `countryProfile.coiClassification` (determines default strategy) | easy |
| 58 | 57 | What are the top 5 products in {country}'s product opportunities table? | **API**: `productSpace(...)` → sort by opportunity gain, take top 5 | hard |
| 59 | 57 | What is the global size and 5-year growth rate for {top_product} in {country}'s product table? | **API**: `productYear(product: "{product_id}", year: 2024)` → `globalTradeValue`, compute growth from `productYearRange` | hard |
| 60 | 58 | Which sectors are identified as high-potential for {country}'s diversification? | **Browser**: Read text description on product-table page (client-generated narrative) | medium |

---

### 3.10 Frontier Edge Cases

**Subpage**: `/countries/{id}/growth-opportunities` and `/countries/{id}/product-table`
**Assigned countries**: USA (840), Germany (276)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 61 | 55 | Does the Atlas show growth opportunity products for {country}? | **Browser**: Navigate to growth-opportunities, read "Visualization not available" message | easy |
| 62 | 57 | Does the Atlas show a product opportunities table for {country}? | **Browser**: Navigate to product-table, read "Visualization not available" message | easy |

---

### 3.11 Summary Page Cross-Check

**Subpage**: `/countries/{id}/summary`
**Assigned countries**: Turkiye (792), Brazil (76)

| # | DP# | Question Template | Extraction Method | Difficulty |
|---|-----|------------------|-------------------|------------|
| 63 | 59 | What is the complexity rank change shown on {country}'s summary page? | **API**: `countryYearRange(...)` → compute ECI rank change (same data as export-complexity) | easy |
| 64 | 60 | How many new products are shown on {country}'s summary page? | **API**: `newProductsCountry(...)` → count (same data as new-products page) | easy |
| 65 | 61 | What growth projection is shown on {country}'s summary page? | **API**: `countryProfile.growthProjection` (same data as main page) | easy |
| 66 | 62 | What strategic approach is described on {country}'s summary page? | **API** + **Browser**: `countryProfile.coiClassification` for label; browser for description text | medium |

---

### Template Count Summary

| Category | Templates | Countries | Est. Questions |
|----------|-----------|-----------|---------------|
| Country Profile Overview | 11 | 2 | 11-22 |
| Total Export Values | 7 | 2 | 7-14 |
| Sectoral Export Composition | 4 | 2 | 4-8 |
| Trade Partners & Market Position | 7 | 2 | 7-14 |
| Growth & Performance | 7 | 2 | 7-14 |
| Economic Complexity | 7 | 2 | 7-14 |
| Diversification Strategies | 8 | 2 | 8-16 |
| Product Space & Strategic Approach | 4 | 2 | 4-8 |
| Growth Opportunities (non-frontier) | 5 | 2 | 5-10 |
| Frontier Edge Cases | 2 | 2 | 2-4 |
| Summary Cross-Check | 4 | 2 | 4-8 |
| **Total** | **66** | | **~66-132** |

---

## 4. Ground Truth Recording Format

### 4.1 `question.json` Schema

```json
{
  "question_id": "61",
  "user_question": "What is the GDP per capita of Kenya?",
  "category": "Country Profile Overview",
  "difficulty": "easy",
  "source": "atlas_country_page",
  "atlas_url": "https://atlas.hks.harvard.edu/countries/404"
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `question_id` | string | yes | Unique ID, starting at 61 (IDs 1-60 are taken) |
| `user_question` | string | yes | The natural-language question |
| `category` | string | yes | One of the category names from section 3 |
| `difficulty` | string | yes | `"easy"`, `"medium"`, or `"hard"` |
| `source` | string | yes | `"atlas_country_page"` — distinguishes from DB-derived questions |
| `atlas_url` | string | yes | The exact Atlas page URL where the answer is found |

**Notes on `atlas_url`:**
- Must be the specific subpage URL, not just the country root.
- For main page data: `https://atlas.hks.harvard.edu/countries/404`
- For export basket data: `https://atlas.hks.harvard.edu/countries/404/export-basket`
- This URL serves dual purposes: (1) verifiable reference for ground truth, (2) the eval system will compare the agent's suggested link against this URL.

### 4.2 `results.json` Schema

```json
{
  "question_id": "61",
  "execution_timestamp": "2026-02-21T15:30:00.000000+00:00Z",
  "source": "atlas_country_page",
  "atlas_url": "https://atlas.hks.harvard.edu/countries/404",
  "results": {
    "data": [
      {
        "metric": "GDP per capita",
        "value": "$2,274",
        "year": "2024",
        "rank": "116th of 145"
      }
    ]
  }
}
```

**Field descriptions:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `question_id` | string | yes | Matches the question's ID |
| `execution_timestamp` | string | yes | ISO 8601 timestamp of when data was extracted |
| `source` | string | yes | `"atlas_country_page"` |
| `atlas_url` | string | yes | Same URL as in question.json |
| `results.data` | array | yes | Array of extracted data objects |

**Data object fields vary by question type.** Common patterns:

- **Single metric**: `{ "metric": "...", "value": "...", "year": "..." }`
- **Ranked metric**: `{ "metric": "...", "value": "...", "rank": "...", "year": "..." }`
- **Trade partner**: `{ "rank": 1, "country": "...", "share": "..." }`
- **Product entry**: `{ "product_name": "...", "hs92_code": "...", "export_value": "...", "share": "..." }`
- **Comparison table**: `{ "country": "...", "new_products": ..., "usd_per_capita": "...", "usd_total": "..." }`
- **Text description**: `{ "description": "..." }` (for qualitative answers like strategic approach)
- **Boolean/status**: `{ "available": false, "message": "Visualization not available for highest complexity countries" }`

**Important:** No `execution_stats` block is needed for website-sourced results (no SQL queries were executed). The existing eval results include `execution_stats` because they come from DB queries. Omit it for country page questions.

---

## 5. Integration with the Eval System

### File Locations

| What | Where |
|------|-------|
| Master question list | `evaluation/eval_questions.json` |
| Individual question metadata | `evaluation/questions/{id}/question.json` |
| Ground truth results | `evaluation/results/{id}/ground_truth/results.json` |

### ID Numbering

- Existing questions: IDs 1-60
- New country page questions: **start at ID 61**
- Assign IDs sequentially as you create questions

### Adding to `eval_questions.json`

The master file has two top-level keys: `categories` and `questions`.

**Add new categories** (if not already present):

```json
"country_profile_overview": {
  "name": "Country Profile Overview",
  "description": "Questions about GDP, population, income classification, and growth projections from Atlas country pages"
}
```

Other new categories to add as needed: use the existing category naming pattern (snake_case ID, human-readable name).

**Add new questions** to the `questions` array:

```json
{
  "id": 61,
  "category_id": "country_profile_overview",
  "difficulty": "easy",
  "text": "What is the GDP per capita of Kenya?",
  "source": "atlas_country_page",
  "atlas_url": "https://atlas.hks.harvard.edu/countries/404"
}
```

### Directory Structure for Each New Question

```
evaluation/
  questions/
    61/
      question.json          # Question metadata (see schema in section 4.1)
  results/
    61/
      ground_truth/
        results.json         # Extracted website data (see schema in section 4.2)
```

**No SQL queries directory needed.** Existing questions have `queries/01.sql` and `queries/plan.txt` — country page questions do not need these since ground truth comes directly from the website.

### Scoring

Country page questions will be scored using the existing **ground truth mode** (factual correctness + data accuracy). The `atlas_url` field enables a future **link accuracy** scoring dimension where the agent's suggested URL is compared against the ground truth URL.

---

## 6. Batch Workflow (Three-Layer Approach)

The GraphQL API (see `atlas_country_pages_exploration.md`) fundamentally changes the collection approach. Instead of visiting 80+ subpages via browser automation, use a **three-layer strategy**:

1. **Layer 1 — GraphQL API script** (~85% of data points, no browser needed)
2. **Layer 2 — Browser text extraction** (~15% of data points, narrative text only)
3. **Layer 3 — Screenshot verification** (spot-check accuracy)

---

### Layer 1: GraphQL API Script (Primary)

Write a Python script that queries the GraphQL API for all 8 countries. This covers ~50-56 of 62 data points with zero browser overhead.

**What to query per country:**

| GraphQL Query | Data Points Covered | Section |
|---------------|-------------------|---------|
| `countryProfile(location: "location-{id}")` | GDP, population, ECI, COI, growth projection, diversification grade, trade values, all rankings, income classification | 3.1, 3.2, 3.6, 3.7, 3.8 |
| `treeMap(facet: CPY_C, productClass: HS, year: 2024, productLevel: fourDigit, locationLevel: country, location: "location-{id}")` | All product exports with values, PCI, RCA, distance, opportunity gain | 3.3, 3.5, 3.6 |
| `newProductsCountry(...)` | New product counts, values, diversification data | 3.7 |
| `newProductsComparisonCountries(...)` | Peer country comparison table | 3.7 |
| `countryYearRange(...)` / `allCountryYear(...)` | Time series for growth dynamics, market share trends | 3.5 |
| `productSpace(productClass: HS, year: 2024, location: "location-{id}")` | Product space network data, RCA counts | 3.8 |

**Script structure:**

```python
import asyncio
import httpx

ENDPOINT = "https://atlas.hks.harvard.edu/api/countries/graphql"
COUNTRIES = {
    "Kenya": "location-404", "Turkiye": "location-792",
    "Brazil": "location-76", "India": "location-356",
    "Spain": "location-724", "Ethiopia": "location-231",
    "USA": "location-840", "Germany": "location-276",
}

async def fetch_country_profile(client, location_id):
    query = """{ countryProfile(location: "%s") {
        latestGdpPerCapita { quantity year }
        latestGdpPerCapitaRank { quantity year }
        latestGdpPerCapitaPpp { quantity year }
        incomeClassification
        latestPopulation { quantity year }
        exportValue importValue exportValueRank
        latestEci latestEciRank
        latestCoi latestCoiRank
        growthProjection growthProjectionRank
        diversificationGrade diversityRank diversity
        currentAccount { quantity year }
        marketShareMainSector { shortName code }
    }}""" % location_id
    resp = await client.post(ENDPOINT, json={"query": query})
    return resp.json()

async def fetch_treemap(client, location_id):
    query = """{ treeMap(facet: CPY_C, productClass: HS, year: 2024,
        productLevel: fourDigit, locationLevel: country,
        location: "%s") {
        ... on TreeMapProduct {
            product { shortName code }
            exportValue pci
        }
    }}""" % location_id
    resp = await client.post(ENDPOINT, json={"query": query})
    return resp.json()

async def main():
    async with httpx.AsyncClient() as client:
        for name, loc_id in COUNTRIES.items():
            profile = await fetch_country_profile(client, loc_id)
            treemap = await fetch_treemap(client, loc_id)
            # Write question.json and results.json files
            ...
```

**Execution time:** Seconds (not hours). All 8 countries can be queried in parallel.

### Layer 2: Browser Text Extraction (Secondary)

Only needed for ~6-12 data points that are **client-rendered narrative text** not available in the API. Use Claude in Chrome (`get_page_text`) or Playwright.

**Data points requiring browser:**

| DP# | Data Point | Subpage | Why Browser Needed |
|-----|-----------|---------|-------------------|
| 6 | GDP per capita growth (5-year avg) | main page | Generated narrative text |
| 7 | GDP growth vs regional average | main page | "above"/"below" comparison text |
| 9 | ECI rank change description | main page | "worsening 7 positions" narrative |
| 10 | Complexity trend driver | main page | Generated explanation text |
| 13 | Complexity-income relationship | main page | "more/less complex than expected" text |
| 14 | Projected growth speed | main page | "slowly"/"moderately" descriptor |
| 18 | Export growth rate (5-year avg) | export-basket | Generated text paragraph |
| 19 | Non-oil export growth rate | export-basket | Generated text paragraph |
| 33 | Growth pattern description | growth-dynamics | "static"/"promising" text |
| 34 | Sectors driving growth | growth-dynamics | Generated text |
| 40 | Structural transformation status | market-share | Generated text |
| 42 | Growth mechanism description | market-share | Generated text |

**Workflow:** Visit each assigned country's relevant subpages (main page, export-basket, growth-dynamics, market-share). Use `get_page_text` to extract the narrative paragraphs. This is ~30-40 page visits total, manageable in a single session.

### Layer 3: Screenshot Verification (Quality Check)

Spot-check 3-5 countries by visiting their Atlas pages and visually confirming that:
- API-extracted numeric values match displayed stat cards
- Product treemap data matches hover tooltips
- Rankings and grades match page content

### Step-by-Step Procedure

**Step 1: Run the GraphQL script**
- Execute the Python script to query all 8 countries
- Script outputs `question.json` and `results.json` files for API-sourced data points
- This covers ~85% of questions in minutes

**Step 2: Browser extraction session**
- Open Chrome to `atlas.hks.harvard.edu`
- For each country with narrative-text data points:
  1. Navigate to the relevant subpage
  2. Wait 4-5 seconds for JS rendering
  3. Extract narrative text via `get_page_text` or manual reading
  4. Record the text values
- Write `question.json` and `results.json` for browser-sourced data points

**Step 3: Write all files**
- For each question, create:
  - `evaluation/questions/{id}/question.json`
  - `evaluation/results/{id}/ground_truth/results.json`
- Use IDs starting from 61
- Record `execution_timestamp` as current time

**Step 4: Bulk-update `eval_questions.json`**
1. Read the current `evaluation/eval_questions.json`
2. Add new category definitions to the `categories` object
3. Append all new question entries to the `questions` array
4. Write back the updated file
5. Verify the total question count

**Step 5: Verify**
- Count all `question.json` files in `evaluation/questions/` — should be 60 + new
- Count all `results.json` files in `evaluation/results/*/ground_truth/` — should match
- Verify every new question in `eval_questions.json` has a corresponding directory
- Screenshot-verify 3-5 data points against live Atlas pages

---

## 7. Scale & Time Estimate

### Expected Question Count

- **66 question templates** (section 3) x **1-2 countries each** = **~80-130 questions**
- Some templates yield a single question (1 country), others may warrant 2 countries for diversity.
- Some data points have sub-parts (e.g., "top 3 export destinations" = 1 question with 3-item answer, not 3 separate questions).
- A few "verify unavailability" questions for frontier countries add to the count.

### Collection Time (Revised with GraphQL API)

The GraphQL API discovery reduces collection time from 4-10 hours to ~1-2 hours.

| Layer | Activity | Time | Notes |
|-------|----------|------|-------|
| **1 — API** | Write GraphQL collection script | ~30-60 min | One-time script development |
| **1 — API** | Run script for all 8 countries | ~1-2 min | Parallelizable, no browser |
| **1 — API** | Generate question.json + results.json files | ~10-15 min | Template-based file generation |
| **2 — Browser** | Extract narrative text (~30-40 pages) | ~30-60 min | Only for text descriptions |
| **2 — Browser** | Write files for browser-sourced data | ~15-20 min | Small subset of questions |
| **3 — Verify** | Bulk-update eval_questions.json | ~10 min | One batch operation |
| **3 — Verify** | Screenshot verification (3-5 countries) | ~15-20 min | Spot-check accuracy |
| | **Total** | **~1.5-3 hours** | Down from 4-10 hours |

**Single session is now feasible.** The three-layer approach eliminates the need for multiple sessions. The API handles the bulk of data extraction in seconds, leaving only narrative text for browser work.

### Comparison: Old vs New Approach

| Dimension | Old (Browser-Only) | New (API + Browser) |
|-----------|-------------------|-------------------|
| Pages to visit | ~80 | ~30-40 (narrative text only) |
| Tooltip hovers needed | ~20 | 0 (API provides data) |
| LLM token cost | High (screenshots + text) | Minimal (browser text only) |
| Parallelization | None (sequential browser) | Full (async API queries) |
| Time estimate | 4-10 hours (3 sessions) | 1.5-3 hours (1 session) |
| Reproducibility | Low (UI may change) | High (API is stable) |
