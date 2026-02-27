# GraphQL API Guide

**Purpose:** Reference for the two Atlas GraphQL APIs — when to use each, how IDs and arguments differ, and how to construct valid queries.

**When to load this document:** Load when the agent must decide which GraphQL API to use (Explore vs. Country
Pages API), needs to confirm ID format (integer M49 vs. "location-404" string),
is uncertain about argument names (yearMin/yearMax vs. minYear/maxYear), or when
the question involves data available only in the API (HS22, product space
coordinates). Also load when troubleshooting a GraphQL query or when the agent
needs to understand what data is exclusive to each endpoint. NOTE: This document
is marked for review once the GraphQL pipeline is fully implemented (see GitHub
issue).

---

## Two Separate APIs

The Atlas exposes **two independent GraphQL APIs** with different schemas, different ID formats, and different data.

| Aspect | Explore API | Country Pages API |
|--------|-------------|-------------------|
| **Endpoint** | `POST https://atlas.hks.harvard.edu/api/graphql` | `POST https://atlas.hks.harvard.edu/api/countries/graphql` |
| **Query count** | 27 | 25 |
| **Official docs** | Yes — [github.com/harvard-growth-lab/api-docs](https://github.com/harvard-growth-lab/api-docs/blob/main/atlas.md) | No — reverse-engineered only |
| **Country ID format** | Integer: `countryId: 404` | Prefixed string: `location: "location-404"` |
| **Product ID format** | Integer: `productId: 726` | Prefixed string: `product: "product-HS-726"` |
| **Year params** | `yearMin`, `yearMax` | `minYear`, `maxYear` (or `year` for single-year) |
| **Product classifications** | `HS92`, `HS12`, `HS22`, `SITC` (explicit revisions) | `HS`, `SITC` (generic; maps to HS92 internally) |
| **Product levels** | `1`, `2`, `4`, `6` (integer) | `section`, `twoDigit`, `fourDigit` (enum) |
| **Authentication** | None required | None required |
| **Rate limit** | 120 req/min (shared) | 120 req/min (shared) |
| **GraphiQL interface** | Yes — navigate to endpoint URL in browser | Yes — navigate to endpoint URL in browser |

**Country IDs use M49 codes** (UN M49, which coincide with ISO 3166-1 numeric for most countries). Examples: Kenya = 404, USA = 840, Brazil = 76, Germany = 276, India = 356, Spain = 724.

---

## When to Use Each Source

### Use Country Pages API when the question involves:
- **Diversification grade** (`diversificationGrade`: A+, A, B, C, D, D-)
- **Strategic approach / policy recommendation** (`policyRecommendation`: LightTouch, ParsimoniousIndustrial, StrategicBets, TechFrontier)
- **10-year GDP growth projection** (`growthProjection`, `growthProjectionRank`, `growthProjectionClassification`)
- **ECI/COI rank and rank changes** (`latestEciRank`, `eciRankChange`, `latestCoiRank`)
- **Income classification** (`incomeClassification`: High, UpperMiddle, LowerMiddle, Low)
- **New products count / diversification** (`newProductsCountry`, `newProductsComparisonCountries`)
- **Pre-computed product space** with x/y coordinates (`productSpace`)
- **Export growth pattern classification** (`exportValueGrowthClassification`: Troubling, Mixed, Static, Promising)
- **Structural transformation status** (`structuralTransformationStep`)
- **Complexity-income relationship** (`growthProjectionRelativeToIncome`, `eciNatResourcesGdpControlled`)
- **Narrative-ready enum fields** (many fields designed for text generation, e.g., `GDPPCConstantCAGRRegionalDifference`, `MarketShareMainSectorDirection`)
- **Historical change with lookback periods** (`countryLookback` with 3/5/10/15-year ranges)

### Use Explore API when the question involves:
- **Raw trade data** with full field set (22 fields on `CountryProductYear`, including `exportRca`, `distance`, `cog`, `normalizedPci`, etc.)
- **HS22 data** (2022–2024 only; not available in Country Pages API or SQL)
- **6-digit product detail** (`productLevel: 6`)
- **Bilateral trade by product** (`countryCountryProductYear`, `countryCountryYear`)
- **Product-to-product relatedness** (`productProduct` — edges for product space network)
- **Group/regional trade** (`groupYear`, `groupGroupProductYear`, `countryGroupProductYear`)
- **Code conversion between classifications** (`conversionPath`, `conversionSources`, `conversionWeights`)
- **Data quality flags** (`dataFlags`, `countryYearThresholds`)
- **More metadata fields** on countries (22-field `LocationCountry` vs 15-field `Location`)

### Use SQL DB when:
- Custom aggregations, multi-table JOINs, or filters not expressible in GraphQL
- Historical entities (Soviet Union, Yugoslavia) or small countries with sparse API coverage
- Bulk data analysis across many countries/years (SQL is faster for large result sets)
- Cross-referencing trade data with non-Atlas tables

### When either works (prefer Explore API):
- Country-level GDP, ECI, exports by year: both `countryYear` queries return equivalent data
- Product complexity (PCI): available in both `productYear` queries
- Product catalog: both APIs have it; Explore has more fields (21 vs 10)
- Country exports by product: both APIs cover this; Explore has more fields and supports 6-digit

---

## Explore API Query Catalog (27 Queries)

All queries verified via introspection, February 2026.

### Core Trade Data

| Query | Required Args | Optional Args | Key Fields Returned |
|-------|---------------|---------------|---------------------|
| `countryProductYear` | `productLevel: Int!` | `productClass`, `servicesClass`, `countryId`, `productId`, `yearMin`, `yearMax` | `exportValue`, `importValue`, `exportRca`, `distance`, `cog`, `normalizedPci`, `globalMarketShare`, `productStatus`, `isNew` |
| `countryYear` | — | `countryId`, `productClass`, `servicesClass`, `yearMin`, `yearMax` | `exportValue`, `importValue`, `gdp`, `gdppc`, `population`, `eci`, `eciFixed`, `coi`, `growthProj`, `currentAccount` |
| `productYear` | `productLevel: Int!` | `productClass`, `servicesClass`, `productId`, `yearMin`, `yearMax` | `pci`, `exportValue`, `importValue`, `exportValueConstCagr5`, `complexityEnum` |
| `countryCountryYear` | — | `countryId`, `partnerCountryId`, `productClass`, `servicesClass`, `yearMin`, `yearMax` | `exportValue`, `importValue`, `exportValueReported`, `importValueReported` |
| `countryCountryProductYear` | — | `countryId`, `partnerCountryId`, `yearMin`, `yearMax`, `productClass`, `servicesClass`, `productLevel`, `productId`, `productIds` | `exportValue`, `importValue`, `exportValueReported`, `importValueReported` |
| `countryCountryProductYearGrouped` | — | Same as above | `productIds: [ID]`, `data: [CountryCountryProductYear]` |

### Group / Regional Data

| Query | Required Args | Optional Args | Key Fields Returned |
|-------|---------------|---------------|---------------------|
| `groupYear` | — | `productClass`, `servicesClass`, `groupId`, `groupType`, `yearMin`, `yearMax` | `exportValue`, `importValue`, `gdp`, `gdpPpp`, `population` |
| `groupGroupProductYear` | — | `productClass`, `servicesClass`, `productLevel`, `productId`, `groupId`, `partnerGroupId`, `yearMin`, `yearMax` | `exportValue`, `importValue`, `groupType`, `partnerType` |
| `countryGroupProductYear` | `partnerGroupId: Int!` | `productClass`, `servicesClass`, `productLevel`, `productId`, `countryId`, `yearMin`, `yearMax` | `exportValue`, `importValue` |
| `groupCountryProductYear` | `groupId: Int!` | `productClass`, `servicesClass`, `productLevel`, `productId`, `partnerCountryId`, `yearMin`, `yearMax` | `exportValue`, `importValue` |

### Product Relatedness

| Query | Required Args | Optional Args | Key Fields Returned |
|-------|---------------|---------------|---------------------|
| `productProduct` | `productClass: ProductClass!`, `productLevel: Int!` | — | `productId`, `targetId`, `strength`, `productLevel` |

### Reference / Catalog Data

| Query | Required Args | Optional Args | Key Fields Returned |
|-------|---------------|---------------|---------------------|
| `locationCountry` | — | — | `countryId`, `iso3Code`, `iso2Code`, `nameEn`, `incomelevelEnum`, `isTrusted`, `inRankings`, `inCp` |
| `locationGroup` | — | `groupType` | `groupId`, `groupName`, `groupType`, `members`, `exportValueSum`, `gdpSum`, CAGR fields |
| `productHs92` | — | `productLevel`, `servicesClass` | `productId`, `code`, `nameEn`, `nameShortEn`, `clusterId`, `productSpaceX`, `productSpaceY`, `naturalResource`, `greenProduct` |
| `productHs12` | — | `productLevel`, `servicesClass` | Same as `productHs92` |
| `productHs22` | — | `productLevel`, `servicesClass` | Same as `productHs92` |
| `productSitc` | — | `productLevel`, `servicesClass` | Same as `productHs92` |
| `year` | — | `yearMin`, `yearMax` | `year`, `deflator` |

### Classification Conversion

| Query | Required Args | Key Fields Returned |
|-------|---------------|---------------------|
| `conversionPath` | `sourceCode: String!`, `sourceClassification: ClassificationEnum!`, `targetClassification: ClassificationEnum!` | `fromClassification`, `toClassification`, `codes` |
| `conversionSources` | `targetCode: String!`, `targetClassification: ClassificationEnum!`, `sourceClassification: ClassificationEnum!` | Same |
| `conversionWeights` | — (optional: individual version codes as strings) | Weighted concordance between HS/SITC revisions |

### Metadata & Diagnostics

| Query | Required Args | Optional Args | Key Fields Returned |
|-------|---------------|---------------|---------------------|
| `countryYearThresholds` | `productClass: ProductClass!` | `countryId`, `yearMin`, `yearMax` | Percentile distributions (10/20/25/30/40/50/60/70/75/80/90), mean, median, min, max, std |
| `dataFlags` | — | `countryId` | 20 eligibility/coverage booleans (e.g., `rankingsEligible`, `countryProfilesEligible`) |
| `dataAvailability` | — | — | `productClassification`, `yearMin`, `yearMax` |
| `downloadsTable` | — | — | Pre-generated dataset catalog |
| `metadata` | — | — | `serverName`, `ingestionCommit`, `ingestionDate`, `apiCommit` |
| `banner` | — | — | Site announcement banners |

### Explore API Enum Values

| Enum | Values |
|------|--------|
| `ProductClass` | `HS92`, `HS12`, `HS22`, `SITC` |
| `ServicesClass` | `unilateral` |
| `ProductStatus` | `absent`, `lost`, `new`, `present` |
| `ComplexityLevel` | `low`, `moderate`, `high` |
| `LocationLevel` | `country`, `group` |
| `IncomeLevel` | `high`, `upper_middle`, `lower_middle`, `low` |
| `GroupType` | `continent`, `political`, `region`, `subregion`, `trade`, `wdi_income_level`, `wdi_region`, `world` |
| `ClassificationEnum` | `SITC1962`, `SITC1976`, `SITC1988`, `HS1992`, `HS1997`, `HS2002`, `HS2007`, `HS2012`, `HS2017`, `HS2022` |

### Data Availability by Classification

| Classification | Year Range | Notes |
|----------------|------------|-------|
| HS92 | 1995–2024 | Default; most commonly used |
| HS12 | 2012–2024 | Updated product categories |
| HS22 | 2022–2024 | Explore API only — not in SQL or Country Pages API |
| SITC | 1962–2024 | Longest history; use for pre-1995 analysis |

---

## Country Pages API Query Catalog (25 Queries)

All queries verified via introspection, February 2026. This API is not officially documented by the Growth Lab.

### Core Country Data

| Query | Required Args | Optional Args | Purpose |
|-------|---------------|---------------|---------|
| `countryProfile` | `location: ID!` | `comparisonLocationsQuantity` | **Richest query**: 46 derived analytical fields — GDP, ECI, COI, growth projections, diversification grade, strategic approach, peer comparisons, all narrative enums |
| `allCountryProfiles` | — | — | All countries' key metrics (`diversificationGrade`, `policyRecommendation`, `eciNatResourcesGdpControlled`) |
| `countryYear` | `location: ID!`, `year: Int!` | `eciProductClass`, `coiProductClass` | Single-year snapshot: GDP, exports, ECI, COI, population |
| `allCountryYear` | `year: Int!` | `eciProductClass`, `coiProductClass` | All countries for a given year |
| `countryYearRange` | `location: ID!`, `minYear: Int!`, `maxYear: Int!` | `eciProductClass`, `coiProductClass` | Time series (sparkline data); numeric fields return `{ quantity, year }` arrays |
| `allCountryYearRange` | `minYear: Int!`, `maxYear: Int!` | `eciProductClass`, `coiProductClass` | All countries time series |
| `countryLookback` | `id: ID!` | `yearRange`, `exportValueGrowthCagrYearRange`, `eciRankChangeYearRange`, `gdpChangeCagrYearRange`, and many more lookback range params | Historical change metrics: CAGR, ECI rank change, diversity rank change — all with configurable `LookBackYearRange` |
| `globalDatum` | — | `yearRange`, `gdpChangeConstCagrYearRange`, etc. | Global aggregates and rank totals (e.g., `latestEciRankTotal: 145`) |

### Trade Visualization Data

| Query | Required Args | Optional Args | Purpose |
|-------|---------------|---------------|---------|
| `treeMap` | `facet: TreeMapType!` | `productClass`, `year`, `productLevel`, `locationLevel`, `location`, `product`, `partner`, `mergePci` | Treemap-ready data; `CPY_C` returns products, `CCY_C` returns trade partners |
| `allCountryProductYear` | `location: ID!`, `year: Int!`, `productClass: ProductClass!` | `productLevel` | Per-product data with decile classifications for growth opportunity scatter and table |
| `manyCountryProductYear` | `year: Int!`, `productClass: ProductClass!` | `group`, `productLevel`, `aggregate` | Multi-country product data |

### Product Data

| Query | Required Args | Optional Args | Purpose |
|-------|---------------|---------------|---------|
| `product` | `id: ID!` | — | Single product details |
| `allProducts` | `productClass: ProductClass!` | `productLevel` | Product catalog: names, codes, hierarchy (10 fields) |
| `productYear` | `product: ID!`, `year: Int!` | — | Single product-year: PCI, global export value, 5-year change |
| `allProductYear` | `productClass: ProductClass!`, `productLevel: ProductLevel!`, `year: Int!` | — | All products for a year |
| `productYearRange` | `product: ID!`, `minYear: Int!`, `maxYear: Int!` | — | Product time series |
| `allProductYearRange` | `productClass: ProductClass!`, `productLevel: ProductLevel!`, `minYear: Int!`, `maxYear: Int!` | — | All products time series |

### Specialized Country Data

| Query | Required Args | Optional Args | Purpose |
|-------|---------------|---------------|---------|
| `productSpace` | `productClass: ProductClass!`, `year: Int!`, `location: ID!` | — | Country-specific product space: RCA, x/y coordinates, connected products |
| `newProductsCountry` | `location: ID!`, `year: Int!` | — | New products: list, count, total value, per-capita value |
| `newProductsComparisonCountries` | `location: ID!`, `year: Int!` | `quantity` | Peer country comparison for new products diversification |
| `countryProductLookback` | `location: ID!` | `yearRange`, `productLevel` | Per-product export growth CAGR over 3/5/10/15-year lookback |

### Reference Data

| Query | Required Args | Optional Args | Purpose |
|-------|---------------|---------------|---------|
| `location` | `id: ID!` | — | Single location details |
| `allLocations` | — | `level` | All locations (countries and regions) |
| `group` | `id: ID!` | — | Single group details |
| `allGroups` | — | `groupType` | All groups |

### `CountryProfile` Fields (46 fields — the richest single query)

```
location: Location
latestPopulation: IntForYear            # { quantity, year }
latestGdp: FloatForYear
latestGdpRank: IntForYear
latestGdpPpp: FloatForYear
latestGdpPppRank: IntForYear
latestGdpPerCapita: IntForYear
latestGdpPerCapitaRank: IntForYear
latestGdpPerCapitaPpp: IntForNotRequiredYear
latestGdpPerCapitaPppRank: IntForYear
incomeClassification: IncomeClassification
exportValue: Float
importValue: Float
exportValueRank: Int
exportValueNatResources: Int
importValueNatResources: Int
netExportValueNatResources: Int
exportValueNonOil: Int
newProductExportValue: Float
newProductExportValuePerCapita: Int
newProductsIncomeGrowthComments: NewProductsIncomeGrowthComments
newProductsComments: NewProductsComments
newProductsComplexityStatusGrowthPrediction: NewProductsComplexityStatusGrowthPrediction
currentAccount: FloatForNotRequiredYear
latestEci: Float
latestEciRank: Int
eciNatResourcesGdpControlled: Float
latestCoi: Float
latestCoiRank: Int
coiClassification: COIClassification
growthProjection: Float
growthProjectionRank: Int
growthProjectionClassification: GrowthProjectionClassification
growthProjectionRelativeToIncome: GrowthProjectionRelativeToIncome
growthProjectionPercentileClassification: GrowthProjectionPercentileClassification
comparisonLocations: [Location]
diversity: Int                          # count of products with RCA > 1
diversityRank: Int
diversificationGrade: DiversificationGrade
marketShareMainSector: Product
marketShareMainSectorDirection: MarketShareMainSectorDirection
marketShareMainSectorPositiveGrowth: Boolean
structuralTransformationStep: StructuralTransformationStep
structuralTransformationSector: Product
structuralTransformationDirection: StructuralTransformationDirection
policyRecommendation: PolicyRecommendation
```

### `CountryLookback` Fields (13 fields)

```
id
eciRankChange: Int
exportValueConstGrowthCagr: Float
exportValueGrowthNonOilConstCagr: Float
largestContributingExportProduct: [Product]
eciChange: Float
diversityRankChange: Int
diversityChange: Int
gdpPcConstantCagrRegionalDifference: GDPPCConstantCAGRRegionalDifference
exportValueGrowthClassification: ExportValueGrowthClassification
gdpChangeConstantCagr: Float
gdpPerCapitaChangeConstantCagr: Float
gdpGrowthConstant: Float
```

### TreeMap Facet Behavior

| Facet | Returns | Use Case |
|-------|---------|----------|
| `CPY_C` | `[TreeMapProduct]` — products with exportValue, rca, pci, distance, opportunityGain | Export basket, export complexity, market share |
| `CPY_P` | `[TreeMapProduct]` — requires a specific `product` ID | Product-specific drilldown |
| `CCY_C` | `[TreeMapLocation]` — trade partners with exportValue, importValue | Top-3 export destinations / import origins |

### Country Pages API Enum Values

| Enum | Values |
|------|--------|
| `ProductClass` | `HS`, `SITC` |
| `ProductLevel` | `section`, `twoDigit`, `fourDigit` |
| `LookBackYearRange` | `ThreeYears`, `FiveYears`, `TenYears`, `FifteenYears` |
| `TreeMapType` | `CPY_C`, `CPY_P`, `CCY_C` |
| `PolicyRecommendation` | `ParsimoniousIndustrial`, `StrategicBets`, `LightTouch`, `TechFrontier` |
| `DiversificationGrade` | `APlus`, `A`, `B`, `C`, `D`, `DMinus` |
| `IncomeClassification` | `High`, `UpperMiddle`, `LowerMiddle`, `Low` |
| `COIClassification` | `low`, `medium`, `high` |
| `GrowthProjectionClassification` | `rapid`, `moderate`, `slow` |
| `GrowthProjectionRelativeToIncome` | `More`, `Less`, `Same`, `ModeratelyMore`, `ModeratelyLess` |
| `ExportValueGrowthClassification` | `Troubling`, `Mixed`, `Static`, `Promising` |
| `StructuralTransformationStep` | `NotStarted`, `TextilesOnly`, `ElectronicsOnly`, `MachineryOnly`, `Completed` |
| `DecileClassification` | `Last`, `Second`, `Third`, `Fourth`, `Fifth`, `Sixth`, `Seventh`, `Eighth`, `Ninth`, `Top` |
| `MarketShareMainSectorDirection` | `rising`, `falling`, `stagnant` |
| `GDPPCConstantCAGRRegionalDifference` | `Above`, `InLine`, `Below` |

---

## Country ID Mapping Reference

Both APIs use M49 codes. The Explore API takes bare integers; the Country Pages API takes prefixed strings.

| Country | M49 Code | Explore API | Country Pages API |
|---------|----------|-------------|-------------------|
| USA | 840 | `countryId: 840` | `location: "location-840"` |
| Kenya | 404 | `countryId: 404` | `location: "location-404"` |
| Brazil | 76 | `countryId: 76` | `location: "location-76"` |
| Germany | 276 | `countryId: 276` | `location: "location-276"` |
| India | 356 | `countryId: 356` | `location: "location-356"` |
| Spain | 724 | `countryId: 724` | `location: "location-724"` |
| China | 156 | `countryId: 156` | `location: "location-156"` |
| Ethiopia | 231 | `countryId: 231` | `location: "location-231"` |

Full mapping: `locationCountry { countryId iso3Code nameEn }` (Explore) or `allLocations { id code shortName }` (Country Pages).

---

## Example Queries

### Example 1: Country analytical profile (Country Pages API)

Use when asked about diversification grade, strategic approach, growth projection, or income classification.

```graphql
# Country Pages API: POST https://atlas.hks.harvard.edu/api/countries/graphql
{
  countryProfile(location: "location-404") {
    latestEci
    latestEciRank
    latestCoi
    latestCoiRank
    growthProjection
    growthProjectionRank
    growthProjectionClassification
    growthProjectionRelativeToIncome
    diversificationGrade
    diversityRank
    policyRecommendation
    incomeClassification
    exportValue
    exportValueRank
  }
}
```

### Example 2: Historical change with lookback period (Country Pages API)

Use when asked about ECI rank change over 10 years, non-oil export growth, or export growth pattern.

```graphql
# Country Pages API: POST https://atlas.hks.harvard.edu/api/countries/graphql
{
  countryLookback(
    id: "location-404"
    eciRankChangeYearRange: TenYears
    exportValueGrowthCagrYearRange: FiveYears
    exportValueGrowthNonOilCagrYearRange: FiveYears
  ) {
    eciRankChange
    exportValueConstGrowthCagr
    exportValueGrowthNonOilConstCagr
    exportValueGrowthClassification
    largestContributingExportProduct {
      shortName
      code
    }
  }
}
```

### Example 3: Raw country-product trade data (Explore API)

Use when asked about RCA, distance, complexity of specific products, or for custom analysis.

```graphql
# Explore API: POST https://atlas.hks.harvard.edu/api/graphql
{
  countryProductYear(
    countryId: 404
    productClass: HS92
    productLevel: 4
    yearMin: 2024
    yearMax: 2024
  ) {
    productId
    exportValue
    exportRca
    distance
    cog
    normalizedPci
    globalMarketShare
    productStatus
  }
}
```

### Example 4: Bilateral trade between two countries (Explore API)

```graphql
# Explore API: POST https://atlas.hks.harvard.edu/api/graphql
{
  countryCountryYear(
    countryId: 840
    partnerCountryId: 156
    yearMin: 2020
    yearMax: 2024
  ) {
    year
    exportValue
    importValue
  }
}
```

### Example 5: New products diversification (Country Pages API)

```graphql
# Country Pages API: POST https://atlas.hks.harvard.edu/api/countries/graphql
{
  newProductsCountry(location: "location-404", year: 2024) {
    newProductCount
    newProductExportValue
    newProductExportValuePerCapita
    newProducts {
      shortName
      code
    }
  }
  newProductsComparisonCountries(location: "location-404", year: 2024, quantity: 3) {
    location { shortName }
    newProductCount
    newProductExportValue
    newProductExportValuePerCapita
  }
}
```

---

## Data Exclusive to Each Source

### Country Pages API only (not in SQL or Explore API)

- `diversificationGrade` (A+ through D-)
- `policyRecommendation` / `structuralTransformationStep`
- `growthProjection`, `growthProjectionRank`, `growthProjectionClassification`
- `growthProjectionRelativeToIncome` (complexity-income relationship)
- `exportValueGrowthClassification` (Troubling / Mixed / Static / Promising)
- `eciNatResourcesGdpControlled` (ECI adjusted for natural resources and GDP — used for strategic approach quadrant)
- `newProductsComparisonCountries` (peer country comparison)
- Pre-computed product space x/y coordinates via `productSpace`
- `DecileClassification` fields on `allCountryProductYear`
- All narrative enum fields on `CountryLookback` and `CountryProfile`

### Explore API only (not in Country Pages or SQL)

- `productProduct` — product-to-product relatedness strengths (product space edges)
- HS22 classification (`productHs22`, `productClass: HS22`)
- 6-digit product level (`productLevel: 6`)
- `conversionPath` / `conversionSources` / `conversionWeights` — cross-classification code conversion
- `dataFlags` — detailed per-country eligibility and data quality booleans
- `countryYearThresholds` — percentile distributions for any metric
- `countryCountryProductYear` — bilateral trade broken down by product
- Group/regional queries (`groupYear`, `groupGroupProductYear`, etc.)
- `locationGroup` with full CAGR statistics

### SQL DB only (not available via either API)

- Custom aggregations not expressible as GraphQL filters
- Raw table access for bulk exports or multi-table JOINs
- Historical entities (e.g., Soviet Union pre-1991 data)
- `data_flags` table details beyond what `dataFlags` query returns

---

## Response Size Tiers

Any query that returns "all products" (~1,200 items) produces 40–350 KB responses that cannot be passed directly to the LLM. The pipeline automatically post-processes Tier 4 responses (sort, truncate to top-N, enrich with human-readable names).

### Tier 1 — Always Safe (< 500 bytes)

| Query Pattern | API | Typical Size | Use Case |
|---------------|-----|-------------|----------|
| `countryProfile` (specific fields) | CP | 56–240 B | Country metrics |
| `countryLookback` | CP | 49–122 B | Historical change |
| `countryYear` (single year) | Both | 107–356 B | Country snapshot |
| `countryCountryYear` (bilateral) | Explore | 76–103 B | Bilateral trade totals |
| `productYear` (single product) | Both | 76–117 B | Product stats |
| `countryProductYear` (with productId) | Explore | 126 B | Specific product for country |
| `groupYear` (single) | Explore | 67–96 B | Regional aggregate |
| `globalDatum` | CP | 392 B | Global rank totals |
| `dataAvailability` | Explore | 281 B | Year ranges |
| `dataFlags` (single country) | Explore | ~200 B | Data quality |
| `conversionPath` | Explore | ~450 B | Code conversion |
| `newProductsComparisonCountries` | CP | 530 B | Peer comparison |

### Tier 2 — Safe (500 B – 10 KB)

| Query Pattern | API | Typical Size | Use Case |
|---------------|-----|-------------|----------|
| `countryYear` (10-year range) | Explore | 330 B | Time series |
| `countryYearRange` (10 years) | CP | 1,335 B | Sparkline data |
| `countryProfile` (all 46 fields) | CP | 1,110 B | Full profile |
| `newProductsCountry` | CP | 1,306 B | New products list |
| `locationGroup` (with CAGR stats) | Explore | 3,428 B | Regional groups |
| `year` (deflators) | Explore | 4,920 B | All deflators |
| `downloadsTable` | Explore | 9,392 B | Dataset catalog |

### Tier 3 — Borderline (10–40 KB)

| Query Pattern | API | Typical Size | Items | Use Case |
|---------------|-----|-------------|-------|----------|
| `treeMap` CCY_C (partners) | CP | 18 KB | 217 | Trade partners |
| `allCountryProfiles` | CP | 26 KB | 145 | All countries' key metrics |
| `allLocations` | CP | 29 KB | 252 | Location catalog |
| `locationCountry` | Explore | 33 KB | 252 | Country catalog |
| `allCountryYear` | CP | 36 KB | 145 | All countries one year |

### Tier 4 — Must Truncate (> 40 KB)

| Query Pattern | API | Typical Size | Items | Use Case |
|---------------|-----|-------------|-------|----------|
| `productHs92` (level 4, min fields) | Explore | 42 KB | 1,248 | Product catalog |
| `countryProductYear` (all products) | Explore | 59–300 KB | ~1,200 | Top N products |
| `countryCountryProductYear` (all) | Explore | 69 KB | ~1,200 | Bilateral products |
| `countryProductLookback` | CP | 98 KB | 551 | Per-product CAGR |
| `treeMap` CPY_C (products) | CP | 113 KB | ~1,200 | Export basket |
| `productSpace` | CP | 131 KB | 852 | Product space coords |
| `countryGroupProductYear` (level 4) | Explore | 153 KB | 1,054 | Country→group products |
| `groupCountryProductYear` (level 4) | Explore | 153 KB | 1,054 | Group→country products |
| `allProducts` (CP) | CP | 281 KB | 1,248 | CP product catalog |
| `allProductYear` | CP | 282 KB | 1,246 | All products PCI |
| `allCountryProductYear` | CP | 352 KB | 1,194 | Growth opportunities |

### Broken / Unpopulated Queries

| Query | API | Issue |
|-------|-----|-------|
| `productProduct` | Explore | Returns 0 items for all classifications — use Country Pages `productSpace` instead |
| `groupGroupProductYear` | Explore | Returns 0 items — appears unpopulated |
| `manyCountryProductYear` | CP | Server error (`NoneType` bug) — do not use |
| `allProductYearRange` | CP | Server error (missing `product_level` arg) — do not use |

---

## Important Operational Notes

- **Rate limit:** 120 requests per minute — shared across both APIs from the same IP. Budget queries carefully; batch fields within a single query rather than making multiple requests.
- **Request only needed fields** to reduce response size. GraphQL allows selecting exactly the fields required. Field selection controls response size linearly: bytes ≈ field_count × item_count × ~60 bytes/field.
- **No server-side sort or limit:** For "top N" queries, the server returns all matching items — the client must sort and truncate. The pipeline does this automatically via `post_process_response` for Tier 4 query types.
- **Cache responses** when possible. API data changes only when Atlas ingests new data (annually).
- **For bulk downloads:** Use [atlas.hks.harvard.edu/data-downloads](https://atlas.hks.harvard.edu/data-downloads) instead of the API for large pre-generated tables.
- **Explore API product IDs** are internal (not HS codes). Use `productHs92` catalog to resolve `productId` → human-readable name and HS code.
- **Goods and services are mixed** in responses. Filter by non-numeric product codes if you need goods-only results.
- **Usage warning:** The Atlas API is "best used to access data for stand-alone economic analysis, not to support other software applications" (official docs). The Growth Lab reserves the right to restrict access for abusive usage.
- **Country Pages API**: Not officially documented. Schema may change without warning. Verify with introspection if behavior is unexpected.
- **HS22 caveat**: Only 2022–2024 data. Not available in the SQL database or the Country Pages API.
