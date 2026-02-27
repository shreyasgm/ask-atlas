# Growth Dynamics

**Purpose:** Technical reference for the Export Growth Dynamics visualization on Atlas Country Pages and the related growth metrics available through the Country Pages GraphQL API.

**When to load this document:** Load when the user asks about a country's historical export growth rate or GDP
growth over a specific period, what the Growth Dynamics bubble chart shows (axes,
bubble sizing, ECI reference line), non-oil export CAGR, or how to use the
`countryLookback` or `countryProductLookback` API for historical growth data.
Also load for `StructuralTransformationStep` or `ExportValueGrowthClassification`
enum values. Do NOT load for forward-looking income growth projections (see
`strategic_approaches.md`) or for complete country page reproduction recipes
(see `country_page_reproduction.md`).

---

## 1. The Growth Dynamics Visualization

**Page URL:** `https://atlas.hks.harvard.edu/countries/{m49_id}/growth-dynamics`

### Chart Type and Axes

The Growth Dynamics page shows a **bubble/scatter chart** where each bubble represents one product group at the **2-digit HS level**.

| Axis | Meaning | Data Source |
|------|---------|-------------|
| **X-axis** | Product Complexity Index (PCI) — "Less Complex ← → More Complex" | `allProductYear.pci` |
| **Y-axis** | Annual Export Growth (CAGR) over the selected lookback period | `countryProductLookback.exportValueConstCagr` |

### Bubble Properties

| Property | Encoding | Options |
|----------|----------|---------|
| **Size** | Trade volume (configurable) | Country Trade, World Trade, None |
| **Color** | Sector (same color coding as other country pages) | By top-level HS sector |
| **Label** | Visible on largest bubbles (e.g., "Mineral fuels, oils and waxes") | Product short name |

### ECI Reference Line

A **dashed vertical line** is drawn at the country's current ECI value (e.g., "ECI (2024): −0.13"). Products to the **right** of this line are more complex than the country's current average complexity. Products to the **left** are less complex than the country average.

**Data source:** `countryYear.eci` (from the Country Pages GraphQL API)

### Lookback Periods (CAGR Dropdown)

The user can select a lookback window for the CAGR calculation:

| Dropdown Label | `LookBackYearRange` Enum | Example Period |
|----------------|--------------------------|----------------|
| 3 Years | `ThreeYears` | 2021–2024 |
| 5 Years | `FiveYears` | 2019–2024 |
| 10 Years | `TenYears` | 2014–2024 |

Note: 15-year lookback (`FifteenYears`) is available in the `countryLookback` API (used on the New Products page) but is **not** offered as a dropdown option on the Growth Dynamics chart itself.

### Chart Quadrant Interpretation

| Quadrant | Meaning |
|----------|---------|
| Top-right (high CAGR, high PCI) | Growing and complex — positive structural transformation |
| Top-left (high CAGR, low PCI) | Growing but in simple products — may not support long-term income growth |
| Bottom-right (negative CAGR, high PCI) | Complex products shrinking — possible deindustrialization risk |
| Bottom-left (negative CAGR, low PCI) | Shrinking simple products — least favorable growth pattern |

### Tooltip Data (on hover)

- Product name and HS92 code (e.g., "27 HS92")
- Gross Country Export value (absolute USD)
- Export Growth percentage (CAGR for selected period)

### Narrative Text Fields

The text section beneath the chart is powered by:
- `countryProfile.exportValueGrowthClassification` — overall growth pattern enum
- `countryLookback.largestContributingExportProduct` — products/sectors driving growth

---

## 2. GraphQL API: `countryProductLookback`

This query provides **per-product** growth data powering the scatter chart.

**Endpoint:** `/api/countries/graphql` (Country Pages API)

**Required argument:** `location: ID!` (string M49 code, e.g., `"location-404"` for Kenya)

**Optional arguments:** `yearRange: LookBackYearRange`, `productLevel: ProductLevel`

### `CountryProductLookback` Type (3 fields)

```graphql
product: Product                # Product identity (id, code, shortName, etc.)
exportValueConstGrowth: Float   # Absolute change in constant-dollar exports over the period
exportValueConstCagr: Float     # CAGR of constant-dollar exports over the period (Y-axis value)
```

**Note:** `exportValueConstCagr` is inflation-adjusted (constant dollars). This is the field used for the Y-axis of the Growth Dynamics scatter chart.

---

## 3. GraphQL API: `countryLookback`

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

## 4. Non-Oil Export Growth Rate

### Definition

`exportValueGrowthNonOilConstCagr` is the CAGR of a country's exports **after excluding oil and petroleum products**. Oil products are HS92 section 27 ("Mineral fuels, mineral oils and products of their distillation").

### Why It Matters

For resource-rich economies, total export CAGR can be dominated by commodity price swings rather than genuine productive transformation. The non-oil growth rate isolates capability-driven growth from resource windfall effects. For example:

