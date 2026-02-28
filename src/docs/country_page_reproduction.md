---
title: Country Page Reproduction Guide
purpose: >
  SQL and GraphQL recipes for reproducing every data point shown on Atlas Country
  Pages, covering all 12 subpage sections.
keywords:
  - country page
  - Country Pages API
  - GraphQL recipe
  - SQL recipe
  - export basket
  - growth dynamics
  - strategic approach
  - new products
  - treemap
  - product space
  - growth opportunities
  - market share
  - export complexity
  - country profile
  - diversification
  - countryProfile
  - countryLookback
  - treeMap
when_to_load: >
  Load when the agent is constructing a GraphQL query for the Country Pages API
  (`/api/countries/graphql`) and needs to know which query name and fields
  correspond to a specific Country Page section (export basket, growth dynamics,
  strategic approach, new products, etc.). Also load when the agent needs to
  distinguish between what is available via the Country Pages API vs. the Explore
  API.
when_not_to_load: >
  Do not load for understanding what metrics mean (see metrics_glossary.md) or
  for Explore page queries (see explore_page_reproduction.md).
related_docs:
  - metrics_glossary.md
  - explore_page_reproduction.md
---

## System Overview

Country Pages are found at `https://atlas.hks.harvard.edu/countries/{m49_id}`. All 12 subpages are rendered in a single page load. The left sidebar navigates to sections via anchor links, not separate HTTP requests.

**Default classification:** HS92 (Harmonized System 1992), 4-digit product level, most recent available year (currently 2024).

> **Important:** The Country Pages API `ProductClass` enum has exactly two values: `HS` and `SITC`. There is no `HS12` or `HS22`. `HS` is equivalent to HS92 — the data returned is identical to the Explore API's `HS92` (verified empirically: same product count and export values). Passing `HS12` or `HS22` will return a GraphQL validation error.

**Two data sources available:**

| Source | Endpoint | When to use |
|--------|----------|-------------|
| SQL database | `hs92.*`, `classification.*` schemas | Raw trade values, RCA, distance, COI, ECI, product names |
| Country Pages GraphQL API | `POST https://atlas.hks.harvard.edu/api/countries/graphql` | Derived/narrative metrics (growth projections, diversification grades, strategic approach, new product analysis, treemap-ready data) |

**ID format in Country Pages API:** String-prefixed — `"location-404"` for Kenya (M49 code 404), `"product-HS-726"` for a product. The Explore API at `/api/graphql` uses bare integers (`countryId: 404`).

**Country lookup in SQL:**
```sql
SELECT country_id, iso3_code, name_en, in_cp
FROM classification.location_country
WHERE iso3_code = 'KEN';
-- country_id = 404, in_cp = true means eligible for Country Pages
```

---

## Shared Reference: Column Glossary

The following columns appear across multiple subpages and are defined once here.

### `hs92.country_product_year_4` — core per-product columns

| Column | Type | Description |
|--------|------|-------------|
| `country_id` | int4 | M49 country code |
| `product_id` | int4 | Internal Atlas product ID (not HS code) |
| `year` | int4 | Data year |
| `product_level` | int4 | Aggregation level (1, 2, 4 for HS92) |
| `export_value` | int8 | Export value in USD |
| `import_value` | int8 | Import value in USD |
| `export_rca` | float8 | Revealed Comparative Advantage (RCA > 1 = has comparative advantage) |
| `global_market_share` | float8 | Country's share of global trade in this product |
| `distance` | float8 | Distance from country's capabilities to this product (0 = very close) |
| `cog` | float8 | Complexity Outlook Gain — how many pathways this product opens |
| `normalized_pci` | float8 | Normalized Product Complexity Index (country-specific normalization) |
| `normalized_cog` | float8 | Normalized COG |
| `normalized_distance` | float8 | Normalized distance |
| `is_new` | bool | Whether this is a newly acquired export |
| `product_status` | ENUM | `new`, `absent`, `lost`, `present` |

### `hs92.country_year` — country-level annual columns

| Column | Type | Description |
|--------|------|-------------|
| `country_id` | int4 | M49 country code |
| `year` | int4 | Data year |
| `export_value` | int8 | Total goods exports (USD) |
| `import_value` | int8 | Total goods imports (USD) |
| `eci` | float8 | Economic Complexity Index |
| `coi` | float8 | Complexity Outlook Index |
| `diversity` | int4 | Count of products with RCA > 1 |
| `gdp` | float8 | GDP (current USD) |
| `gdppc` | float8 | GDP per capita (current USD) |
| `gdp_const` | float8 | GDP (constant USD) |
| `gdppc_ppp` | float8 | GDP per capita PPP |
| `current_account` | float8 | Current account balance (USD) |
| `growth_proj` | float8 | 10-year growth projection |
| `population` | float8 | Population |

### `classification.product_hs92` — product metadata columns

