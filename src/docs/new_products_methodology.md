# New Products Methodology

**Purpose:** Defines how the Atlas identifies, measures, and compares "new products" for a country — products in which a country has newly developed a revealed comparative advantage — as shown on the Country Pages new-products section.

**When to load this document:** Load when the user asks how Atlas identifies new products, what `product_status`
field values mean (new/present/lost/absent), how lookback periods (3/5/10/15
years) work, what `is_new` captures vs. `product_status`, or how to query
`country_product_lookback` tables. Also load for `newProductsCountry` or
`newProductsComparisonCountries` GraphQL query details. Do NOT load for
diversification grades (see `strategic_approaches.md`) or for general product
space diversification analysis (see `product_space_and_relatedness.md`).

---

## 1. Definition of a "New Product"

A product is classified as **new** for a country when it transitions from not meaningfully exported to firmly exported over an observation window — specifically, when the country's Revealed Comparative Advantage (RCA) crosses the threshold of 1.

**The `newProductsCountry` GraphQL query** applies a stricter, rolling-window criterion to filter out noise:

- **Observation window**: approximately 18 years of RCA history (anchored to the latest data year)
- **Absence condition**: RCA < 0.5 in each of the **first 3 years** of the window — the country was not meaningfully exporting the product at the start
- **Presence condition**: RCA ≥ 1.0 in each of the **last 3 years** of the window — the country now firmly exports the product

This sustained-transition filter excludes temporary RCA spikes and counts only products where the new capability has been demonstrated consistently over multiple years.

**The default lookback period shown on the New Products page is 15 years.** The year range shown in the page title (e.g., "New Products Exported, 2009–2024") reflects this 15-year window.

---

## 2. `product_status` Field (4 Values)

`product_status` is stored as an enum in `country_product_year` tables (all digit-level variants) and classifies the **current-year status** of each country–product pair relative to its past RCA trajectory:

| Value | Meaning |
|-------|---------|
| `new` | RCA < 1 in the base/lookback year; RCA ≥ 1 in the current year — newly acquired comparative advantage |
| `present` | RCA ≥ 1 continuously — the country has been exporting this product throughout the window |
| `lost` | RCA ≥ 1 in the base year; RCA < 1 in the current year — comparative advantage was lost |
| `absent` | RCA < 1 in both the base and current year — the country never meaningfully exported this product |

The base year against which status is evaluated depends on the **lookback period** used (3, 5, 10, or 15 years). The `product_status` stored in the DB reflects the default lookback period used at data-build time. The Country Pages new-products section uses the 15-year default.

---

## 3. `is_new` Boolean Flag

`is_new` (type: `bool`) is a convenience shorthand stored alongside `product_status` in `country_product_year` tables:

```
is_new = (product_status == 'new')
```

It is `TRUE` when a product transitions from non-exported to exported over the lookback window, and `FALSE` for all other statuses (`present`, `lost`, `absent`). It does **not** encode information about the direction of loss — to detect `lost` products, filter on `product_status = 'lost'` directly.

Both `is_new` and `product_status` are available at all product digit levels (1, 2, 4) in every `country_product_year_*` table in the HS92, HS12, and SITC schemas.

---

## 4. Lookback Periods

Four lookback periods are available. They appear as interactive options on other Country Pages sections (e.g., Growth Dynamics uses 3/5/10 years) and map to the `LookBackYearRange` GraphQL enum:

| Period | Enum Value | What It Measures |
|--------|------------|-----------------|
| 3 years | `ThreeYears` | Very recent export diversification; sensitive to short-term shifts |
| 5 years | `FiveYears` | Medium-term new exports; default for export growth dynamics |
| 10 years | `TenYears` | Decade-scale structural change |
| 15 years | `FifteenYears` | Long-run diversification; **default for the new-products page** |

The new-products page always displays the 15-year window. The `countryLookback(yearRange: FifteenYears)` query returns the `diversityRankChange` over this same window.

---

## 5. DB Table: `hs92.country_product_lookback_4`

The **"4" suffix** denotes the 4-digit HS92 product level (the most granular level used for new-product analysis). Equivalent tables exist at 1-digit (`country_product_lookback_1`) and 2-digit (`country_product_lookback_2`). These tables exist **only in the HS92 schema** — not in SITC, HS12, or other schemas.

These tables store pre-calculated export change metrics over the configured lookback periods. **They do not contain `product_status` or `is_new` — those fields live in `country_product_year_*` tables.**

