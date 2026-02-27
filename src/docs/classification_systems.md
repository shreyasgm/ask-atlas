---
title: Classification Systems
purpose: >
  Technical reference for the product classification systems used in the Atlas of
  Economic Complexity -- HS92, HS12, HS22, SITC Rev. 2, and Services -- covering
  year ranges, product counts, hierarchy levels, SQL table naming conventions,
  GraphQL enum values, and cross-classification conversion queries.
keywords: [classification, HS92, HS12, HS22, SITC, services, product level, product hierarchy, schema, SQL table naming, ProductClass, productLevel, conversion, concordance, EBOPS]
when_to_load: >
  Load when the user asks which classification system to use (HS92 vs HS12 vs HS22
  vs SITC), needs the correct SQL schema name or GraphQL enum value for a
  classification, asks about product hierarchy levels (HS2/HS4/HS6 digit levels),
  needs to convert between classification systems, or asks about year coverage and
  API access for a specific classification. Also load for HS22 questions (Explore
  API only -- no SQL schema).
when_not_to_load: >
  Do NOT load for year-range availability caveats (see `data_coverage.md`) or for
  metric formula questions.
related_docs: [data_coverage.md]
---

## Goods Classifications Overview

| Classification | Full Name | Year Range (Atlas) | ~Products at 4-digit | Default? |
|---|---|---|---|---|
| **HS92** | Harmonized System 1992 | 1995–2024 | ~1,200 | Yes — use when unspecified |
| **HS12** | Harmonized System 2012 | 2012–2024 | ~1,200 | No |
| **HS22** | Harmonized System 2022 | 2022–2024 | ~1,200 | No — **Explore API / GraphQL only** |
| **SITC** | Standard International Trade Classification Rev. 2 | 1962–2024 | ~780 | No — use for pre-1995 queries |

**Key guidance:**
- **HS92** — default for all goods queries; longest HS time series; most commonly studied.
- **HS12** — use when the user explicitly requests HS 2012, or when analysis starts from 2012 onward and updated product categories matter.
- **HS22** — available **only via the Explore API GraphQL endpoint** (`https://atlas.hks.harvard.edu/api/graphql`). It is **NOT in the SQL database** and cannot be queried via SQL. Do not attempt SQL queries against an `hs22` schema — it does not exist in the Atlas database.
- **SITC** — use when the user needs pre-1995 data (goes back to 1962); covers ~780 products at 4-digit level; based on SITC Revision 2. Does not capture products introduced after 1962 (e.g., smartphones).

**When classification is ambiguous:** If the user does not specify, use HS92.

---

## Data Availability by Classification

| Classification | SQL DB Schemas | Explore API enum | Country Pages API | Year Range |
|---|---|---|---|---|
| HS92 | `hs92.*` | `HS92` | `HS` | 1995–2024 |
| HS12 | `hs12.*` | `HS12` | Not supported | 2012–2024 |
| HS22 | **None — no SQL schema** | `HS22` | Not supported | 2022–2024 |
| SITC | `sitc.*` | `SITC` | `SITC` | 1962–2024 |
| Services | `services_unilateral.*`, `services_bilateral.*` | `servicesClass: unilateral` | Bundled | 1980–2024 |

**Note:** HS22 does not exist in the PostgreSQL database. Any SQL query for HS22 will fail. Route HS22 requests to the Explore API GraphQL pipeline.

---

## Product Hierarchy Levels

All HS classifications share the same hierarchy structure. SITC has an analogous but non-identical structure.

### HS Hierarchy

| Level | Name | Digit Count | Approximate Count | Example |
|---|---|---|---|---|
| 1 | Section | 1-digit groupings | ~21 sections | Section XVI: Machinery and Electrical Equipment |
| 2 | Chapter | 2-digit | ~97 chapters | Chapter 09: Coffee, Tea, Maté, Spices |
| 4 | Heading | 4-digit | ~1,200 headings | 0901: Coffee, whether or not roasted |
| 6 | Subheading | 6-digit | ~5,000 subheadings | 090111: Coffee, not roasted, not decaffeinated |

**Note on level 6:** 6-digit (subheading) data is available via the Explore API GraphQL only. It is not available in Country Pages API queries, and complexity metrics (ECI, PCI) are not computed at the 6-digit level.

### SITC Hierarchy

| Level | Approximate Count | Notes |
|---|---|---|
| 1-digit | ~10 sections | Broad categories |
| 2-digit | ~67 divisions | |
| 4-digit | ~780 groups | Most detailed level available in Atlas for SITC |

SITC does **not** have a 6-digit level in the Atlas.

### GraphQL `productLevel` integer values

The `productLevel` integer argument in GraphQL corresponds directly to the digit count:

| `productLevel` value | Meaning |
|---|---|
| `1` | 1-digit (section) |
| `2` | 2-digit (chapter/division) |
| `4` | 4-digit heading — most commonly used |
| `6` | 6-digit subheading — Explore API only |