| Column | Type | Description |
|--------|------|-------------|
| `product_id` | int4 | Internal Atlas product ID |
| `product_level` | int4 | Aggregation level |
| `code` | text | HS92 code (e.g., "0901" for coffee) |
| `name_en` | text | Full English name |
| `name_short_en` | text | Short English name (used in visualizations) |
| `top_parent_id` | int4 | 1-digit parent — maps to the 11 color-coded sectors |
| `parent_id` | int4 | Direct parent in hierarchy |
| `product_space_x` | float8 | X coordinate for product space visualization |
| `product_space_y` | float8 | Y coordinate for product space visualization |
| `natural_resource` | bool | True if classified as natural resource/extractive |
| `show_feasibility` | bool | False for highest-complexity countries (suppresses growth opportunities pages) |

---

## Subpage 1: Country Introduction / Hero (`/countries/{id}`)

**What is shown:** GDP per capita (nominal + PPP + rank + sparkline), population, ECI ranking (+ sparkline), 5-year GDP per capita growth vs regional average, 10-year growth projection + rank, income classification, complexity-vs-income comparison.

### SQL Patterns

```sql
-- Current-year snapshot: GDP per capita, ECI, COI, exports
SELECT
    cy.year,
    cy.gdppc,
    cy.gdppc_ppp,
    cy.eci,
    cy.coi,
    cy.diversity,
    cy.export_value,
    cy.growth_proj,
    cy.population
FROM hs92.country_year cy
WHERE cy.country_id = 404  -- Kenya
  AND cy.year = 2024;

-- ECI rank (current year)
SELECT
    country_id,
    year,
    eci,
    RANK() OVER (PARTITION BY year ORDER BY eci DESC) AS eci_rank
FROM hs92.country_year
WHERE year = 2024
ORDER BY eci_rank;

-- GDP per capita rank
SELECT
    country_id,
    year,
    gdppc,
    RANK() OVER (PARTITION BY year ORDER BY gdppc DESC) AS gdppc_rank
FROM hs92.country_year
WHERE year = 2024
ORDER BY gdppc_rank;

-- Time series for sparklines (ECI, GDP per capita)
SELECT year, eci, gdppc, gdppc_ppp
FROM hs92.country_year
WHERE country_id = 404
  AND year BETWEEN 2012 AND 2024
ORDER BY year;
```

### GraphQL API (Country Pages)

```graphql
# Richest single query — 46 derived fields
{
  countryProfile(location: "location-404") {
    latestGdpPerCapita { quantity year }
    latestGdpPerCapitaRank { quantity }
    latestGdpPerCapitaPpp { quantity }
    latestGdpPerCapitaPppRank { quantity }
    incomeClassification
    latestPopulation { quantity }
    latestEci
    latestEciRank
    growthProjection
    growthProjectionRank
    growthProjectionClassification
    growthProjectionRelativeToIncome
    growthProjectionPercentileClassification
  }
  countryLookback(id: "location-404", yearRange: FiveYears) {
    gdpPerCapitaChangeConstantCagr
    gdpPcConstantCagrRegionalDifference   # Above | InLine | Below
  }
  countryYearRange(location: "location-404", minYear: 2012, maxYear: 2024) {
    eci { quantity year }
    gdpPerCapita { quantity year }
    eciRank { quantity year }
  }
  globalDatum {
    latestEciRankTotal     # e.g., 145
    latestGdpRankTotal
  }
}
```

**Pitfall:** `growth_proj` in `hs92.country_year` is the raw growth projection float. The Country Pages API adds the rank and the `growthProjectionRelativeToIncome` enum (which classifies the country as More/ModeratelyMore/Same/ModeratelyLess/Less complex than expected for its income level using OLS regression against all 145 countries). The SQL alone cannot produce the rank or the classification.

---

## Subpage 2: Export Basket (`/countries/{id}/export-basket`)

**What is shown:** Total exports, exporter rank, current account, export growth rate (5-year CAGR), non-oil export growth rate, total imports, trade balance, top-3 export destination / import origin countries, treemap of products by export value (sized and colored by sector).

### SQL Patterns

