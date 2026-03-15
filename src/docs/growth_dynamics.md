---
title: Growth Dynamics
purpose: >
  Technical reference for the Export Growth Dynamics visualization on Atlas Country
  Pages and the related growth metrics available through the Country Pages GraphQL API.
keywords: [growth dynamics, CAGR, export growth, GDP growth, ECI, countryLookback, countryProductLookback, bubble chart, scatter, non-oil, structural transformation, growth projection, lookback]
when_to_load: >
  Load when the user asks about a country's historical export growth rate or GDP
  growth over a specific period, how to interpret growth dynamics (which products
  are growing and at what complexity level), non-oil export CAGR, or how to use
  the `countryLookback` or `countryProductLookback` API for historical growth data.
  Also load for `StructuralTransformationStep` or `ExportValueGrowthClassification`
  enum values.
when_not_to_load: >
  Do NOT load for forward-looking income growth projections (see
  `strategic_approaches.md`).
related_docs: [strategic_approaches.md]
---

## 1. Growth Dynamics: Export Growth vs. Product Complexity Interpretation

**Page URL:** `https://atlas.hks.harvard.edu/countries/{m49_id}/growth-dynamics`

Growth dynamics analysis examines whether a country's export growth is concentrated in complex or simple products. Each product group (at the 2-digit HS level) has two key attributes:
- **Product Complexity (PCI):** How sophisticated the product is — sourced from `allProductYear.pci`
- **Export Growth (CAGR):** How fast the country's exports of that product are growing — sourced from `countryProductLookback.exportValueConstCagr`

The country's current **ECI** value (`countryYear.eci`) serves as a benchmark. Products more complex than the country's average ECI represent structural upgrading when they grow; products less complex represent deepening of existing capabilities.

### Quadrant Interpretation: Growth vs. PCI Relative to Country ECI

| Growth + Complexity Combination | Meaning |
|------|---------|
| High growth, high PCI (above country ECI) | Growing and complex — positive structural transformation |
| High growth, low PCI (below country ECI) | Growing but in simple products — may not support long-term income growth |
| Negative growth, high PCI | Complex products shrinking — possible deindustrialization risk |
| Negative growth, low PCI | Shrinking simple products — least favorable growth pattern |

The key diagnostic question: **Is the country growing into more complex products, or is growth concentrated in simple commodities?** Countries experiencing genuine structural transformation show strong growth in products above their ECI benchmark.

### Lookback Periods: 3, 5, and 10-Year CAGR Windows

The user can select a lookback window for the CAGR calculation:

| Dropdown Label | `LookBackYearRange` Enum | Example Period |
|----------------|--------------------------|----------------|
| 3 Years | `ThreeYears` | 2021–2024 |
| 5 Years | `FiveYears` | 2019–2024 |
| 10 Years | `TenYears` | 2014–2024 |

Note: 15-year lookback (`FifteenYears`) is available in the `countryLookback` API (used on the New Products page) but is **not** offered as a dropdown option on the Growth Dynamics chart itself.

### Chart Quadrant Layout: CAGR (Y-axis) vs. PCI (X-axis)

| Quadrant | Meaning |
|----------|---------|
| Top-right (high CAGR, high PCI) | Growing and complex — positive structural transformation |
| Top-left (high CAGR, low PCI) | Growing but in simple products — may not support long-term income growth |
| Bottom-right (negative CAGR, high PCI) | Complex products shrinking — possible deindustrialization risk |
| Bottom-left (negative CAGR, low PCI) | Shrinking simple products — least favorable growth pattern |

### Tooltip and Narrative Data

**Tooltip (on hover):**
- Product name and HS92 code (e.g., "27 HS92")
- Gross Country Export value (absolute USD)
- Export Growth percentage (CAGR for selected period)

**Narrative text fields** beneath the chart are powered by:
- `countryProfile.exportValueGrowthClassification` — overall growth pattern enum
- `countryLookback.largestContributingExportProduct` — products/sectors driving growth

---

## 2. GraphQL API: `countryProductLookback` — Per-Product Export CAGR

This query provides **per-product** export growth data for growth dynamics analysis.

