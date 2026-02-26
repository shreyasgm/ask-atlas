# Atlas Country Pages Exploration

This document comprehensively maps the Atlas Country Pages — their structure, visible data, interactive elements, and relationship to the GraphQL API. It serves as the authoritative reference for understanding how country profile pages work and how to generate verification links from API query results.

---

## Table of Contents

1. [URL Structure & Navigation](#url-structure--navigation)
2. [Page Architecture](#page-architecture)
3. [Page-by-Page Exploration](#page-by-page-exploration)
   - [3.1 Country Introduction / Hero](#31-country-introduction--hero)
   - [3.2 Export Basket](#32-export-basket)
   - [3.3 Export Complexity](#33-export-complexity)
   - [3.4 Export Growth Dynamics](#34-export-growth-dynamics)
   - [3.5 Growth in Global Market Share](#35-growth-in-global-market-share)
   - [3.6 Diversification into New Products](#36-diversification-into-new-products)
   - [3.7 What is the Product Space?](#37-what-is-the-product-space)
   - [3.8 Country's Product Space](#38-countrys-product-space)
   - [3.9 Recommended Strategic Approach](#39-recommended-strategic-approach)
   - [3.10 Potential Growth Opportunities](#310-potential-growth-opportunities)
   - [3.11 New Product Opportunities (Table)](#311-new-product-opportunities-table)
   - [3.12 Country Summary](#312-country-summary)
4. [GraphQL API Overview (Country Pages Endpoint)](#graphql-api-overview-country-pages-endpoint)
5. [GraphQL API → Website Component Mapping](#graphql-api--website-component-mapping)
6. [URL Construction Guide (API → Verification Links)](#url-construction-guide-api--verification-links)
7. [Country Pages API vs Explore API](#country-pages-api-vs-explore-api)
8. [Interactive Elements Summary](#interactive-elements-summary)
9. [Cross-Country Consistency](#cross-country-consistency)
10. [Extractable Data Points Catalog](#extractable-data-points-catalog)

---

## URL Structure & Navigation

### Base URL

- **Base**: `https://atlas.hks.harvard.edu/countries/{id}`
- **Country ID scheme**: [M49 codes as designated by the UN](https://unstats.un.org/unsd/methodology/m49/) (which coincide with ISO 3166-1 numeric codes for most countries) — e.g., 840=USA, 404=Kenya, 724=Spain, 392=Japan, 792=Turkiye
- **Total countries**: 145 studied
- **Country selector**: Dropdown on every page shows country name + ISO alpha-3 code (e.g., "Afghanistan (AFG)"). The dropdown is searchable.
- **Navigating to `/countries`** (no ID): Redirects to an arbitrary country page (e.g., `/countries/792`)

### Subpage URL Patterns

| # | Subpage Slug | Full URL Pattern | Section Group |
|---|---|---|---|
| 1 | (none) | `/countries/{id}` | Introduction |
| 2 | `export-basket` | `/countries/{id}/export-basket` | Economic Structure |
| 3 | `export-complexity` | `/countries/{id}/export-complexity` | Economic Structure |
| 4 | `growth-dynamics` | `/countries/{id}/growth-dynamics` | Market Dynamics |
| 5 | `market-share` | `/countries/{id}/market-share` | Market Dynamics |
| 6 | `new-products` | `/countries/{id}/new-products` | Market Dynamics |
| 7 | `product-space` | `/countries/{id}/product-space` | Strategy Space (explanatory) |
| 8 | `paths` | `/countries/{id}/paths` | Strategy Space (country-specific) |
| 9 | `strategic-approach` | `/countries/{id}/strategic-approach` | Strategy Space |
| 10 | `growth-opportunities` | `/countries/{id}/growth-opportunities` | Growth Opportunities |
| 11 | `product-table` | `/countries/{id}/product-table` | Growth Opportunities |
| 12 | `summary` | `/countries/{id}/summary` | Summary |

---

## Page Architecture

The country profile is a **single scrollable page** — all 12 "subpages" are sections rendered on one page load. Navigating to a specific subpage URL (e.g., `/countries/404/growth-dynamics`) scrolls to that section. This means:

- All GraphQL data for the entire country profile is fetched on initial page load (~15–20 GraphQL requests to `/api/countries/graphql`)
- Subpage URLs function as **anchor links**, not separate page loads
- The sidebar navigation on the left shows all sections with the current section highlighted
- Sections are grouped under 4 main headings: Economic Structure, Market Dynamics, Strategy Space, Growth Opportunities

**Data sources** (stated at bottom of introduction): "UN COMTRADE (HS 1992) and the IMF's WEO data"

---

## Page-by-Page Exploration

### 3.1 Country Introduction / Hero

**URL**: `/countries/{id}`

**Description**: Overview section with stat cards, sparkline charts, a globe visualization, and a narrative text summary.

- **Visible data**:
  - **Country name** and income classification (e.g., "a lower-middle-income country")
  - **GDP Per Capita** (current year, e.g., 2024): Dollar amount, PPP amount, rank out of 145, sparkline chart (2012–present), min/max values on sparkline
  - **Population**: Mentioned in text (e.g., "52.4 million inhabitants")
  - **GDP per capita growth**: 5-year average (e.g., "averaged 2.6% over the past five years"), compared to regional averages ("above" or "below")
  - **ECI Ranking**: Rank out of 145, sparkline chart (2012–present), direction of change (e.g., "improving 3 positions")
  - **Complexity trend description**: Whether complexity improved or worsened, and the driver (e.g., "driven by diversifying its exports")
  - **Growth Projection to 2034**: Percentage, rank out of 145
  - **Complexity-income relationship**: e.g., "slightly more complex than expected" for its income level
  - **Projected growth speed**: e.g., "moderately"
  - **Globe visualization**: Highlights country location
- **Interactions**: Country selector dropdown (searchable, 145 countries)
- **Navigation**: "Jump To Specific Section" with 4 icons linking to Economic Structure, Market Dynamics, Strategy Space, Growth Opportunities

**GraphQL queries**: `countryProfile` (all narrative metrics), `countryYearRange` (sparkline time series for GDP per capita, ECI rank), `countryYear` (current year snapshot), `globalDatum` (rank totals like "out of 145")

---

### 3.2 Export Basket

**URL**: `/countries/{id}/export-basket`

**Top Bar Stats:**
- **Total Exports**: USD dollar amount (e.g., "USD $16.2B")
- **Exporter Rank**: Rank out of 145 (e.g., "90th of 145")
- **Current Account**: USD dollar amount (e.g., "USD -$13.1B")

**Section: Export Basket in {year}**
- **Type**: Treemap visualization
- **Visible data**:
  - Products shown as colored rectangles sized by export share
  - Each product labeled with name and percentage share (e.g., "Travel & tourism 21.57%")
  - Sector color coding: Services (pink/red), Textiles (green), Agriculture (yellow), Stone (tan), Minerals (brown), Metals (dark red), Chemicals (magenta), Vehicles (purple), Machinery (blue), Electronics (cyan), Other (dark)
- **Tooltip on hover**: Product name, HS92 code (e.g., "0902 HS92"), Gross Export value (e.g., "$795M"), Share percentage
- **Dropdowns**:
  - **Trade Flow**: Gross, Net
- **Text data**:
  - Total export value and year
  - Export growth rate (5-year annual average)
  - Non-oil export growth rate
  - Total imports
  - Trade balance (deficit or surplus)
  - **Top 3 export destination / import origin countries**: Country name + percentage share. Toggle dropdown switches between "export destination" and "import origin"

**GraphQL queries**: `treeMap(facet: CPY_C)` (product treemap), `treeMap(facet: CCY_C)` (trade partner breakdown for top-3), `countryProfile` (top bar stats, text narrative), `countryLookback` (export growth rates)

---

### 3.3 Export Complexity

**URL**: `/countries/{id}/export-complexity`

**Top Bar Stats:**
- **ECI Ranking**: Rank out of 145 (e.g., "91st of 145")
- **Rank Change**: Direction + number of positions over 10 years (e.g., "↑3 positions over 10 years")

**Section: Export Complexity in {year}**
- **Type**: Treemap visualization (same layout as export basket but colored by complexity)
- **Visible data**:
  - Products shown with their **Product Complexity Index (PCI)** values overlaid (e.g., "Travel & tourism -0.48", "Tea -1.02")
  - Color scale: Low Complexity (teal/blue) → High Complexity (coral/brown)
- **Tooltip on hover**: Product name, HS92 code, Gross Export value, PCI value
- **Dropdowns**:
  - **Trade Flow**: Gross, Net
  - **Colored by**: Complexity, Entry Year
- **Text**: Description of complexity concept, identifies which sectors contain the largest exports by complexity level

**GraphQL queries**: `treeMap(facet: CPY_C)` (same product treemap with PCI overlay), `allProductYear` (for PCI values), `countryProfile` (ECI rank), `countryLookback(yearRange: TenYears)` (ECI rank change)

---

### 3.4 Export Growth Dynamics

**URL**: `/countries/{id}/growth-dynamics`

**Section: Export Growth Dynamics**
- **Type**: Bubble/scatter chart
- **Axes**:
  - X-axis: Product Complexity (Less Complex ← → More Complex)
  - Y-axis: Annual Export Growth (CAGR) over selected period (e.g., 2019–2024)
- **Bubble properties**: Sized by trade volume, colored by sector
- **Reference line**: Dashed vertical line at country's ECI value (e.g., "ECI (2024): −0.13")
- **Visible data**:
  - Each bubble represents a product group (2-digit HS level)
  - Named labels on largest bubbles (e.g., "Travel & tourism", "Mineral fuels, oils and waxes")
  - Total export value shown in top bar (e.g., "USD $3.50B")
- **Tooltip on hover**: Product name, HS92 code (e.g., "27 HS92"), Gross Country Export value, Export Growth percentage
- **Dropdowns**:
  - **Year Range**: 3 Years, 5 Years, 10 Years (maps to `LookBackYearRange` enum)
  - **Sizing Products by**: Country Trade, World Trade, None
- **Legend**: Same sector color coding as other pages
- **Text**: Description of export growth pattern (e.g., "troubling", "promising"), which complexity level and sectors drive growth

**GraphQL queries**: `countryProductLookback` (product-level export growth CAGR), `allProductYear` (PCI for x-axis positioning), `countryYear` (ECI value for reference line), `countryProfile` (text narrative classification)

---

### 3.5 Growth in Global Market Share

**URL**: `/countries/{id}/market-share`

**Top Bar Stats:**
- **Largest Market Share**: Sector name (e.g., "Services")
- **Share of Global Trade**: Percentage (e.g., "0.06%")

**Section: Growth in Global Market Share**
- **Type**: Multi-line time series chart
- **Axes**:
  - X-axis: Years (1996–2024)
  - Y-axis: Share of World Market by Sector (0%–20%)
- **Lines**: One per sector, color-coded
- **Toggleable sector filters**: Each sector has an "X" button to remove it from the chart (Textiles, Agriculture, Stone, Minerals, Metals, Chemicals, Vehicles, Machinery, Electronics, Services)
- **Tooltip on hover** (crosshair): Shows year and all sector market share percentages at that point
- **Text**: Description of structural transformation status, which sectors drive export growth, whether growth is from market share gains or global sector growth

**GraphQL queries**: `treeMap(facet: CPY_C)` per year (or `allCountryProductYear` time series for sector-level shares), `allProductYear` (global totals per sector for market share calculation), `countryProfile` (structural transformation status, market share main sector, text narrative)

---

### 3.6 Diversification into New Products

**URL**: `/countries/{id}/new-products`

**Top Bar Stats:**
- **Economic Diversification Grade**: Letter grade (e.g., "B")
- **Diversity Rank**: Rank out of 145 (e.g., "38th of 145")
- **Rank Change**: Direction + number of positions over 15 years (e.g., "↓7 over 15 years")

**Section: Diversification into New Products**
- **Type**: Treemap + comparison table
- **Treemap**: "New Products Exported, {start_year} - {end_year}" showing new products the country has added, sized by their share of new exports
- **Dropdowns**:
  - **Colored by**: Sector (likely also other options)
- **Comparison mini-visual**: Shows current Export Basket alongside "New Export Proportion (Added in 15 years)" as percentage
- **Table: "New Export Products, {start_year} - {end_year}"**:
  - Columns: Country, New Products (count), USD Per Capita, USD (Total Value)
  - Compares the selected country with 3 peer countries
  - Example for Kenya: Uganda 28/$5/$211M, Kenya 24/$5/$260M, Ethiopia 21/$2/$181M, Tanzania 17/$7/$468M
- **Text**: Number of new products added, per-capita income contribution, assessment of diversification impact

**GraphQL queries**: `newProductsCountry` (new product list, count, values), `newProductsComparisonCountries` (peer country comparison table), `countryProfile` (diversification grade, diversity rank), `countryLookback(yearRange: FifteenYears)` (diversity rank change)

---

### 3.7 What is the Product Space?

**URL**: `/countries/{id}/product-space`

**Section: What is the Product Space?**
- **Type**: Network graph visualization (explanatory/generic, not country-specific)
- **Visible data**: Product Space network with all products as colored nodes grouped by sector (Chemicals, Machinery, Minerals, Stone, Agriculture, Electronics, Vehicles, Metals, Textiles)
- **Text**: Explanation of the Product Space concept and how countries diversify into related products
- **No country-specific data** — this is a reference/educational page

**GraphQL queries**: `productSpace` (generic product-to-product relatedness), `allProducts` (product catalog for node labels and positions)

---

### 3.8 Country's Product Space

**URL**: `/countries/{id}/paths`

**Top Bar Stats:**
- **Export Products**: Count with RCA>1 (e.g., "226 (RCA>1)")
- **Complexity Outlook Index**: Rank out of 145 (e.g., "8th of 145")

**Section: {Country}'s Product Space**
- **Type**: Network graph visualization (country-specific)
- **Visible data**:
  - **Colored nodes**: Products the country exports (with RCA > 1)
  - **Gray nodes**: Products the country does not export
  - Node size reflects world trade volume
  - "How to read" legend: Colored Node = product the country exports, Gray Node = product the country does not export
- **Dropdowns**:
  - **Sizing of Dots**: World Trade (possibly other options)
- **Interactions**: +ZOOM, -ZOOM, RESET ZOOM controls
- **Tooltip on hover** (nodes): Product name, export status, RCA value

**GraphQL queries**: `productSpace(location: "location-{id}")` (country-specific product space with RCA, x, y coordinates, connections), `countryProfile` (diversity count, COI rank for top bar)

---

### 3.9 Recommended Strategic Approach

**URL**: `/countries/{id}/strategic-approach`

**Section: Recommended Strategic Approach**
- **Type**: Scatter plot of all countries
- **Axes**:
  - X-axis: "Is the {country} complex enough for its income to grow?" (Low relative complexity ← → High relative complexity)
  - Y-axis: "Is the {country} well-connected to many new opportunities (COI)?" (Not well connected ← → Well connected)
- **Four quadrants** (labeled with strategic approach names):
  - Top-left: **Parsimonious Industrial Policy Approach**
  - Top-right: **Light Touch Approach**
  - Bottom-left: **Strategic Bets Approach**
  - Bottom-right: **Technological Frontier Approach**
- **Country highlight**: Selected country shown with label, positioned in its quadrant
- **Visible data**:
  - Which quadrant/approach is recommended
  - Country position relative to all others
- **Text**: Description of the recommended approach and what it means

**GraphQL queries**: `allCountryProfiles` (all countries' ECI, COI, and policy recommendation for plotting), `countryProfile` (this country's `policyRecommendation`), `allCountryYear` (ECI and COI for all countries to plot the scatter)

---

### 3.10 Potential Growth Opportunities

**URL**: `/countries/{id}/growth-opportunities`

**Section: Potential Growth Opportunities**
- **Type**: Scatter plot of products (NOT available for highest-complexity countries)
- **For complex countries (e.g., USA)**: Shows "Visualization not available for highest complexity countries" with a "Continue to Country Summary" button
- **For other countries (e.g., Kenya)**:
  - **Axes**: X-axis = Distance (to existing capabilities), Y-axis = Complexity / Opportunity Gain
  - **Reference line**: Average Complexity value
  - **Bubbles**: Colored by sector, sized by global trade
  - **Controls**:
    - **(1) Your Strategic Approach**: Shows the recommended approach (e.g., "Light Touch Approach")
    - **(2) Product Selection Criteria** (radio buttons): Low-hanging Fruit, Balanced Portfolio, Long Jumps
    - **Weight visualization** (pie chart): Shows relative weights of Opportunity Gain, Distance, Complexity (e.g., 20%/60%/20%)
  - **Text**: Explanation of Distance, Complexity, and Opportunity Gain concepts

**GraphQL queries**: `allCountryProductYear` (distance, opportunity gain, PCI per product), `allProductYear` (global export value for bubble sizing), `countryProfile` (policy recommendation for default strategy)

---

### 3.11 New Product Opportunities (Table)

**URL**: `/countries/{id}/product-table`

**Section: New Product Opportunities**
- **Type**: Data table (NOT available for highest-complexity countries)
- **For complex countries (e.g., USA)**: Shows "Visualization not available for highest complexity countries"
- **For other countries (e.g., Kenya)**:
  - **Title**: "Top 50 Products Based on Strategy Approach"
  - **Table columns**:
    - Product Name (with HS92 code)
    - "Nearby" Distance (diamond rating scale, ~5 diamonds)
    - Opportunity Gain (diamond rating scale)
    - Product Complexity (diamond rating scale)
    - Global Size (USD)
    - Global Growth 5 YR (percentage with ↑/↓ indicator)
  - **Strategy label**: Shows which approach is applied (e.g., "Light Touch Approach / Balanced Portfolio")
  - **Interactive**: "Click on product names to explore in the Atlas"
  - **Text**: Lists high-potential sectors for diversification

**Diamond ratings** map to the API's `DecileClassification` enum: Last, Second, Third, Fourth, Fifth, Sixth, Seventh, Eighth, Ninth, Top — these decile values are rendered as filled/unfilled diamonds.

**GraphQL queries**: `allCountryProductYear` (normalizedDistanceDecileClassification, normalizedOpportunityGainDecileClassification, normalizedPciDecileClassification per product), `allProductYear` (globalExportValue, globalExportValueChangeFiveYears for Global Size and Global Growth columns), `countryProfile` (policy recommendation)

---

### 3.12 Country Summary

**URL**: `/countries/{id}/summary`

**Section: {Country} in Summary**
- **Type**: Summary stat cards
- **Visible data**:
  - **Economic Structure**: Complexity rank change (e.g., "↑3"), Number of new products added (e.g., "24 New Products were added in the last 15 years")
  - **Future Dynamics**: Growth projection (e.g., "3.4% — Kenya is expected to grow 3.4% per year over the next 10 years")
  - **Path to Diversification**: Recommended strategic approach name + description (e.g., "Light Touch Approach — Ample space to diversify calls for leveraging existing successes to enter more complex production.")
- **Bottom CTAs**:
  - Search a New Country
  - Analyze & Explore This Country Further
  - Explore This Country's Cities with Metroverse

**GraphQL queries**: `countryProfile` (all summary fields — growthProjection, policyRecommendation, diversificationGrade, etc.), `countryLookback` (eciRankChange), `newProductsCountry` (newProductCount)

---

## GraphQL API Overview (Country Pages Endpoint)

### Endpoint

```
POST https://atlas.hks.harvard.edu/api/countries/graphql
Content-Type: application/json
```

No authentication required. Introspection enabled. This API is **not officially documented** by the Growth Lab — only the Explore API has [official documentation](https://github.com/harvard-growth-lab/api-docs/blob/main/atlas.md). Rate limit: ≤ 120 req/min (shared with Explore API).

> **Note:** The Atlas API is best used to access data for stand-alone economic analysis, not to support other software applications. See the [official API documentation](https://github.com/harvard-growth-lab/api-docs/blob/main/atlas.md) for full terms.

### ID Format

Unlike the Explore API which uses bare integers (`countryId: 404`), the Country Pages API uses **prefixed string IDs**:
- Countries: `"location-404"` (for Kenya, M49 code 404)
- Products: `"product-HS-726"` (internal product ID, not HS code)

### All Query Types (25 total)

Verified via live introspection (February 2026).

#### Core Country Data (8 queries)

| Query | Required Args | Optional Args | Returns | Purpose |
|-------|---------------|---------------|---------|---------|
| `countryProfile` | `location: ID!` | `comparisonLocationsQuantity` | `CountryProfile` | **Richest query**: 46 derived analytical fields (GDP, ECI, COI, growth projections, diversification, strategic approach, narrative enums) |
| `allCountryProfiles` | — | — | `[AllCountryProfile]` | All countries' key metrics (diversificationGrade, policyRecommendation, ECI) |
| `countryYear` | `location: ID!, year: Int!` | `eciProductClass, coiProductClass` | `CountryYear` | Single-year country snapshot (GDP, exports, ECI, COI) |
| `allCountryYear` | `year: Int!` | `eciProductClass, coiProductClass` | `[CountryYear]` | All countries for a single year |
| `countryYearRange` | `location: ID!, minYear: Int!, maxYear: Int!` | `eciProductClass, coiProductClass` | `CountryYearRange` | Country time series (sparklines) |
| `allCountryYearRange` | `minYear: Int!, maxYear: Int!` | `eciProductClass, coiProductClass` | `[CountryYearRange]` | All countries time series |
| `countryLookback` | `id: ID!` | `yearRange, productClass, eciRankChangeYearRange, exportValueGrowthYearRange, exportValueGrowthCagrYearRange, exportValueConstGrowthCagrYearRange, exportValueGrowthNonOilCagrYearRange, exportValueGrowthNonOilConstCagrYearRange, gdpChangeCagrYearRange, largestContributingExportProductYearRange, eciChangeYearRange, ...` | `CountryLookback` | Historical change metrics with configurable year ranges |
| `globalDatum` | — | `yearRange, productClass, gdpChangeConstCagrYearRange, exportValueConstChangeCagrYearRange, exportValueNonOilConstChangeCagrYearRange` | `GlobalDatum` | Global aggregates and rank totals |

#### Trade Visualization Data (3 queries)

| Query | Required Args | Optional Args | Returns | Purpose |
|-------|---------------|---------------|---------|---------|
| `treeMap` | `facet: TreeMapType!` | `productClass, year, productLevel, locationLevel, location, product, partner, mergePci` | `[TreeMapDatum]` | Treemap-ready data (products or partners) — union of `TreeMapProduct` and `TreeMapLocation` |
| `allCountryProductYear` | `location: ID!, year: Int!, productClass: ProductClass!` | `productLevel` | `[CountryProductYear]` | Country × product data with decile classifications (for growth opportunities scatter and table) |
| `manyCountryProductYear` | `year: Int!, productClass: ProductClass!` | `group, productLevel, aggregate` | `[CountryProductYear]` | Multi-country product data |

#### Product Data (6 queries)

| Query | Required Args | Optional Args | Returns | Purpose |
|-------|---------------|---------------|---------|---------|
| `product` | `id: ID!` | — | `Product` | Single product details |
| `allProducts` | `productClass: ProductClass!` | `productLevel` | `[Product]` | Product catalog (names, codes, hierarchy) |
| `productYear` | `product: ID!, year: Int!` | — | `ProductYear` | Single product-year (PCI, global export value) |
| `allProductYear` | `productClass: ProductClass!, productLevel: ProductLevel!, year: Int!` | — | `[ProductYear]` | All products for a year |
| `productYearRange` | `product: ID!, minYear: Int!, maxYear: Int!` | — | `ProductYearRange` | Product time series |
| `allProductYearRange` | `productClass: ProductClass!, productLevel: ProductLevel!, minYear: Int!, maxYear: Int!` | — | `[ProductYearRange]` | All products time series |

#### Specialized Country Data (3 queries)

| Query | Required Args | Optional Args | Returns | Purpose |
|-------|---------------|---------------|---------|---------|
| `productSpace` | `productClass: ProductClass!, year: Int!, location: ID!` | — | `[ProductSpaceDatum]` | Country-specific product space with RCA, x/y coordinates, connections |
| `newProductsCountry` | `location: ID!, year: Int!` | — | `NewProductsCountry` | New products list with counts and values |
| `newProductsComparisonCountries` | `location: ID!, year: Int!` | `quantity` | `[NewProductsComparisonCountries]` | Peer country comparison for new products |

#### Lookback & Growth Analysis (2 queries)

| Query | Required Args | Optional Args | Returns | Purpose |
|-------|---------------|---------------|---------|---------|
| `countryProductLookback` | `location: ID!` | `yearRange, productLevel` | `[CountryProductLookback]` | Per-product export growth CAGR over selected period |
| `countryLookback` | `id: ID!` | Multiple `yearRange` params | `CountryLookback` | Country-level historical change metrics |

#### Reference Data (3 queries)

| Query | Required Args | Optional Args | Returns | Purpose |
|-------|---------------|---------------|---------|---------|
| `location` | `id: ID!` | — | `Location` | Single location details |
| `allLocations` | — | `level` | `[Location]` | All locations (countries/regions) |
| `group` | `id: ID!` | — | `Group` | Single group details |
| `allGroups` | — | `groupType` | `[Group]` | All groups (regions, continents, etc.) |

### Key Type Schemas

#### `CountryProfile` (46 fields) — richest single query

```
location: Location                                    # country name, code, etc.
latestPopulation: IntForYear                          # { quantity, year }
latestGdp: FloatForYear
latestGdpRank: IntForYear
latestGdpPpp: FloatForYear
latestGdpPppRank: IntForYear
latestGdpPerCapita: IntForYear
latestGdpPerCapitaRank: IntForYear
latestGdpPerCapitaPpp: IntForNotRequiredYear
latestGdpPerCapitaPppRank: IntForYear
incomeClassification: IncomeClassification            # High, UpperMiddle, LowerMiddle, Low
exportValue: Float
importValue: Float
exportValueRank: Int
exportValueNatResources: Int
importValueNatResources: Int
netExportValueNatResources: Int
exportValueNonOil: Int
newProductExportValue: Float
newProductExportValuePerCapita: Int
newProductsIncomeGrowthComments: NewProductsIncomeGrowthComments   # LargeEnough, TooSmall
newProductsComments: NewProductsComments              # TooFew, Sufficient
newProductsComplexityStatusGrowthPrediction: NewProductsComplexityStatusGrowthPrediction  # More, Same, Less
currentAccount: FloatForNotRequiredYear
latestEci: Float
latestEciRank: Int
eciNatResourcesGdpControlled: Float
latestCoi: Float
latestCoiRank: Int
coiClassification: COIClassification                  # low, medium, high
growthProjection: Float
growthProjectionRank: Int
growthProjectionClassification: GrowthProjectionClassification  # rapid, moderate, slow
growthProjectionRelativeToIncome: GrowthProjectionRelativeToIncome  # More, Less, Same, ModeratelyMore, ModeratelyLess
growthProjectionPercentileClassification: GrowthProjectionPercentileClassification  # TopDecile, TopQuartile, TopHalf, BottomHalf
comparisonLocations: [Location]
diversity: Int                                        # count of products with RCA > 1
diversityRank: Int
diversificationGrade: DiversificationGrade            # APlus, A, B, C, D, DMinus
marketShareMainSector: Product                        # { shortName }
marketShareMainSectorDirection: MarketShareMainSectorDirection  # rising, falling, stagnant
marketShareMainSectorPositiveGrowth: Boolean
structuralTransformationStep: StructuralTransformationStep  # NotStarted, TextilesOnly, ElectronicsOnly, MachineryOnly, Completed
structuralTransformationSector: Product
structuralTransformationDirection: StructuralTransformationDirection  # risen, fallen, stagnated
policyRecommendation: PolicyRecommendation            # ParsimoniousIndustrial, StrategicBets, LightTouch, TechFrontier
```

#### `TreeMapDatum` (union type)

A union of `TreeMapProduct` and `TreeMapLocation`, selected by the `facet` argument:

**`TreeMapProduct`** (returned for `CPY_C` and `CPY_P` facets) — 12 fields:
```
product: Product
exportValue: Float
importValue: Float
rca: Float
distance: Float
opportunityGain: Float
pci: Float
normalizedPci: Float
normalizedDistance: Float
normalizedOpportunityGain: Float
globalMarketShare: Float
year: Int
```

**`TreeMapLocation`** (returned for `CCY_C` facet) — 4 fields:
```
location: Location
exportValue: Float
importValue: Float
year: Int
```

#### `CountryYear` / `CountryYearRange` (14 fields each)

```
location: Location
population, exportValue, importValue, exportValueRank
gdp, gdpRank, gdpPpp, gdpPerCapita, gdpPerCapitaPpp
eci, eciRank, coi, coiRank
```

Note: In `CountryYearRange`, numeric fields return arrays of `IntForYear` or `FloatForYear` (each with `quantity` and `year`), providing time-series data for sparklines.

#### `CountryLookback` (13 fields)

```
id
eciRankChange: Int
exportValueConstGrowthCagr: Float
exportValueGrowthNonOilConstCagr: Float
largestContributingExportProduct: [Product]
eciChange: Float
diversityRankChange: Int
diversityChange: Int
gdpPcConstantCagrRegionalDifference: GDPPCConstantCAGRRegionalDifference  # Above, InLine, Below
exportValueGrowthClassification: ExportValueGrowthClassification  # Troubling, Mixed, Static, Promising
gdpChangeConstantCagr: Float
gdpPerCapitaChangeConstantCagr: Float
gdpGrowthConstant: Float
```

#### `CountryProductYear` (7 fields)

```
id
product: Product
exportValue: Float
importValue: Float
normalizedOpportunityGainDecileClassification: DecileClassification
normalizedDistanceDecileClassification: DecileClassification
normalizedPciDecileClassification: DecileClassification
```

#### `ProductSpaceDatum` (7 fields)

```
product: Product
exportValue: Float
importValue: Float
rca: Float
x: Float                    # X coordinate for product space visualization
y: Float                    # Y coordinate for product space visualization
connections: [Product]       # Connected products in the network
```

#### `NewProductsCountry` (5 fields)

```
location: Location
newProducts: [Product]
newProductExportValue: Float
newProductExportValuePerCapita: Int
newProductCount: Int
```

#### `NewProductsComparisonCountries` (4 fields)

```
location: Location
newProductExportValue: Float
newProductExportValuePerCapita: Int
newProductCount: Int
```

#### `CountryProductLookback` (3 fields)

```
product: Product
exportValueConstGrowth: Float
exportValueConstCagr: Float
```

#### `Product` (10 fields)

```
id: ID!
code: String
level: ProductLevel
parent: Product
topLevelParent: Product
longName: String
shortName: String
productType: ProductType     # Goods, Service
neverShow: Boolean
hideFeasibility: Boolean
```

#### `Location` (15 fields)

```
id: ID!
code: String                 # ISO alpha-3 code (e.g., "KEN")
level: LocationLevel
parent: Location
topLevelParent: Location
longName: String
shortName: String
nameAbbr: String
thePrefix: Boolean
isInCountryPages: Boolean
isFormerCountry: Boolean
isInComplexityRankings: Boolean
isDataTrustworthy: Boolean
hasReportedServicesLastYear: Boolean
hasReportedServicesInAnyYear: Boolean
```

#### `GlobalDatum` (10 fields)

```
gdpChangeConstCagr: Float
exportValueConstChangeCagr: Float
exportValueNonOilConstChangeCagr: Float
globalExportValue: Float
latestEciRankTotal: Int      # e.g., 145 — used for "X of 145" display
latestCoiRankTotal: Int
latestExporterRankTotal: Int
latestGdpRankTotal: Int
latestGdpPppPerCapitaRankTotal: Int
latestDiversityRankTotal: Int
```

#### `AllCountryProfile` (4 fields)

```
location: Location
diversificationGrade: DiversificationGrade
eciNatResourcesGdpControlled: Float
policyRecommendation: PolicyRecommendation
```

#### `ProductYear` (5 fields)

```
product: Product
pci: Float
globalExportValue: Float
globalExportValueChangeFiveYears: Float
complexityLevel: ComplexityLevel    # low, moderate, high
```

#### `Group` (5 fields)

```
id: ID!
groupName: String
groupType: GroupType
members: [ID]
parent: Group
```

#### Helper types

```
IntForYear:              { quantity: Int, year: Int }
FloatForYear:            { quantity: Float, year: Int }
IntForNotRequiredYear:   { quantity: Int, year: Int }
FloatForNotRequiredYear: { quantity: Float, year: Int }
```

### Enum Values

| Enum | Values |
|------|--------|
| `TreeMapType` | `CPY_C`, `CPY_P`, `CCY_C` |
| `ProductClass` | `HS`, `SITC` |
| `ProductLevel` | `section`, `twoDigit`, `fourDigit` |
| `LocationLevel` | `country`, `region` |
| `ProductType` | `Goods`, `Service` |
| `LookBackYearRange` | `ThreeYears`, `FiveYears`, `TenYears`, `FifteenYears` |
| `PolicyRecommendation` | `ParsimoniousIndustrial`, `StrategicBets`, `LightTouch`, `TechFrontier` |
| `DiversificationGrade` | `APlus`, `A`, `B`, `C`, `D`, `DMinus` |
| `IncomeClassification` | `High`, `UpperMiddle`, `LowerMiddle`, `Low` |
| `COIClassification` | `low`, `medium`, `high` |
| `GrowthProjectionClassification` | `rapid`, `moderate`, `slow` |
| `GrowthProjectionRelativeToIncome` | `More`, `Less`, `Same`, `ModeratelyMore`, `ModeratelyLess` |
| `GrowthProjectionPercentileClassification` | `TopDecile`, `TopQuartile`, `TopHalf`, `BottomHalf` |
| `MarketShareMainSectorDirection` | `rising`, `falling`, `stagnant` |
| `StructuralTransformationStep` | `NotStarted`, `TextilesOnly`, `ElectronicsOnly`, `MachineryOnly`, `Completed` |
| `StructuralTransformationDirection` | `risen`, `fallen`, `stagnated` |
| `ExportValueGrowthClassification` | `Troubling`, `Mixed`, `Static`, `Promising` |
| `DecileClassification` | `Last`, `Second`, `Third`, `Fourth`, `Fifth`, `Sixth`, `Seventh`, `Eighth`, `Ninth`, `Top` |
| `GDPPCConstantCAGRRegionalDifference` | `Above`, `InLine`, `Below` |
| `NewProductsIncomeGrowthComments` | `LargeEnough`, `TooSmall` |
| `NewProductsComments` | `TooFew`, `Sufficient` |
| `NewProductsComplexityStatusGrowthPrediction` | `More`, `Same`, `Less` |
| `ComplexityLevel` | `low`, `moderate`, `high` |
| `GroupType` | `region`, `subregion`, `rock_song`, `trade`, `wdi_income_level`, `wdi_region`, `political`, `continent`, `world` |

---

## GraphQL API → Website Component Mapping

This section maps each country page section to the specific GraphQL queries that power it.

### Query Mapping Table

| Page Section | Primary Query | Supporting Queries | Key Fields Used |
|--------------|--------------|-------------------|-----------------|
| **Introduction / Hero** | `countryProfile` | `countryYearRange` (sparklines), `globalDatum` (rank totals) | `latestGdpPerCapita`, `latestEciRank`, `incomeClassification`, `growthProjection`, `growthProjectionRank`, `growthProjectionRelativeToIncome` |
| **Export Basket** (treemap) | `treeMap(facet: CPY_C)` | `countryProfile` (top bar stats, text), `countryLookback` (growth rates) | `exportValue`, `rca`, `product.shortName`, `product.code` |
| **Export Basket** (top-3 partners) | `treeMap(facet: CCY_C)` | — | `location.shortName`, `exportValue` (sorted, top 3) |
| **Export Basket** (text) | `countryProfile` + `countryLookback` | — | `exportValue`, `importValue`, `currentAccount`, `exportValueConstGrowthCagr`, `exportValueGrowthNonOilConstCagr` |
| **Export Complexity** (treemap) | `treeMap(facet: CPY_C)` | `allProductYear` (PCI values) | `exportValue`, `pci`, `normalizedPci` |
| **Export Complexity** (top bar) | `countryProfile` + `countryLookback(yearRange: TenYears)` | — | `latestEciRank`, `eciRankChange` |
| **Growth Dynamics** (scatter) | `countryProductLookback` | `allProductYear` (PCI), `countryYear` (ECI reference line) | `exportValueConstCagr`, `pci` (x-axis), `eci` (reference line) |
| **Growth Dynamics** (text) | `countryProfile` | — | `exportValueGrowthClassification`, `largestContributingExportProduct` |
| **Market Share** (line chart) | `treeMap(facet: CPY_C)` (multi-year) | `allProductYear` (global totals) | `exportValue` per sector per year / `globalExportValue` |
| **Market Share** (top bar) | `countryProfile` | — | `marketShareMainSector`, `marketShareMainSectorDirection` |
| **Market Share** (text) | `countryProfile` | — | `structuralTransformationStep`, `structuralTransformationSector`, `structuralTransformationDirection` |
| **New Products** (treemap) | `newProductsCountry` | — | `newProducts` (list), `newProductExportValue`, `newProductCount` |
| **New Products** (table) | `newProductsComparisonCountries` | — | `location`, `newProductCount`, `newProductExportValue`, `newProductExportValuePerCapita` |
| **New Products** (top bar) | `countryProfile` + `countryLookback(yearRange: FifteenYears)` | — | `diversificationGrade`, `diversityRank`, `diversityRankChange` |
| **Product Space** (generic) | `allProducts` | — | Product network positions |
| **Product Space** (country) | `productSpace(location: "location-{id}")` | `countryProfile` (diversity, COI rank) | `rca`, `x`, `y`, `connections` |
| **Strategic Approach** (scatter) | `allCountryProfiles` + `allCountryYear` | `countryProfile` | `policyRecommendation`, `eciNatResourcesGdpControlled`, `latestCoi` |
| **Growth Opportunities** (scatter) | `allCountryProductYear` | `allProductYear` (bubble sizing), `countryProfile` (strategy) | `normalizedDistanceDecileClassification`, `normalizedOpportunityGainDecileClassification`, `normalizedPciDecileClassification` |
| **Product Table** | `allCountryProductYear` | `allProductYear` (global size, growth) | Same decile fields + `globalExportValue`, `globalExportValueChangeFiveYears` |
| **Summary** | `countryProfile` | `countryLookback`, `newProductsCountry` | `growthProjection`, `policyRecommendation`, `eciRankChange`, `newProductCount` |

### TreeMap Facet Behavior

The `treeMap` query uses the `facet` argument to determine what data is returned:

| Facet | Returns | Use Case |
|-------|---------|----------|
| `CPY_C` | `[TreeMapProduct]` — products with exportValue, rca, pci | Export Basket treemap, Export Complexity treemap, Market Share calculation |
| `CPY_P` | `[TreeMapProduct]` — requires a specific `product` ID | Product-specific drilldown (not used on main country pages) |
| `CCY_C` | `[TreeMapLocation]` — trade partners with exportValue, importValue | Top-3 export destination / import origin countries |

---

## URL Construction Guide (API → Verification Links)

This mapping is critical for generating country page links from API query results, enabling trust and verification in the AskAtlas system.

### Country Page URL Construction

```
Base: https://atlas.hks.harvard.edu/countries/{m49_code}

Subpage URLs (append to base):
  /export-basket          → Export basket treemap + trade stats
  /export-complexity      → Complexity-colored treemap
  /growth-dynamics        → Growth dynamics scatter
  /market-share           → Market share line chart
  /new-products           → New products treemap + peer comparison
  /product-space          → Product Space explainer (generic)
  /paths                  → Country's product space (country-specific)
  /strategic-approach     → Strategic approach quadrant
  /growth-opportunities   → Growth opportunities scatter
  /product-table          → Product opportunities table
  /summary                → Country summary
```

### API Query → Verification Link Mapping

When the system answers a question using a Country Pages API query, it can generate a verification link that takes the user to the exact page section showing that data:

| Data Category | API Query Used | Verification Link |
|--------------|---------------|-------------------|
| GDP per capita, ECI rank, growth projection, income classification | `countryProfile`, `countryYear` | `/countries/{id}` |
| Total exports, imports, current account, trade balance, top trade partners | `countryProfile`, `treeMap(facet: CCY_C)` | `/countries/{id}/export-basket` |
| Product-level exports, export shares | `treeMap(facet: CPY_C)` | `/countries/{id}/export-basket` |
| ECI ranking, ECI rank change, product complexity values | `countryProfile`, `countryLookback`, `allProductYear` | `/countries/{id}/export-complexity` |
| Export growth by product, growth pattern classification | `countryProductLookback`, `countryProfile` | `/countries/{id}/growth-dynamics` |
| Market share by sector, structural transformation | `countryProfile`, `treeMap` (multi-year) | `/countries/{id}/market-share` |
| New products count, diversification grade, peer comparison | `newProductsCountry`, `newProductsComparisonCountries` | `/countries/{id}/new-products` |
| Products with RCA > 1, product space network | `productSpace` | `/countries/{id}/paths` |
| Diversity count, COI rank | `countryProfile` | `/countries/{id}/paths` |
| Strategic approach / policy recommendation | `countryProfile`, `allCountryProfiles` | `/countries/{id}/strategic-approach` |
| Product opportunities (distance, complexity, opportunity gain) | `allCountryProductYear` | `/countries/{id}/growth-opportunities` |
| Ranked product table with feasibility metrics | `allCountryProductYear`, `allProductYear` | `/countries/{id}/product-table` |
| Growth projection summary, approach summary | `countryProfile` | `/countries/{id}/summary` |

### Explore Page Cross-Link

Some country page data can also be explored in more detail on the Explore pages. When the user wants to drill deeper:

| Country Page Data | Explore Page Cross-Link |
|-------------------|------------------------|
| Product in export basket | `https://atlas.hks.harvard.edu/explore/treemap?year={year}&exporter=country-{id}` |
| Trade partner | `https://atlas.hks.harvard.edu/explore/treemap?year={year}&exporter=country-{id}&importer=country-{partnerId}&view=markets` |
| Product over time | `https://atlas.hks.harvard.edu/explore/overtime?startYear=1995&endYear={year}&exporter=country-{id}` |
| Product in product space | `https://atlas.hks.harvard.edu/explore/productspace?year={year}&exporter=country-{id}` |
| Growth opportunities detail | `https://atlas.hks.harvard.edu/explore/feasibility?year={year}&exporter=country-{id}` |
| Growth opportunities table | `https://atlas.hks.harvard.edu/explore/feasibility/table?year={year}&exporter=country-{id}&productLevel=4` |

### Country ID Mapping

Country IDs use M49 codes. Common examples:

| Country | M49 Code | Country Pages API ID | Explore API ID |
|---------|----------|---------------------|----------------|
| Kenya | 404 | `"location-404"` | `countryId: 404` |
| USA | 840 | `"location-840"` | `countryId: 840` |
| Spain | 724 | `"location-724"` | `countryId: 724` |
| Brazil | 76 | `"location-76"` | `countryId: 76` |
| Germany | 276 | `"location-276"` | `countryId: 276` |
| India | 356 | `"location-356"` | `countryId: 356` |
| Turkiye | 792 | `"location-792"` | `countryId: 792` |
| Ethiopia | 231 | `"location-231"` | `countryId: 231` |

The full mapping can be retrieved via `allLocations { id code shortName }`.

---

## Country Pages API vs Explore API

The Atlas has **two separate GraphQL APIs** with different schemas. For full details on the Explore API, see `evaluation/atlas_explore_pages_exploration.md`.

| Aspect | Country Pages API (`/api/countries/graphql`) | Explore API (`/api/graphql`) |
|--------|----------------------------------------------|------------------------------|
| **Query count** | 25 | 27 |
| **Official docs** | ❌ Not documented | ✅ [Officially documented](https://github.com/harvard-growth-lab/api-docs/blob/main/atlas.md) |
| **ID format** | String IDs (`location: "location-404"`) | Numeric integers (`countryId: 404`) |
| **Year params** | `year`, `minYear` / `maxYear` | `yearMin` / `yearMax` |
| **Product class** | `HS`, `SITC` (generic) | `HS92`, `HS12`, `HS22`, `SITC` (explicit revisions) |
| **Product levels** | `section`, `twoDigit`, `fourDigit` | 2, 4, **6** digit |
| **Arg descriptions** | ❌ `None` for all arguments | ✅ Human-readable for all arguments |

### Country Pages API Unique Features

| Feature | Details |
|---------|---------|
| **`countryProfile`** | 46 derived analytical fields — growth projections, diversification grade, strategic approach, income classification, narrative enums |
| **`countryLookback`** | Historical change metrics with configurable year ranges (3/5/10/15 years) |
| **`newProductsCountry` / `newProductsComparisonCountries`** | New product counts with peer comparison |
| **`productSpace`** | Pre-computed country-specific product space with x/y coordinates and connections |
| **Policy enums** | `PolicyRecommendation`, `DiversificationGrade`, `ExportValueGrowthClassification`, `StructuralTransformationStep` |
| **TreeMap facets** | `treeMap(facet: CPY_C)` returns treemap-ready data in a single query |
| **Narrative-ready enums** | Many fields designed for text generation (e.g., `GDPPCConstantCAGRRegionalDifference`, `MarketShareMainSectorDirection`, `NewProductsComments`) |
| **Decile classifications** | `normalizedOpportunityGainDecileClassification`, etc. — pre-binned for diamond ratings |

### Explore API Unique Features (not in Country Pages)

| Feature | Details |
|---------|---------|
| **Bilateral trade** (dedicated queries) | `countryCountryYear`, `countryCountryProductYear` — full bilateral trade data |
| **Group/regional trade** | `groupYear`, `groupGroupProductYear`, etc. |
| **6-digit products** | `productLevel: 6` supported |
| **HS 2022** | `productHs22` catalog + filtering |
| **Product space edges** | `productProduct` — product-to-product relatedness strengths |
| **Code conversion** | `conversionPath`, `conversionSources`, `conversionWeights` |
| **Data quality flags** | `dataFlags` with detailed eligibility criteria |
| **22-field `LocationCountry`** | More metadata fields than Country Pages' 15-field `Location` |

### Overlap: Same Data Available in Both

| Data Point | Country Pages API | Explore API | Prefer |
|-----------|-------------------|-------------|--------|
| Country exports by product | `treeMap(facet: CPY_C)` | `countryProductYear.exportValue` | Explore (more fields, 6-digit) |
| Country-level GDP, ECI | `countryYear.gdpPerCapita, eci` | `countryYear.gdppc, eci` | Either (equivalent) |
| Product catalog | `allProducts` (10 fields) | `productHs92` (21 fields) | Explore (more fields) |
| Product complexity (PCI) | `allProductYear.pci` | `productYear.pci` | Either |
| Country metadata | `allLocations` (15 fields) | `locationCountry` (22 fields) | Explore (more fields) |
| Derived analytical metrics | `countryProfile` (46 fields) | ❌ Not available | **Country Pages only** |
| Growth projections | `countryProfile.growthProjection` | `countryYear.growthProj` | Country Pages (has rank + classification) |
| Product space (country) | `productSpace` (pre-computed x/y) | `countryProductYear` + `productProduct` (must compute) | Country Pages (pre-computed) |

**General guidance**: Prefer the Explore API for raw trade data — it has more fields, better introspection, and explicit HS revision support. Use the Country Pages API for derived analytical metrics (growth projections, diversification grades, strategic approach, narrative descriptions) that would be expensive to recompute.

---

## Interactive Elements Summary

| Element | Location | Options/Range |
|---|---|---|
| Country selector | All pages (top-left) | 145 countries, searchable dropdown |
| Trade Flow | export-basket, export-complexity | Gross, Net |
| Colored by | export-complexity | Complexity, Entry Year |
| Year Range | growth-dynamics | 3 Years, 5 Years, 10 Years |
| Sizing Products by | growth-dynamics | Country Trade, World Trade, None |
| Sizing of Dots | paths | World Trade (possibly others) |
| Sector toggles | market-share | 10 sectors, each removable via X |
| Export dest / Import origin toggle | export-basket | export destination, import origin |
| Product Selection Criteria | growth-opportunities | Low-hanging Fruit, Balanced Portfolio, Long Jumps |
| Colored by | new-products | Sector (possibly others) |
| Zoom controls | paths | +ZOOM, -ZOOM, RESET ZOOM |

---

## Cross-Country Consistency

- **Structure identical**: Yes. All countries tested (USA, Spain, Kenya, Turkiye, Ethiopia, Brazil, Germany, India) have the same page layout, same 12 subpage URLs, same navigation icons, same top bar stats per section.
- **Data availability varies**:
  - **Growth Opportunities scatter** (`/growth-opportunities`): NOT available for highest-complexity countries (e.g., USA). Available for others (e.g., Kenya).
  - **New Product Opportunities table** (`/product-table`): NOT available for highest-complexity countries. Available for others.
  - All other pages are populated for all countries.
- **Text is dynamically generated** per country — wording changes based on data (e.g., "improving" vs "worsening", "high-income" vs "lower-middle-income", specific sector names). The narrative text is driven by the Country Pages API's enum fields (e.g., `ExportValueGrowthClassification: Troubling` → "troubling pattern of export growth").
- **Strategic approach varies** by country position on complexity/COI scatter: USA → Technological Frontier, Kenya → Light Touch Approach
- **Canvas vs DOM**: Treemaps, product space, growth dynamics scatter, market share chart, and growth opportunities scatter are **canvas-rendered** (no DOM access). The product table is the only section with DOM-accessible HTML data.
- **No Explore API calls**: Country pages exclusively use `/api/countries/graphql` — never `/api/graphql`.

---

## Extractable Data Points Catalog

### From Main Page (`/countries/{id}`)

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 1 | GDP per capita (nominal, USD) | stat card | `countryProfile` | `latestGdpPerCapita.quantity` |
| 2 | GDP per capita (PPP, USD) | text | `countryProfile` | `latestGdpPerCapitaPpp.quantity` |
| 3 | GDP per capita rank | stat card | `countryProfile` | `latestGdpPerCapitaRank.quantity` |
| 4 | Income classification | text | `countryProfile` | `incomeClassification` |
| 5 | Population | text | `countryProfile` | `latestPopulation.quantity` |
| 6 | GDP per capita growth (5-year avg) | text | `countryLookback` | `gdpPerCapitaChangeConstantCagr` |
| 7 | GDP per capita growth vs regional avg | text | `countryLookback` | `gdpPcConstantCagrRegionalDifference` |
| 8 | ECI ranking | stat card | `countryProfile` | `latestEciRank` |
| 9 | ECI rank change (decade) | text | `countryLookback(yearRange: TenYears)` | `eciRankChange` |
| 10 | Complexity trend driver | text | `countryProfile` | Derived from `newProductsComplexityStatusGrowthPrediction` |
| 11 | Growth projection to 2034 | stat card | `countryProfile` | `growthProjection` |
| 12 | Growth projection rank | stat card | `countryProfile` | `growthProjectionRank` |
| 13 | Complexity-income relationship | text | `countryProfile` | `growthProjectionRelativeToIncome` |
| 14 | Projected growth speed | text | `countryProfile` | `growthProjectionClassification` |

### From Export Basket (`/countries/{id}/export-basket`)

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 15 | Total exports (USD) | top bar | `countryProfile` | `exportValue` |
| 16 | Exporter rank | top bar | `countryProfile` | `exportValueRank` |
| 17 | Current account (USD) | top bar | `countryProfile` | `currentAccount.quantity` |
| 18 | Export growth rate (5-year annual avg) | text | `countryLookback(yearRange: FiveYears)` | `exportValueConstGrowthCagr` |
| 19 | Non-oil export growth rate | text | `countryLookback(yearRange: FiveYears)` | `exportValueGrowthNonOilConstCagr` |
| 20 | Total imports (USD) | text | `countryProfile` | `importValue` |
| 21 | Trade balance | text | Derived | `exportValue - importValue` (sign determines deficit/surplus) |
| 22 | Top 3 export destination countries | stat cards | `treeMap(facet: CCY_C)` | `location.shortName`, `exportValue` (sorted, top 3) |
| 23 | Top 3 import origin countries | stat cards (toggle) | `treeMap(facet: CCY_C)` | `location.shortName`, `importValue` (sorted, top 3) |
| 24 | Product-level export share | treemap | `treeMap(facet: CPY_C)` | `exportValue` (derived share) |
| 25 | Product-level export value | tooltip | `treeMap(facet: CPY_C)` | `exportValue` |
| 26 | Product HS92 code | tooltip | `treeMap(facet: CPY_C)` | `product.code` |

### From Export Complexity (`/countries/{id}/export-complexity`)

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 27 | ECI ranking (repeated) | top bar | `countryProfile` | `latestEciRank` |
| 28 | ECI rank change (10 years) | top bar | `countryLookback(yearRange: TenYears)` | `eciRankChange` |
| 29 | Product Complexity Index (PCI) per product | treemap overlay | `allProductYear` | `pci` |
| 30 | Largest goods export sectors by complexity level | text | `countryProfile` | Derived from treemap data + PCI |

### From Export Growth Dynamics (`/countries/{id}/growth-dynamics`)

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 31 | Product export growth (CAGR) | tooltip | `countryProductLookback` | `exportValueConstCagr` |
| 32 | Country's ECI value | reference line | `countryYear` | `eci` |
| 33 | Growth pattern description | text | `countryProfile` → `countryLookback` | `exportValueGrowthClassification` |
| 34 | Sectors/products driving growth | text | `countryLookback` | `largestContributingExportProduct` |
| 35 | Product gross country export | tooltip | `countryProductLookback` | `exportValueConstGrowth` |

### From Growth in Global Market Share (`/countries/{id}/market-share`)

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 36 | Largest market share sector | top bar | `countryProfile` | `marketShareMainSector.shortName` |
| 37 | Share of global trade (total) | top bar | Derived | `countryProfile.exportValue / globalDatum.globalExportValue` |
| 38 | Sector-level global market share (per year) | tooltip | `treeMap(facet: CPY_C)` + `allProductYear` | `exportValue / globalExportValue` per sector |
| 39 | Market share trends (1996–2024) | line chart | Multi-year treemap data | Time series of above |
| 40 | Structural transformation status | text | `countryProfile` | `structuralTransformationStep` |
| 41 | Sectors driving export growth | text | `countryProfile` | `structuralTransformationSector`, `structuralTransformationDirection` |
| 42 | Growth mechanism | text | `countryProfile` | `marketShareMainSectorDirection`, `marketShareMainSectorPositiveGrowth` |

### From Diversification into New Products (`/countries/{id}/new-products`)

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 43 | Economic Diversification Grade | top bar | `countryProfile` | `diversificationGrade` |
| 44 | Diversity Rank | top bar | `countryProfile` | `diversityRank` |
| 45 | Diversity rank change (15 years) | top bar | `countryLookback(yearRange: FifteenYears)` | `diversityRankChange` |
| 46 | New products count | text/treemap | `newProductsCountry` | `newProductCount` |
| 47 | New products income contribution (per capita) | text | `newProductsCountry` | `newProductExportValuePerCapita` |
| 48 | New products total value | table | `newProductsCountry` | `newProductExportValue` |
| 49 | New export proportion | mini-visual | Derived | `newProductExportValue / exportValue` |
| 50 | Peer country comparison | table | `newProductsComparisonCountries` | `location`, `newProductCount`, `newProductExportValue`, `newProductExportValuePerCapita` |

### From Country's Product Space (`/countries/{id}/paths`)

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 51 | Export products count (RCA>1) | top bar | `countryProfile` | `diversity` |
| 52 | Complexity Outlook Index rank | top bar | `countryProfile` | `latestCoiRank` |
| 53 | Product RCA values | node coloring | `productSpace` | `rca` |
| 54 | Product space coordinates | node positions | `productSpace` | `x`, `y` |
| 55 | Product connections | network edges | `productSpace` | `connections` |

### From Strategic Approach (`/countries/{id}/strategic-approach`)

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 56 | Recommended strategic approach | quadrant label | `countryProfile` | `policyRecommendation` |
| 57 | Approach description | text | `countryProfile` | Derived from `policyRecommendation` |
| 58 | All countries' positions | scatter plot | `allCountryProfiles` + `allCountryYear` | `eciNatResourcesGdpControlled`, `coi` |

### From Growth Opportunities (`/countries/{id}/growth-opportunities`) — non-frontier countries only

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 59 | Product distance (decile) | scatter position | `allCountryProductYear` | `normalizedDistanceDecileClassification` |
| 60 | Product opportunity gain (decile) | scatter position | `allCountryProductYear` | `normalizedOpportunityGainDecileClassification` |
| 61 | Product complexity (decile) | scatter position | `allCountryProductYear` | `normalizedPciDecileClassification` |
| 62 | Strategy type | radio buttons | `countryProfile` | `policyRecommendation` |

### From Product Table (`/countries/{id}/product-table`) — non-frontier countries only

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 63 | Product name + HS code | table column | `allCountryProductYear` | `product.shortName`, `product.code` |
| 64 | "Nearby" Distance (diamond rating) | table column | `allCountryProductYear` | `normalizedDistanceDecileClassification` |
| 65 | Opportunity Gain (diamond rating) | table column | `allCountryProductYear` | `normalizedOpportunityGainDecileClassification` |
| 66 | Product Complexity (diamond rating) | table column | `allCountryProductYear` | `normalizedPciDecileClassification` |
| 67 | Global Size (USD) | table column | `allProductYear` | `globalExportValue` |
| 68 | Global Growth 5 YR (%) | table column | `allProductYear` | `globalExportValueChangeFiveYears` |
| 69 | High-potential sectors | text | Derived | From top-ranked products in table |

### From Summary (`/countries/{id}/summary`)

| # | Data Point | Source | API Query | API Field |
|---|-----------|--------|-----------|-----------|
| 70 | Complexity rank change | stat card | `countryLookback` | `eciRankChange` |
| 71 | New products count (repeated) | stat card | `newProductsCountry` | `newProductCount` |
| 72 | Growth projection (repeated) | stat card | `countryProfile` | `growthProjection` |
| 73 | Strategic approach (repeated) | stat card + description | `countryProfile` | `policyRecommendation` |

**Total: 73 extractable data points** (all browser-visible, with complete API field mapping)

---

## Conceptual Framework & Metric Interpretation

The sections above document *how to get* data from the Atlas. This section explains *what the data means* — the economic complexity theory, metric definitions, and interpretive logic the Atlas uses to generate its narrative analyses. This context is essential for generating meaningful answers from API data.

### Core Principles of Economic Complexity

1. **Complexity Predicts Growth**: Countries whose exports are more complex than expected for their income level grow faster. Economic complexity is a strong predictor of long-term growth.

2. **Knowhow-Based Diversification**: Countries diversify by moving into products that are related to what they already produce. The Product Space represents this relatedness, derived from real-world patterns of which countries produce similar product combinations.

3. **Structural Transformation**: Economic development typically follows a progression from Agriculture → Textiles → Electronics/Machinery Manufacturing — a reallocation of activity from low-productivity to high-productivity sectors. Limited diversification into high-complexity products constrains income growth.

4. **Strategic Product Selection**: Diversification decisions involve balancing three criteria that often conflict:
   - **Feasibility** (distance to existing capabilities)
   - **Income potential** (product complexity)
   - **Future opportunity** (opportunity gain for further diversification)

### Key Metric Definitions

| Metric | What It Measures | How to Interpret |
|--------|-----------------|-----------------|
| **ECI** (Economic Complexity Index) | A country's capacity to produce complex goods, based on the diversity and complexity of its export basket | Higher = more complex economy. Negative values are less complex; positive values are more complex. Linked to long-term growth prospects. |
| **PCI** (Product Complexity Index) | How complex a specific product is to produce | Higher = more capabilities required. Products with higher PCI tend to support higher wages. |
| **COI** (Complexity Outlook Index) | How well-connected a country is to complex products it doesn't yet export | Higher = more nearby opportunities for upgrading. Low COI means limited diversification paths. |
| **Distance** | How closely related a potential product is to a country's current knowhow | Close to 0 = "nearby" (easier to achieve). Higher values = larger capability jumps required. |
| **Opportunity Gain** | How many linkages a product has to other high-complexity products | Higher = entering this product opens more pathways for continued diversification. A measure of strategic positioning. |
| **RCA** (Revealed Comparative Advantage) | Whether a country exports more of a product than expected given its size | RCA > 1 = the country has a revealed comparative advantage in that product (it "exports" it meaningfully). |
| **RPOP** (Population-adjusted RCA) | RCA adjusted for population size | Used in structural transformation analysis. RPOP threshold of 0.25 determines sector "presence." |

### Strategic Approach Definitions

The Atlas classifies countries into four strategic approaches based on their position on a complexity-vs-connectivity scatter (ECI adjusted for natural resources and GDP, plotted against COI):

| Approach | Quadrant | When It Applies | What It Means |
|----------|----------|-----------------|---------------|
| **Parsimonious Industrial Policy** | Low complexity, high COI | Country has opportunities nearby but hasn't converted them | Focus on removing specific bottlenecks to jump short distances into related products. Targeted policy interventions needed. |
| **Light Touch** | High complexity, high COI | Country is complex and well-connected to opportunities | Ample space to diversify. Leverage existing successes to enter more complex production. Less intervention needed. |
| **Strategic Bets** | Low complexity, low COI | Country has few nearby opportunities | Limited paths forward. Must make deliberate, ambitious investments in specific sectors. Highest-risk approach. |
| **Technological Frontier** | High complexity, low COI | Country is already very complex (e.g., USA, Germany, Japan) | Already at the frontier. Growth comes from innovation and pushing technological boundaries, not product diversification. |

### Structural Transformation Framework

The Atlas evaluates where countries stand on the structural transformation pathway using sector presence analysis:

- **Sector presence** is determined by whether a country's RPOP exceeds a threshold (typically 0.25) averaged over a 3-year rolling window for key sectors: Textiles, Electronics, and Machinery.
- **Classification mapping**:
  - "Not started" — no presence in Textiles, Electronics, or Machinery
  - "Textiles only" — only Textiles present
  - "Machinery but not electronics" — Machinery present, Electronics absent
  - "Electronics but not machinery" — Electronics present, Machinery absent
  - "Completed" — both Electronics and Machinery present

The `structuralTransformationStep` enum in the API (`NotStarted`, `TextilesOnly`, `ElectronicsOnly`, `MachineryOnly`, `Completed`) maps directly to these categories.

### How the Atlas Identifies "New Products"

The `newProductsCountry` query returns products a country has newly started exporting. The identification logic uses:

- **Rolling window**: ~18 years of RCA history
- **Absence condition**: The first 3 years of the window must all have RCA < 0.5 (product was not meaningfully exported)
- **Presence condition**: The last 3 years must all have RCA ≥ 1.0 (product is now firmly exported)
- This filters out noisy, temporary RCA spikes — only sustained new exports qualify

The **Diversification Grade** (`APlus` through `DMinus`) is assigned based on ranking countries by new product count, with thresholds:
- **A+**: Top 10 countries by count
- **A**: ≥30 new products
- **B**: ≥13 new products
- **C**: ≥6 new products
- **D**: ≥3 new products
- **D-**: <3 new products

### How Products Are Classified by Complexity Level

When the Atlas describes a country's exports as "high complexity" or "low complexity," it uses a benchmark comparison:

1. **Benchmark**: Either the mean PCI of products the country exports with RCA > 1 (national PCI mean), or the country's ECI value
2. **Classification**: Products are compared against the benchmark ± a standard deviation cutoff (typically 0.25–0.5 SD):
   - **High complexity**: PCI > benchmark + (SD_cutoff × country_PCI_std)
   - **Low complexity**: PCI < benchmark − (SD_cutoff × country_PCI_std)
   - **Moderate complexity**: Between the two thresholds

The `ComplexityLevel` enum in the API (`low`, `moderate`, `high`) reflects this classification.

### Export Growth Pattern Classification

The `ExportValueGrowthClassification` enum (`Troubling`, `Mixed`, `Static`, `Promising`) is derived by:

1. Identifying the two fastest-growing export products (by CAGR)
2. Classifying each as low, medium, or high complexity relative to the country benchmark
3. Mapping the combination:
   - Both high or one high + one medium → **Promising**
   - One low + one high → **Mixed**
   - Both medium → **Static**
   - One low + one medium, or both low → **Troubling**

### Growth in Market Share Interpretation

When the Atlas says growth was "driven by gains in global market share" versus "good luck" (concentrating in a growing sector), it compares:

1. The country's change in market share for its top-contributing export sector over 5 years
2. Against a threshold derived from the cross-country distribution of market share changes:
   - **Rising**: Market share increased above the threshold
   - **Stagnant**: Market share increased but below the threshold
   - **Falling**: Market share decreased

The `marketShareMainSectorDirection` enum (`rising`, `falling`, `stagnant`) encodes this.

### Relative Complexity Status (Complexity vs. Income)

When the Atlas says a country is "more complex than expected for its income level," it uses an OLS regression of ECI rank on GDP per capita rank across all 145 countries, then checks whether the country falls outside prediction intervals:

- **More complex than expected**: ECI rank is better (lower number) than the lower prediction bound
- **In line with expectations**: Within the prediction interval
- **Less complex than expected**: ECI rank is worse (higher number) than the upper prediction bound

The `growthProjectionRelativeToIncome` enum (`More`, `ModeratelyMore`, `Same`, `ModeratelyLess`, `Less`) conveys this.
