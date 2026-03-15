---
title: New Products Methodology
purpose: >
  Defines how the Atlas identifies, measures, and compares "new products" for a
  country -- products in which a country has newly developed a revealed comparative
  advantage. Covers both the GraphQL API (Country Pages, HS92-only) and the SQL
  computation method (works with any classification: HS92, HS12, SITC).
keywords: [new products, product_status, is_new, RCA, lookback, diversification, country_product_lookback, newProductsCountry, newProductsComparisonCountries, diversificationGrade, comparative advantage, peer comparison]
when_to_load: >
  Load when the user asks how Atlas identifies new products, how to compute new
  products from SQL for any classification system, what `product_status` field
  values mean (new/present/lost/absent), how lookback periods work, or how to
  query `newProductsCountry` / `newProductsComparisonCountries` via GraphQL.
when_not_to_load: >
  Do NOT load for diversification grades (see `strategic_approaches.md`) or for
  general product space diversification analysis (see `product_space_and_relatedness.md`).
related_docs: [strategic_approaches.md, product_space_and_relatedness.md]
---

## 1. Definition of a "New Product": RCA Threshold Transition Over a 15-Year Window

A product is classified as **new** for a country when it transitions from not meaningfully exported to firmly exported over an observation window. The determination is based on **3-year averaged export values** at each end of the window:

- **Observation window**: approximately 15 years (default), anchored to the latest data year. The Atlas Country Pages title (e.g., "New Products Exported, 2009–2024") reflects this window.
- **Start period**: Average each country-product `export_value` over the first 3 years of the window (e.g., 2009–2011). Compute RCA from those averages across all countries and all 4-digit products.
- **End period**: Repeat for the last 3 years of the window (e.g., 2022–2024).
- **Absence condition**: Start-period RCA < 0.5 — the country was not meaningfully exporting the product at the start of the window.
- **Presence condition**: End-period RCA ≥ 1.0 — the country now firmly exports the product.

All 4-digit products are eligible (including natural resources). No product filters are applied. This approach filters out noisy, one-off export spikes — only sustained new comparative advantages qualify.

### Classification system availability

| Method | HS92 | HS12 | SITC |
|--------|------|------|------|
| **GraphQL** (`newProductsCountry`) | Yes (default, only option) | No | No |
| **SQL** (recompute from `export_value`) | Yes (data from 1995) | Yes (data from 2012, max ~11-year window) | Yes (data from 1962) |

The GraphQL Country Pages API only returns new products in HS92. For HS12 or SITC new products, use SQL with the same RCA-averaging method — just use the appropriate `{schema}.country_product_year_4` table and `classification.product_{schema}` for product names. See the few-shot example `new_products_country.sql` for the full SQL pattern.

---

## 2. SQL: Computing New Products from Raw export_value Using 3-Year Averaged RCA

Since the `is_new` and `product_status` columns are **ALL NULL** in the current database (see Section 4), new products must be computed from raw export values in SQL. The method works identically across all goods schemas.

### SQL pattern (any schema)

Use `{schema}.country_product_year_4` (e.g., `hs92.country_product_year_4`, `hs12.country_product_year_4`, `sitc.country_product_year_4`):

1. Average `export_value` per country-product over the first 3 years of the window → compute RCA from those averages across ALL countries and products
2. Repeat for the last 3 years
3. Filter: start-period RCA < 0.5 AND end-period RCA >= 1.0

See `src/example_queries/new_products_country.sql` for the complete CTE pattern.

### Schema-specific considerations

| Schema | Data starts | Max window (to 2022) | Default start period | Notes |
|--------|-------------|---------------------|---------------------|-------|
| `hs92` | 1995 | 28 years | 2009–2011 (for ~15-yr window) | Matches GraphQL Country Pages output |
| `hs12` | 2012 | 11 years | 2012–2014 (max available) | Shorter window catches more transitions |
| `sitc` | 1962 | 61 years | 2009–2011 (for ~15-yr window) | Different product granularity than HS |

### Primary table: `{schema}.country_product_year_4`

This is the primary table for new-product SQL queries. It has one row per country–product–year and stores export values. The table exists in all goods schemas (hs92, hs12, sitc).

| Column | Type | Notes |
|--------|------|-------|
| `country_id` | `int4` | M49 code |
| `product_id` | `int4` | Product ID for the schema |
| `year` | `int4` | Data year |
| `export_value` | `int8` | Gross export value (USD) — use this for RCA computation |
| `export_rca` | `float8` | Pre-computed single-year RCA (not used for new products — use averaged RCA instead) |
| `is_new` | `bool` | **ALL NULL** — do not use |
| `product_status` | `ENUM` | **ALL NULL** — do not use |

### Lookback table: `hs92.country_product_lookback_4`

> **HS92-only.** This table exists only in the `hs92` schema — not in HS12 or SITC.

