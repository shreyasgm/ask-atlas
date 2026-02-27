# Inflation Adjustment and Trade Valuation

**Purpose:** Reference for how trade values are denominated, how to convert between current and constant (inflation-adjusted) USD, and how CIF/FOB valuation conventions affect Atlas import and export figures.

**When to load this document:** Load when the user asks about constant vs. current USD trade values, the FRED
PPIACO deflator Atlas uses (how to apply it, which DB table stores it), CIF vs.
FOB valuation conventions (why import and export values for the same flow differ),
or GDP variant choices (PPP, constant, per capita). Also load when import/export
value discrepancies are traced to valuation methodology. Do NOT load for historical
export growth rate queries (see `growth_dynamics.md`) or for trade data construction
methodology (see `trade_methodology.md`).

---

## 1. Current vs. Constant USD

All raw trade values in the Atlas database (`export_value`, `import_value`) are stored in **current (nominal) USD**. This is the default on Atlas Explore visualizations.

| Mode | What it means | When to use |
|---|---|---|
| **Current USD** (nominal) | Values expressed in the USD purchasing power of the reporting year. A dollar in 1990 is worth more than a dollar in 2020. | Point-in-time comparisons, single-year rankings, market share calculations. |
| **Constant USD** (real) | Values deflated to the purchasing power of the most recent Atlas data year (currently **2024**). Cross-year comparisons are meaningful. | Historical trend analysis, growth rates, long-run comparisons across years. |

**Default in Atlas Explore:** The "Trade Over Time" page (overtime) defaults to **Current Gross Exports**. The user can toggle to "Constant (2024 USD)", "Per Capita", or "Per Capita Constant (2024 USD)" using the Y-axis selector on the left side panel.

**Recommendation from Atlas documentation:** For growth rates and long-run trend analysis, constant-dollar values are recommended.

---

## 2. The FRED PPIACO Deflator

### What it is

Atlas constant-dollar values use the **FRED Producer Price Index by Commodity: All Commodities** (series identifier: PPIACO), published by the U.S. Bureau of Labor Statistics. The raw index uses **base year 1982 = 100**.

Atlas re-expresses PPIACO as a multiplicative deflation factor normalized so that the **most recent Atlas data year = 1.0**. Any prior year has a deflator > 1.0 (meaning values must be scaled up to reach base-year purchasing power — but Atlas applies division, so the math yields constant values in base-year USD).

**Source:** Federal Reserve Economic Data (FRED), accessed by the Growth Lab for annual data ingestion.

### How Atlas uses it

The constant-dollar conversion formula is:

```
constant_value = current_value / deflator
```

where `deflator` for the base year (e.g., 2024) = **1.0**, and deflators for earlier years are > 1.0 (e.g., the 1960 deflator is approximately 7.37, meaning 1960 nominal values are divided by 7.37 to express them in 2024 dollars).

### Where deflators are stored in the DB

Deflators live in the `public.year` table — a small lookup table with one row per calendar year.

---

## 3. `public.year` Table

### Schema

```sql
-- Schema: public (no schema prefix needed in cross-schema queries)
CREATE TABLE public.year (
    year     INTEGER,          -- Calendar year (e.g., 1995, 2024)
    deflator DOUBLE PRECISION  -- Multiplicative deflation factor (base year = 1.0)
);
```

### Sample rows (from seed data)

| year | deflator |
|------|----------|
| 1960 | 7.372967 |
| 1970 | 5.616368 |
| 1980 | ~2.8 (estimated) |
| 1990 | ~1.7 (estimated) |
| 2000 | ~1.2 (estimated) |
| 2010 | ~1.05 |
| 2020 | 0.842527 |
| 2023 | 0.715636 |
| 2024 | 1.000000 (base year) |

Note: Deflators < 1.0 for years near the base year reflect the re-normalization. The exact value for 2024 is 1.0 by construction.

### SQL: Converting current to constant USD

To express HS92 export values in constant 2024 USD, join `public.year` on the year column:

```sql
SELECT
    cpy.country_id,
    cpy.year,
    cpy.export_value                           AS export_value_current_usd,
    cpy.export_value / y.deflator              AS export_value_constant_2024_usd
FROM hs92.country_product_year_4 AS cpy
JOIN public.year AS y
    ON cpy.year = y.year
WHERE cpy.country_id = 404   -- Kenya
  AND cpy.product_id = 726   -- example product
ORDER BY cpy.year;
```

The same join pattern applies to all trade tables across all schemas (sitc, hs12, services_unilateral, etc.) because `public.year` is classification-agnostic.

### SQL: Constant-dollar country totals over time

```sql
SELECT
    cy.year,
    cy.export_value                             AS export_current_usd,
    cy.export_value / y.deflator                AS export_constant_2024_usd
FROM hs92.country_year AS cy
JOIN public.year AS y
    ON cy.year = y.year
WHERE cy.country_id = 404
ORDER BY cy.year;
```

---

## 4. CIF vs. FOB Valuation

### Definitions

| Convention | Full name | What it includes | Used by |
|---|---|---|---|
| **FOB** | Free On Board | Value at the point of export (port of departure); excludes shipping and insurance costs | Exporters when reporting shipments |
| **CIF** | Cost, Insurance, Freight | FOB value plus international shipping and insurance costs | Importers when recording receipts |