---

## SQL Database Table Naming Convention

The Atlas SQL database uses PostgreSQL schemas to separate classifications. The schema name is the classification abbreviation; the table name encodes the data type and product level.

### Schema Names (SQL)

| SQL Schema | Classification |
|---|---|
| `hs92` | Harmonized System 1992 |
| `hs12` | Harmonized System 2012 |
| `sitc` | Standard International Trade Classification Rev. 2 |
| `services_unilateral` | Services trade, unilateral (exporter-product-year) |
| `services_bilateral` | Services trade, bilateral (exporter-importer-product-year) |

**No `hs22` schema exists in the SQL database.** HS22 is Explore API / GraphQL only.

### Table Naming Pattern

```
{schema}.{data_type}_{level}
```

Examples:

| Table | Meaning |
|---|---|
| `hs92.country_product_year_4` | HS92 classification, country × product × year, 4-digit level |
| `hs92.country_product_year_2` | HS92, 2-digit level |
| `hs92.country_product_year_6` | HS92, 6-digit level |
| `hs92.country_country_product_year_4` | HS92, bilateral trade by product, 4-digit |
| `hs92.country_year` | HS92, country × year aggregates (no product dimension) |
| `hs12.country_product_year_4` | HS12 classification, 4-digit |
| `sitc.country_product_year_4` | SITC classification, 4-digit |
| `services_unilateral.country_product_year_4` | Services unilateral, 4-digit |
| `services_bilateral.country_country_product_year_4` | Services bilateral, 4-digit |

### Classification Reference Tables (always in `classification` schema)

| Table | Purpose |
|---|---|
| `classification.product_hs92` | HS92 product catalog (codes, names, hierarchy, product space coordinates) |
| `classification.product_hs12` | HS12 product catalog |
| `classification.product_sitc` | SITC product catalog |
| `classification.product_services_unilateral` | Services (unilateral) product catalog |
| `classification.product_services_bilateral` | Services (bilateral) product catalog |
| `classification.location_country` | Country reference (ISO codes, income level, rankings eligibility) |
| `classification.location_group` | Country group reference (continents, regions, trade blocs, etc.) |

The `classification` schema does not contain a `product_hs22` table. HS22 catalog lookup must use the `productHs22` GraphQL query.

### Typical SQL JOIN Pattern

```sql
SELECT
    lc.name_short_en AS country,
    p.code,
    p.name_short_en AS product,
    cpy.export_value
FROM hs92.country_product_year_4 cpy
JOIN classification.location_country lc ON cpy.country_id = lc.country_id
JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
WHERE lc.iso3_code = 'KEN'
  AND cpy.year = 2022
ORDER BY cpy.export_value DESC;
```

---

## GraphQL API: Classification Enum Values

**Endpoint:** `https://atlas.hks.harvard.edu/api/graphql` (Explore API)

### `ProductClass` Enum (used in most trade queries)

| Enum value | Classification |
|---|---|
| `HS92` | Harmonized System 1992 |
| `HS12` | Harmonized System 2012 |
| `HS22` | Harmonized System 2022 |
| `SITC` | Standard International Trade Classification Rev. 2 |

**Usage example:**
```graphql
{
  countryProductYear(
    countryId: 404
    productClass: HS92
    productLevel: 4
    yearMin: 2022
    yearMax: 2022
  ) {
    productId
    exportValue
    exportRca
  }
}
```

### `ServicesClass` Enum

| Enum value | Meaning |
|---|---|
| `unilateral` | Services data (the only services class supported by the Explore API) |

### Product Catalog Queries (one per classification)

| GraphQL query | Returns |
|---|---|
| `productHs92(productLevel: Int, servicesClass: ServicesClass)` | HS92 product catalog |
| `productHs12(productLevel: Int, servicesClass: ServicesClass)` | HS12 product catalog |
| `productHs22(productLevel: Int, servicesClass: ServicesClass)` | HS22 product catalog |
| `productSitc(productLevel: Int, servicesClass: ServicesClass)` | SITC product catalog |

**Important:** Product IDs returned by these catalog queries are internal Atlas numerical IDs — they do **not** correspond to published HS or SITC codes. Use the `code` field in the response to get the HS/SITC code. The same internal product ID can be used across queries to join data.

### `ClassificationEnum` (used only in conversion queries)

This enum covers all historical HS and SITC vintages used in the Growth Lab's cross-classification conversion pipeline:

`SITC1962`, `SITC1976`, `SITC1988`, `HS1992`, `HS1997`, `HS2002`, `HS2007`, `HS2012`, `HS2017`, `HS2022`

### Country Pages API classification note

The Country Pages API (`/api/countries/graphql`) uses a simplified `ProductClass` enum with only `HS` and `SITC` (no explicit revision year). It defaults to HS92 when `HS` is specified.

---

## Services Classification

Services trade uses a separate classification system not based on HS or SITC.