Stores pre-calculated export change metrics over configured lookback periods (3, 5, 10, 15 years). **Does not contain `product_status` or `is_new`** — those fields are in `country_product_year_*` tables only (and are NULL).

| Column | Type | Description |
|--------|------|-------------|
| `country_id` | `int4` | M49 country code |
| `product_id` | `int4` | HS92 4-digit product ID |
| `lookback` | `int4` | Lookback length in years (3, 5, 10, or 15) |
| `lookback_year` | `int4` | Base year of the lookback window |
| `export_value_change` | `float8` | Absolute change in export value (USD) |
| `export_value_cagr` | `float8` | CAGR of export value over the period |
| `export_value_growth` | `float8` | Total growth ratio |
| `export_value_percent_change` | `float8` | Percentage change |
| `export_rpop_change` | `float8` | Change in population-adjusted RCA |
| `global_market_share_change` | `float8` | Change in global market share |
| `global_market_share_growth` | `float8` | Growth ratio of global market share |
| `global_market_share_cagr` | `float8` | CAGR of global market share |

---

## 3. Lookback Periods: 3, 5, 10, and 15-Year Windows for New Product Detection

Four lookback periods are available in the GraphQL API. They appear as interactive options on Country Pages sections (e.g., Growth Dynamics uses 3/5/10 years) and map to the `LookBackYearRange` GraphQL enum:

| Period | Enum Value | What It Measures |
|--------|------------|-----------------|
| 3 years | `ThreeYears` | Very recent export diversification; sensitive to short-term shifts |
| 5 years | `FiveYears` | Medium-term new exports; default for export growth dynamics |
| 10 years | `TenYears` | Decade-scale structural change |
| 15 years | `FifteenYears` | Long-run diversification; **default for the new-products page** |

The new-products page always displays the 15-year window. In SQL, any window length can be used — just adjust the start/end year ranges.

---

## 4. WARNING: `product_status` and `is_new` DB Columns Are ALL NULL — Do Not Use

> **WARNING:** These columns exist in the database schema but are **ALL NULL** in the current data. Do NOT use them in SQL queries. Compute new products from raw `export_value` as described in Section 2.

The schema defines:

| Column | Type | Intended Meaning |
|--------|------|-----------------|
| `product_status` | `ENUM(new, absent, lost, present)` | Export status relative to a lookback period |
| `is_new` | `bool` | Shorthand for `product_status = 'new'` |

**Intended values** (for reference if they become populated in a future data build):

| Value | Meaning |
|-------|---------|
| `new` | RCA crossed from < 1 to ≥ 1 over the lookback window |
| `present` | RCA ≥ 1 throughout the window |
| `lost` | RCA dropped from ≥ 1 to < 1 over the window |
| `absent` | RCA < 1 throughout the window |

Both columns are defined at all product digit levels (1, 2, 4) in every `country_product_year_*` table across HS92, HS12, and SITC schemas.

---

## 5. GraphQL API: `newProductsCountry` Query (HS92 Only, Country Pages API)

> **HS92-only.** The Country Pages API only supports HS92 for new products. For HS12 or SITC, use SQL.

**Endpoint**: `POST https://atlas.hks.harvard.edu/api/countries/graphql`

**ID format**: Country Pages API uses string IDs (`"location-404"` for Kenya, not the bare integer).

### Query Signature

```graphql
query {
  newProductsCountry(
    location: ID!   # e.g., "location-404"
    year: Int!      # e.g., 2024
  ) {
    location {
      id
      shortName
      code
    }
    newProductCount              # Integer: number of new products
    newProductExportValue        # Float: total USD value of new product exports
    newProductExportValuePerCapita  # Int: USD per capita from new products
    newProducts {                # [Product]: list of individual new products
      id
      code                       # HS92 code (e.g., "0901")
      shortName
      longName
    }
  }
}
```

### Required Arguments

| Argument | Type | Example |
|----------|------|---------|
| `location` | `ID!` | `"location-404"` |
| `year` | `Int!` | `2024` |

This query has **no optional arguments** — all fields are always returned. There is no `productClass` parameter; it always uses HS92.

---

## 6. GraphQL API: `newProductsComparisonCountries` Query and Peer Country Selection

> **HS92-only.** Same as `newProductsCountry`.

Used exclusively for the peer comparison table on the new-products page.

### Query Signature

```graphql
query {
  newProductsComparisonCountries(
    location: ID!     # e.g., "location-404"
    year: Int!        # e.g., 2024
    quantity: Int     # optional: number of peer countries (default: 3)
  ) {
    location {
      id
      shortName
      code
    }
    newProductCount
    newProductExportValue
    newProductExportValuePerCapita
  }
}
```

Returns a list of `NewProductsComparisonCountries` objects — one per peer country plus the queried country itself (typically 4 rows total: the country + 3 peers).

### Peer Country Selection