### `hs92.country_product_lookback_4` — All Columns

| Column | Type | Description |
|--------|------|-------------|
| `country_id` | `int4` | M49 country code |
| `product_id` | `int4` | HS92 4-digit product ID (foreign key to `classification.product_hs92`) |
| `location_level` | `ENUM(country, group)` | Row applies to a single country or a group |
| `product_level` | `int4` | Product digit level (4 for this table) |
| `lookback` | `int4` | Lookback length in years (3, 5, 10, or 15) |
| `lookback_year` | `int4` | Base year of the lookback window (current year minus `lookback`) |
| `export_value_change` | `float8` | Absolute change in export value over the period (USD) |
| `export_value_cagr` | `float8` | Compound annual growth rate of export value over the period |
| `export_value_growth` | `float8` | Total (cumulative) growth ratio over the period |
| `export_value_percent_change` | `float8` | Percentage change in export value over the period |
| `export_rpop_change` | `float8` | Change in population-adjusted RCA (RPOP) over the period |
| `global_market_share_change` | `float8` | Absolute change in the country's global market share for this product |
| `global_market_share_growth` | `float8` | Total growth ratio of global market share |
| `global_market_share_cagr` | `float8` | CAGR of global market share over the period |

To identify new products in SQL, use `hs92.country_product_year_4` (not the lookback table), filtering on `product_status` or `is_new`.

---

## 6. DB Table: `hs92.country_product_year_4` — New-Product Relevant Columns

This is the primary table for new-product SQL queries. It has one row per country–product–year and stores the point-in-time RCA alongside status flags.

| Column | Type | Notes |
|--------|------|-------|
| `country_id` | `int4` | M49 code |
| `product_id` | `int4` | HS92 4-digit product ID |
| `year` | `int4` | Data year |
| `location_level` | `ENUM(country, group)` | Filter to `'country'` for country-level rows |
| `product_level` | `int4` | Filter to `4` for 4-digit results |
| `export_rca` | `float8` | Revealed Comparative Advantage (threshold: 1.0) |
| `export_value` | `int8` | Gross export value (USD) |
| `is_new` | `bool` | `TRUE` when `product_status = 'new'` |
| `product_status` | `ENUM(new, absent, lost, present)` | Full status classification |
| `distance` | `float8` | Distance from current capabilities |
| `cog` | `float8` | Complexity Outlook Gain (opportunity gain) |
| `normalized_pci` | `float8` | Normalized Product Complexity Index |
| `normalized_distance` | `float8` | Normalized distance |
| `normalized_cog` | `float8` | Normalized COG |
| `global_market_share` | `float8` | Country's share of world exports for this product |
| `export_rpop` | `float8` | Population-adjusted RCA |

---

## 7. SQL Pattern: Identifying New Products

### New products for a given country in the most recent year

```sql
SELECT
    cpy.country_id,
    cpy.product_id,
    p.long_name            AS product_name,
    p.code                 AS hs92_code,
    cpy.export_value,
    cpy.export_rca
FROM hs92.country_product_year_4 cpy
JOIN classification.product_hs92 p
    ON p.id = cpy.product_id
WHERE cpy.country_id    = 404          -- Kenya (M49)
  AND cpy.year          = 2024         -- latest year
  AND cpy.location_level = 'country'
  AND cpy.product_level  = 4
  AND cpy.is_new         = TRUE        -- or: cpy.product_status = 'new'
ORDER BY cpy.export_value DESC;
```

### Count of new products per country, latest year

```sql
SELECT
    country_id,
    COUNT(*) FILTER (WHERE product_status = 'new')    AS new_count,
    COUNT(*) FILTER (WHERE product_status = 'present') AS present_count,
    COUNT(*) FILTER (WHERE product_status = 'lost')    AS lost_count,
    COUNT(*) FILTER (WHERE product_status = 'absent')  AS absent_count
FROM hs92.country_product_year_4
WHERE year          = 2024
  AND location_level = 'country'
  AND product_level  = 4
GROUP BY country_id;
```

### Per-capita income contribution from new products