**Endpoint:** `/api/countries/graphql` (Country Pages API)

**Required argument:** `location: ID!` (string M49 code, e.g., `"location-404"` for Kenya)

**Optional arguments:** `yearRange: LookBackYearRange`, `productLevel: ProductLevel`

### `CountryProductLookback` Type (3 fields)

```graphql
product: Product                # Product identity (id, code, shortName, etc.)
exportValueConstGrowth: Float   # Absolute change in constant-dollar exports over the period
exportValueConstCagr: Float     # CAGR of constant-dollar exports over the period (Y-axis value)
```

**Note:** `exportValueConstCagr` is inflation-adjusted (constant dollars). This is the field used to measure export growth rate across products in growth dynamics analysis.

---

## 3. GraphQL API: `countryLookback` — Country-Level Export, GDP, and ECI Growth Metrics

This query provides **country-level** aggregate growth metrics, used on the Export Basket page, Export Complexity page, and Summary page — as well as for answering direct user questions about growth rates.

**Endpoint:** `/api/countries/graphql` (Country Pages API)

**Required argument:** `id: ID!` (string, e.g., `"location-404"`)

**Optional arguments:** Multiple named `yearRange` parameters per field (each field's lookback period is independently configurable).

### `CountryLookback` Type (13 fields)

```graphql
id
eciRankChange: Int                          # Change in ECI rank over the yearRange
exportValueConstGrowthCagr: Float           # CAGR of total exports (constant USD)
exportValueGrowthNonOilConstCagr: Float     # CAGR of non-oil exports (constant USD)
largestContributingExportProduct: [Product] # Products/sectors driving growth
eciChange: Float                            # Change in raw ECI value over the yearRange
diversityRankChange: Int                    # Change in diversity rank
diversityChange: Int                        # Change in product diversity count
gdpPcConstantCagrRegionalDifference: GDPPCConstantCAGRRegionalDifference  # Above | InLine | Below
exportValueGrowthClassification: ExportValueGrowthClassification  # Troubling | Mixed | Static | Promising
gdpChangeConstantCagr: Float                # CAGR of total GDP (constant USD)
gdpPerCapitaChangeConstantCagr: Float       # CAGR of GDP per capita (constant USD)
gdpGrowthConstant: Float                    # GDP growth in constant dollars
```

### Common Year Range Patterns

| Use Case | Query Pattern | Field |
|----------|---------------|-------|
| 5-year export growth rate | `countryLookback(id: $id, exportValueConstGrowthCagrYearRange: FiveYears)` | `exportValueConstGrowthCagr` |
| Non-oil export growth (5yr) | `countryLookback(id: $id, exportValueGrowthNonOilConstCagrYearRange: FiveYears)` | `exportValueGrowthNonOilConstCagr` |
| ECI rank change (decade) | `countryLookback(id: $id, eciRankChangeYearRange: TenYears)` | `eciRankChange` |
| GDP per capita growth (5yr) | `countryLookback(id: $id, gdpChangeCagrYearRange: FiveYears)` | `gdpPerCapitaChangeConstantCagr` |
| GDP vs regional avg | `countryLookback(id: $id, gdpChangeCagrYearRange: FiveYears)` | `gdpPcConstantCagrRegionalDifference` |
| Diversity rank change (15yr) | `countryLookback(id: $id, eciRankChangeYearRange: FifteenYears)` | `diversityRankChange` |

---

## 4. Non-Oil Export Growth Rate: Isolating Capability-Driven Growth from Resource Windfalls

`exportValueGrowthNonOilConstCagr` is the CAGR of a country's exports **after excluding oil and petroleum products**. Oil products are HS92 section 27 ("Mineral fuels, mineral oils and products of their distillation").

For resource-rich economies, total export CAGR can be dominated by commodity price swings rather than genuine productive transformation. The non-oil growth rate isolates capability-driven growth from resource windfall effects. For example:

- A country whose total exports grew 8% but non-oil exports grew only 1% is primarily riding an oil price cycle.
- A country where non-oil growth exceeds total export growth may be actively diversifying away from resource dependence.

The non-oil export growth rate appears on the Export Basket page text narrative alongside `exportValueConstGrowthCagr`. Present both figures when a country has significant natural resource exports (`countryProfile.exportValueNatResources` or `netExportValueNatResources` can indicate this).

---

## 5. ECI Rank Changes Over Time: Trends and Cross-Year Comparability Caveats

### ECI Rank Change (API)

`countryLookback.eciRankChange` returns the integer change in ECI rank. A **negative value** means the country moved **up** in the rankings (rank 10 → rank 5 is a change of −5). A **positive value** means it moved **down**.

The default lookback for the Export Complexity page top bar is **10 years** (`eciRankChangeYearRange: TenYears`).

### ECI Value Change (API)

`countryLookback.eciChange` returns the change in the raw ECI score. Because ECI is re-computed via eigendecomposition each year, **raw ECI values are not directly comparable across years** — only within-year rankings are methodologically sound. Use `eciRankChange` for trend statements; use `eciChange` only with appropriate caveats.

### Historical ECI Time Series (SQL)

For multi-year ECI trajectories, use:

```sql
SELECT year, eci, eci_rank
FROM hs92.country_year
WHERE country_id = :country_id
  AND year BETWEEN :start_year AND :end_year
ORDER BY year;
```

Use HS12 schema (`hs12.country_year`) when the user specifies HS 2012. The HS12 schema covers 2012–2024 only.

---

## 6. Structural Transformation Assessment: Manufacturing Shift Stages and Growth Pattern Classification

Structural transformation refers to the shift in a country's export composition toward more complex, higher-value-added products. In Atlas terminology, it specifically tracks whether the country has gained market share in manufacturing sectors.

### API Fields (from `countryProfile`)

```graphql
structuralTransformationStep: StructuralTransformationStep
structuralTransformationSector: Product
structuralTransformationDirection: StructuralTransformationDirection
```

### `StructuralTransformationStep` Enum Values

| Value | Meaning |
|-------|---------|
| `NotStarted` | Country has not yet developed significant manufacturing export share |
| `TextilesOnly` | Has developed textiles/apparel exports (first stage of industrial transformation) |
| `ElectronicsOnly` | Has developed electronics exports |
| `MachineryOnly` | Has developed machinery exports |
| `Completed` | Has developed across multiple manufacturing sectors |

### `StructuralTransformationDirection` Enum Values

| Value | Meaning |
|-------|---------|
| `risen` | The structural transformation sector's market share is increasing |
| `fallen` | The structural transformation sector's market share is declining |
| `stagnated` | No meaningful change in market share |

The `structuralTransformationSector` field names the specific sector being assessed (e.g., Textiles, Electronics, Machinery).

### Growth Pattern Classification

`countryLookback.exportValueGrowthClassification` provides a narrative-ready assessment:

| Value | Meaning |
|-------|---------|
| `Troubling` | Export growth is negative or very low across most complexity levels |
| `Mixed` | Growth in some sectors/complexity levels, decline in others |
| `Static` | Little growth in any direction |
| `Promising` | Positive growth, especially in higher-complexity products |

**Derivation algorithm:** The API derives `ExportValueGrowthClassification` by identifying the two fastest-growing products (by CAGR), classifying each as high/medium/low complexity relative to the country's ECI benchmark, then mapping the pair: both high (or one high + one medium) = Promising; one low + one high = Mixed; both medium = Static; otherwise = Troubling.

---

## 7. Growth Projections vs. Realized Growth: Forward-Looking Forecast vs. Historical CAGR

The Atlas provides **two distinct types** of growth information:

| Type | Field | Source | What It Measures |
|------|-------|--------|-----------------|
| **Projected growth** (forward-looking) | `countryProfile.growthProjection` | Country Pages API | 10-year GDP per capita growth projection based on ECI, COI, current income, and natural resource exports |
| **Realized growth** (backward-looking) | `countryLookback.gdpPerCapitaChangeConstantCagr` | Country Pages API | Actual historical CAGR of GDP per capita |
| **Export growth realized** | `countryLookback.exportValueConstGrowthCagr` | Country Pages API | Actual historical CAGR of total exports |

### Growth Projection Model

The `growthProjection` is derived from an OLS regression with 5 features + decade dummies:

1. `ln_gdppc_const` — log of constant GDP per capita (convergence term)
2. `nr_growth_10` — 10-year change in real natural resource net exports per capita
3. `eci` — Economic Complexity Index (SITC classification)
4. `oppval` — Complexity Outlook Index (COI)
5. `eci × oppval` — interaction term (captures synergy between complexity and opportunity connectedness)

Ten cohort regressions are averaged, outliers > 2.5× RMSE are removed, crisis countries (VEN, LBN, YEM) are excluded from training, and high-growth Asian countries (CHN, KOR, SGP) are restricted to post-1989 data. Final GDP growth = `100 × ((1 + point_est) × (1 + pop_est) - 1)`.

Countries with **higher ECI than their income suggests** tend to grow faster (unexploited productive potential). Countries with **lower ECI than their income suggests** tend to grow slower (income may be propped up by resource rents).

### Projection Classification Fields

```graphql
growthProjection: Float                                   # Annualized % growth rate
growthProjectionRank: Int                                 # Rank among all countries
growthProjectionClassification: GrowthProjectionClassification
  # rapid | moderate | slow
growthProjectionRelativeToIncome: GrowthProjectionRelativeToIncome
  # More | ModeratelyMore | Same | ModeratelyLess | Less
growthProjectionPercentileClassification: GrowthProjectionPercentileClassification
  # TopDecile | TopQuartile | TopHalf | BottomHalf
```

---

## 8. SQL Patterns for Growth Dynamics: Export CAGR, ECI Time Series, and Non-Oil Calculations

### Country-Level Growth Data

```sql
-- Export CAGR over N years (manual calculation from time series)
SELECT
    cy_end.year       AS end_year,
    cy_start.year     AS start_year,
    cy_end.export_value AS export_end,
    cy_start.export_value AS export_start,
    POWER(cy_end.export_value::float / NULLIF(cy_start.export_value, 0),
          1.0 / (cy_end.year - cy_start.year)) - 1 AS export_cagr
FROM hs92.country_year cy_end
JOIN hs92.country_year cy_start
    ON cy_start.country_id = cy_end.country_id
WHERE cy_end.country_id = :country_id
  AND cy_end.year   = :end_year
  AND cy_start.year = :start_year;
```

```sql
-- ECI time series for a country
SELECT year, eci, eci_rank
FROM hs92.country_year
WHERE country_id = :country_id
  AND year BETWEEN :start_year AND :end_year
ORDER BY year;
```

### Product-Level CAGR for Growth Dynamics Scatter

The `hs92.country_product_lookback_{1,2,4}` tables (HS92 schema only) contain **pre-calculated** lookback metrics. These tables are the SQL equivalent of the `countryProductLookback` GraphQL query.

```sql
-- Pre-calculated product-level CAGR (HS92 only)
-- The 'lookback' column stores the number of lookback years (3, 5, 10, 15)
SELECT
    cpl.product_id,
    p.name_en,
    p.code,
    cpl.export_value_cagr,   -- equivalent to countryProductLookback.exportValueConstCagr
    cpl.export_value_change,  -- equivalent to countryProductLookback.exportValueConstGrowth
    py.pci                    -- X-axis value for Growth Dynamics scatter
FROM hs92.country_product_lookback_4 cpl
JOIN classification.product_hs92 p  ON cpl.product_id = p.product_id
JOIN hs92.product_year_4 py         ON cpl.product_id = py.product_id
                                    AND py.year = :end_year
WHERE cpl.country_id = :country_id
  AND cpl.lookback   = :lookback_years  -- 3, 5, or 10
ORDER BY py.pci DESC;
```

**Important schema caveat:** `country_product_lookback` tables exist **only in the hs92 schema**. They are absent from `hs12`, `sitc`, `services_unilateral`, and `services_bilateral`.

### Growth Dynamics Scatter (Manual Calculation Without Lookback Table)

```sql
-- Calculate CAGR manually when lookback table is not available or for hs12/sitc
SELECT
    p.name_en,
    p.code,
    py_latest.pci,
    POWER(
        cpy_end.export_value::float / NULLIF(cpy_start.export_value, 0),
        1.0 / :n_years
    ) - 1 AS export_cagr,
    cy.eci  -- for ECI reference line
FROM hs92.country_product_year_4 cpy_end
JOIN hs92.country_product_year_4 cpy_start
    ON  cpy_start.country_id = cpy_end.country_id
    AND cpy_start.product_id = cpy_end.product_id
    AND cpy_start.year       = :start_year
JOIN classification.product_hs92 p  ON cpy_end.product_id = p.product_id
JOIN hs92.product_year_4 py_latest  ON cpy_end.product_id = py_latest.product_id
                                    AND py_latest.year = :end_year
CROSS JOIN (
    SELECT eci FROM hs92.country_year
    WHERE country_id = :country_id AND year = :end_year
) cy
WHERE cpy_end.country_id = :country_id
  AND cpy_end.year       = :end_year
  AND cpy_end.export_value > 0
ORDER BY py_latest.pci;
```

### Non-Oil Export CAGR (Manual SQL)

Oil products are HS92 product code 27 at the 2-digit level. The `classification.product_hs92` table has a `natural_resource` boolean column that can be used as a broader natural resource filter.

```sql
-- Non-oil export CAGR: exclude HS2 section 27 (mineral fuels)
WITH oil_products AS (
    SELECT product_id FROM classification.product_hs92
    WHERE code LIKE '27%'
)
SELECT
    cy_end.year,
    SUM(CASE WHEN op.product_id IS NULL THEN cpy_end.export_value ELSE 0 END) AS non_oil_exports_end,
    SUM(CASE WHEN op.product_id IS NULL THEN cpy_start.export_value ELSE 0 END) AS non_oil_exports_start,
    POWER(
        SUM(CASE WHEN op.product_id IS NULL THEN cpy_end.export_value ELSE 0 END)::float /
        NULLIF(SUM(CASE WHEN op.product_id IS NULL THEN cpy_start.export_value ELSE 0 END), 0),
        1.0 / :n_years
    ) - 1 AS non_oil_cagr
FROM hs92.country_product_year_4 cpy_end
JOIN hs92.country_product_year_4 cpy_start
    ON  cpy_start.country_id = cpy_end.country_id
    AND cpy_start.product_id = cpy_end.product_id
    AND cpy_start.year = :start_year
LEFT JOIN oil_products op ON cpy_end.product_id = op.product_id
WHERE cpy_end.country_id = :country_id
  AND cpy_end.year = :end_year;
```

---

## 9. Key Relationships and Caveats: Constant vs. Current Dollars, ECI Comparability, and Schema Availability

- **`countryProductLookback` vs. `country_product_lookback` tables:** The GraphQL query (`countryProductLookback`) and the SQL tables (`hs92.country_product_lookback_{1,2,4}`) contain equivalent pre-calculated CAGR data. The SQL tables include a `lookback` column (integer: 3, 5, 10, 15) to filter by period. The SQL tables are HS92-only.

- **Constant vs. current dollar CAGR:** All CAGR fields with `Const` or `const` in the name are **inflation-adjusted** (constant USD). Prefer these when comparing growth rates across time. Raw export values in `country_year` and `country_product_year` tables are in **current USD** and require deflation for accurate CAGR calculation.

- **ECI cross-year comparability:** Raw ECI values (`eciChange`) are computed independently via eigendecomposition each year and are **not directly comparable across years**. Use `eciRankChange` for trend statements. Only within-year ECI rankings are methodologically sound for comparative claims.

- **Growth Dynamics data is HS92, 2-digit level:** Growth dynamics analysis uses `countryProductLookback` at the `twoDigit` product level, organized by the 11 top-level sectors from `classification.product_hs92`.

- **`countryLookback` per-field year ranges:** Each field in `CountryLookback` has its own year range argument. Calling `countryLookback(id: $id, exportValueConstGrowthCagrYearRange: FiveYears, eciRankChangeYearRange: TenYears)` returns export CAGR over 5 years and ECI rank change over 10 years in a single API call.