```sql
-- Top bar: total exports, current account
SELECT
    cy.export_value   AS total_exports,
    cy.import_value   AS total_imports,
    cy.export_value - cy.import_value AS trade_balance,
    cy.current_account
FROM hs92.country_year cy
WHERE cy.country_id = 404
  AND cy.year = 2024;

-- Exporter rank
SELECT
    country_id,
    year,
    export_value,
    RANK() OVER (PARTITION BY year ORDER BY export_value DESC) AS exporter_rank
FROM hs92.country_year
WHERE year = 2024
ORDER BY exporter_rank;

-- Export basket treemap: product shares
SELECT
    cpy.product_id,
    p.name_short_en         AS product_name,
    p.code                  AS hs92_code,
    p.top_parent_id         AS sector_id,   -- JOIN to get sector name
    cpy.export_value,
    cpy.export_rca,
    cpy.import_value,
    ROUND(100.0 * cpy.export_value / SUM(cpy.export_value) OVER (), 2) AS export_share_pct
FROM hs92.country_product_year_4 cpy
JOIN classification.product_hs92 p
    ON cpy.product_id = p.product_id
    AND p.product_level = 4
WHERE cpy.country_id = 404
  AND cpy.year = 2024
  AND cpy.export_value > 0
ORDER BY cpy.export_value DESC;

-- Top-3 export destination countries
SELECT
    partner_id,
    lc.name_short_en        AS partner_name,
    export_value
FROM hs92.country_country_year ccy
JOIN classification.location_country lc
    ON ccy.partner_id = lc.country_id
WHERE ccy.country_id = 404
  AND ccy.year = 2024
  AND ccy.location_level = 'country'
  AND ccy.partner_level = 'country'
ORDER BY export_value DESC
LIMIT 3;

-- Top-3 import origin countries
SELECT
    partner_id,
    lc.name_short_en        AS partner_name,
    import_value
FROM hs92.country_country_year ccy
JOIN classification.location_country lc
    ON ccy.partner_id = lc.country_id
WHERE ccy.country_id = 404
  AND ccy.year = 2024
  AND ccy.location_level = 'country'
  AND ccy.partner_level = 'country'
ORDER BY import_value DESC
LIMIT 3;

-- 5-year export CAGR (using lookback table)
SELECT
    country_id,
    lookback,
    lookback_year,
    export_value_cagr   -- pre-computed CAGR
FROM hs92.country_product_lookback_1  -- 1-digit = aggregate; use _4 for product detail
WHERE country_id = 404
  AND lookback = 5;     -- 3, 5, or 10 years
```

### GraphQL API (Country Pages)

```graphql
{
  countryProfile(location: "location-404") {
    exportValue
    importValue
    exportValueRank
    currentAccount { quantity year }
    exportValueNonOil
  }
  countryLookback(
    id: "location-404"
    yearRange: FiveYears
    exportValueConstGrowthCagrYearRange: FiveYears
    exportValueGrowthNonOilConstCagrYearRange: FiveYears
  ) {
    exportValueConstGrowthCagr
    exportValueGrowthNonOilConstCagr
    largestContributingExportProduct { shortName code }
  }
  treeMap(facet: CPY_C, location: "location-404", year: 2024) {
    ... on TreeMapProduct {
      product { shortName code }
      exportValue
      importValue
      rca
    }
  }
  treeMap(facet: CCY_C, location: "location-404", year: 2024) {
    ... on TreeMapLocation {
      location { shortName }
      exportValue
      importValue
    }
  }
}
```

**Pitfall:** The Country Pages treemap (`treeMap(facet: CPY_C)`) includes **services** (when the country reports services data) in addition to goods. The SQL schemas `hs92.*` cover goods only. To include services alongside goods, you must UNION data from `services_unilateral.country_product_year_4`. Always check `classification.location_country.reported_serv_recent` to determine if services data should be included.

---

## Subpage 3: Export Complexity (`/countries/{id}/export-complexity`)

**What is shown:** ECI ranking, 10-year ECI rank change, same treemap as Export Basket but colored by PCI (Product Complexity Index) instead of sector.

### Metric: Complexity of Exports (COG) — derivation