- A country whose total exports grew 8% but non-oil exports grew only 1% is primarily riding an oil price cycle.
- A country where non-oil growth exceeds total export growth may be actively diversifying away from resource dependence.

### How to Use It

The non-oil export growth rate appears on the Export Basket page text narrative alongside `exportValueConstGrowthCagr`. Present both figures when a country has significant natural resource exports (`countryProfile.exportValueNatResources` or `netExportValueNatResources` can indicate this).

---

## 5. ECI Rank Changes Over Time

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

## 6. Structural Transformation Assessment

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

---

## 7. Growth Projections vs. Realized Growth

The Atlas provides **two distinct types** of growth information:

| Type | Field | Source | What It Measures |
|------|-------|--------|-----------------|
| **Projected growth** (forward-looking) | `countryProfile.growthProjection` | Country Pages API | 10-year GDP per capita growth projection based on ECI, COI, current income, and natural resource exports |
| **Realized growth** (backward-looking) | `countryLookback.gdpPerCapitaChangeConstantCagr` | Country Pages API | Actual historical CAGR of GDP per capita |
| **Export growth realized** | `countryLookback.exportValueConstGrowthCagr` | Country Pages API | Actual historical CAGR of total exports |

### Growth Projection Model

The `growthProjection` (annualized % for the next 10 years) is derived from four inputs:

1. **ECI** — current productive capability level
2. **COI** — connectedness to new complex products (Complexity Outlook Index)
3. **Current income level** — GDP per capita (countries with high complexity relative to income grow faster)
4. **Expected natural resource exports per capita** — adjusts for resource-driven income

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

## 8. SQL Patterns for Growth Dynamics Data

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

## 9. Extractable Data Points: Growth Dynamics Page

| # | Data Point | Displayed As | API Query | API Field |
|---|-----------|-------------|-----------|-----------|
| 31 | Product export growth (CAGR) | Tooltip Y-value | `countryProductLookback` | `exportValueConstCagr` |
| 32 | Country's current ECI value | Reference line label | `countryYear` | `eci` |
| 33 | Growth pattern description | Narrative text | `countryProfile` / `countryLookback` | `exportValueGrowthClassification` |
| 34 | Products/sectors driving growth | Narrative text | `countryLookback` | `largestContributingExportProduct` |
| 35 | Product gross export value | Tooltip absolute value | `countryProductLookback` | `exportValueConstGrowth` |

### Related Data Points on Other Pages

| # | Data Point | Page | API Query | API Field |
|---|-----------|------|-----------|-----------|
| 18 | Export growth rate (5yr annual avg) | Export Basket text | `countryLookback(FiveYears)` | `exportValueConstGrowthCagr` |
| 19 | Non-oil export growth rate | Export Basket text | `countryLookback(FiveYears)` | `exportValueGrowthNonOilConstCagr` |
| 6 | GDP per capita growth (5yr) | Main page text | `countryLookback` | `gdpPerCapitaChangeConstantCagr` |
| 7 | GDP per capita vs regional avg | Main page text | `countryLookback` | `gdpPcConstantCagrRegionalDifference` |
| 9 | ECI rank change (decade) | Main page text | `countryLookback(TenYears)` | `eciRankChange` |
| 28 | ECI rank change (10 years) | Export Complexity top bar | `countryLookback(TenYears)` | `eciRankChange` |
| 40 | Structural transformation status | Market Share text | `countryProfile` | `structuralTransformationStep` |

---

## 10. Key Relationships and Caveats

- **`countryProductLookback` vs. `country_product_lookback` tables:** The GraphQL query (`countryProductLookback`) and the SQL tables (`hs92.country_product_lookback_{1,2,4}`) contain equivalent pre-calculated CAGR data. The SQL tables include a `lookback` column (integer: 3, 5, 10, 15) to filter by period. The SQL tables are HS92-only.

- **Constant vs. current dollar CAGR:** All CAGR fields with `Const` or `const` in the name are **inflation-adjusted** (constant USD). Prefer these when comparing growth rates across time. Raw export values in `country_year` and `country_product_year` tables are in **current USD** and require deflation for accurate CAGR calculation.

- **ECI cross-year comparability:** Raw ECI values (`eciChange`) are computed independently via eigendecomposition each year and are **not directly comparable across years**. Use `eciRankChange` for trend statements. Only within-year ECI rankings are methodologically sound for comparative claims.

- **Growth Dynamics chart is HS92, 2-digit level:** The scatter chart uses `countryProductLookback` at the `twoDigit` product level, colored by the 11 top-level sectors from `classification.product_hs92`.

- **`countryLookback` per-field year ranges:** Each field in `CountryLookback` has its own year range argument. Calling `countryLookback(id: $id, exportValueConstGrowthCagrYearRange: FiveYears, eciRankChangeYearRange: TenYears)` returns export CAGR over 5 years and ECI rank change over 10 years in a single API call.
