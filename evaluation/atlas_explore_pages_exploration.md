# Atlas Explore Pages Exploration

This document comprehensively maps the Atlas Explore pages — their structure, visible data, interactive elements, and relationship to the GraphQL API. It follows the format established by `evaluation/atlas_country_pages_exploration.md` (which catalogs 12 country page subpages with 62 extractable data points).

---

## Table of Contents

1. [URL Structure & Navigation](#url-structure--navigation)
2. [Global Controls (Top Bar)](#global-controls-top-bar)
3. [Page-by-Page Exploration](#page-by-page-exploration)
   - [3.1 Trade Composition (Treemap)](#31-trade-composition-treemap)
   - [3.2 Trade Map (Geomap)](#32-trade-map-geomap)
   - [3.3 Trade Over Time (Overtime)](#33-trade-over-time-overtime)
   - [3.4 Global Market Share (Marketshare)](#34-global-market-share-marketshare)
   - [3.5 Product Space (Productspace)](#35-product-space-productspace)
   - [3.6 Growth Opportunity — Graph View (Feasibility)](#36-growth-opportunity--graph-view-feasibility)
   - [3.7 Growth Opportunity — Table View (Feasibility/Table)](#37-growth-opportunity--table-view-feasibilitytable)
4. [GraphQL API Overview (Explore Endpoint)](#graphql-api-overview-explore-endpoint)
5. [GraphQL API → Website Component Mapping](#graphql-api--website-component-mapping)
6. [URL Parameter → API Argument Mapping](#url-parameter--api-argument-mapping)
7. [Explore API vs Country Pages API](#explore-api-vs-country-pages-api)
8. [Products vs Locations Mode Summary](#products-vs-locations-mode-summary)
9. [Interactive Elements Summary](#interactive-elements-summary)
10. [Extractable Data Points Catalog](#extractable-data-points-catalog)

---

## URL Structure & Navigation

### Base URL

- **Base**: `https://atlas.hks.harvard.edu/explore`
- Navigating to `/explore` (no slug) **redirects** to `/explore/treemap?year=2024` (the default visualization).
- The site **always requires an exporter** — if none is specified in the URL, it defaults to an arbitrary country (e.g., Saudi Arabia or Australia), not a "World" aggregate view.

### Seven Visualization Types

| # | Slug | Full URL Pattern | Sidebar Label | Description |
|---|------|-----------------|---------------|-------------|
| 1 | `treemap` | `/explore/treemap?year=2024` | TRADE COMPOSITION | Trade composition treemap |
| 2 | `geomap` | `/explore/geomap?year=2024` | TRADE MAP | Choropleth world map |
| 3 | `overtime` | `/explore/overtime?year=2024&startYear=1995&endYear=2024` | TRADE OVER TIME | Stacked area time series |
| 4 | `marketshare` | `/explore/marketshare?year=2024&startYear=1995&endYear=2024` | GLOBAL SHARE | Sector market share multi-line chart |
| 5 | `productspace` | `/explore/productspace?year=2024` | PRODUCT SPACE | Product relatedness network |
| 6 | `feasibility` | `/explore/feasibility?year=2024` | GROWTH OPPORTUNITY | Growth opportunity scatter plot |
| 6b | `feasibility/table` | `/explore/feasibility/table?year=2024&productLevel=4` | (Table View toggle) | Growth opportunity ranked table |

### Left Sidebar Navigation

All Explore pages share a persistent left sidebar with 6 icons + labels linking to each visualization type: TRADE COMPOSITION, TRADE MAP, TRADE OVER TIME, GLOBAL SHARE, PRODUCT SPACE, GROWTH OPPORTUNITY. The active page is highlighted. A collapsible MENU hamburger sits at the top. At the bottom: a help icon (?), a download icon (↓), and a feedback/chat icon.

### URL Query Parameters

| Parameter | Values | Description | Auto-added? |
|-----------|--------|-------------|-------------|
| `year` | `1995`–`2024` | Display year | Yes |
| `startYear` | `1995`–`2024` | Time series start (overtime, marketshare) | Only on time series pages |
| `endYear` | `1995`–`2024` | Time series end | Only on time series pages |
| `exporter` | `country-{iso}`, `group-{id}` | Set exporter country/group | Auto-added on navigation |
| `importer` | `country-{iso}`, `group-1` (World) | Set importer country/group | Auto-added as `group-1` (World) |
| `product` | `product-HS92-{id}` | Filter to specific product | Only when product selected |
| `productLevel` | `2`, `4`, `6` | Product detail level (HS digits) | Auto-added on feasibility/table (`4`) |
| `view` | `markets` | Switch to Locations view | Only when Locations mode active |
| `tradeDirection` | `imports` | Switch to import flows | Only when imports selected (default = exports) |

**Key URL behaviors observed:**
- `importer=group-1` is auto-appended and represents "World" (all partners).
- Switching to Locations mode adds `view=markets` and removes `importer`.
- The `exporter` ↔ `importer` swap button (⇆) on the top bar physically swaps the two parameters.

---

## Global Controls (Top Bar)

The top control bar varies by visualization type but always includes:

| Control | Present On | Options/Behavior |
|---------|-----------|-----------------|
| **Products / Locations** toggle | treemap, overtime | Switches between product breakdown and trade partner breakdown |
| **Exporter** dropdown | All pages | Searchable country selector; also accepts groups |
| **Importer** dropdown | treemap, geomap, overtime (Products mode) | Defaults to "World"; searchable |
| **⇆ Swap** button | treemap, geomap, overtime | Swaps exporter and importer |
| **Products** dropdown | treemap (Locations), geomap | "All Products (HS92)" default; can filter to specific product |
| **Year** dropdown | All pages | 1995–2024 |
| **Start Year / End Year** | overtime, marketshare | Range selectors |
| **Graph View / Table View** | feasibility only | Toggles scatter ↔ table |
| **SETTINGS** gear icon | All pages | Opens visualization settings panel |
| **FIND IN VIZ** search | treemap, overtime, productspace, feasibility | Search for products/countries in the visualization |

---

## Page-by-Page Exploration

### 3.1 Trade Composition (Treemap)

**URL**: `/explore/treemap?year=2024&exporter=country-{iso}`

**Description**: Interactive treemap where rectangles are sized proportionally to trade value and colored by sector/region.

#### Products Mode (Default)

- **Title**: "What did {Country} export in {year}?"
- **Total Value**: Displayed top-left (e.g., "$16B") — includes both goods and services
- **Treemap**: Rectangles sized by export value, labeled with product name + dollar value (e.g., "Tea $1.39B")
- **Legend**: "PRODUCT SECTORS" — Services, Textiles, Agriculture, Stone, Minerals, Metals, Chemicals, Vehicles, Machinery, Electronics, Other (11 sectors)
- **Canvas-based**: The treemap is rendered on `<canvas>`, so product data is NOT accessible via DOM queries

**Tooltip (hover over product):**

| Field | Example | API Field |
|-------|---------|-----------|
| Product name | "Tea" | `productHs92.nameShortEn` |
| HS code | "0902 HS92" | `productHs92.code` |
| Sector | Agriculture (with color swatch) | Derived from `productHs92.topParent` |
| Export Value | $1.39B | `countryProductYear.exportValue` |
| Share | 8.54% | Derived: `exportValue / totalExportValue` |

**Expanded tooltip ("Show more"):**

| Field | Example | API Field |
|-------|---------|-----------|
| Revealed Comparative Advantage (RCA) | 37.65 | `countryProductYear.exportRca` |
| Distance | 0.781 | `countryProductYear.distance` |
| Product Complexity Index (PCI) | -0.779 | `countryProductYear.normalizedPci` (or `productYear.pci`) |

**Drill-down links in tooltip:**
- "Who exported this product?" → switches to product-centric location view
- "Where did {Country} export this product to?" → bilateral by-product view

**Settings Panel:**

| Setting | Options | Default |
|---------|---------|---------|
| Detail Level | 2 digit, 4 digit, 6 digit | 4 digit |
| Trade Flow | Gross, Net | Gross |
| Product Class | HS 1992, HS 2012, HS 2022, SITC | HS 1992 |
| Color by | Sector, Complexity, Entry Year | Sector |

#### Locations Mode

- **Toggle**: Click "Locations" button in top bar
- **URL change**: Adds `view=markets`, removes `importer`
- **Title**: "Where did {Country} export All Products to in {year}?"
- **Total Value**: Changes — shows goods-only value (e.g., "$8.2B" vs "$16B" in Products mode for Kenya), because services bilateral data is excluded
- **Treemap**: Rectangles sized by bilateral trade value, labeled with country name + dollar value (e.g., "Uganda $901M")
- **Legend**: Changes to "REGIONS" — Services Partners, Africa, Americas, Asia, Europe, Oceania, Other
- **Top bar changes**: "IMPORTER" label becomes "PRODUCTS" dropdown (filter by specific product)

**Tooltip (hover over country):**

| Field | Example | API Field |
|-------|---------|-----------|
| Product name (if filtered) | "Transport" | From product filter |
| Sector | Services (with color swatch) | Derived |
| Export Value | $2.19B | `countryCountryYear.exportValue` |
| Share | 26.83% | Derived |

**Drill-down links:**
- "What products did {Country} export to {Partner}?" → bilateral product breakdown
- "Where did {Partner} export to?" → partner's export destinations

**GraphQL queries used:**
- Products mode: `countryProductYear` (aliased as `data: countryProductYear(...)`)
- Locations mode: `countryCountryYear` (aliased as `data: countryCountryYear(...)`)

---

### 3.2 Trade Map (Geomap)

**URL**: `/explore/geomap?year=2024&exporter=country-{iso}`

**Description**: Choropleth world map showing trade intensity by color gradient.

- **Locations mode ONLY** — the "Products" button is disabled/grayed out
- **Title**: "Where did {Country} export All Products to in {year}?"
- **Total Value**: Goods-only (e.g., "$8.2B")
- **Map provider**: Mapbox (© Mapbox © OpenStreetMap)
- **Color scale**: Continuous gradient from $10k (light/green) to $1B (dark blue), labeled "GROSS TRADE"
- **Selected country**: Highlighted in yellow/gold with label "Selected Location"
- **Controls**: +ZOOM, -ZOOM, RESET ZOOM
- **Top bar**: EXPORTER, PRODUCTS dropdown (can filter to specific product), YEAR, SETTINGS

**Settings Panel:**

| Setting | Options | Default |
|---------|---------|---------|
| Trade Flow | Gross, Net | Gross |
| Product Class | HS 1992, HS 2012, HS 2022, SITC | HS 1992 |

**GraphQL query**: `countryCountryYear` (same as treemap Locations mode)

**Key difference from treemap**: No tooltip access via canvas — the map uses Mapbox GL, and hovering shows browser-native tooltips with country name + trade value.

---

### 3.3 Trade Over Time (Overtime)

**URL**: `/explore/overtime?year=2024&startYear=1995&endYear=2024&exporter=country-{iso}`

**Description**: Stacked area chart showing trade composition over time.

#### Products Mode (Default)

- **Title**: "What did {Country} export, {startYear} – {endYear}?"
- **Total Value**: "{value} ({year})" — value for the selected year (e.g., "$16B (2024)")
- **Chart**: Stacked area, X-axis = years, Y-axis = trade value
- **Areas**: Colored by sector (same 11-sector color scheme)
- **Legend**: "PRODUCT SECTORS" (same as treemap)

**Y-axis metric selector** (clickable ">" on left side):
1. **Current Gross Exports** (default)
2. **Constant (2024 USD)**
3. **Per Capita**
4. **Per Capita Constant (2024 USD)**

**Settings Panel:**

| Setting | Options | Default |
|---------|---------|---------|
| Detail Level | 2 digit, 4 digit, 6 digit | 4 digit |
| Trade Flow | Gross, Net | Gross |
| Product Class | HS 1992, HS 2012, HS 2022, SITC | HS 1992 |

#### Locations Mode

- Same chart type but areas represent trade partner regions instead of product sectors
- Legend changes to "REGIONS"

**GraphQL queries used:**
- Products mode: `countryProductYear` with `yearMin`/`yearMax` spanning the full range
- Locations mode: `countryCountryYear` with `yearMin`/`yearMax`
- Constant-dollar and per-capita metrics use `countryYear` (for GDP, population) + `year` (for deflators)

---

### 3.4 Global Market Share (Marketshare)

**URL**: `/explore/marketshare?year=2024&startYear=1995&endYear=2024&exporter=country-{iso}`

**Description**: Multi-line time series showing a country's global market share by sector over time.

- **Title**: "{Country}'s global market share, {startYear} – {endYear}"
- **Chart type**: Multi-line chart (one line per sector)
- **Y-axis**: "Share of World Market by Sector" (percentage, e.g., 0% – 0.22% for Kenya)
- **X-axis**: Years (e.g., 1996–2024)
- **Lines**: Color-coded by sector (same color scheme)
- **No Products/Locations toggle** — this is a single-perspective view
- **No Importer dropdown** — only EXPORTER, START YEAR, END YEAR, SETTINGS
- **Legend**: "PRODUCT SECTORS"

**Settings Panel:**

| Setting | Options | Default |
|---------|---------|---------|
| Product Class | HS 1992, HS 2012, HS 2022, SITC | HS 1992 |

**GraphQL queries**: Computed from `countryProductYear.exportValue` / `productYear.exportValue` (global total) per sector per year. Requires both queries across the full year range.

---

### 3.5 Product Space (Productspace)

**URL**: `/explore/productspace?year=2024&exporter=country-{iso}`

**Description**: Network graph showing all products as nodes positioned by their relatedness, with the country's exports highlighted.

- **Title**: "{Country} in the Product Space, {year}"
- **Total Value**: Goods-only (e.g., "$7.9B" for Kenya)
- **Tutorial overlay**: First visit shows an interactive tutorial about the product space concept, with "LET'S LEARN" and "SKIP THE TUTORIAL" buttons
- **Network visualization**: Canvas-based
  - **Colored nodes**: Products the country exports with high RCA (comparative advantage)
  - **Grey nodes**: Products with low or no export
  - **Node sizing**: Based on Global Exports
  - **Edges**: Lines connecting related products (close = similar capabilities)
- **Cluster labels** (8 clusters, different from the 11 treemap sectors):
  - Agricultural Goods, Construction Goods, Electronics, Chemicals and Basic Metals, Metalworking and Machinery, Minerals, Textile and Home Goods, Apparel
- **Legend**:
  - "CLUSTERS" (not "PRODUCT SECTORS") with 8 cluster colors
  - "How to Read" diagram: Similar Products (close) ↔ Distinct Products (distant)
  - "Node Color": Colored = High export, Grey = Low or no export
  - "Node Sizing based on Global Exports"
- **Controls**: EXPORTER, YEAR, SETTINGS, +ZOOM/-ZOOM/RESET ZOOM, FIND IN VIZ, "Learn How to Use" button
- **No Products/Locations toggle**

**Settings Panel:**

| Setting | Options | Default |
|---------|---------|---------|
| Product Class | HS 1992, HS 2012, HS 2022, SITC | HS 1992 |

**GraphQL queries**: `countryProductYear` (for RCA to color nodes) + `productProduct` (for network edges/relatedness) + product catalog (`productHs92` etc.) for node positions.

---

### 3.6 Growth Opportunity — Graph View (Feasibility)

**URL**: `/explore/feasibility?year=2024&exporter=country-{iso}`

**Description**: Scatter plot of products showing feasibility vs. desirability for a given country.

- **Title**: "Growth Opportunities for {Country}, {year}"
- **Total Value**: Goods-only (e.g., "$947M" for Kenya — this is the value of "opportunity" products, not total exports)
- **X-axis**: **Distance** — labeled "More Nearby ◄" to "Less Nearby ►" (range ~0.65–0.95)
- **Y-axis**: Dual-labeled — **Opportunity Gain** (top, "More Complex ▲") and **Product Complexity** (bottom, "Less Complex ▼") (range ~-3.5 to 2.5)
- **Reference line**: Dashed horizontal at country's ECI value (e.g., "ECI (HS92) -0.27" for Kenya)
- **Bubbles**: Sized by global trade value, colored by sector
- **Legend**: "PRODUCT SECTORS" (excludes Services — only goods shown)
- **Controls**: EXPORTER, YEAR, **Graph View / Table View** toggle, SETTINGS, +ZOOM/-ZOOM/RESET ZOOM, FIND IN VIZ
- **No Products/Locations toggle**

**Key differences from Country Pages' growth-opportunities scatter:**
- Explore page shows **numeric axis values** (distance 0.68–0.92, complexity -3.5 to 2.5)
- Country pages show qualitative labels and radio buttons for strategy approach (Low-hanging Fruit / Balanced Portfolio / Long Jumps)
- Explore page is available for **all countries** including frontier economies; Country pages hide it for highest-complexity countries

**Settings Panel:**

| Setting | Options | Default |
|---------|---------|---------|
| Product Class | HS 1992, HS 2012, HS 2022, SITC | HS 1992 |

**GraphQL queries**: `countryProductYear` (for distance, cog, normalizedPci, exportRca per product) + `productYear` (for global trade value, pci)

---

### 3.7 Growth Opportunity — Table View (Feasibility/Table)

**URL**: `/explore/feasibility/table?year=2024&exporter=country-{iso}&productLevel=4`

**Description**: Ranked HTML table of products sorted by opportunity metrics. This is the **only Explore page with DOM-accessible tabular data** (not canvas-rendered).

- **Title**: "Growth Opportunities for {Country}, {year}"
- **Total Value**: Same as graph view
- **Table is HTML** — data can be read via DOM queries

**Table Columns:**

| Column | Display | Example | API Field | Notes |
|--------|---------|---------|-----------|-------|
| Sector indicator | Colored bar on left | Purple bar (Chemicals) | Derived from `topParent` | |
| Product Name | Name + HS code | "Photographic film, developed (3705 HS)" | `productHs92.nameEn` + `code` | |
| "Nearby" Distance | Diamond rating (7 diamonds) | ◆◆◇◇◇◇◇ | `countryProductYear.distance` | Inverted: more diamonds = closer |
| Opportunity Gain | Diamond rating (7 diamonds) | ◆◆◆◆◆◇◇ | `countryProductYear.cog` | |
| Product Complexity | Diamond rating (7 diamonds) | ◆◆◆◆◆◆◇ | `countryProductYear.normalizedPci` | |
| Global Size (USD) | Dollar amount | $2.61B | `productYear.exportValue` | |
| Global Growth 5 YR | Percentage + arrow | ↑ 13.1% | `productYear.exportValueConstCagr5` | ↑=positive, ↓=negative |

**Table behavior:**
- All columns are **sortable** (clickable headers with ▲ indicator)
- Default sort: Product Complexity (descending)
- Rows include all products where the country does NOT have RCA > 1 (opportunity products only)
- `&productLevel=4` is auto-appended to URL

**Key difference from Country Pages' product-table:**
- Explore table uses **diamond ratings** (same visual style) but the column headers use different labels ("Nearby" Distance vs just "Distance")
- Explore table shows **all** opportunity products, not just "Top 50"
- Explore table is available for **all countries**; Country pages' product-table is hidden for frontier economies

**GraphQL queries**: Same as feasibility graph view — `countryProductYear` + `productYear`

---

## GraphQL API Overview (Explore Endpoint)

### Endpoint

```
POST https://atlas.hks.harvard.edu/api/graphql
POST https://staging.atlas.growthlab-dev.com/api/graphql
Content-Type: application/json
```

No authentication required. Introspection enabled. Rate limit: ≤ 120 req/min (2 req/sec). Include `User-Agent` header.

> **Note:** The Atlas API is best used to access data for stand-alone economic analysis, not to support other software applications. See the [official API documentation](https://github.com/harvard-growth-lab/api-docs/blob/main/atlas.md) for full terms.

**GraphiQL interface:** Navigating to [`https://atlas.hks.harvard.edu/api/graphql`](https://atlas.hks.harvard.edu/api/graphql) in a browser opens the **GraphiQL interface**. The "Docs" menu in the top right of the page opens the **Documentation Explorer**, which allows you to browse all queries, types, field descriptions, and arguments interactively. This is the definitive, always-up-to-date schema reference.

### All Query Types (27 total)

The full API exposes 27 query types — 2 more than previously documented (the existing `explore_page_collection_guide.md` lists 26). The complete list:

#### Core Trade Data (5 queries)

| Query | Arguments | Returns | Purpose |
|-------|-----------|---------|---------|
| `countryProductYear` | `productClass, servicesClass, productLevel!, countryId, productId, yearMin, yearMax` | `[CountryProductYear]` | Country × product trade data (richest type) |
| `countryYear` | `countryId, productClass, servicesClass, yearMin, yearMax` | `[CountryYear]` | Country-level aggregates (GDP, ECI, etc.) |
| `productYear` | `productClass, servicesClass, productLevel!, productId, yearMin, yearMax` | `[ProductYear]` | Global product-level data |
| `countryCountryYear` | `productClass, servicesClass, countryId, partnerCountryId, yearMin, yearMax` | `[CountryCountryYear]` | Bilateral trade totals |
| `countryCountryProductYear` | `countryId, partnerCountryId, yearMin, yearMax, productClass, servicesClass, productLevel, productId, productIds` | `[CountryCountryProductYear]` | Bilateral trade by product |

#### Previously Undocumented Queries (3 queries)

| Query | Arguments | Returns | Purpose |
|-------|-----------|---------|---------|
| `countryCountryProductYearGrouped` | Same as `countryCountryProductYear` | `[CountryCountryProductYearGrouped]` | Grouped bilateral trade (returns `productIds` + `data` arrays) |
| `productProduct` | `productClass!, productLevel!` | `[ProductProduct]` | Product-to-product relatedness strengths (product space edges) |
| `banner` | *(none)* | `[Banner]` | Site announcement banners (6 fields: `bannerId`, `startTime`, `endTime`, `text`, `ctaText`, `ctaLink`) |

#### Group / Regional Data (4 queries)

| Query | Arguments | Returns | Purpose |
|-------|-----------|---------|---------|
| `groupYear` | `productClass, servicesClass, groupId, groupType, yearMin, yearMax` | `[GroupYear]` | Group-level aggregate trade |
| `groupGroupProductYear` | `productClass, servicesClass, productLevel, productId, groupId, partnerGroupId, yearMin, yearMax` | `[GroupGroupProductYear]` | Group-to-group bilateral |
| `countryGroupProductYear` | `productClass, servicesClass, productLevel, productId, countryId, partnerGroupId!, yearMin, yearMax` | `[CountryGroupProductYear]` | Country-to-group bilateral |
| `groupCountryProductYear` | `productClass, servicesClass, productLevel, productId, groupId!, partnerCountryId, yearMin, yearMax` | `[GroupCountryProductYear]` | Group-to-country bilateral |

#### Reference Data (7 queries)

| Query | Arguments | Returns | Purpose |
|-------|-----------|---------|---------|
| `locationCountry` | *(none)* | `[LocationCountry]` | All countries (ISO codes, income level, flags) |
| `locationGroup` | `groupType` | `[LocationGroup]` | Groups with CAGR stats |
| `productHs92` | `productLevel, servicesClass` | `[Product]` | HS92 product catalog |
| `productHs12` | `productLevel, servicesClass` | `[Product]` | HS 2012 catalog |
| `productHs22` | `productLevel, servicesClass` | `[Product]` | HS 2022 catalog |
| `productSitc` | `productLevel, servicesClass` | `[Product]` | SITC catalog |
| `year` | `yearMin, yearMax` | `[Year]` | Available years + deflators |

#### Metadata & Diagnostics (8 queries)

| Query | Arguments | Returns | Purpose |
|-------|-----------|---------|---------|
| `countryYearThresholds` | `productClass!, countryId, yearMin, yearMax` | `[CountryYearThresholds]` | Percentile distributions |
| `dataFlags` | `countryId` | `[DataFlags]` | Data quality flags |
| `dataAvailability` | *(none)* | `[DataAvailability]` | Year ranges per classification |
| `conversionPath` | `sourceCode!, sourceClassification!, targetClassification!` | `[ConversionClassifications]` | HS/SITC code conversion |
| `conversionSources` | `targetCode!, targetClassification!, sourceClassification!` | `[ConversionClassifications]` | Reverse code lookup |
| `conversionWeights` | `sitc1962, sitc1976, sitc1988, hs1992, hs1997, hs2002, hs2007, hs2012, hs2017, hs2022` | `[ConversionWeights]` | Weighted conversion between classifications |
| `downloadsTable` | *(none)* | `[DownloadsTable]` | Data download catalog (70 entries) |
| `metadata` | *(none)* | `Metadata` | Server/ingestion info |

### Key Type Schemas

#### `CountryProductYear` (richest type — 22 fields)

```
countryId, locationLevel, productId, productLevel, year
exportValue, importValue, globalMarketShare
exportRca, exportRpop
isNew, productStatus (absent/lost/new/present)
cog, distance
normalizedPci, normalizedCog, normalizedDistance, normalizedExportRca
normalizedPciRcalt1, normalizedCogRcalt1, normalizedDistanceRcalt1, normalizedExportRcaRcalt1
```

#### `CountryYear` (18 fields)

```
countryId, year
exportValue, importValue
population, gdp, gdppc, gdpPpp, gdppcPpp
gdpConst, gdpPppConst, gdppcConst, gdppcPppConst
eci, eciFixed, coi
currentAccount, growthProj
```

#### `ProductYear` (11 fields)

```
productId, productLevel, year
exportValue, importValue
exportValueConstGrowth5, importValueConstGrowth5
exportValueConstCagr5, importValueConstCagr5
pci, complexityEnum (low/moderate/high)
```

#### `CountryCountryYear` (7 fields)

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

#### `ProductProduct` (4 fields) — previously undocumented

```
productId, targetId, strength, productLevel
```

Purpose: Encodes the product space network edges. Each row is a pair of products with a `strength` value indicating how related they are (based on co-export patterns).

#### `Product` (21 fields)

```
productId, productLevel, parent, topParent, productIdHierarchy
code, productType (good/service)
nameEn, nameShortEn, nameEs, nameShortEs
clusterId, productSpaceX, productSpaceY
legacyProductSpaceX, legacyProductSpaceY
isShown, globalExportThreshold, showFeasibility
naturalResource, greenProduct
```

#### `LocationCountry` (22 fields)

```
countryId, locationLevel, iso3Code, iso2Code, legacyCountryId
nameEn, nameShortEn, nameAbbrEn, nameEs, nameShortEs
thePrefix, isTrusted, formerCountry
incomelevelEnum (high/upper_middle/lower_middle/low)
reportedServ, reportedServRecent
countryProject, rankingsOverride, cpOverride
inRankings, inCp, inMv
```

#### `LocationGroup` (32 fields)

```
groupId, groupName, groupType, members
parentId, parentName, parentType, legacyGroupId
gdpMean, gdpSum
exportValueMean, exportValueSum
exportValueCagr3/5/10/15
exportValueNonOilCagr3/5/10/15
gdpCagr3/5/10/15, gdpConstCagr3/5/10/15
gdppcConstCagr3/5/10/15
```

#### `GroupYear` (8 fields)

```
groupId: ID
groupType: GroupType
year: Int
population: Float
gdp: Float
gdpPpp: Float
exportValue: Float
importValue: Float
```

#### `GroupGroupProductYear` (11 fields)

```
groupId: ID
groupType: GroupType
locationLevel: LocationLevel
partnerGroupId: ID
partnerType: GroupType
partnerLevel: LocationLevel
productId: ID
productLevel: Int
year: Int
exportValue: Float
importValue: Float
```

#### `CountryGroupProductYear` (9 fields)

```
locationLevel: LocationLevel
partnerLevel: LocationLevel
productId: ID
productLevel: Int
year: Int
exportValue: Float
importValue: Float
countryId: ID
partnerGroupId: ID
```

#### `GroupCountryProductYear` (9 fields)

```
locationLevel: LocationLevel
partnerLevel: LocationLevel
productId: ID
productLevel: Int
year: Int
exportValue: Float
importValue: Float
groupId: ID
partnerCountryId: ID
```

#### `DataFlags` (20 fields)

```
countryId: ID
formerCountry: Boolean
countryProject: Boolean
rankingsOverride: Boolean
cpOverride: Boolean
year: Boolean
minPopulation: Boolean
population: Int
minAvgExport: Boolean
avgExport3: Float
complexityCurrentYearCoverage: Boolean
complexityLookbackYearsCoverage: Boolean
imfAnyCoverage: Boolean
imfCurrentYearsCoverage: Boolean
imfLookbackYearsCoverage: Boolean
rankingsEligible: Boolean
countryProfilesEligible: Boolean
inRankings: Boolean
inCp: Boolean
inMv: Boolean
```

#### `CountryYearThresholds` (18 fields)

```
countryId: ID
year: Int
variable: String
mean: Float
median: Float
min: Float
max: Float
std: Float
percentile10: Float
percentile20: Float
percentile25: Float
percentile30: Float
percentile40: Float
percentile50: Float
percentile60: Float
percentile70: Float
percentile75: Float
percentile80: Float
percentile90: Float
```

#### `ConversionWeights` (19 fields)

```
sitc1962: String
sitc1976: String
weightSitc1962Sitc1976: Float
sitc1988: String
weightSitc1976Sitc1988: Float
hs1992: String
weightSitc1988Hs1992: Float
hs1997: String
weightHs1992Hs1997: Float
hs2002: String
weightHs1997Hs2002: Float
hs2007: String
weightHs2002Hs2007: Float
hs2012: String
weightHs2007Hs2012: Float
hs2017: String
weightHs2012Hs2017: Float
hs2022: String
weightHs2017Hs2022: Float
```

#### `DownloadsTable` (17+ fields)

```
tableId: ID!
tableName: String
tableDataType: DownloadTableDataType
repo: DownloadTableRepo
complexityData: Boolean
productLevel: Int
facet: DownloadTableFacet
yearMin: Int
yearMax: Int
displayName: String
productClassificationHs92: Boolean
productClassificationHs12: Boolean
productClassificationHs22: Boolean
productClassificationSitc: Boolean
productClassificationServicesUnilateral: Boolean
dvFileId: Int
dvFileName: String
dvFileSize: String
dvPublicationDate: String
doi: String
columns: [DownloadsColumn]
```

#### `Banner` (6 fields)

```
bannerId: Int!
startTime: DateTime
endTime: DateTime
text: String
ctaText: String
ctaLink: String
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

### Data Availability

| Classification | Year Range |
|---------------|-----------|
| HS92 | 1995–2024 |
| HS12 | 2012–2024 |
| HS22 | 2022–2024 |
| SITC | 1962–2024 |

### Server Metadata (4 fields)

The `Metadata` type has 4 fields: `serverName`, `ingestionCommit`, `ingestionDate`, and `apiCommit`.

```
serverName: rhea
ingestionCommit: ffab5de
ingestionDate: 2026-02-01
apiCommit: (string — commit hash for the API code)
```

---

## GraphQL API → Website Component Mapping

This section maps each visualization page to the specific GraphQL queries the frontend makes.

### Query Mapping Table

| Page | Mode | Primary Query | Supporting Queries | Fields Used |
|------|------|--------------|-------------------|-------------|
| **Treemap** | Products | `countryProductYear` | `productHs92` (catalog), `locationCountry`, `year` | `exportValue`, `importValue`, `exportRca`, `distance`, `normalizedPci`, `globalMarketShare`, `productStatus`, `cog` |
| **Treemap** | Locations | `countryCountryYear` | `locationCountry` | `exportValue`, `importValue` |
| **Geomap** | Locations only | `countryCountryYear` | `locationCountry` | `exportValue`, `importValue` |
| **Overtime** | Products | `countryProductYear` | `countryYear` (for GDP, population), `year` (deflators) | `exportValue`, `importValue` per year |
| **Overtime** | Locations | `countryCountryYear` | `countryYear`, `year` | `exportValue`, `importValue` per year |
| **Marketshare** | (single) | `countryProductYear` + `productYear` | `productHs92` (for sector mapping) | `exportValue` per sector per year (country vs global) |
| **Product Space** | (single) | `countryProductYear` | `productProduct` (edges), `productHs92` (positions) | `exportRca` (to color nodes), `strength` (edges) |
| **Feasibility (graph)** | (single) | `countryProductYear` | `productYear`, `countryYear` (for ECI line) | `distance`, `cog`, `normalizedPci`, `exportRca`, `pci`, `exportValue` (global) |
| **Feasibility (table)** | (single) | `countryProductYear` | `productYear`, `productHs92` | `distance`, `cog`, `normalizedPci`, `exportValue` (global), `exportValueConstCagr5` |

### How the Frontend Fetches Data

The Atlas frontend makes GraphQL calls using named operation aliases:

```graphql
query CCY($countryId: Int, $yearMin: Int, $yearMax: Int, ...) {
  data: countryCountryYear(
    countryId: $countryId
    yearMin: $yearMin
    yearMax: $yearMax
    ...
  ) {
    countryId partnerCountryId year
    exportValue importValue
  }
}
```

Key patterns observed:
- Queries are aliased as `data:` (e.g., `data: countryCountryYear(...)`)
- Variables use operation-level parameters (e.g., `$countryId: Int`)
- Reference data queries (`locationCountry`, `productHs92`, etc.) are likely cached on initial page load
- The frontend makes ~10 GraphQL requests on initial treemap page load (product catalog, country list, deflators, trade data, etc.)

### Bilateral Query Selection Logic

When the user has a specific exporter and importer, the query type depends on whether each is a country or group:

| Exporter | Importer | Query Type |
|----------|----------|-----------|
| Country | World (group-1) | `countryProductYear` (no partner filter) |
| Country | Country | `countryCountryProductYear` |
| Country | Group | `countryGroupProductYear` |
| Group | Country | `groupCountryProductYear` |
| Group | Group | `groupGroupProductYear` |

---

## URL Parameter → API Argument Mapping

This mapping is critical for generating Explore page links from API query results.

### Parameter Transformations

| URL Parameter | API Argument | Transformation | Example |
|---------------|-------------|----------------|---------|
| `exporter=country-404` | `countryId: 404` | Strip `country-` prefix, parse as integer | Kenya |
| `exporter=group-5` | `groupId: 5` | Strip `group-` prefix, parse as integer | A region |
| `importer=country-840` | `partnerCountryId: 840` | Strip `country-` prefix, parse as integer | USA |
| `importer=group-1` | *(no partner filter)* | `group-1` = World = query without partner constraint | All partners |
| `year=2024` | `yearMin: 2024, yearMax: 2024` | Single year becomes min=max | |
| `startYear=1995` | `yearMin: 1995` | Direct mapping | |
| `endYear=2024` | `yearMax: 2024` | Direct mapping | |
| `product=product-HS92-726` | `productId: 726` | Strip `product-HS92-` prefix, parse as integer | Coffee (0901) |
| `productLevel=4` | `productLevel: 4` | Direct mapping | 4-digit HS |
| `tradeDirection=imports` | *(no arg change)* | Not a query argument; changes which response field to read (`importValue` instead of `exportValue`) | |
| `view=markets` | *(different query type)* | Switches from `countryProductYear` to `countryCountryYear` | Locations mode |

### Reverse Mapping: API Result → URL Construction

To generate an Atlas Explore link from an API query:

```
Base: https://atlas.hks.harvard.edu/explore/{vizType}

Required params:
  ?year={yearMax}
  &exporter=country-{countryId}

Optional params (add if present):
  &importer=country-{partnerCountryId}   # if bilateral
  &product=product-HS92-{productId}       # if product-specific
  &startYear={yearMin}                    # if time series (overtime, marketshare)
  &endYear={yearMax}                      # if time series
  &productLevel={productLevel}            # if feasibility/table
  &view=markets                           # if locations mode
  &tradeDirection=imports                 # if import data
```

### Product ID ↔ HS Code Mapping

The URL uses internal product IDs (e.g., `product-HS92-726`), NOT HS codes directly. The mapping comes from the `productHs92` query:

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

(These IDs can be retrieved at runtime via `productHs92(productLevel: 4) { productId code nameShortEn }`)

---

## Explore API vs Country Pages API

The Atlas has **two separate GraphQL APIs** with different schemas, both available on production and staging:

| Aspect | Explore API (`/api/graphql`) | Country Pages API (`/api/countries/graphql`) |
|--------|------------------------------|----------------------------------------------|
| **Query count** | 27 | 25 |
| **Custom types** | 40 | 49 |
| **ID format** | Numeric integers (`countryId: 404`) — [M49 codes](https://unstats.un.org/unsd/methodology/m49/) as designated by the UN (which coincide with ISO 3166-1 numeric codes for most countries) | String IDs (`location: "location-404"`) |
| **Year params** | `yearMin` / `yearMax` ranges | `year`, `minYear` / `maxYear` |
| **Product class** | `HS92`, `HS12`, `HS22`, `SITC` (explicit revisions) | `HS`, `SITC` (generic) |
| **Product levels** | 2, 4, **6** digit | section, twoDigit, fourDigit |
| **Services** | `servicesClass: unilateral` (explicit param) | Bundled into product class |
| **Arg descriptions** | ✅ Human-readable for ALL arguments | ❌ `None` for all arguments |

### Explore API Unique Features (not in Country Pages)

| Feature | Details |
|---------|---------|
| **Bilateral trade** (dedicated queries) | `countryCountryYear`, `countryCountryProductYear`, `countryCountryProductYearGrouped` — full bilateral trade data |
| **Group/regional trade** | `groupYear`, `groupGroupProductYear`, `countryGroupProductYear`, `groupCountryProductYear` |
| **6-digit products** | `productLevel: 6` supported |
| **HS 2022** | `productHs22` catalog + filtering |
| **Product space edges** | `productProduct` — product-to-product relatedness strengths |
| **Code conversion** | `conversionPath`, `conversionSources`, `conversionWeights` |
| **Download catalog** | `downloadsTable` — 70 downloadable dataset entries |
| **Data quality flags** | `dataFlags` with detailed eligibility criteria per country |
| **Percentile thresholds** | `countryYearThresholds` — percentile distributions for complexity variables |
| **Argument descriptions** | All arguments have human-readable descriptions via introspection |
| **Country metadata** | `LocationCountry` has 22 fields (more than Country Pages' `Location` with 15) |
| **Group CAGR stats** | `LocationGroup` has 32 fields including 3/5/10/15-year CAGR for export, non-oil export, GDP |

### Country Pages API Unique Features (not in Explore)

| Feature | Details |
|---------|---------|
| **`countryProfile`** | 46 derived analytical fields — growth projections, diversification grade, strategic approach, income classification, narrative context. This is the richest single query in either API. |
| **`countryLookback`** | Historical change metrics with configurable year ranges (3/5/10/15 years) — rank changes, export growth, ECI changes |
| **`newProductsCountry`** | New product counts with comparison countries |
| **Policy enums** | `PolicyRecommendation` (ParsimoniousIndustrial, StrategicBets, LightTouch, TechFrontier), `DiversificationGrade` (A+ through D-), `ExportValueGrowthClassification`, `StructuralTransformationStep` |
| **TreeMap facets** | `treeMap(facet: CPY_C)` for products, `CPY_P` for products+PCI, `CCY_C` for partners — single query returns treemap-ready data |
| **Pre-computed product space** | `productSpace` returns country-specific data with connections pre-resolved |
| **Narrative-ready data** | Many fields designed for text generation (e.g., `GDPPCConstantCAGRRegionalDifference`, `MarketShareMainSectorDirection`, `NewProductsComments`) |

### Overlap: Same Data Available in Both

| Data Point | Explore API | Country Pages API | Prefer |
|-----------|-------------|-------------------|--------|
| Country exports by product | `countryProductYear.exportValue` | `treeMap(facet: CPY_C)` | Explore (more fields) |
| Country-level GDP, ECI | `countryYear.gdppc, eci` | `countryYear.gdppc, eci` | Either (equivalent) |
| Product catalog | `productHs92` (21 fields) | `allProducts` (10 fields) | Explore (more fields) |
| Product complexity (PCI) | `productYear.pci` | `allProductYear.pci` | Either |
| Country metadata | `locationCountry` (22 fields) | `allLocations` (15 fields) | Explore (more fields) |

**General guidance**: Prefer the Explore API for raw trade data — it has more fields, better introspection, and explicit HS revision support. Use the Country Pages API for derived analytical metrics (growth projections, diversification grades, strategic approach, narrative descriptions) that would be expensive to recompute.

---

## Products vs Locations Mode Summary

| Visualization | Products Mode | Locations Mode | Default |
|--------------|--------------|---------------|---------|
| **Treemap** | ✅ Product breakdown by sector | ✅ Trade partner breakdown by region | Products |
| **Geomap** | ❌ Disabled | ✅ Only mode (choropleth) | Locations |
| **Overtime** | ✅ Stacked by sector | ✅ Stacked by region | Products |
| **Marketshare** | Single view (sector market share) | N/A | N/A |
| **Product Space** | Single view (network) | N/A | N/A |
| **Feasibility** | Single view (scatter/table) | N/A | N/A |

### What Changes Between Modes

| Element | Products Mode | Locations Mode |
|---------|--------------|---------------|
| Top bar labels | EXPORTER, IMPORTER | EXPORTER ↔ IMPORTER, PRODUCTS (filter) |
| Legend | "PRODUCT SECTORS" (11 sectors) | "REGIONS" (Services Partners, Africa, Americas, Asia, Europe, Oceania, Other) |
| Total Value | Goods + services | Goods only (services bilateral excluded) |
| API query | `countryProductYear` | `countryCountryYear` |
| URL | Default (no `view` param) | `view=markets` |

---

## Interactive Elements Summary

| Element | Location | Options/Range | Notes |
|---------|----------|--------------|-------|
| Products / Locations toggle | treemap, overtime top bar | Two buttons | Changes query type and legend |
| Exporter dropdown | All pages | ~230 countries + groups, searchable | Always required |
| Importer dropdown | treemap, geomap, overtime | ~230 countries + groups, searchable | Defaults to "World" |
| ⇆ Swap button | treemap, geomap, overtime | Swaps exporter ↔ importer | |
| Products filter | treemap (Locations), geomap | "All Products (HS92)" + individual products | Filters bilateral view to one product |
| Year dropdown | All pages | 1995–2024 | Depends on classification |
| Start Year / End Year | overtime, marketshare | 1995–2024 | |
| Y-axis metric selector | overtime | Current Gross Exports, Constant (2024 USD), Per Capita, Per Capita Constant (2024 USD) | Clickable ">" on left side |
| Graph View / Table View | feasibility | Toggle between scatter and HTML table | |
| Detail Level (Settings) | treemap, overtime | 2 digit, 4 digit, 6 digit | Default: 4 digit |
| Trade Flow (Settings) | treemap, geomap, overtime | Gross, Net | Default: Gross |
| Product Class (Settings) | All pages | HS 1992, HS 2012, HS 2022, SITC | Default: HS 1992 |
| Color by (Settings) | treemap | Sector, Complexity, Entry Year | Default: Sector |
| FIND IN VIZ search | treemap, overtime, productspace, feasibility | Text search for products/countries | |
| +ZOOM / -ZOOM / RESET ZOOM | geomap, productspace, feasibility | Zoom controls | |
| Product space tutorial | productspace | "LET'S LEARN" / "SKIP THE TUTORIAL" | Shows once per session |
| "Learn How to Use" button | productspace | Opens tutorial overlay | Top-right |
| Download button | All pages (sidebar) | Export data/image | Bottom of sidebar |
| Tooltip "Show more" | treemap (Products mode) | Expands to show RCA, Distance, PCI | |
| Tooltip drill-down links | treemap | "Who exported this product?" / "Where did {Country} export this product to?" | Navigates to filtered views |
| Column sort | feasibility/table | Click column header to sort ▲▼ | All columns sortable |

---

## Extractable Data Points Catalog

### From Treemap — Products Mode (`/explore/treemap`)

| # | Data Point | Source | API Field |
|---|-----------|--------|-----------|
| 1 | Total export value | Header stat | `countryYear.exportValue` (or sum of `countryProductYear.exportValue`) |
| 2 | Product export value | Treemap label / tooltip | `countryProductYear.exportValue` |
| 3 | Product export share | Tooltip | Derived: `exportValue / total` |
| 4 | Product HS code | Tooltip | `productHs92.code` |
| 5 | Product sector | Tooltip / color | Derived from `productHs92.topParent` |
| 6 | Revealed Comparative Advantage (RCA) | Expanded tooltip | `countryProductYear.exportRca` |
| 7 | Distance | Expanded tooltip | `countryProductYear.distance` |
| 8 | Product Complexity Index (PCI) | Expanded tooltip | `countryProductYear.normalizedPci` |
| 9 | Import value by product | Tooltip (trade direction: imports) | `countryProductYear.importValue` |
| 10 | Global market share per product | API only | `countryProductYear.globalMarketShare` |
| 11 | Product status (new/present/lost/absent) | API only | `countryProductYear.productStatus` |
| 12 | Complexity Outlook Gain (COG) per product | API only | `countryProductYear.cog` |

### From Treemap — Locations Mode (`/explore/treemap?view=markets`)

| # | Data Point | Source | API Field |
|---|-----------|--------|-----------|
| 13 | Bilateral export value (country → partner) | Treemap label / tooltip | `countryCountryYear.exportValue` |
| 14 | Bilateral export share | Tooltip | Derived |
| 15 | Total bilateral goods exports | Header stat | Sum of `countryCountryYear.exportValue` |

### From Geomap (`/explore/geomap`)

| # | Data Point | Source | API Field |
|---|-----------|--------|-----------|
| 16 | Bilateral export value (country → partner, per country on map) | Map color + tooltip | `countryCountryYear.exportValue` |

### From Trade Over Time (`/explore/overtime`)

| # | Data Point | Source | API Field |
|---|-----------|--------|-----------|
| 17 | Current gross exports over time (by sector) | Stacked area chart | `countryProductYear.exportValue` per year |
| 18 | Constant-dollar exports over time | Y-axis toggle | Derived from `exportValue`, `year.deflator` |
| 19 | Per-capita exports over time | Y-axis toggle | Derived: `exportValue / countryYear.population` |
| 20 | Per-capita constant exports over time | Y-axis toggle | Combined derivation |
| 21 | Trade partner exports over time | Locations mode | `countryCountryYear.exportValue` per year |
| 22 | Total export value for specific year | Header stat | `countryYear.exportValue` |

### From Global Market Share (`/explore/marketshare`)

| # | Data Point | Source | API Field |
|---|-----------|--------|-----------|
| 23 | Sector-level global market share (per year) | Line chart | Derived: `countryProductYear.exportValue / productYear.exportValue` per sector |
| 24 | Market share trend over time (per sector) | Line chart | Time series of above |

### From Product Space (`/explore/productspace`)

| # | Data Point | Source | API Field |
|---|-----------|--------|-----------|
| 25 | Products exported with RCA > 1 (colored nodes) | Node color | `countryProductYear.exportRca > 1` |
| 26 | Product relatedness (edges between nodes) | Edge connections | `productProduct.strength` |
| 27 | Total goods export value | Header stat | Sum of goods `countryProductYear.exportValue` |

### From Growth Opportunity — Graph View (`/explore/feasibility`)

| # | Data Point | Source | API Field |
|---|-----------|--------|-----------|
| 28 | Product distance (X-axis) | Scatter position | `countryProductYear.distance` |
| 29 | Product opportunity gain / complexity (Y-axis) | Scatter position | `countryProductYear.cog` / `normalizedPci` |
| 30 | Country ECI | Reference line | `countryYear.eci` |
| 31 | Global product trade value | Bubble size | `productYear.exportValue` |
| 32 | Total opportunity value | Header stat | Sum of opportunity products |

### From Growth Opportunity — Table View (`/explore/feasibility/table`)

| # | Data Point | Source | API Field |
|---|-----------|--------|-----------|
| 33 | Product name + HS code | Table column | `productHs92.nameEn` + `code` |
| 34 | "Nearby" Distance (diamond rating) | Table column | `countryProductYear.distance` |
| 35 | Opportunity Gain (diamond rating) | Table column | `countryProductYear.cog` |
| 36 | Product Complexity (diamond rating) | Table column | `countryProductYear.normalizedPci` |
| 37 | Global Size (USD) | Table column | `productYear.exportValue` |
| 38 | Global Growth 5 YR (percentage) | Table column | `productYear.exportValueConstCagr5` |

### API-Only Data Points (not directly visible on any page)

| # | Data Point | API Field |
|---|-----------|-----------|
| 39 | Bilateral trade by product (export + import) | `countryCountryProductYear.exportValue / importValue` |
| 40 | GDP per capita (nominal, PPP, constant) | `countryYear.gdppc / gdppcPpp / gdppcConst / gdppcPppConst` |
| 41 | GDP (nominal, PPP, constant) | `countryYear.gdp / gdpPpp / gdpConst / gdpPppConst` |
| 42 | Population | `countryYear.population` |
| 43 | ECI (standard and fixed) | `countryYear.eci / eciFixed` |
| 44 | Complexity Outlook Index (COI) | `countryYear.coi` |
| 45 | Growth projection | `countryYear.growthProj` |
| 46 | Current account | `countryYear.currentAccount` |
| 47 | Regional/group export value | `groupYear.exportValue` |
| 48 | Regional export CAGR (3/5/10/15 year) | `locationGroup.exportValueCagr3/5/10/15` |
| 49 | Regional non-oil export CAGR | `locationGroup.exportValueNonOilCagr3/5/10/15` |
| 50 | Regional GDP CAGR | `locationGroup.gdpCagr3/5/10/15` |
| 51 | Group membership (countries in region) | `locationGroup.members` |
| 52 | Product natural resource flag | `productHs92.naturalResource` |
| 53 | Product green product flag | `productHs92.greenProduct` |
| 54 | HS code conversion between revisions | `conversionPath` / `conversionSources` |
| 55 | Data availability (year ranges per classification) | `dataAvailability` |
| 56 | Country data quality flags | `dataFlags` |
| 57 | Year deflators | `year.deflator` |
| 58 | Product-to-product relatedness strength | `productProduct.strength` |
| 59 | Percentile distributions for complexity variables | `countryYearThresholds` |
| 60 | Export RCA population-relative (RPOP) | `countryProductYear.exportRpop` |
| 61 | Normalized fields (RCA < 1 variants) | `countryProductYear.normalizedPciRcalt1` etc. |
| 62 | Bilateral reported values | `countryCountryYear.exportValueReported / importValueReported` |

**Total: 62 extractable data points** (38 browser-visible + 24 API-only)

---

## Cross-Page Consistency Notes

- **Structure is identical** across all countries tested (Kenya, USA, Australia, Saudi Arabia). All 7 pages exist for every country.
- **Feasibility is available for ALL countries** — unlike Country Pages where growth-opportunities and product-table are hidden for highest-complexity frontier economies (USA, Germany, Japan, etc.).
- **Product Space tutorial** appears on first visit per browser session.
- **Canvas vs DOM**: Treemap, product space, geomap, and feasibility graph are **canvas-rendered** (no DOM access to data). Only the feasibility **table** view renders as DOM-accessible HTML. Overtime and marketshare use SVG/canvas-hybrid charting.
- **No narrative text** on any Explore page — everything is data-driven visualization. This contrasts sharply with Country Pages, which have ~15% narrative-text data points.
- **Sector naming differs** between Product Space (8 clusters: Agricultural Goods, Construction Goods, etc.) and all other pages (11 sectors: Services, Textiles, Agriculture, Stone, Minerals, Metals, Chemicals, Vehicles, Machinery, Electronics, Other). Product Space uses `clusterId` from the product catalog; other pages use `topParent`.
