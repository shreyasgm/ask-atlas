# Atlas Explore Pages — Ground Truth Collection Guide

This guide describes how to systematically collect ground truth Q&A pairs from the Atlas of Economic Complexity **Explore pages** and integrate them into the eval system. It complements the Country Page guide — focusing on data points that Country Pages **do not already cover**.

---

## Table of Contents

1. [Navigation & Technical Requirements](#1-navigation--technical-requirements)
2. [GraphQL API Reference (Explore Endpoint)](#2-graphql-api-reference-explore-endpoint)
3. [Data Points: What's New vs What's Already Covered](#3-data-points-whats-new-vs-whats-already-covered)
4. [Country & Product Selection Matrix](#4-country--product-selection-matrix)
5. [Question Templates by Category](#5-question-templates-by-category)
6. [Ground Truth Recording Format](#6-ground-truth-recording-format)
7. [Integration with the Eval System](#7-integration-with-the-eval-system)
8. [Batch Workflow](#8-batch-workflow)
9. [Scale & Time Estimate](#9-scale--time-estimate)

---

## 1. Navigation & Technical Requirements

### URL Structure

- **Base URL**: `https://atlas.hks.harvard.edu/explore`
- **Seven visualization types** (six base types, with Growth Opportunity having both graph and table views), each with a URL slug:

| # | Slug | Full URL Pattern | Content |
|---|------|-----------------|---------|
| 1 | `treemap` | `/explore/treemap?year=2024` | Trade Composition treemap |
| 2 | `geomap` | `/explore/geomap?year=2024` | Trade Map (choropleth world map) |
| 3 | `overtime` | `/explore/overtime?year=2024&startYear=1995&endYear=2024` | Trade Over Time (stacked area) |
| 4 | `marketshare` | `/explore/marketshare?year=2024&startYear=1995&endYear=2024` | Global Market Share (multi-line) |
| 5 | `productspace` | `/explore/productspace?year=2024` | Product Space network |
| 6 | `feasibility` | `/explore/feasibility?year=2024` | Growth Opportunity scatter |
| 6b | `feasibility/table` | `/explore/feasibility/table?year=2024&productLevel=4` | Growth Opportunity table |

### URL Query Parameters

| Parameter | Values | Description |
|-----------|--------|-------------|
| `year` | `1995`–`2024` | Display year |
| `startYear` | `1995`–`2024` | Time series start (overtime, marketshare) |
| `endYear` | `1995`–`2024` | Time series end |
| `view` | `markets` | Switch to Locations view (default is Products) |
| `product` | `product-HS92-{id}` | Filter to specific product (internal numeric ID, not HS code) |
| `tradeDirection` | `imports` | Switch to import flows (default = exports) |
| `exporter` | `country-{iso}`, `group-1` (World) | Set exporter country |
| `importer` | `country-{iso}`, `group-1` (World) | Set importer country |
| `productLevel` | `2`, `4`, `6` | Product detail level (HS digits) |

### Controls & Settings

Each visualization has a **Settings** panel with:

| Setting | Options | Available On |
|---------|---------|-------------|
| Detail Level | 2 digit, 4 digit, 6 digit | treemap, overtime |
| Trade Flow | Gross, Net | treemap, overtime, geomap |
| Product Class | HS 1992, HS 2012, HS 2022, SITC | All visualizations |
| Color by | Sector, Complexity, Entry Year | treemap |

The **Trade Over Time** visualization has a Y-axis selector with 4 metric options:
- Current Gross Exports
- Constant (2024 USD)
- Per Capita
- Per Capita Constant (2024 USD)

### Two Modes: Products vs Locations

Most visualizations support two perspectives toggled via Products/Locations buttons:

| Mode | Shows | Example Question |
|------|-------|-----------------|
| **Products** | Product breakdown by sector | "What did Kenya export in 2024?" |
| **Locations** | Trade partner breakdown by region | "Where did Kenya export to in 2024?" |

**Total value distinction**: In Products mode, the total value includes both goods and services (e.g., "$16B" for Kenya). In Locations mode, the total shows goods-only (e.g., "$8.2B" for Kenya), because bilateral services trade data is excluded. This is important for data accuracy when collecting ground truth — the same country can show different total export figures depending on the mode.

### Tooltip Data (Treemap)

Hovering over a product in the treemap shows:

**Basic tooltip:**
- Product name + HS92 code (e.g., "Iron ores and concentrates | 2601 HS92")
- Sector (with color swatch)
- Export Value (e.g., "$74.1B")
- Share (e.g., "19.68%")

**Expanded tooltip (click "Show more"):**
- Revealed Comparative Advantage (RCA) (e.g., 37.65)
- Distance (e.g., 0.781)
- Product Complexity Index (PCI) (e.g., -2.695)

**Drill-down links:**
- "Who exported this product?" → switches to product-centric location view
- "Where did [country] export this product to?" → bilateral by-product view

### Technical Notes

- **The site is a JavaScript SPA** (React). Static HTTP fetches will not work for page content.
- **The Explore pages use a different GraphQL API** from Country pages — these are two completely separate APIs with different schemas:
  - Explore API: `POST /api/graphql` (27 query types, explicit HS revisions, bilateral trade, groups, better introspection)
  - Country Pages API: `POST /api/countries/graphql` (25 query types, `countryProfile` with derived narrative metrics)
  - Both are available on production (`atlas.hks.harvard.edu`) and staging (`staging.atlas.growthlab-dev.com`), with identical schemas within each type.
- **Canvas-based visualizations**: treemap and product space are rendered on `<canvas>`, so tooltip data is not accessible via DOM queries. Use the GraphQL API.
- **The "Growth Opportunity" table view** (`/explore/feasibility/table`) renders an HTML table that IS DOM-accessible — no canvas overlay.
- **Product IDs in URLs** use an internal numeric format (`product-HS92-726` for Coffee/0901), not the HS code directly. The mapping comes from the `productHs92` query.

---

## 2. GraphQL API Reference (Explore Endpoint)

> **NOTE:** The Explore API documented here (`/api/graphql`) is a separate API from the Country Pages API (`/api/countries/graphql`). Both are available on production (`atlas.hks.harvard.edu`) and staging (`staging.atlas.growthlab-dev.com`), with identical schemas within each API type. The Explore API has **27 query types** and **40 custom types**; the Country Pages API has **25 query types** and **49 custom types**. The Country Pages API provides unique derived metrics (`countryProfile`, lookback, etc.) not present in this API. See `docs/backend_redesign_analysis.md` for full details on how both APIs complement each other. **Rate limit (per Atlas `llms.txt`): ≤ 120 req/min (2 req/sec) for automated access. Include a `User-Agent` header.**

### Endpoints

**Explore API** (available on both production and staging with identical schemas):

```
POST https://atlas.hks.harvard.edu/api/graphql
POST https://staging.atlas.growthlab-dev.com/api/graphql
Content-Type: application/json
```

No authentication headers required. Introspection enabled. Per the Atlas `llms.txt`, automated access must:
- **Limit to ≤ 120 requests per minute** (2 req/sec)
- **Include a `User-Agent` header** (e.g., `User-Agent: ask-atlas/1.0`)
- Prefer small, targeted queries — request only needed fields, avoid exhaustive introspection
- Cache and reuse previous results when possible

### Key Difference from Country Pages API

| Aspect | Explore API (`/api/graphql`) | Country Pages API (`/api/countries/graphql`) |
|--------|------------------------------|----------------------------------------------|
| Query count | 27 query types, 40 custom types | 25 query types, 49 custom types |
| ID format | Numeric integers (`countryId: 404`) | String IDs (`location: "location-404"`) |
| Year params | `yearMin` / `yearMax` ranges | `year`, `minYear` / `maxYear` |
| Product class | `HS92`, `HS12`, `HS22`, `SITC` (explicit revisions) | `HS`, `SITC` (generic) |
| Product levels | 2, 4, **6** digit | section, twoDigit, fourDigit |
| Services | `servicesClass: unilateral` (explicit param) | Bundled into product class |
| Arg descriptions | Human-readable for ALL arguments | `None` for all arguments |
| Focus | Raw trade data, bilateral flows | Analytical profiles, recommendations |
| Unique features | Bilateral trade, groups, product relatedness (`productProduct`), 6-digit products, HS22, code conversion, data quality flags, percentile thresholds, download catalog, argument descriptions | `countryProfile` (46 derived fields), `countryLookback`, peer comparisons, policy enums, narrative-ready data |

### Available Query Types (27 total)

#### Core Trade Data

| Query | Arguments | Returns | Purpose |
|-------|-----------|---------|---------|
| `countryProductYear` | `productClass, servicesClass, productLevel!, countryId, productId, yearMin, yearMax` | `[CountryProductYear]` | Country × product trade data |
| `countryYear` | `countryId, productClass, servicesClass, yearMin, yearMax` | `[CountryYear]` | Country-level aggregates (GDP, ECI, etc.) |
| `productYear` | `productClass, servicesClass, productLevel!, productId, yearMin, yearMax` | `[ProductYear]` | Global product-level data |
| `countryCountryYear` | `productClass, servicesClass, countryId, partnerCountryId, yearMin, yearMax` | `[CountryCountryYear]` | Bilateral trade totals |
| `countryCountryProductYear` | `countryId, partnerCountryId, yearMin/Max, productClass, servicesClass, productLevel, productId, productIds` | `[CountryCountryProductYear]` | Bilateral trade by product |
| `countryCountryProductYearGrouped` | Same as `countryCountryProductYear` | `[CountryCountryProductYearGrouped]` | Grouped bilateral trade (returns `productIds` + `data` arrays) |

#### Product Relatedness

| Query | Arguments | Returns | Purpose |
|-------|-----------|---------|---------|
| `productProduct` | `productClass!, productLevel!` | `[ProductProduct]` | Product-to-product relatedness strengths (product space edges); fields: `productId, targetId, strength, productLevel` |

#### Group / Regional Data

| Query | Arguments | Returns | Purpose |
|-------|-----------|---------|---------|
| `groupYear` | `productClass, servicesClass, groupId, groupType, yearMin, yearMax` | `[GroupYear]` | Group-level aggregate trade |
| `groupGroupProductYear` | `productClass, servicesClass, productLevel, productId, groupId, partnerGroupId, yearMin, yearMax` | `[GroupGroupProductYear]` | Group-to-group bilateral |
| `countryGroupProductYear` | `productClass, servicesClass, productLevel, productId, countryId, partnerGroupId!, yearMin, yearMax` | `[CountryGroupProductYear]` | Country-to-group bilateral |
| `groupCountryProductYear` | `productClass, servicesClass, productLevel, productId, groupId!, partnerCountryId, yearMin, yearMax` | `[GroupCountryProductYear]` | Group-to-country bilateral |

#### Reference Data

| Query | Arguments | Returns | Purpose |
|-------|-----------|---------|---------|
| `locationCountry` | *(none)* | `[LocationCountry]` | All countries with ISO codes, income level |
| `locationGroup` | `groupType` | `[LocationGroup]` | Groups (continents, regions, trade blocs) with CAGR stats |
| `productHs92` | `productLevel, servicesClass` | `[Product]` | HS92 product catalog |
| `productHs12` | `productLevel, servicesClass` | `[Product]` | HS 2012 product catalog |
| `productHs22` | `productLevel, servicesClass` | `[Product]` | HS 2022 product catalog |
| `productSitc` | `productLevel, servicesClass` | `[Product]` | SITC product catalog |
| `year` | `yearMin, yearMax` | `[Year]` | Available years with deflators |

#### Metadata & Diagnostics

| Query | Arguments | Returns | Purpose |
|-------|-----------|---------|---------|
| `countryYearThresholds` | `productClass!, countryId, yearMin, yearMax` | `[CountryYearThresholds]` | Percentile distributions for complexity vars |
| `dataFlags` | `countryId` | `[DataFlags]` | Data quality flags per country |
| `dataAvailability` | *(none)* | `[DataAvailability]` | Year ranges per classification |
| `conversionPath` | `sourceCode!, sourceClassification!, targetClassification!` | `[ConversionClassifications]` | HS/SITC code conversion |
| `conversionSources` | `targetCode!, targetClassification!, sourceClassification!` | `[ConversionClassifications]` | Reverse code lookup |
| `conversionWeights` | `sitc1962, sitc1976, sitc1988, hs1992, hs1997, hs2002, hs2007, hs2012, hs2017, hs2022` | `[ConversionWeights]` | Weighted conversion between classifications |
| `downloadsTable` | *(none)* | `[DownloadsTable]` | Data download catalog (70 entries) |
| `banner` | *(none)* | `[Banner]` | Site announcement banners (currently empty) |
| `metadata` | *(none)* | `Metadata` | Server/ingestion info |

### Key Type Schemas

#### `CountryProductYear` (the richest type)

```
countryId, locationLevel, productId, productLevel, year
exportValue, importValue, globalMarketShare
exportRca, exportRpop
isNew, productStatus (absent/lost/new/present)
cog, distance
normalizedPci, normalizedCog, normalizedDistance, normalizedExportRca
normalizedPciRcalt1, normalizedCogRcalt1, normalizedDistanceRcalt1, normalizedExportRcaRcalt1
```

#### `CountryYear`

```
countryId, year
exportValue, importValue
population, gdp, gdppc, gdpPpp, gdppcPpp
gdpConst, gdpPppConst, gdppcConst, gdppcPppConst
eci, eciFixed, coi
currentAccount, growthProj
```

#### `ProductYear`

```
productId, productLevel, year
exportValue, importValue
exportValueConstGrowth5, importValueConstGrowth5
exportValueConstCagr5, importValueConstCagr5
pci, complexityEnum (low/moderate/high)
```

#### `CountryCountryYear`

```
countryId, partnerCountryId, year
exportValue, exportValueReported
importValue, importValueReported
```

#### `CountryCountryProductYear` (11 fields)

```
countryId, locationLevel, partnerCountryId, partnerLevel
productId, productLevel, year
exportValue, importValue
exportValueReported, importValueReported
```

#### `ProductProduct` (4 fields)

```
productId, targetId, strength, productLevel
```

Purpose: Encodes the product space network edges. Each row is a pair of products with a `strength` value indicating how related they are (based on co-export patterns).

#### `Product`

```
productId, productLevel, code
nameEn, nameShortEn
productType (good/service)
parent, topParent, productIdHierarchy
clusterId, productSpaceX, productSpaceY
naturalResource, greenProduct
isShown, globalExportThreshold, showFeasibility
```

#### `LocationCountry`

```
countryId, locationLevel
iso3Code, iso2Code, legacyCountryId
nameEn, nameShortEn, nameAbbrEn
incomelevelEnum (high/upper_middle/lower_middle/low)
isTrusted, formerCountry
inRankings, inCp, inMv
reportedServ, reportedServRecent
```

#### `LocationGroup`

```
groupId, groupName, groupType
members (list of country IDs)
parentId, parentName, parentType
gdpMean, gdpSum
exportValueMean, exportValueSum
exportValueCagr3/5/10/15
exportValueNonOilCagr3/5/10/15
gdpCagr3/5/10/15, gdpConstCagr3/5/10/15
gdppcConstCagr3/5/10/15
```

### Enum Values

| Enum | Values |
|------|--------|
| `ProductClass` | `HS92`, `HS12`, `HS22`, `SITC` |
| `ServicesClass` | `unilateral` |
| `ComplexityLevel` | `low`, `moderate`, `high` |
| `LocationLevel` | `country`, `group` |
| `GroupType` | `continent`, `political`, `region`, `rock_song`, `subregion`, `trade`, `wdi_income_level`, `wdi_region`, `world` |
| `ProductType` | `good`, `service` |
| `ProductStatus` | `absent`, `lost`, `new`, `present` |
| `IncomeLevel` | `high`, `upper_middle`, `lower_middle`, `low` |
| `ClassificationEnum` | `SITC1962`, `SITC1976`, `SITC1988`, `HS1992`, `HS1997`, `HS2002`, `HS2007`, `HS2012`, `HS2017`, `HS2022` |
| `DownloadTableDataType` | `unilateral`, `bilateral`, `product`, `classification`, `product_space`, `rankings` |
| `DownloadTableFacet` | `CPY`, `CY`, `PY`, `CCY`, `CCPY` |
| `DownloadTableRepo` | `rankings`, `hs92`, `hs12`, `hs22`, `sitc`, `services_unilateral`, `classification`, `product_space` |

### Working Sample Queries

**Country-product data for Kenya, Coffee (0901), 2024:**

```graphql
{
  countryProductYear(
    productClass: HS92,
    productLevel: 4,
    countryId: 404,
    productId: 726,
    yearMin: 2024,
    yearMax: 2024
  ) {
    countryId productId year
    exportValue importValue
    globalMarketShare exportRca
    distance cog
    normalizedPci normalizedCog normalizedDistance
    productStatus
  }
}
```

**Note on product IDs**: The Explore API uses internal numeric product IDs (e.g., 726 for Coffee/0901), NOT the HS code directly. Use `productHs92(productLevel: 4)` to get the mapping from HS code → product ID.

**Product ID mapping for selected products** (retrieved from the `productHs92` query):

| HS92 Code | Internal Product ID | Product Name |
|-----------|-------------------|-------------|
| 0901 | 726 | Coffee |
| 0902 | 727 | Tea |
| 2601 | 1506 | Iron ores |
| 2710 | 1584 | Petroleum oils, refined |
| 3004 | 1748 | Medicaments |
| 6109 | 2801 | T-shirts |
| 8542 | 3595 | Electronic integrated circuits |
| 8703 | 3667 | Cars |

These IDs are used in URLs (e.g., `product=product-HS92-726` for Coffee) and API queries (e.g., `productId: 726`).

**Global product data for Coffee, 2020-2024:**

```graphql
{
  productYear(
    productClass: HS92,
    productLevel: 4,
    productId: 726,
    yearMin: 2020,
    yearMax: 2024
  ) {
    productId year
    exportValue importValue
    pci complexityEnum
    exportValueConstCagr5
  }
}
```

**Bilateral trade: Kenya → USA, Coffee, 2024:**

```graphql
{
  countryCountryProductYear(
    countryId: 404,
    partnerCountryId: 840,
    productClass: HS92,
    productLevel: 4,
    productId: 726,
    yearMin: 2024,
    yearMax: 2024
  ) {
    countryId partnerCountryId productId year
    exportValue importValue
  }
}
```

**Country-level time series for Kenya, 2015-2024:**

```graphql
{
  countryYear(
    countryId: 404,
    productClass: HS92,
    yearMin: 2015,
    yearMax: 2024
  ) {
    countryId year
    exportValue importValue
    gdppc gdppcPpp
    eci coi growthProj
    population
  }
}
```

**All countries metadata:**

```graphql
{
  locationCountry {
    countryId iso3Code iso2Code
    nameEn nameShortEn
    incomelevelEnum
    inRankings inCp
  }
}
```

**Product catalog (HS92, 4-digit):**

```graphql
{
  productHs92(productLevel: 4) {
    productId code
    nameEn nameShortEn
    productType
    naturalResource greenProduct
  }
}
```

---

## 3. Data Points: What's New vs What's Already Covered

### Principle: No Duplication

Country page ground truth already covers: GDP, population, ECI ranking, growth projection, export/import totals, top products by share, top trade partners, diversification grade, COI, strategic approach, and narrative descriptions. **We do not re-collect these.**

Instead, Explore pages provide data points that country pages **cannot** answer:

### Unique Explore Page Data Points

| # | Data Point | Source | API Query | Country Pages? |
|---|-----------|--------|-----------|---------------|
| 1 | **Product-level RCA** for a country | Treemap tooltip | `countryProductYear.exportRca` | No — treemap on country pages uses canvas, no tooltip access |
| 2 | **Product-level distance** for a country | Treemap tooltip | `countryProductYear.distance` | No |
| 3 | **Product-level PCI** (from Explore) | Treemap tooltip | `countryProductYear.normalizedPci` or `productYear.pci` | Partial — export-complexity page shows PCI on canvas |
| 4 | **Global export value** of a product | Product-level page | `productYear.exportValue` | No — country pages only show country-specific values |
| 5 | **Global 5-year CAGR** for a product | Feasibility table | `productYear.exportValueConstCagr5` | No |
| 6 | **Bilateral trade value** (country → country, specific product) | Bilateral treemap | `countryCountryProductYear.exportValue` | No — country pages show top 3 partners but not product-level bilateral |
| 7 | **Bilateral trade value** (country → country, all products) | Bilateral treemap | `countryCountryYear.exportValue` | No |
| 8 | **Import value by product** for a country | Treemap (import direction) | `countryProductYear.importValue` | No — country pages show total imports but not product breakdown |
| 9 | **Constant-dollar exports** over time | Trade Over Time (Y-axis toggle) | `countryYear.gdpConst` and deflator | No |
| 10 | **Per-capita exports** over time | Trade Over Time (Y-axis toggle) | Derive from `countryYear.exportValue / population` | No |
| 11 | **Product status** (new/lost/present/absent) for specific products | API | `countryProductYear.productStatus` | Partial — new-products page shows count, not per-product |
| 12 | **Complexity Outlook Gain (COG)** per product | API | `countryProductYear.cog` | No |
| 13 | **Global market share per product** | API | `countryProductYear.globalMarketShare` | No — country pages show sector-level market share only |
| 14 | **Feasibility table** (ranked products with distance, COG, PCI, global size, 5yr growth) | Feasibility table view | Multiple API fields | Partial — country page product-table shows similar but with diamond ratings, not numbers. Explore table shows **all** opportunity products (not just "Top 50") and is available for **all countries** including frontier economies (USA, Germany, etc.) where the country page version is hidden. |
| 15 | **Product classification across HS revisions** | API | `conversionPath`, `conversionSources` | No |
| 16 | **Regional/group trade aggregates** | API | `groupYear`, `locationGroup` | No |
| 17 | **Regional export CAGR** (3/5/10/15 year) | API | `locationGroup.exportValueCagr5` etc. | No |
| 18 | **Country-level time series** (GDP, ECI, exports year-by-year) | Trade Over Time | `countryYear` with year range | Partial — country pages show sparklines but not extractable values |
| 19 | **Export CAGR** (5-year, constant dollars) for individual products | API | `productYear.exportValueConstCagr5` | No |
| 20 | **Product classification** (natural resource flag, green product flag) | API | `productHs92.naturalResource`, `greenProduct` | No |
| 21 | **Export RCA population-relative (RPOP)** | API | `countryProductYear.exportRpop` | No |
| 22 | **Product-to-product relatedness strength** | API | `productProduct.strength` | No — product space edges are pre-computed only in the Country Pages API |
| 23 | **Bilateral reported vs mirror values** | API | `countryCountryYear.exportValueReported`, `importValueReported` | No |
| 24 | **Year deflators** | API | `year.deflator` | No — needed to compute constant-dollar values |
| 25 | **Percentile distributions for complexity variables** | API | `countryYearThresholds` | No |
| 26 | **Country data quality flags** | API | `dataFlags` | No |
| 27 | **Sector-level global market share over time** | Marketshare chart | Derived: `countryProductYear.exportValue / productYear.exportValue` per sector per year | No |

### What to Skip (Already in Country Pages)

- Country profile stats (GDP, population, ECI, COI, growth projection, income classification)
- Top products by export share (already from `treeMap` on country pages API)
- Top 3 trade partners (already from `treeMap(facet: CCY_C)`)
- Diversification grade, diversity rank, new product count
- Strategic approach / policy recommendation
- All narrative descriptions (growth pattern, structural transformation, etc.)

---

## 4. Country & Product Selection Matrix

### Selected Countries (same 8 as Country Pages)

| Country | ISO ID | Income Level | Role |
|---------|--------|-------------|------|
| USA | 840 | High | Frontier, high bilateral trade |
| Germany | 276 | High | Major exporter |
| Spain | 724 | High | Mid-complexity |
| Turkiye | 792 | Upper-middle | Growing exporter |
| Brazil | 76 | Upper-middle | Commodity-heavy |
| India | 356 | Lower-middle | Diverse, large |
| Kenya | 404 | Lower-middle | Developing, agriculture-heavy |
| Ethiopia | 231 | Low | Low complexity, strategic bets |

### Selected Products (for product-level questions)

Choose 6–8 products spanning different sectors, complexity levels, and trade volumes:

| Product | HS92 Code | Sector | Complexity | Why |
|---------|-----------|--------|-----------|-----|
| Coffee | 0901 | Agriculture | Low | Key developing-country export |
| Cars | 8703 | Vehicles | High | High-value manufactured good |
| Petroleum oils, refined | 2710 | Minerals | Low | Major commodity, natural resource |
| Electronic integrated circuits | 8542 | Electronics | High | High-tech, global supply chains |
| Medicaments (pharma) | 3004 | Chemicals | High | Complex, growing sector |
| T-shirts | 6109 | Textiles | Low | Labor-intensive manufacturing |
| Iron ores | 2601 | Minerals | Low | Commodity with concentrated exporters |
| Business services | (service) | Services | Moderate | Services trade dimension |

### Deduplication: Country-Product Pairings

Each question template uses 1–2 country-product combinations. Spread across the matrix:

| Category | Countries | Products |
|----------|-----------|----------|
| Product-level RCA & PCI | Kenya, India | Coffee, T-shirts |
| Bilateral trade (product) | Brazil→China, Kenya→USA | Iron ores, Coffee |
| Bilateral trade (total) | Germany→USA, India→China | All products |
| Global product stats | N/A (global) | Cars, Electronic ICs, Pharma |
| Import composition | USA, Ethiopia | Petroleum, Coffee |
| Time series & growth | Brazil, Turkiye | All products, Textiles |
| Feasibility metrics | Kenya, Turkiye | (country-wide) |
| Regional aggregates | N/A | Sub-Saharan Africa, East Asia |

---

## 5. Question Templates by Category

Each template specifies:
- **Question template** with `{country}`, `{product}`, `{partner}` placeholders
- **Atlas Explore URL** where the answer is found
- **Extraction method** — **API** (GraphQL query) or **Browser** (requires page rendering)
- **Category**, **difficulty** for question metadata

---

### 5.1 Product-Level Complexity & Competitiveness

**Focus**: RCA, PCI, distance, COG for specific country×product pairs — data visible in Explore treemap tooltips but not extractable from country pages.

**Assigned countries**: Kenya (404), India (356)
**Assigned products**: Coffee (0901), T-shirts (6109), Electronic ICs (8542)

| # | Question Template | Extraction | Difficulty | Explore URL |
|---|------------------|------------|------------|-------------|
| 1 | What is {country}'s Revealed Comparative Advantage (RCA) in {product}? | **API**: `countryProductYear.exportRca` | easy | `/explore/treemap?year=2024` |
| 2 | What is {country}'s distance to {product} in the product space? | **API**: `countryProductYear.distance` | medium | `/explore/treemap?year=2024` |
| 3 | What is the Product Complexity Index (PCI) of {product}? | **API**: `productYear.pci` | easy | `/explore/treemap?year=2024` |
| 4 | What is {country}'s Complexity Outlook Gain (COG) for {product}? | **API**: `countryProductYear.cog` | medium | `/explore/feasibility?year=2024` |
| 5 | What is {country}'s global market share in {product}? | **API**: `countryProductYear.globalMarketShare` | medium | `/explore/treemap?year=2024` |
| 6 | Is {product} classified as a new, present, lost, or absent export for {country}? | **API**: `countryProductYear.productStatus` | easy | `/explore/treemap?year=2024` |

---

### 5.2 Global Product Statistics

**Focus**: Product-level global data that country pages never show — global trade value, PCI, growth rate.

**Products**: Cars (8703), Electronic ICs (8542), Medicaments (3004), Coffee (0901), Iron ores (2601)

| # | Question Template | Extraction | Difficulty | Explore URL |
|---|------------------|------------|------------|-------------|
| 7 | What is the total global export value of {product}? | **API**: `productYear.exportValue` | easy | `/explore/treemap?year=2024` (product view) |
| 8 | What is the 5-year export growth rate (CAGR) for {product} globally? | **API**: `productYear.exportValueConstCagr5` | medium | `/explore/feasibility/table` |
| 9 | What is the complexity classification (low/moderate/high) of {product}? | **API**: `productYear.complexityEnum` | easy | `/explore/treemap?year=2024` |
| 10 | Which country is the largest exporter of {product}? | **API**: `countryProductYear` filtered by `productId`, sort by `exportValue` | medium | `/explore/treemap?year=2024&view=markets` (product-centric) |
| 11 | What are the top 3 exporters of {product} by value? | **API**: same as above, top 3 | medium | `/explore/treemap?year=2024` (product view) |

---

### 5.3 Bilateral Trade

**Focus**: Country-to-country trade flows (total and by product) — a dimension country pages don't expose beyond top 3 partners.

**Pairings**: Brazil→China, Kenya→USA, Germany→USA, India→China, Turkiye→Germany

| # | Question Template | Extraction | Difficulty | Explore URL |
|---|------------------|------------|------------|-------------|
| 12 | What is the total export value from {country} to {partner}? | **API**: `countryCountryYear.exportValue` | easy | `/explore/treemap?year=2024&view=markets` |
| 13 | What is the total import value of {country} from {partner}? | **API**: `countryCountryYear.importValue` | easy | `/explore/treemap?year=2024&view=markets&tradeDirection=imports` |
| 14 | What is the trade balance between {country} and {partner}? | **API**: Derive from `countryCountryYear.exportValue - importValue` | medium | `/explore/treemap?year=2024&view=markets` |
| 15 | What is the value of {product} exports from {country} to {partner}? | **API**: `countryCountryProductYear.exportValue` | medium | `/explore/treemap?year=2024&view=markets&product=product-HS92-{id}` |
| 16 | What are the top 3 products {country} exports to {partner}? | **API**: `countryCountryProductYear` sorted by `exportValue` | hard | `/explore/treemap?year=2024&product=...` |

---

### 5.4 Import Composition

**Focus**: Product-level imports — country pages show total imports but not the product breakdown.

**Assigned countries**: USA (840), Ethiopia (231)

| # | Question Template | Extraction | Difficulty | Explore URL |
|---|------------------|------------|------------|-------------|
| 17 | What is the top imported product for {country}? | **API**: `countryProductYear` with import direction, sort by `importValue` | easy | `/explore/treemap?year=2024&tradeDirection=imports` |
| 18 | What are the top 3 imported products for {country} by value? | **API**: same as above, top 3 | medium | `/explore/treemap?year=2024&tradeDirection=imports` |
| 19 | What is {country}'s import value for {product}? | **API**: `countryProductYear.importValue` | easy | `/explore/treemap?year=2024&tradeDirection=imports` |
| 20 | From which country does {country} import the most {product}? | **API**: `countryCountryProductYear` with import direction | hard | `/explore/treemap?year=2024&tradeDirection=imports&product=...` |

---

### 5.5 Trade Time Series & Growth

**Focus**: Year-by-year trade data, constant-dollar values, per-capita metrics — visible in Trade Over Time but not extractable from country pages.

**Assigned countries**: Brazil (76), Turkiye (792), Kenya (404)

| # | Question Template | Extraction | Difficulty | Explore URL |
|---|------------------|------------|------------|-------------|
| 21 | What was {country}'s total export value in {year}? | **API**: `countryYear.exportValue` with specific year | easy | `/explore/overtime?year={year}` |
| 22 | How have {country}'s exports changed from {year1} to {year2}? | **API**: `countryYear` for both years, compute change | medium | `/explore/overtime?startYear={year1}&endYear={year2}` |
| 23 | What was {country}'s GDP per capita in {year}? | **API**: `countryYear.gdppc` | easy | `/explore/overtime?year={year}` |
| 24 | What was {country}'s ECI in {year}? | **API**: `countryYear.eci` | easy | `/explore/overtime?year={year}` |
| 25 | How has {country}'s ECI changed from {year1} to {year2}? | **API**: `countryYear.eci` for both years | medium | `/explore/overtime?startYear={year1}&endYear={year2}` |
| 26 | What is {country}'s global market share in {sector} in {year}? | **API**: Derive from `countryProductYear.exportValue / productYear.exportValue` per sector | medium | `/explore/marketshare?year={year}` |

---

### 5.6 Feasibility & Growth Opportunities (Explore Specific)

**Focus**: The Growth Opportunity table with numeric values (not diamond ratings like country pages) — distance, COG, PCI, global size, 5yr growth.

**Assigned countries**: Kenya (404), Turkiye (792)

| # | Question Template | Extraction | Difficulty | Explore URL |
|---|------------------|------------|------------|-------------|
| 27 | What are the top 5 growth opportunity products for {country} ranked by opportunity gain? | **API**: `countryProductYear` sorted by `cog` | hard | `/explore/feasibility/table?year=2024` |
| 28 | What is the global market size of {country}'s top growth opportunity product? | **API**: `productYear.exportValue` for top COG product | medium | `/explore/feasibility/table?year=2024` |
| 29 | What is the 5-year growth rate of {product} globally according to the Atlas? | **API**: `productYear.exportValueConstCagr5` | medium | `/explore/feasibility/table?year=2024` |

---

### 5.7 Regional & Group Aggregates

**Focus**: Trade data at regional/continental/income-group level — entirely unique to Explore.

**Groups**: Sub-Saharan Africa, East Asia & Pacific, European Union, Low Income

| # | Question Template | Extraction | Difficulty | Explore URL |
|---|------------------|------------|------------|-------------|
| 30 | What is the total export value of {region/group}? | **API**: `groupYear.exportValue` | medium | N/A (API only) |
| 31 | What is the 5-year export growth rate for {region}? | **API**: `locationGroup.exportValueCagr5` | medium | N/A (API only) |
| 32 | What is the non-oil export growth rate for {region}? | **API**: `locationGroup.exportValueNonOilCagr5` | medium | N/A (API only) |
| 33 | Which countries belong to {group} according to the Atlas? | **API**: `locationGroup.members` | easy | N/A (API only) |

---

### 5.8 Product Classification & Metadata

**Focus**: Product classification details, natural resource flags, classification conversion — data accessible only via the Explore API.

| # | Question Template | Extraction | Difficulty | Explore URL |
|---|------------------|------------|------------|-------------|
| 34 | Is {product} classified as a natural resource on the Atlas? | **API**: `productHs92.naturalResource` | easy | N/A (API only) |
| 35 | Is {product} classified as a green product on the Atlas? | **API**: `productHs92.greenProduct` | easy | N/A (API only) |
| 36 | What HS 2012 code corresponds to {product} (HS 1992 code {code})? | **API**: `conversionPath` | hard | N/A (API only) |
| 37 | How many 4-digit HS92 products does the Atlas track? | **API**: `productHs92(productLevel: 4)` count | easy | N/A (API only) |
| 38 | What years of trade data are available for HS 1992 on the Atlas? | **API**: `dataAvailability` | easy | N/A (API only) |
| 39 | Does the Atlas flag any data quality issues for {country}? | **API**: `dataFlags(countryId: {id})` | easy | N/A (API only) |

---

### Template Count Summary

| Category | Templates | Est. Questions |
|----------|-----------|---------------|
| Product-Level Complexity & Competitiveness | 6 | 12–18 |
| Global Product Statistics | 5 | 10–15 |
| Bilateral Trade | 5 | 10–15 |
| Import Composition | 4 | 8–12 |
| Trade Time Series & Growth | 6 | 12–18 |
| Feasibility & Growth Opportunities | 3 | 6–9 |
| Regional & Group Aggregates | 4 | 8–12 |
| Product Classification & Metadata | 6 | 6–12 |
| **Total** | **39** | **~75–110** |

---

## 6. Ground Truth Recording Format

### 6.1 `question.json` Schema

```json
{
  "question_id": "170",
  "user_question": "What is Kenya's Revealed Comparative Advantage (RCA) in Coffee?",
  "category": "Product-Level Complexity (Explore Page)",
  "difficulty": "easy",
  "source": "atlas_explore_page",
  "atlas_url": "https://atlas.hks.harvard.edu/explore/treemap?year=2024"
}
```

**Field notes:**
- `source`: Use `"atlas_explore_page"` to distinguish from `"atlas_country_page"` and DB-sourced questions.
- `atlas_url`: The Explore page URL where the answer is visually verifiable. For API-only data points, use the closest relevant Explore URL.
- `question_id`: Continue from the highest existing ID (currently 169).

### 6.2 `results.json` Schema

```json
{
  "question_id": "170",
  "execution_timestamp": "2026-02-22T15:30:00.000000+00:00",
  "source": "atlas_explore_page",
  "atlas_url": "https://atlas.hks.harvard.edu/explore/treemap?year=2024",
  "results": {
    "data": [
      {
        "metric": "Revealed Comparative Advantage (RCA)",
        "country": "Kenya",
        "product": "Coffee (0901 HS92)",
        "value": 42.3,
        "year": "2024"
      }
    ]
  }
}
```

**Data object patterns:**

- **Product-country metric**: `{ "metric": "...", "country": "...", "product": "...", "value": ..., "year": "..." }`
- **Global product stat**: `{ "metric": "...", "product": "...", "value": "...", "year": "..." }`
- **Bilateral trade**: `{ "metric": "...", "exporter": "...", "importer": "...", "product": "...", "value": "...", "year": "..." }`
- **Time series**: `{ "metric": "...", "country": "...", "values": [{"year": 2020, "value": ...}, ...] }`
- **Ranked list**: `{ "metric": "...", "rankings": [{"rank": 1, "name": "...", "value": "..."}, ...] }`
- **Classification**: `{ "metric": "...", "product": "...", "classification": "...", "value": ... }`

---

## 7. Integration with the Eval System

### File Locations

Same structure as country page questions:

| What | Where |
|------|-------|
| Master question list | `evaluation/eval_questions.json` |
| Individual question metadata | `evaluation/questions/{id}/question.json` |
| Ground truth results | `evaluation/results/{id}/ground_truth/results.json` |

### ID Numbering

- Existing questions: IDs 1–169
- New Explore page questions: **start at ID 170**

### New Categories to Add

```json
"explore_product_complexity": {
  "name": "Product-Level Complexity (Explore Page)",
  "description": "Product-level RCA, PCI, distance, COG from Atlas Explore pages"
},
"explore_global_product_stats": {
  "name": "Global Product Statistics (Explore Page)",
  "description": "Global export values, growth rates, and complexity for specific products"
},
"explore_bilateral_trade": {
  "name": "Bilateral Trade (Explore Page)",
  "description": "Country-to-country trade flows, total and by product"
},
"explore_import_composition": {
  "name": "Import Composition (Explore Page)",
  "description": "Product-level import breakdown for countries"
},
"explore_trade_time_series": {
  "name": "Trade Time Series (Explore Page)",
  "description": "Year-by-year trade data, GDP, ECI time series"
},
"explore_feasibility": {
  "name": "Growth Opportunities (Explore Page)",
  "description": "Feasibility metrics: opportunity gain, distance, global size, growth"
},
"explore_regional_aggregates": {
  "name": "Regional Aggregates (Explore Page)",
  "description": "Regional and group-level trade data and growth rates"
},
"explore_product_metadata": {
  "name": "Product Classification & Metadata (Explore Page)",
  "description": "Product catalog details, natural resource flags, classification conversion"
}
```

---

## 8. Batch Workflow

### Layer 1: GraphQL API Script (Primary — ~95%)

The Explore API is even more powerful than the Country Pages API. Nearly all Explore data points are queryable via the API — there are **no narrative text sections** on Explore pages, so browser extraction is barely needed.

**Script structure:**

```python
import asyncio
import httpx

ENDPOINT = "https://atlas.hks.harvard.edu/api/graphql"

COUNTRIES = {
    "Kenya": 404, "Turkiye": 792, "Brazil": 76,
    "India": 356, "Spain": 724, "Ethiopia": 231,
    "USA": 840, "Germany": 276,
}

# Selected products (HS92 code → internal product ID)
# Get mapping from: productHs92(productLevel: 4)
# Known IDs: Coffee=726, Tea=727, Iron ores=1506, Petroleum=1584,
#             Medicaments=1748, T-shirts=2801, Electronic ICs=3595, Cars=3667
PRODUCTS = {}  # Populated at runtime from API

async def fetch_product_catalog(client):
    """Get HS code → product ID mapping."""
    query = '{ productHs92(productLevel: 4) { productId code nameShortEn } }'
    resp = await client.post(ENDPOINT, json={"query": query})
    return resp.json()

async def fetch_country_product_year(client, country_id, product_id, year):
    """Get RCA, distance, COG, PCI, market share for a country×product."""
    query = """{ countryProductYear(
        productClass: HS92, productLevel: 4,
        countryId: %d, productId: %d,
        yearMin: %d, yearMax: %d
    ) {
        countryId productId year
        exportValue importValue globalMarketShare
        exportRca distance cog
        normalizedPci productStatus
    }}""" % (country_id, product_id, year, year)
    resp = await client.post(ENDPOINT, json={"query": query})
    return resp.json()

async def fetch_bilateral_trade(client, country_id, partner_id, year):
    """Get total bilateral trade between two countries."""
    query = """{ countryCountryYear(
        productClass: HS92,
        countryId: %d, partnerCountryId: %d,
        yearMin: %d, yearMax: %d
    ) {
        countryId partnerCountryId year
        exportValue importValue
    }}""" % (country_id, partner_id, year, year)
    resp = await client.post(ENDPOINT, json={"query": query})
    return resp.json()

async def fetch_product_year(client, product_id, year_min, year_max):
    """Get global product stats."""
    query = """{ productYear(
        productClass: HS92, productLevel: 4,
        productId: %d,
        yearMin: %d, yearMax: %d
    ) {
        productId year
        exportValue importValue
        pci complexityEnum
        exportValueConstCagr5
    }}""" % (product_id, year_min, year_max)
    resp = await client.post(ENDPOINT, json={"query": query})
    return resp.json()

async def fetch_country_year_series(client, country_id, year_min, year_max):
    """Get country-level time series."""
    query = """{ countryYear(
        countryId: %d, productClass: HS92,
        yearMin: %d, yearMax: %d
    ) {
        countryId year
        exportValue importValue
        gdppc gdppcPpp eci coi
        population growthProj
    }}""" % (country_id, year_min, year_max)
    resp = await client.post(ENDPOINT, json={"query": query})
    return resp.json()
```

### Layer 2: Browser Verification (Spot-Check — ~5%)

Explore pages have **no narrative text** — everything is data-driven. Browser spot-checks verify that API-collected ground truth values match what the website actually displays. This catches cases where the API returns raw data that the frontend transforms, rounds, or filters before display.

**Estimated browser work**: 8–12 page visits across the checks below.

#### Why Spot-Check?

The API returns raw numeric values, but the website may:
- Round or format values differently (e.g., `$1,387,000,000` → `$1.39B`)
- Apply filters the API doesn't (e.g., excluding services from Locations mode totals)
- Use derived metrics (e.g., share = `exportValue / total`, constant-dollar = `exportValue / deflator`)
- Display normalized or discretized versions (e.g., diamond ratings instead of raw distance)

Spot-checks confirm the ground truth answers match the user-visible experience.

#### Check 1: Treemap Tooltip Values (3–4 page visits)

The treemap is canvas-rendered — data is not in the DOM. Verify by hovering over specific products to trigger the tooltip overlay.

**How to do it:**

1. Navigate to `/explore/treemap?year=2024&exporter=country-404` (Kenya)
2. Hover over a large, easily findable product rectangle (e.g., Tea)
3. Read the basic tooltip: **Product name**, **HS code**, **Sector**, **Export Value**, **Share**
4. Click "Show more" in the tooltip to expand it
5. Read the expanded fields: **RCA**, **Distance**, **PCI**
6. Compare each value against the API result from `countryProductYear` for the same country, product, and year

**What to compare:**

| Tooltip Field | API Field | Expected Match |
|---------------|-----------|---------------|
| Export Value | `countryProductYear.exportValue` | Same value after rounding (e.g., API `1387000000` → tooltip `$1.39B`) |
| Share | `exportValue / sum(all exportValues)` | Within 0.01% |
| RCA | `countryProductYear.exportRca` | Exact or rounded to 2 decimal places |
| Distance | `countryProductYear.distance` | Exact or rounded to 3 decimal places |
| PCI | `countryProductYear.normalizedPci` | Exact or rounded to 3 decimal places |

**Recommended spot-check pairs** (pick 3–4):

| Country | Product | Why |
|---------|---------|-----|
| Kenya (404) | Tea (0902, ID 727) | Large rectangle, easy to find |
| Kenya (404) | Coffee (0901, ID 726) | Second-largest, key reference product |
| Brazil (76) | Iron ores (2601, ID 1506) | Commodity-heavy country |
| India (356) | T-shirts (6109, ID 2801) | Smaller product, tests precision |

#### Check 2: Feasibility Table Values (2–3 page visits)

The feasibility table is the **only Explore page with DOM-accessible data** — it renders as an HTML `<table>`. This makes it the easiest to verify programmatically.

**How to do it:**

1. Navigate to `/explore/feasibility/table?year=2024&exporter=country-404&productLevel=4` (Kenya)
2. The table loads with all opportunity products (products where the country does NOT have RCA > 1)
3. Read the first 5–10 rows and compare against API values

**What to compare:**

| Table Column | API Field | Match Type |
|-------------|-----------|-----------|
| Product Name + HS code | `productHs92.nameEn` + `code` | Exact |
| Global Size (USD) | `productYear.exportValue` | Same after rounding (e.g., `$2.61B`) |
| Global Growth 5 YR | `productYear.exportValueConstCagr5` | Same percentage (e.g., `↑ 13.1%`) |
| "Nearby" Distance (diamonds) | `countryProductYear.distance` | Ordinal — 7 diamond levels; more diamonds = smaller distance value |
| Opportunity Gain (diamonds) | `countryProductYear.cog` | Ordinal — 7 diamond levels; more diamonds = higher COG |
| Product Complexity (diamonds) | `countryProductYear.normalizedPci` | Ordinal — 7 diamond levels; more diamonds = higher PCI |

**Diamond rating note:** The distance, COG, and PCI columns use 7-level diamond ratings (e.g., ◆◆◆◆◆◇◇), NOT exact numbers. Don't expect an exact match between the raw API value and the diamond count — instead verify that higher API values correspond to more filled diamonds and that the sort order is consistent.

**Global Size and Global Growth 5 YR** are displayed as exact formatted values ($USD and percentage) and should match the API data after rounding.

**Recommended spot-checks:**

| Country | Notes |
|---------|-------|
| Kenya (404) | Default test country; moderate number of opportunity products |
| Turkiye (792) | More complex economy; tests a different product mix |
| USA (840) | Frontier economy — verify the table is available (country pages hide it for frontier countries) |

#### Check 3: Header Total Values (2–3 page visits)

Each Explore page shows a total value in the header. This is a quick sanity check.

**How to do it:**

1. Navigate to `/explore/treemap?year=2024&exporter=country-404` (Kenya, Products mode)
2. Read the total value (e.g., "$16B") — this is goods + services
3. Compare against `sum(countryProductYear.exportValue)` or `countryYear.exportValue` from the API
4. Switch to Locations mode (click "Locations" toggle; URL adds `view=markets`)
5. Read the new total (e.g., "$8.2B") — this is goods only (services bilateral data excluded)
6. Compare against `sum(countryCountryYear.exportValue)` from the API

**Important:** The goods+services vs goods-only difference is expected behavior, not a discrepancy. Verify that Products mode total > Locations mode total for countries with significant services exports.

#### Check 4: URL Parameter Fidelity (1–2 page visits)

Verify that the URL parameters in the ground truth `atlas_url` field actually render the correct data.

**How to do it:**

1. Pick 2–3 ground truth entries that have specific `atlas_url` values
2. Open each URL in a browser
3. Confirm the page loads the correct country, year, product, and trade direction
4. Confirm the visualization matches what the question asks about

**Example URLs to verify:**

| URL | Should Show |
|-----|------------|
| `/explore/treemap?year=2024&exporter=country-404&tradeDirection=imports` | Kenya's imports by product, 2024 |
| `/explore/treemap?year=2024&exporter=country-76&importer=country-156&view=markets` | Brazil's exports to China |
| `/explore/feasibility/table?year=2024&exporter=country-792&productLevel=4` | Turkiye's growth opportunities |

#### Handling Discrepancies

If a browser value doesn't match the API value:

1. **Rounding differences** (e.g., API says `$1,387,432,100`, tooltip says `$1.39B`): Expected — document the rounding convention but don't flag as error
2. **Small percentage differences** (< 1%): Likely floating-point or aggregation-order differences — acceptable
3. **Large value mismatches** (> 5% or wrong order of magnitude): Investigate — could indicate wrong product ID mapping, wrong year, or an API-to-frontend transformation not accounted for
4. **Missing data**: If the API returns a value but the tooltip/table doesn't show it (or vice versa), check if a filter is applied (e.g., `productLevel`, `servicesClass`, `tradeDirection`)

Document any discrepancies as comments in the ground truth `results.json` and adjust values if the browser is the authoritative source for user-visible answers.

### Step-by-Step Procedure

**Step 1: Build product ID mapping**
- Query `productHs92(productLevel: 4)` to get HS code → product ID mapping
- Store this for all subsequent queries

**Step 2: Run API queries for all country×product pairs**
- For each assigned country-product combination, query `countryProductYear`
- For global product stats, query `productYear`
- For bilateral pairs, query `countryCountryYear` and `countryCountryProductYear`
- For time series, query `countryYear` with year ranges
- For regional data, query `locationGroup` and `groupYear`

**Step 3: Generate question.json and results.json files**
- Use templates from section 5 to generate questions
- Fill in actual values from API responses
- Write files to `evaluation/questions/{id}/` and `evaluation/results/{id}/ground_truth/`

**Step 4: Bulk-update eval_questions.json**
- Add new categories
- Append new question entries
- Verify total count

**Step 5: Spot-check via browser** (see Layer 2 above for full details)
- **Treemap tooltips** (3–4 visits): Hover over specific products for Kenya, Brazil, India; compare export value, RCA, distance, PCI against API values
- **Feasibility table** (2–3 visits): Read the HTML table for Kenya, Turkiye, USA; compare Global Size and Global Growth 5 YR columns against API values; verify diamond ratings sort correctly
- **Header totals** (2–3 visits): Confirm Products mode total (goods+services) and Locations mode total (goods-only) match API aggregates
- **URL fidelity** (1–2 visits): Open ground truth `atlas_url` values in a browser and confirm the correct country/year/product loads

---

## 9. Scale & Time Estimate

### Expected Question Count

- **39 question templates** × 2–3 instantiations each ≈ **~75–110 questions**
- After deduplication and focusing on unique data points: **~85 questions**

### Collection Time

| Layer | Activity | Time | Notes |
|-------|----------|------|-------|
| **0 — Setup** | Build product ID mapping from API | ~5 min | One query |
| **1 — API** | Write collection script | ~45–60 min | Extend existing script |
| **1 — API** | Run script for all combinations | ~2–3 min | Fully parallelizable |
| **1 — API** | Generate question/results files | ~15–20 min | Template-based |
| **2 — Verify** | Browser spot-checks (8–12 pages) | ~20–30 min | Treemap tooltips, feasibility table, header totals, URL fidelity |
| **3 — Integrate** | Update eval_questions.json | ~10 min | One batch operation |
| | **Total** | **~1.5–2 hours** | |

### Comparison: Country Pages vs Explore Pages

| Dimension | Country Pages | Explore Pages |
|-----------|--------------|---------------|
| Narrative text (browser only) | ~15% of data points | ~0% (all data via API) |
| Browser page visits needed | ~30–40 | ~8–12 (spot-checks only) |
| API endpoints | 1 (`/api/countries/graphql`) | 1 (`/api/graphql`) |
| Unique query types needed | ~6 | ~8–10 |
| New question categories | 11 | 8 |
| Estimated questions | ~109 (done) | ~85 |
| Time estimate | ~1.5–3 hours | ~1.5–2 hours |