### Why this matters for Atlas data

International trade is recorded twice: by exporters (FOB) and importers (CIF). Because CIF > FOB for the same shipment, a country's reported imports will typically exceed the mirrored exports recorded by its trading partners — even for the same bilateral flow in the same year. The average CIF/FOB margin is approximately **5%**, though it varies by distance, product, and transport mode.

### How Atlas handles it

The Growth Lab reconciliation methodology:

1. **Adjusts** importer-reported (CIF) values toward FOB comparability before combining them with exporter reports.
2. **Weights** each country's report by a reliability score derived from its cross-partner consistency.
3. **Combines** exporter (FOB) and importer (CIF-adjusted) reports into a single estimated bilateral trade value, placing greater weight on more reliable sources.

This is the primary reason Atlas values differ from raw UN COMTRADE data and WTO reported figures. A full description is in Bustos et al. (2026), *Tackling Discrepancies in Trade Data*, Scientific Data.

**Practical implication for queries:** When a user asks why Atlas import values differ from export values for the same country pair, the CIF/FOB margin (and reliability weighting) is the expected explanation, not a data error.

---

## 5. GDP Variants in `country_year` Tables

The SQL `country_year` tables and the GraphQL `countryYear` type carry four GDP variants. The Atlas Country Pages use `gdppc_const` (constant USD per capita) for complexity-income comparisons and growth projections.

| Column (SQL) | GraphQL field | Definition |
|---|---|---|
| `gdp` | `gdp` | GDP in current USD (nominal) |
| `gdp_ppp` | `gdpPpp` | GDP in current PPP-adjusted USD |
| `gdp_const` | `gdpConst` | GDP in constant USD (PPIACO-deflated, base = most recent year) |
| `gdp_ppp_const` | `gdpPppConst` | GDP in constant PPP-adjusted USD |
| `gdppc` | `gdppc` | GDP per capita, current USD |
| `gdppc_ppp` | `gdppcPpp` | GDP per capita, current PPP |
| `gdppc_const` | `gdppcConst` | GDP per capita, **constant USD** — used for Atlas complexity-income analysis |
| `gdppc_ppp_const` | `gdppcPppConst` | GDP per capita, constant PPP |

**Source:** IMF World Economic Outlook (WEO).

**When to use which variant:**
- Use `gdppc_const` for cross-year income comparisons and growth-rate calculations.
- Use `gdppc_ppp` for cross-country living standards comparisons (removes price-level differences between countries).
- Use `gdppc` (current) only for single-year nominal comparisons.

**Per-capita trade values:** Divide `export_value` (current) or the constant-adjusted export value by `population` (from the same `country_year` row). The GraphQL `countryYear.population` field holds the same value.

---

## 6. GraphQL API: Requesting Deflators and Constant Values

### Fetching deflators from the Explore API

The `year` query returns the deflator for each calendar year:

```graphql
query GetDeflators($yearMin: Int, $yearMax: Int) {
  year(yearMin: $yearMin, yearMax: $yearMax) {
    year
    deflator
  }
}
```

The frontend uses this response to compute constant-dollar values client-side by dividing each year's `exportValue` by the corresponding `deflator`.

### Constant-dollar fields in `productYear`

The `ProductYear` type exposes pre-computed constant-dollar growth metrics directly (no manual deflation needed):

```
exportValueConstGrowth5     -- 5-year export value growth in constant USD
importValueConstGrowth5     -- 5-year import value growth in constant USD
exportValueConstCagr5       -- 5-year CAGR of export value, constant USD
importValueConstCagr5       -- 5-year CAGR of import value, constant USD
```

### Constant-dollar fields in `CountryLookback` (Country Pages API)

The Country Pages API `countryLookback` type exposes pre-aggregated constant-dollar growth rates:

```
exportValueNonOilCagr3/5/10/15    -- Non-oil export CAGR over 3/5/10/15 years (constant USD)
gdpConstCagr3/5/10/15             -- GDP CAGR, constant USD
gdppcConstCagr3/5/10/15           -- GDP per capita CAGR, constant USD
```

These are the authoritative source for "real export growth rate" questions and do not require joining `public.year`.

---

## 7. Quick Reference: Answering Common User Questions

| User question | Correct approach |
|---|---|
| "What were Kenya's exports in real terms in 2010?" | SQL: join `hs92.country_year` + `public.year`, compute `export_value / deflator` |
| "What is Kenya's real export growth rate?" | Country Pages API `countryLookback.exportValueNonOilCagr5`, or SQL CAGR computed on deflated values |
| "Why do Atlas import values differ from WTO data?" | CIF/FOB adjustment + reliability-weighted mirroring; Atlas applies FOB normalization before combining reports |
| "What is constant 2024 USD?" | Nominal values divided by the PPIACO-derived deflator for the reporting year; base year (2024) deflator = 1.0 |
| "Which GDP variant does Atlas use for per-capita income?" | `gdppc_const` (constant USD) for complexity-income analysis; source is IMF WEO |
| "Is the default view on Atlas inflation-adjusted?" | No. Atlas Explore defaults to current (nominal) USD. The user must toggle the Y-axis to "Constant (2024 USD)" |