The peer countries shown in the comparison table are selected automatically by the Country Pages API. The selection criterion is geographic and economic similarity — Atlas uses `countryProfile.comparisonLocations` (a list of `Location` objects) to determine peers. Peer selection is not configurable by the caller; it is pre-computed server-side based on the country's income level, region, and export structure.

The `countryProfile` query exposes the peer list directly:

```graphql
query {
  countryProfile(location: "location-404") {
    comparisonLocations {
      id
      shortName
      code
    }
  }
}
```

The same peers returned here appear in the `newProductsComparisonCountries` response.

**Example (Kenya)**: Peers are Uganda, Ethiopia, and Tanzania — all East African countries at comparable income levels.

---

## 7. `countryProfile` GraphQL Fields for New Products: diversificationGrade, diversityRank, newProductExportValue

> **GraphQL only (HS92).**

The `countryProfile` query (required args: `location: ID!`) returns several fields that power the new-products page top bar and narrative text:

| Field | Type | Meaning |
|-------|------|---------|
| `diversificationGrade` | `DiversificationGrade` | Letter grade `APlus`, `A`, `B`, `C`, `D`, `DMinus` based on new product count rank |
| `diversityRank` | `Int` | Rank out of 145 by number of products exported with RCA > 1 |
| `diversity` | `Int` | Raw count of products with RCA > 1 |
| `newProductExportValue` | `Float` | Total USD value of new product exports |
| `newProductExportValuePerCapita` | `Int` | USD per capita from new product exports |
| `newProductsComments` | `NewProductsComments` | `TooFew` or `Sufficient` — narrative classifier |
| `newProductsIncomeGrowthComments` | `NewProductsIncomeGrowthComments` | `LargeEnough` or `TooSmall` — whether new-product income contribution is significant |
| `newProductsComplexityStatusGrowthPrediction` | `NewProductsComplexityStatusGrowthPrediction` | `More`, `Same`, `Less` — whether new products are more/less/equally complex than existing basket |

The `countryLookback(yearRange: FifteenYears)` query returns `diversityRankChange` (the 15-year change in diversity rank, used for the "↓7 over 15 years" stat in the top bar).

---

## 8. Diversification Grade Thresholds (A+ through D-) and Per-Capita Income Contribution

### Diversification Grade Thresholds

The `diversificationGrade` is assigned by ranking all countries by their new product count and applying fixed cut-offs:

| Grade | Threshold |
|-------|-----------|
| `APlus` | Top 10 countries by new product count |
| `A` | ≥ 30 new products |
| `B` | ≥ 13 new products |
| `C` | ≥ 6 new products |
| `D` | ≥ 3 new products |
| `DMinus` | < 3 new products |

### Per-Capita Income Contribution

**What it measures**: The total export value of new products divided by the country's population. It answers "how much additional income per person comes from the products the country has newly started exporting."

- **API field** (GraphQL, HS92): `newProductExportValuePerCapita` (Int, USD) in both `newProductsCountry` and `countryProfile`
- **API field**: `newProductExportValue` (Float, USD) for the total (non-per-capita) value
- **Derived field** on the page: "New Export Proportion" = `newProductExportValue / exportValue` — the share of the current export basket consisting of newly added products
- **SQL**: Can be computed for any schema by joining new products with population data from `{schema}.country_year`

The `newProductsIncomeGrowthComments` enum (`LargeEnough` / `TooSmall`) classifies whether the per-capita contribution is considered economically significant.

---

## 9. Known Limitations: is_new NULL, HS92-Only GraphQL, Lookback Constraints

- **GraphQL new products are HS92-only.** The `newProductsCountry` query has no `productClass` parameter. For HS12 or SITC new products, use SQL.
- **`is_new` and `product_status` are ALL NULL** in the current database. Compute new products from raw `export_value` using the 3-year averaging method.
- **`country_product_lookback_*` tables exist only in the `hs92` schema.** They are not present in HS12, SITC, or services schemas.
- **HS12 data starts in 2012**, limiting the max new-products window to ~11 years (vs. 15+ for HS92 and SITC).
- **The `newProductsCountry` GraphQL query does not expose the lookback period** as an argument — it always uses the server-side default (~15-year window). In SQL, any window length can be used.
- **No historical series**: The new-products count is a snapshot at `year`. There is no API query to retrieve new product counts for a historical year other than by changing the `year` argument, and the server-side RCA window shifts accordingly.
- **Peer selection is not documented** in any public API specification. The peers returned by `newProductsComparisonCountries` match those in `countryProfile.comparisonLocations` but the selection algorithm is proprietary.
- **Country Pages API is undocumented** by the Growth Lab (only the Explore API at `/api/graphql` has official docs). The schema described here was verified via live introspection in February 2026.
- **Highest-complexity countries** (e.g., USA, Germany) may show unusual new product counts because at the technological frontier, RCA transitions happen rarely and the diversification grade thresholds may classify them as low-grade diversifiers despite strong absolute export performance.