The treemap colors products by PCI. The overall export basket complexity (COG, Country's COG = the weighted average PCI of the basket) can be approximated as:

```sql
-- Weighted-average PCI of export basket (approximation of COG metric)
SELECT
    cpy.country_id,
    cpy.year,
    SUM(cpy.export_value * py.pci) / NULLIF(SUM(cpy.export_value), 0) AS weighted_avg_pci
FROM hs92.country_product_year_4 cpy
JOIN hs92.product_year_4 py
    ON cpy.product_id = py.product_id
    AND cpy.year = py.year
WHERE cpy.country_id = 404
  AND cpy.year = 2024
  AND cpy.export_value > 0;
```

Note: The Atlas ECI is derived via method of reflections or fitness/complexity algorithms on the full trade matrix, not as a simple weighted average. The weighted PCI above is an approximation for illustrative purposes only.

### SQL Patterns

```sql
-- ECI history for rank trend
SELECT
    cy.year,
    cy.eci,
    RANK() OVER (PARTITION BY cy.year ORDER BY cy.eci DESC) AS eci_rank
FROM hs92.country_year cy
WHERE cy.country_id = 404
  AND cy.year BETWEEN 2014 AND 2024  -- 10-year window
ORDER BY cy.year;

-- ECI rank change over 10 years (manual)
WITH ranked AS (
    SELECT year, RANK() OVER (PARTITION BY year ORDER BY eci DESC) AS eci_rank
    FROM hs92.country_year
    WHERE year IN (2014, 2024)
)
SELECT
    MAX(CASE WHEN year = 2014 THEN eci_rank END) AS rank_10yr_ago,
    MAX(CASE WHEN year = 2024 THEN eci_rank END) AS rank_current,
    MAX(CASE WHEN year = 2014 THEN eci_rank END)
        - MAX(CASE WHEN year = 2024 THEN eci_rank END) AS rank_improvement
FROM ranked;

-- Product PCI values for treemap coloring
SELECT
    cpy.product_id,
    p.name_short_en,
    p.code,
    py.pci,
    cpy.export_value,
    cpy.normalized_pci   -- country-specific normalized PCI (0-1 scale)
FROM hs92.country_product_year_4 cpy
JOIN hs92.product_year_4 py
    ON cpy.product_id = py.product_id AND cpy.year = py.year
JOIN classification.product_hs92 p
    ON cpy.product_id = p.product_id AND p.product_level = 4
WHERE cpy.country_id = 404
  AND cpy.year = 2024
  AND cpy.export_value > 0
ORDER BY cpy.export_value DESC;
```

### GraphQL API (Country Pages)

```graphql
{
  countryProfile(location: "location-404") {
    latestEciRank
  }
  countryLookback(
    id: "location-404"
    eciRankChangeYearRange: TenYears
    eciChangeYearRange: TenYears
  ) {
    eciRankChange
    eciChange
  }
  allProductYear(productClass: HS, productLevel: fourDigit, year: 2024) {
    product { code shortName }
    pci
    complexityLevel   # low | moderate | high
  }
}
```

---

## Subpage 4: Export Growth Dynamics (`/countries/{id}/growth-dynamics`)

**What is shown:** Bubble/scatter chart where X = product complexity (PCI), Y = export CAGR over selected period (3/5/10 years), bubble size = trade volume. ECI reference line at country's ECI value. Text classification of growth pattern (Troubling/Mixed/Static/Promising).

### SQL Patterns

```sql
-- Growth dynamics: combine PCI (x-axis) + CAGR (y-axis) per 2-digit product group
SELECT
    lb.product_id,
    p.name_short_en,
    p.code,
    p.top_parent_id         AS sector_id,
    py.pci                  AS product_pci,
    lb.export_value_cagr    AS export_cagr_5yr,  -- CAGR over lookback period
    lb.export_value_change  AS export_value_change,
    -- For bubble size (country trade):
    cpy.export_value        AS country_export_value,
    -- For bubble size (world trade):
    py.export_value         AS world_export_value
FROM hs92.country_product_lookback_2 lb   -- 2-digit level
JOIN hs92.product_year_2 py
    ON lb.product_id = py.product_id
    AND py.year = 2024
JOIN classification.product_hs92 p
    ON lb.product_id = p.product_id
    AND p.product_level = 2
JOIN hs92.country_product_year_2 cpy
    ON lb.country_id = cpy.country_id
    AND lb.product_id = cpy.product_id
    AND cpy.year = 2024
WHERE lb.country_id = 404
  AND lb.lookback = 5  -- 3, 5, or 10 years
ORDER BY country_export_value DESC;

-- ECI reference line value
SELECT eci FROM hs92.country_year
WHERE country_id = 404 AND year = 2024;
```

**`country_product_lookback_{N}` columns:**

| Column | Description |
|--------|-------------|
| `lookback` | Number of lookback years (3, 5, or 10) |
| `lookback_year` | Starting year (e.g., 2019 for a 5-year lookback from 2024) |
| `export_value_cagr` | Compound annual growth rate of exports |
| `export_value_change` | Absolute change in export value |
| `export_value_growth` | Total growth (not annualized) |
| `global_market_share_change` | Change in global market share |
| `global_market_share_cagr` | CAGR of global market share |

Note: `country_product_lookback_*` tables exist only in the `hs92` schema.

### GraphQL API (Country Pages)

```graphql
{
  countryProductLookback(
    location: "location-404"
    yearRange: FiveYears
    productLevel: twoDigit
  ) {
    product { shortName code }
    exportValueConstCagr
    exportValueConstGrowth
  }
  countryYear(location: "location-404", year: 2024) {
    eci
  }
  countryLookback(
    id: "location-404"
    exportValueGrowthClassification: FiveYears
  ) {
    exportValueGrowthClassification  # Troubling | Mixed | Static | Promising
    largestContributingExportProduct { shortName code }
  }
}
```

**Growth pattern classification logic:** The API derives `ExportValueGrowthClassification` by identifying the two fastest-growing products (by CAGR), classifying each as high/medium/low complexity relative to the country benchmark, then mapping the pair: both high (or one high + one medium) = Promising; one low + one high = Mixed; both medium = Static; otherwise = Troubling.

---

## Subpage 5: Growth in Global Market Share (`/countries/{id}/market-share`)

**What is shown:** Multi-line time series chart (1996–2024) showing each sector's share of global trade. One line per sector (Textiles, Agriculture, Stone, Minerals, Metals, Chemicals, Vehicles, Machinery, Electronics, Services). Top bar shows the sector with the largest market share and total share of global trade.

### SQL Patterns

```sql
-- Sector-level market share per year
-- Step 1: Country sector exports per year
WITH country_sector AS (
    SELECT
        cpy.year,
        p.top_parent_id          AS sector_id,
        SUM(cpy.export_value)    AS country_export
    FROM hs92.country_product_year_4 cpy
    JOIN classification.product_hs92 p
        ON cpy.product_id = p.product_id AND p.product_level = 4
    WHERE cpy.country_id = 404
      AND cpy.year BETWEEN 1996 AND 2024
    GROUP BY cpy.year, p.top_parent_id
),
-- Step 2: World sector exports per year
world_sector AS (
    SELECT
        py.year,
        p.top_parent_id          AS sector_id,
        SUM(py.export_value)     AS world_export
    FROM hs92.product_year_4 py
    JOIN classification.product_hs92 p
        ON py.product_id = p.product_id AND p.product_level = 4
    WHERE py.year BETWEEN 1996 AND 2024
    GROUP BY py.year, p.top_parent_id
)
-- Step 3: Market share ratio
SELECT
    cs.year,
    cs.sector_id,
    cs.country_export,
    ws.world_export,
    ROUND(100.0 * cs.country_export / NULLIF(ws.world_export, 0), 4) AS market_share_pct
FROM country_sector cs
JOIN world_sector ws
    ON cs.year = ws.year AND cs.sector_id = ws.sector_id
ORDER BY cs.year, cs.sector_id;

-- Overall share of global trade (single year)
SELECT
    (SELECT export_value FROM hs92.country_year WHERE country_id = 404 AND year = 2024)
    / NULLIF(
        (SELECT SUM(export_value) FROM hs92.country_year WHERE year = 2024
         AND location_level = 'country'),
        0
    ) AS global_trade_share;
```

### GraphQL API (Country Pages)

```graphql
{
  countryProfile(location: "location-404") {
    marketShareMainSector { shortName }
    marketShareMainSectorDirection    # rising | falling | stagnant
    marketShareMainSectorPositiveGrowth
    structuralTransformationStep      # NotStarted | TextilesOnly | ElectronicsOnly | MachineryOnly | Completed
    structuralTransformationSector { shortName }
    structuralTransformationDirection # risen | fallen | stagnated
  }
}
```

**Pitfall:** The `treeMap(facet: CPY_C)` query from the Country Pages API includes services when the country reports services. The SQL `hs92.*` tables cover only goods. For a complete market share chart matching the website, also query `services_unilateral.country_product_year_4` and join against `services_unilateral.product_year_4` for goods-equivalent global totals. The visual on the website merges both.

---

## Subpage 6: Diversification into New Products (`/countries/{id}/new-products`)

**What is shown:** Treemap of newly exported products over a 15-year window. Peer comparison table (country vs. 3 similar countries). Diversification grade (A+ through D-). Diversity rank + rank change (15-year).

### New Products Logic

A product is "new" if:
- First 3 years of an 18-year window: RCA < 0.5 (not meaningfully exported)
- Last 3 years of the window: RCA >= 1.0 (firmly exported)

The `product_status = 'new'` flag in `hs92.country_product_year_4` captures this determination for the most recent year. `is_new = true` is equivalent.

### Diversification Grade Thresholds

| Grade | Criterion |
|-------|-----------|
| A+ | Top 10 countries by new product count |
| A | >= 30 new products |
| B | >= 13 new products |
| C | >= 6 new products |
| D | >= 3 new products |
| D- | < 3 new products |

### SQL Patterns

```sql
-- New products treemap
SELECT
    cpy.product_id,
    p.name_short_en,
    p.code,
    p.top_parent_id         AS sector_id,
    cpy.export_value,
    cpy.export_rca
FROM hs92.country_product_year_4 cpy
JOIN classification.product_hs92 p
    ON cpy.product_id = p.product_id AND p.product_level = 4
WHERE cpy.country_id = 404
  AND cpy.year = 2024
  AND cpy.product_status = 'new'   -- or is_new = true
ORDER BY cpy.export_value DESC;

-- Diversity rank (products with RCA > 1)
SELECT
    country_id,
    year,
    diversity,
    RANK() OVER (PARTITION BY year ORDER BY diversity DESC) AS diversity_rank
FROM hs92.country_year
WHERE year = 2024
ORDER BY diversity_rank;

-- Diversity rank change over 15 years
WITH ranked AS (
    SELECT
        country_id,
        year,
        RANK() OVER (PARTITION BY year ORDER BY diversity DESC) AS div_rank
    FROM hs92.country_year
    WHERE year IN (2009, 2024)  -- 15-year lookback
)
SELECT
    MAX(CASE WHEN year = 2009 THEN div_rank END) AS rank_15yr_ago,
    MAX(CASE WHEN year = 2024 THEN div_rank END) AS rank_current,
    MAX(CASE WHEN year = 2009 THEN div_rank END)
        - MAX(CASE WHEN year = 2024 THEN div_rank END) AS rank_change
FROM ranked
WHERE country_id = 404;
```

### GraphQL API (Country Pages)

```graphql
{
  countryProfile(location: "location-404") {
    diversificationGrade        # APlus | A | B | C | D | DMinus
    diversityRank
    diversity
    newProductExportValue
    newProductExportValuePerCapita
    newProductsComments           # TooFew | Sufficient
    newProductsIncomeGrowthComments  # LargeEnough | TooSmall
  }
  countryLookback(
    id: "location-404"
    yearRange: FifteenYears
  ) {
    diversityRankChange
    diversityChange
  }
  newProductsCountry(location: "location-404", year: 2024) {
    newProducts { shortName code }
    newProductExportValue
    newProductExportValuePerCapita
    newProductCount
  }
  newProductsComparisonCountries(location: "location-404", year: 2024, quantity: 3) {
    location { shortName code }
    newProductCount
    newProductExportValue
    newProductExportValuePerCapita
  }
}
```

**Pitfall:** The peer comparison table visible on the website comes from `newProductsComparisonCountries`, which selects comparison countries using internal logic (income peer group, geographic proximity, data quality). This cannot be replicated purely in SQL — you must call the GraphQL API or pre-select peer countries manually.

---

## Subpages 7 & 8: Product Space (`/countries/{id}/product-space` and `/paths`)

**Subpage 7** (`/product-space`) shows a generic, explanatory product space network — the same for every country. No country-specific data needed.

**Subpage 8** (`/paths`) shows the country-specific product space: colored nodes for products with RCA > 1, gray for non-exported products, network edges representing product proximity.

### SQL Patterns (Subpage 8 — country-specific product space)

```sql
-- Products the country exports (RCA > 1) vs. does not export
SELECT
    cpy.product_id,
    p.name_short_en,
    p.code,
    p.product_space_x       AS x,
    p.product_space_y       AS y,
    p.cluster_id,           -- maps to 8 product space clusters
    cpy.export_rca,
    cpy.export_value,
    CASE WHEN cpy.export_rca >= 1 THEN 'exported' ELSE 'not_exported' END AS node_color
FROM hs92.country_product_year_4 cpy
JOIN classification.product_hs92 p
    ON cpy.product_id = p.product_id AND p.product_level = 4
WHERE cpy.country_id = 404
  AND cpy.year = 2024
ORDER BY cpy.export_value DESC NULLS LAST;

-- Product space edges (relatedness between products)
SELECT
    pp.product_id,
    pp.target_id,
    pp.strength             -- proximity/relatedness score
FROM hs92.product_product_4 pp
WHERE pp.product_level = 4
ORDER BY pp.strength DESC;

-- Top bar: export count (RCA > 1) and COI rank
SELECT
    cy.diversity            AS rca_product_count,  -- count of products with RCA > 1
    cy.coi,
    RANK() OVER (PARTITION BY cy.year ORDER BY cy.coi DESC) AS coi_rank
FROM hs92.country_year cy
WHERE cy.country_id = 404
  AND cy.year = 2024;
```

**`classification.product_hs92_ps_edges` vs. `hs92.product_product_4`:** Both exist. Use `hs92.product_product_4` (`product_id`, `target_id`, `strength`) for edge weights. Use `classification.product_hs92_ps_edges` for additional metrics (`score`, `weight`, `umap_distance`).

### GraphQL API (Country Pages)

```graphql
{
  productSpace(productClass: HS, year: 2024, location: "location-404") {
    product { shortName code }
    rca
    x
    y
    connections { shortName code }
    exportValue
  }
  countryProfile(location: "location-404") {
    diversity
    latestCoiRank
    latestCoi
  }
}
```

---

## Subpage 9: Recommended Strategic Approach (`/countries/{id}/strategic-approach`)

**What is shown:** Scatter plot of all 145 countries, X = relative complexity (ECI adjusted for natural resources and GDP), Y = COI. Country is highlighted in its quadrant. Four quadrants map to four strategic approaches.

### Strategic Approach Quadrants

| Approach | Quadrant | Meaning |
|----------|----------|---------|
| `LightTouch` | High complexity, high COI | Ample nearby opportunities; leverage existing successes |
| `ParsimoniousIndustrial` | Low complexity, high COI | Opportunities nearby but not yet realized; targeted policy interventions |
| `TechFrontier` | High complexity, low COI | Already at frontier; growth from innovation, not diversification |
| `StrategicBets` | Low complexity, low COI | Few nearby paths; must make deliberate ambitious sectoral investments |

**This metric (`policyRecommendation`) is NOT reproducible via SQL alone.** It is a derived classification from `eciNatResourcesGdpControlled` (ECI adjusted via partial correlation removing natural resource rents and GDP effects) and COI, computed by the Country Pages API.

### GraphQL API (Country Pages)

```graphql
{
  countryProfile(location: "location-404") {
    policyRecommendation          # ParsimoniousIndustrial | StrategicBets | LightTouch | TechFrontier
    eciNatResourcesGdpControlled
    latestCoi
    coiClassification             # low | medium | high
  }
  allCountryProfiles {
    location { shortName code }
    eciNatResourcesGdpControlled
    policyRecommendation
  }
  allCountryYear(year: 2024) {
    location { shortName }
    eci
    eciRank
    coi
    coiRank
  }
}
```

### SQL Approximation (position on scatter, not the classification)

```sql
-- Plot all countries by ECI and COI for a given year
SELECT
    cy.country_id,
    lc.name_short_en,
    cy.eci,
    cy.coi,
    RANK() OVER (ORDER BY cy.eci DESC) AS eci_rank,
    RANK() OVER (ORDER BY cy.coi DESC) AS coi_rank
FROM hs92.country_year cy
JOIN classification.location_country lc
    ON cy.country_id = lc.country_id
WHERE cy.year = 2024
  AND cy.location_level = 'country'
ORDER BY cy.eci DESC;
```

---

## Subpages 10 & 11: Growth Opportunities (`/growth-opportunities` and `/product-table`)

**Not available for highest-complexity countries** (e.g., USA, Germany, Japan — those in the Technological Frontier quadrant). The `show_feasibility = false` flag on products and API-side suppression gates these pages.

**What is shown:** Scatter plot (Distance vs. Opportunity Gain) and ranked table of the top 50 non-exported products with their feasibility metrics. Diamond ratings (1–10 deciles) for Distance, Opportunity Gain, and Complexity.

### SQL Patterns

```sql
-- Growth opportunities scatter: non-exported products with feasibility metrics
SELECT
    cpy.product_id,
    p.name_short_en,
    p.code,
    p.top_parent_id            AS sector_id,
    cpy.distance,              -- X-axis (lower = closer = easier)
    cpy.cog,                   -- Y-axis (higher = more pathways opened)
    cpy.normalized_pci,        -- Complexity (for coloring/axis)
    cpy.normalized_distance,   -- Normalized distance (0-1)
    cpy.normalized_cog,        -- Normalized COG (0-1)
    -- Global market size for bubble sizing:
    py.export_value            AS global_export_value,
    py.export_value_cagr_5     AS global_export_cagr_5yr,
    cpy.export_rca             -- Should be < 1 (not yet exported)
FROM hs92.country_product_year_4 cpy
JOIN hs92.product_year_4 py
    ON cpy.product_id = py.product_id AND cpy.year = py.year
JOIN classification.product_hs92 p
    ON cpy.product_id = p.product_id AND p.product_level = 4
WHERE cpy.country_id = 404
  AND cpy.year = 2024
  AND cpy.export_rca < 1        -- Not yet exported (opportunity products only)
  AND p.show_feasibility = true -- Exclude frontier-suppressed products
ORDER BY cpy.cog DESC           -- Sort by opportunity gain (default)
LIMIT 50;

-- To replicate "Balanced Portfolio" sort (weighted composite):
ORDER BY (0.2 * cpy.normalized_pci + 0.6 * cpy.normalized_distance + 0.2 * cpy.normalized_cog) DESC

-- Diamond ratings: the API pre-computes decile bins (1-10).
-- In SQL, approximate with NTILE(10):
SELECT
    product_id,
    distance,
    NTILE(10) OVER (ORDER BY distance ASC)  AS distance_decile_1_is_nearest,
    NTILE(10) OVER (ORDER BY cog DESC)      AS cog_decile_1_is_highest,
    NTILE(10) OVER (ORDER BY normalized_pci DESC) AS pci_decile
FROM hs92.country_product_year_4
WHERE country_id = 404 AND year = 2024 AND export_rca < 1;
```

### GraphQL API (Country Pages)

```graphql
{
  allCountryProductYear(location: "location-404", year: 2024, productClass: HS, productLevel: fourDigit) {
    product { shortName code }
    exportValue
    importValue
    normalizedDistanceDecileClassification     # Last | Second | ... | Top
    normalizedOpportunityGainDecileClassification
    normalizedPciDecileClassification
  }
  allProductYear(productClass: HS, productLevel: fourDigit, year: 2024) {
    product { shortName code }
    pci
    globalExportValue
    globalExportValueChangeFiveYears
    complexityLevel    # low | moderate | high
  }
  countryProfile(location: "location-404") {
    policyRecommendation   # determines which product selection weights to use
  }
}
```

**Product Selection Strategy Weights:**

| Strategy | Distance weight | Complexity weight | Opportunity Gain weight |
|----------|----------------|-------------------|------------------------|
| Low-hanging Fruit | 60% | 20% | 20% |
| Balanced Portfolio | 60% | 20% | 20% |
| Long Jumps | 20% | 20% | 60% |

(Exact weights shown on the website's pie chart for each selection criterion.)

---

## Subpage 12: Country Summary (`/countries/{id}/summary`)

**What is shown:** Aggregate summary of key stats from other pages: ECI rank change, new products count, 10-year growth projection, strategic approach.

This page has no unique data patterns. All stats are fetched from `countryProfile`, `countryLookback`, and `newProductsCountry` — queries already documented above.

```graphql
{
  countryProfile(location: "location-404") {
    growthProjection
    growthProjectionClassification
    policyRecommendation
  }
  countryLookback(id: "location-404", eciRankChangeYearRange: TenYears) {
    eciRankChange
  }
  newProductsCountry(location: "location-404", year: 2024) {
    newProductCount
  }
}
```

---

## API-to-Website Mapping Quick Reference

| Website section | URL slug | Primary SQL tables | Primary GraphQL queries |
|----------------|----------|--------------------|------------------------|
| Introduction / Hero | `/countries/{id}` | `hs92.country_year` | `countryProfile`, `countryYearRange`, `countryLookback`, `globalDatum` |
| Export Basket | `/export-basket` | `hs92.country_product_year_4`, `hs92.country_year`, `hs92.country_country_year` | `treeMap(CPY_C)`, `treeMap(CCY_C)`, `countryProfile`, `countryLookback` |
| Export Complexity | `/export-complexity` | `hs92.country_product_year_4`, `hs92.product_year_4`, `hs92.country_year` | `treeMap(CPY_C)`, `allProductYear`, `countryProfile`, `countryLookback` |
| Growth Dynamics | `/growth-dynamics` | `hs92.country_product_lookback_{N}`, `hs92.product_year_{N}`, `hs92.country_year` | `countryProductLookback`, `allProductYear`, `countryYear`, `countryLookback` |
| Market Share | `/market-share` | `hs92.country_product_year_4`, `hs92.product_year_4`, `hs92.country_year` | `countryProfile`, `treeMap(CPY_C)` (multi-year), `allProductYear` |
| New Products | `/new-products` | `hs92.country_product_year_4` (`product_status='new'`), `hs92.country_year` | `newProductsCountry`, `newProductsComparisonCountries`, `countryProfile`, `countryLookback` |
| Product Space (generic) | `/product-space` | `classification.product_hs92` (coordinates), `hs92.product_product_4` | `allProducts` |
| Product Space (country) | `/paths` | `hs92.country_product_year_4`, `classification.product_hs92`, `hs92.product_product_4` | `productSpace`, `countryProfile` |
| Strategic Approach | `/strategic-approach` | `hs92.country_year` (ECI, COI — approximate only) | `allCountryProfiles`, `allCountryYear`, `countryProfile` |
| Growth Opportunities | `/growth-opportunities` | `hs92.country_product_year_4`, `hs92.product_year_4` | `allCountryProductYear`, `allProductYear`, `countryProfile` |
| Product Table | `/product-table` | `hs92.country_product_year_4`, `hs92.product_year_4`, `classification.product_hs92` | `allCountryProductYear`, `allProductYear`, `countryProfile` |
| Summary | `/summary` | (aggregates above) | `countryProfile`, `countryLookback`, `newProductsCountry` |

---

## Metrics Available Only via GraphQL (Not Reproducible in SQL)

Some Country Pages metrics are derived by the Atlas backend and cannot be reconstructed from raw SQL tables alone:

| Metric | GraphQL field | Why not SQL-reproducible |
|--------|--------------|--------------------------|
| Strategic approach | `countryProfile.policyRecommendation` | Requires ECI adjusted for natural resources and GDP (partial correlation), not in DB columns |
| Diversification grade | `countryProfile.diversificationGrade` | Threshold-based ranking across all countries; threshold values not stored in DB |
| Growth projection rank | `countryProfile.growthProjectionRank` | Requires cross-country ranking of `growth_proj` — available as SQL RANK() but projection methodology is external (IMF WEO-based) |
| Export growth classification | `countryLookback.exportValueGrowthClassification` | Combination of top-2 CAGR products and their complexity classification vs benchmark |
| Peer comparison countries | `newProductsComparisonCountries` | Selection logic uses income peer group + geographic proximity filters |
| Product decile bins | `allCountryProductYear.normalizedDistanceDecileClassification` | Pre-computed decile bins normalized within country, not in raw DB columns |
| Structural transformation step | `countryProfile.structuralTransformationStep` | Computed from rolling 3-year RPOP averages against sector thresholds |

---

## Common Pitfalls

1. **Goods vs. goods+services totals.** The `exportValue` in `countryProfile` includes services when the country reports them. `hs92.country_year.export_value` is goods only. Comparing the two for countries with significant service exports (e.g., tourism-heavy economies) will show discrepancies.

2. **Product IDs are not HS codes.** `product_id` in all tables is an internal Atlas integer that does not correspond to the 4-digit HS code. Always JOIN to `classification.product_hs92` on `product_id` to get the human-readable `code` (e.g., "0901") and `name_short_en`.

3. **Country Pages API ID format.** The Country Pages API (`/api/countries/graphql`) requires string IDs like `"location-404"` and `"product-HS-726"`. The Explore API (`/api/graphql`) uses bare integers like `countryId: 404`. Never mix formats across endpoints.

4. **`location_level` filter.** The `country_year`, `country_product_year_4`, and `country_country_year` tables contain both `country` and `group` rows. Always filter `WHERE location_level = 'country'` when querying individual countries, or you may get aggregate group rows mixed in.

5. **Growth opportunities not available for frontier countries.** Products with `show_feasibility = false` and countries classified as `TechFrontier` do not have growth opportunity pages. Check `classification.product_hs92.show_feasibility` and `countryProfile.policyRecommendation` before querying.

6. **Lookback table exists only in hs92.** The `country_product_lookback_{1,2,4}` tables exist only in the `hs92` schema. For SITC or HS12 lookback analysis, you must calculate CAGRs manually from `country_product_year_*` time series.

7. **Country Pages API only supports `HS` and `SITC`.** The `ProductClass` enum has exactly two values: `HS` (equivalent to HS92) and `SITC`. There is no `HS12`, `HS22`, or `HS92` value — passing any of these returns a GraphQL validation error. The Explore API uses `HS92`, `HS12`, `HS22`, `SITC` instead. All product trade data from the Country Pages API is HS 1992 data.