**Standard:** Extended Balance of Payments Services (EBOPS 2010), approximately 12 top-level categories.

**Services categories** (approximate — actual names from `classification.product_services_unilateral`):
- Travel & tourism
- Transport
- Information and communication technology (ICT)
- Business services
- Financial services
- Insurance
- Government services
- Construction
- Personal, cultural, recreational services
- Maintenance and repair
- Manufacturing on physical inputs
- Other services

**Key differences from goods classifications:**
- No HS codes — services use internal Atlas product IDs only.
- No 6-digit level; maximum detail is 4-digit.
- No standard RCA or PCI computed for services.
- Services are excluded from complexity metrics (ECI, PCI, COG, distance).
- Complexity metrics (`normalizedPci`, `distance`, `cog`) are NULL for services rows.
- Services data covers from 1980 but with variable country coverage (approximately 50–75% of Atlas countries report services data).

**SQL schemas for services:**
- `services_unilateral` — for a single country's service exports/imports by product and year.
- `services_bilateral` — for services trade flows between two specific countries.

**GraphQL:** Pass `servicesClass: unilateral` to catalog queries (`productHs92`, etc.) to include services products in the response. The trade data queries (`countryProductYear`, etc.) accept `servicesClass: unilateral` to fetch services trade values.

---

## Cross-Classification Conversion (GraphQL Only)

The Growth Lab constructs the Atlas's long-run time series by converting trade values across classification vintages using data-driven conversion weights. Three GraphQL queries expose this conversion infrastructure:

### `conversionPath`

Find how a source product code maps forward to a target classification.

```graphql
{
  conversionPath(
    sourceCode: "0901"
    sourceClassification: HS1992
    targetClassification: HS2012
  ) {
    fromClassification
    toClassification
    codes {
      sourceCode
      targetCode
    }
  }
}
```

### `conversionSources`

Find what source codes map into a given target code (reverse lookup).

```graphql
{
  conversionSources(
    targetCode: "090111"
    targetClassification: HS2022
    sourceClassification: HS2012
  ) {
    fromClassification
    toClassification
    codes {
      sourceCode
      targetCode
    }
  }
}
```

### `conversionWeights`

Get the pairwise conversion weights along the full historical chain. Filter by providing one or more code strings; returns rows where those codes appear.

```graphql
{
  conversionWeights(hs1992: "0901") {
    hs1992
    weightSitc1988Hs1992
    hs1997
    weightHs1992Hs1997
    hs2002
    weightHs1997Hs2002
    hs2007
    weightHs2002Hs2007
    hs2012
    weightHs2007Hs2012
    hs2022
    weightHs2017Hs2022
  }
}
```

The `conversionWeights` type includes 19 fields covering the chain: `sitc1962 → sitc1976 → sitc1988 → hs1992 → hs1997 → hs2002 → hs2007 → hs2012 → hs2017 → hs2022`, with a weight field for each consecutive pair (e.g., `weightHs1992Hs1997`). Weights reflect how much of the trade value in the source code maps to the target code, handling splits and merges.

---

## When to Use Each Classification

| Use case | Recommended classification |
|---|---|
| General goods trade query (no specification) | HS92 |
| Historical analysis before 1995 | SITC (goes back to 1962) |
| Analysis starting from 2012 onward, with updated product categories | HS12 |
| Most recent HS revision, 2022–2024 only | HS22 (GraphQL only) |
| Long-run trend comparison across decades | SITC |
| Capturing modern tech products (smartphones, LEDs, solar panels) | HS92 or HS12 |
| Services trade | `services_unilateral` (single country) or `services_bilateral` (two countries) |
| Cross-classification code lookup | `conversionPath` / `conversionSources` / `conversionWeights` (GraphQL) |

**Cross-classification comparison:** ECI and PCI values are computed independently within each classification system per year. Cross-classification comparisons of ECI/PCI levels are not methodologically valid — only within-classification, within-year rankings are comparable.

---

## Data Availability Summary

| Classification | SQL database | Explore API (GraphQL) | Country Pages API |
|---|---|---|---|
| HS92 | Yes, 1995–2024 | Yes, 1995–2024 | Yes (`productClass: HS`) |
| HS12 | Yes, 2012–2024 | Yes, 2012–2024 | No |
| HS22 | **No** | Yes, 2022–2024 | No |
| SITC | Yes, 1962–2024 | Yes, 1962–2024 | Yes (`productClass: SITC`) |
| Services unilateral | Yes, 1980–2024 | Yes (via `servicesClass`) | Yes |
| Services bilateral | Yes, 1980–2024 | Yes (via `servicesClass`) | Limited |

**Note on complexity metrics availability:** Complexity data (ECI, PCI, RCA, COG, distance) is computed only at the 1-digit through 4-digit levels, and only for goods classifications (HS and SITC). It is not available at the 6-digit level, for bilateral trade, or for services data.