```sql
SELECT
    cpy.country_id,
    SUM(cpy.export_value)                         AS new_product_total_value,
    SUM(cpy.export_value) / cy.population::float  AS new_product_value_per_capita
FROM hs92.country_product_year_4 cpy
JOIN hs92.country_year cy
    ON cy.country_id = cpy.country_id
   AND cy.year       = cpy.year
WHERE cpy.country_id     = 404
  AND cpy.year           = 2024
  AND cpy.location_level = 'country'
  AND cpy.product_level  = 4
  AND cpy.product_status = 'new'
GROUP BY cpy.country_id, cy.population;
```

---

## 8. GraphQL API: `newProductsCountry` Query

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

This query has **no optional arguments** — all fields are always returned.

---

## 9. GraphQL API: `newProductsComparisonCountries` Query

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

---

## 10. Peer Country Selection

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

## 11. `countryProfile` Fields Relevant to New Products

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

## 12. Diversification Grade Thresholds

The `diversificationGrade` is assigned by ranking all countries by their new product count and applying fixed cut-offs:

| Grade | Threshold |
|-------|-----------|
| `APlus` | Top 10 countries by new product count |
| `A` | ≥ 30 new products |
| `B` | ≥ 13 new products |
| `C` | ≥ 6 new products |
| `D` | ≥ 3 new products |
| `DMinus` | < 3 new products |

---

## 13. Per-Capita Income Contribution

**What it measures**: The total export value of new products divided by the country's population. It answers "how much additional income per person comes from the products the country has newly started exporting."

- **API field**: `newProductExportValuePerCapita` (Int, USD) in both `newProductsCountry` and `countryProfile`
- **API field**: `newProductExportValue` (Float, USD) for the total (non-per-capita) value
- **Derived field** on the page: "New Export Proportion" = `newProductExportValue / exportValue` — the share of the current export basket consisting of newly added products

The `newProductsIncomeGrowthComments` enum (`LargeEnough` / `TooSmall`) classifies whether the per-capita contribution is considered economically significant.

---

## 14. New-Products Page: Complete Data Point Map

| # | Visible Element | Source Query | API Field |
|---|-----------------|-------------|-----------|
| 43 | Economic Diversification Grade (top bar) | `countryProfile` | `diversificationGrade` |
| 44 | Diversity Rank (top bar) | `countryProfile` | `diversityRank` |
| 45 | Diversity rank change over 15 years (top bar) | `countryLookback(yearRange: FifteenYears)` | `diversityRankChange` |
| 46 | New products count (text/treemap) | `newProductsCountry` | `newProductCount` |
| 47 | Per-capita income contribution (text) | `newProductsCountry` | `newProductExportValuePerCapita` |
| 48 | New products total value (table) | `newProductsCountry` | `newProductExportValue` |
| 49 | New export proportion (mini-visual) | Derived | `newProductExportValue / exportValue` |
| 50 | Peer comparison table | `newProductsComparisonCountries` | `location`, `newProductCount`, `newProductExportValue`, `newProductExportValuePerCapita` |

**Verification link**: `https://atlas.hks.harvard.edu/countries/{m49_code}/new-products`

---

## 15. Known Limitations and Data Gaps

- **`country_product_lookback_*` tables exist only in the `hs92` schema.** They are not present in `sitc`, `hs12`, or any services schema.
- **`product_status` and `is_new` are absent from `country_product_lookback_*` tables.** These fields are in `country_product_year_*` tables only.
- **The `newProductsCountry` GraphQL query does not expose the lookback period** as an argument — it always uses the server-side default (~15-year rolling window with the sustained-transition filter described in Section 1). The `product_status` field in the DB is computed at build time from a fixed lookback.
- **No historical series**: The new-products count is a snapshot at `year`. There is no API query to retrieve new product counts for a historical year other than by changing the `year` argument, and the server-side RCA window shifts accordingly.
- **Peer selection is not documented** in any public API specification. The peers returned by `newProductsComparisonCountries` match those in `countryProfile.comparisonLocations` but the selection algorithm is proprietary.
- **Country Pages API is undocumented** by the Growth Lab (only the Explore API at `/api/graphql` has official docs). The schema described here was verified via live introspection in February 2026.
- **`is_new` reflects a fixed lookback at DB build time** — it cannot be recomputed for arbitrary lookback periods in SQL without re-implementing the full rolling-window logic against the `country_product_year` time series.
- **Highest-complexity countries** (e.g., USA, Germany) may show unusual new product counts because at the technological frontier, RCA transitions happen rarely and the diversification grade thresholds may classify them as low-grade diversifiers despite strong absolute export performance.
