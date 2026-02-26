# Atlas GraphQL API — Official Documentation Reference

This document consolidates the **official** Harvard Growth Lab API documentation from [github.com/harvard-growth-lab/api-docs](https://github.com/harvard-growth-lab/api-docs/blob/main/atlas.md) (last updated January 2026) together with the complete schema obtained via live GraphQL introspection. It serves as the authoritative reference that should override any conflicting information in our research-based documentation.

---

## Table of Contents

1. [Source & Authority](#1-source--authority)
2. [Getting Started](#2-getting-started)
3. [Core Concepts](#3-core-concepts)
4. [Best Practices & Restrictions](#4-best-practices--restrictions)
5. [GraphiQL Documentation Explorer](#5-graphiql-documentation-explorer)
6. [Complete Query Catalog (27 queries)](#6-complete-query-catalog-27-queries)
7. [Complete Type Schemas (from introspection)](#7-complete-type-schemas-from-introspection)
8. [Enum Values (from introspection)](#8-enum-values-from-introspection)
9. [Example Queries (from official docs)](#9-example-queries-from-official-docs)
10. [Additional Resources & Citation](#10-additional-resources--citation)
11. [Comparison: Official Docs vs Our Research](#11-comparison-official-docs-vs-our-research)

---

## 1. Source & Authority

| Item | Detail |
|------|--------|
| **Repository** | [harvard-growth-lab/api-docs](https://github.com/harvard-growth-lab/api-docs) |
| **Document** | `atlas.md` |
| **Last updated** | January 2026 |
| **Scope** | Explore API (`/api/graphql`) only — does NOT cover the Country Pages API (`/api/countries/graphql`) |
| **License** | See repository |

The official documentation covers only the **Explore API** endpoint. Our internal research has additionally documented the **Country Pages API** (`/api/countries/graphql`), which is not officially documented by the Growth Lab.

### Key Official Statements

> "The Atlas API is specifically designed to support functionality for the Atlas of Economic Complexity. Public access is provided as a courtesy to users who may find it useful, but it is always possible that specific endpoints or data values may change suddenly and without warning. **The Atlas API is best used to access data for stand-alone economic analysis, not to support other software applications.**"

> "We reserve the right to monitor usage of the Atlas API for abusive or potentially malicious behavior and further restrict or block access to specific users."

---

## 2. Getting Started

**Base URL:** `https://atlas.hks.harvard.edu/api/graphql`

The API uses GraphQL via POST requests. It is publicly accessible and **does not require authentication**.

### Basic Request Structure

```bash
curl -X POST https://atlas.hks.harvard.edu/api/graphql \
  -H "Content-Type: application/json" \
  -d '{"query":"{ YOUR_QUERY_HERE }"}'
```

---

## 3. Core Concepts

### Product Classifications

The API supports multiple goods classification systems:

| Classification | Full Name | Notes |
|----------------|-----------|-------|
| **HS92** | Harmonized System 1992 | Default for goods |
| **HS12** | Harmonized System 2012 | |
| **HS22** | Harmonized System 2022 | Limited: 2022–2024 data only |
| **SITC** | Standard International Trade Classification | Longest history: 1962–2024 |

The API currently supports a single services classification:

| Classification | Full Name | Notes |
|----------------|-----------|-------|
| **unilateral** | Unilateral services data | Default for services |

#### Product IDs (official definition)

> "Product IDs are **internally-designated, numerical identification codes** that do not correspond to published HS or SITC codes."

These internal IDs can be used as mappings between queries. Product code information is available within classification endpoints (`productHs92`, `productHs12`, `productHs22`, `productSitc`).

#### Product Levels

> "Most endpoints featuring product-level data will allow the user to supply a product level. This is a numerical representation of aggregation level within a specific product classification. For example, providing `4` in an `HS92` query would return products with a 4-digit HS code."

Available product levels: `1` (section), `2` (2-digit), `4` (4-digit), `6` (6-digit, Explore API only).

### Location Classifications

| Classification | Description |
|----------------|-------------|
| **country** | Individual countries |
| **group** | Country groups (continents, regions, trade blocs, etc.) |

#### Country and Group IDs (official definition)

> "Each location is identified with an internally-designated, numerical identification code. Wherever possible, this identifier has been designed to correspond to **a location's M49 code as designated by the UN** ([UN M49](https://unstats.un.org/unsd/methodology/m49/))."

**Important:** The official docs reference M49 codes, not ISO 3166-1 numeric codes (though for most countries these are identical). The IDs "can be used between endpoints to join data in queries."

### Data Availability (from live introspection)

| Classification | Year Range |
|----------------|------------|
| HS92 | 1995–2024 |
| HS12 | 2012–2024 |
| HS22 | 2022–2024 |
| SITC | 1962–2024 |

---

## 4. Best Practices & Restrictions

### Rate Limit

> "The Atlas API currently enforces a **rate limit of 120 requests per minute**."

### Recommendations (from official docs)

- **Cache responses** when possible
- **Batch queries** using GraphQL's query composition
- **Request only needed fields** to reduce response size
- **Filter data within query** to reduce response size (e.g., only request data for a specific country or a specific time period, rather than all records)
- **For bulk data downloads**, visit [the data downloads page](https://atlas.hks.harvard.edu/data-downloads) for pre-generated tables

---

## 5. GraphiQL Documentation Explorer

> "For complete documentation of available queries and endpoints, users can access the **GraphiQL interface** available by navigating to the [API URL](https://atlas.hks.harvard.edu/api/graphql) in a browser. Once in the GraphiQL interface, users should see a 'Docs' menu in the top right of the page. By opening the Documentation Explorer, you can click through the base Query object into descriptions of the various fields, parameters, and response object types available to users via the API."

**Key takeaway:** The GraphiQL interface at `https://atlas.hks.harvard.edu/api/graphql` provides the definitive, always-up-to-date schema documentation. The Documentation Explorer (top-right "Docs" menu) allows browsing all queries, types, and field descriptions interactively.

---

## 6. Complete Query Catalog (27 queries)

Verified via live introspection (February 2026). All 27 root queries on the Explore API:

### Core Trade Data (6 queries)

| Query | Required Args | Optional Args | Returns |
|-------|---------------|---------------|---------|
| `countryProductYear` | `productLevel: Int!` | `productClass, servicesClass, countryId, productId, yearMin, yearMax` | `[CountryProductYear]` |
| `countryYear` | — | `countryId, productClass, servicesClass, yearMin, yearMax` | `[CountryYear]` |
| `productYear` | `productLevel: Int!` | `productClass, servicesClass, productId, yearMin, yearMax` | `[ProductYear]` |
| `countryCountryYear` | — | `productClass, servicesClass, countryId, partnerCountryId, yearMin, yearMax` | `[CountryCountryYear]` |
| `countryCountryProductYear` | — | `countryId, partnerCountryId, yearMin, yearMax, productClass, servicesClass, productLevel, productId, productIds` | `[CountryCountryProductYear]` |
| `countryCountryProductYearGrouped` | — | (same as `countryCountryProductYear`) | `[CountryCountryProductYearGrouped]` |

### Group / Regional Data (4 queries)

| Query | Required Args | Optional Args | Returns |
|-------|---------------|---------------|---------|
| `groupYear` | — | `productClass, servicesClass, groupId, groupType, yearMin, yearMax` | `[GroupYear]` |
| `groupGroupProductYear` | — | `productClass, servicesClass, productLevel, productId, groupId, partnerGroupId, yearMin, yearMax` | `[GroupGroupProductYear]` |
| `countryGroupProductYear` | `partnerGroupId: Int!` | `productClass, servicesClass, productLevel, productId, countryId, yearMin, yearMax` | `[CountryGroupProductYear]` |
| `groupCountryProductYear` | `groupId: Int!` | `productClass, servicesClass, productLevel, productId, partnerCountryId, yearMin, yearMax` | `[GroupCountryProductYear]` |

### Product Relatedness (1 query)

| Query | Required Args | Optional Args | Returns |
|-------|---------------|---------------|---------|
| `productProduct` | `productClass: ProductClass!, productLevel: Int!` | — | `[ProductProduct]` |

### Reference / Catalog Data (7 queries)

| Query | Required Args | Optional Args | Returns |
|-------|---------------|---------------|---------|
| `locationCountry` | — | — | `[LocationCountry]` |
| `locationGroup` | — | `groupType` | `[LocationGroup]` |
| `productHs92` | — | `productLevel, servicesClass` | `[Product]` |
| `productHs12` | — | `productLevel, servicesClass` | `[Product]` |
| `productHs22` | — | `productLevel, servicesClass` | `[Product]` |
| `productSitc` | — | `productLevel, servicesClass` | `[Product]` |
| `year` | — | `yearMin, yearMax` | `[Year]` |

### Classification Conversion (3 queries)

| Query | Required Args | Optional Args | Returns |
|-------|---------------|---------------|---------|
| `conversionPath` | `sourceCode: String!, sourceClassification: ClassificationEnum!, targetClassification: ClassificationEnum!` | — | `[ConversionClassifications]` |
| `conversionSources` | `targetCode: String!, targetClassification: ClassificationEnum!, sourceClassification: ClassificationEnum!` | — | `[ConversionClassifications]` |
| `conversionWeights` | — | `sitc1962, sitc1976, sitc1988, hs1992, hs1997, hs2002, hs2007, hs2012, hs2017, hs2022` (all String) | `[ConversionWeights]` |

### Metadata & Diagnostics (6 queries)

| Query | Required Args | Optional Args | Returns |
|-------|---------------|---------------|---------|
| `countryYearThresholds` | `productClass: ProductClass!` | `countryId, yearMin, yearMax` | `[CountryYearThresholds]` |
| `dataFlags` | — | `countryId` | `[DataFlags]` |
| `dataAvailability` | — | — | `[DataAvailability]` |
| `downloadsTable` | — | — | `[DownloadsTable]` |
| `metadata` | — | — | `Metadata` |
| `banner` | — | — | `[Banner]` |

---

## 7. Complete Type Schemas (from introspection)

All fields verified via live introspection, February 2026.

### CountryProductYear (22 fields)

```
countryId: ID
locationLevel: LocationLevel
productId: ID
productLevel: Int
year: Int
exportValue: Float
importValue: Float
globalMarketShare: Float
exportRca: Float
exportRpop: Float
isNew: Boolean
productStatus: ProductStatus          # absent | lost | new | present
cog: Float
distance: Float
normalizedPci: Float
normalizedCog: Float
normalizedDistance: Float
normalizedExportRca: Float
normalizedPciRcalt1: Float
normalizedCogRcalt1: Float
normalizedDistanceRcalt1: Float
normalizedExportRcaRcalt1: Float
```

### CountryYear (18 fields)

```
countryId: ID
year: Int
exportValue: Float
importValue: Float
population: Int
gdp: Float
gdppc: Int
gdpPpp: Float
gdppcPpp: Int
gdpConst: Float
gdpPppConst: Float
gdppcConst: Float
gdppcPppConst: Float
eci: Float
eciFixed: Float
coi: Float
currentAccount: Float
growthProj: Float
```

### ProductYear (11 fields)

```
productId: ID
productLevel: Int
year: Int
exportValue: Float
importValue: Float
exportValueConstGrowth5: Float
importValueConstGrowth5: Float
exportValueConstCagr5: Float
importValueConstCagr5: Float
pci: Float
complexityEnum: ComplexityLevel       # low | moderate | high
```

### CountryCountryYear (7 fields)

```
countryId: ID
partnerCountryId: ID
year: Int
exportValue: Float
exportValueReported: Float
importValue: Float
importValueReported: Float
```

### CountryCountryProductYear (11 fields)

```
countryId: ID
locationLevel: LocationLevel
partnerCountryId: ID
partnerLevel: LocationLevel
productId: ID
productLevel: Int
year: Int
exportValue: Float
importValue: Float
exportValueReported: Float
importValueReported: Float
```

### CountryCountryProductYearGrouped (2 fields)

```
productIds: [ID]
data: [CountryCountryProductYear]
```

### ProductProduct (4 fields)

```
productId: ID
targetId: ID
strength: Float
productLevel: Int
```

### Product (21 fields)

```
productId: ID!
productLevel: Int!
parent: Product
topParent: Product
productIdHierarchy: String
code: String!
productType: ProductType              # good | service
nameEn: String!
nameShortEn: String!
nameEs: String
nameShortEs: String
clusterId: Int
productSpaceX: Int
productSpaceY: Int
legacyProductSpaceX: Int
legacyProductSpaceY: Int
isShown: Boolean
globalExportThreshold: Boolean
showFeasibility: Boolean
naturalResource: Boolean
greenProduct: Boolean
```

### LocationCountry (22 fields)

```
countryId: ID!
locationLevel: LocationLevel!
iso3Code: String!
iso2Code: String
legacyCountryId: Int
nameEn: String!
nameShortEn: String!
nameAbbrEn: String
nameEs: String
nameShortEs: String
thePrefix: Boolean
isTrusted: Boolean
formerCountry: Boolean
incomelevelEnum: IncomeLevel          # high | upper_middle | lower_middle | low
reportedServ: Boolean
reportedServRecent: Boolean
countryProject: Boolean
rankingsOverride: Boolean
cpOverride: Boolean
inRankings: Boolean
inCp: Boolean
inMv: Boolean
```

### LocationGroup (32 fields)

```
groupId: ID!
groupName: String!
groupType: GroupType!
members: [ID]
parentId: ID
parentName: String
parentType: GroupType
legacyGroupId: ID
gdpMean: Float
gdpSum: Float
exportValueMean: Float
exportValueSum: Float
exportValueCagr3: Float
exportValueCagr5: Float
exportValueCagr10: Float
exportValueCagr15: Float
exportValueNonOilCagr3: Float
exportValueNonOilCagr5: Float
exportValueNonOilCagr10: Float
exportValueNonOilCagr15: Float
gdpCagr3: Float
gdpCagr5: Float
gdpCagr10: Float
gdpCagr15: Float
gdpConstCagr3: Float
gdpConstCagr5: Float
gdpConstCagr10: Float
gdpConstCagr15: Float
gdppcConstCagr3: Float
gdppcConstCagr5: Float
gdppcConstCagr10: Float
gdppcConstCagr15: Float
```

### GroupYear (8 fields)

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

### GroupGroupProductYear (11 fields)

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

### CountryGroupProductYear (9 fields)

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

### GroupCountryProductYear (9 fields)

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

### Year (2 fields)

```
year: Int
deflator: Float
```

### Metadata (4 fields)

```
serverName: String
ingestionCommit: String
ingestionDate: String
apiCommit: String
```

### CountryYearThresholds (18 fields)

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

### DataFlags (20 fields)

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

### DataAvailability (3 fields)

```
productClassification: ProductClass
yearMin: Int
yearMax: Int
```

### ConversionClassifications (3 fields)

```
fromClassification: String
toClassification: String
codes: [ConversionCodes]
```

### ConversionWeights (19 fields)

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

### DownloadsTable (17 fields)

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

### Banner (6 fields)

```
bannerId: Int!
startTime: DateTime
endTime: DateTime
text: String
ctaText: String
ctaLink: String
```

---

## 8. Enum Values (from introspection)

| Enum | Values |
|------|--------|
| `ProductClass` | `HS92`, `HS12`, `HS22`, `SITC` |
| `ServicesClass` | `unilateral` |
| `ProductType` | `good`, `service` |
| `ProductStatus` | `absent`, `lost`, `new`, `present` |
| `ComplexityLevel` | `low`, `moderate`, `high` |
| `LocationLevel` | `country`, `group` |
| `IncomeLevel` | `high`, `upper_middle`, `lower_middle`, `low` |
| `GroupType` | `continent`, `political`, `region`, `rock_song`, `subregion`, `trade`, `wdi_income_level`, `wdi_region`, `world` |
| `ClassificationEnum` | `SITC1962`, `SITC1976`, `SITC1988`, `HS1992`, `HS1997`, `HS2002`, `HS2007`, `HS2012`, `HS2017`, `HS2022` |
| `DownloadTableDataType` | `unilateral`, `bilateral`, `product`, `classification`, `product_space`, `rankings` |
| `DownloadTableRepo` | `rankings`, `hs92`, `hs12`, `hs22`, `sitc`, `services_unilateral`, `classification`, `product_space` |
| `DownloadTableFacet` | `CPY`, `CY`, `PY`, `CCY`, `CCPY` |

---

## 9. Example Queries (from official docs)

### 1. Get Country List with Basic Info

```graphql
{
  locationCountry {
    countryId
    iso3Code
    nameEn
  }
}
```

### 2. Get China's Economic Data (2015-2022)

```graphql
{
  countryYear(countryId: 156, yearMin: 2015, yearMax: 2022) {
    year
    gdp
    gdppc
    population
    exportValue
    importValue
    eci
  }
}
```

### 3. Get US Exports in 2022 (4-digit HS92)

```graphql
{
  countryProductYear(
    countryId: 840
    productClass: HS92
    productLevel: 4
    yearMin: 2022
    yearMax: 2022
  ) {
    productId
    exportValue
    exportRca
    globalMarketShare
  }
}
```

### 4. Get HS92 Product Information

```graphql
{
  productHs92(productLevel: 4) {
    productId
    code
    nameEn
    clusterId
    naturalResource
    greenProduct
  }
}
```

### 5. Get Bilateral Trade Between US and China (2015–2022)

```graphql
{
  countryCountryYear(
    countryId: 840
    partnerCountryId: 156
    yearMin: 2015
    yearMax: 2022
  ) {
    year
    exportValue
    importValue
  }
}
```

### 6. Get Regional Trade Groups

```graphql
{
  locationGroup(groupType: continent) {
    groupId
    groupName
    members
    exportValueSum
    gdpSum
  }
}
```

### 7. Get Product Complexity Over Time (Bananas)

```graphql
{
  productYear(productId: 714, productLevel: 4, yearMin: 2015, yearMax: 2022) {
    productId
    year
    pci
    exportValue
  }
}
```

### 8. Get Brazil's Feasibility Metrics

```graphql
{
  countryProductYear(
    countryId: 76
    productLevel: 4
    yearMin: 2020
  ) {
    productId
    year
    exportValue
    exportRca
    distance
    cog
    normalizedPci
  }
}
```

---

## 10. Additional Resources & Citation

### Links (from official docs)

- **Atlas Website:** https://atlas.hks.harvard.edu
- **Data Downloads:** https://atlas.hks.harvard.edu/data-downloads
- **Growth Lab Viz Hub:** https://growthlab.app
- **Growth Lab Website:** https://growthlab.hks.harvard.edu
- **Growth Lab GitHub:** https://github.com/harvard-growth-lab
- **Support:** growthlabtools@hks.harvard.edu

### Citation (for academic use)

> Growth Lab at Harvard University. "The Atlas of Economic Complexity."
> Web application. Harvard Kennedy School. https://atlas.hks.harvard.edu

---

## 11. Comparison: Official Docs vs Our Research

### Is our documentation a strict superset?

**No.** Our documentation covers far more breadth (two APIs, implementation design, working code, etc.), but the official docs contain specific information we had missed or described imprecisely.

### Information in official docs that we lacked or described incorrectly

| # | Gap | Where it matters | Details |
|---|-----|-------------------|---------|
| 1 | **GraphiQL interface URL** | All docs | Official docs explicitly state: navigate to `https://atlas.hks.harvard.edu/api/graphql` in a browser to access the GraphiQL interface with Documentation Explorer. Our docs noted "No GraphiQL URL documented" — this is incorrect. |
| 2 | **M49 codes, not ISO 3166-1 numeric** | `explore_page_collection_guide.md`, `country_page_collection_guide.md` | Official docs say country IDs "correspond to a location's M49 code as designated by the UN" and link to the [UN M49 page](https://unstats.un.org/unsd/methodology/m49/). Our docs say "ISO 3166-1 numeric codes." For most countries these overlap, but M49 is the authoritative reference per the Growth Lab. |
| 3 | **Usage warning for software applications** | `backend_redesign_analysis.md` | Official docs explicitly state the API is "best used to access data for stand-alone economic analysis, **not to support other software applications**." This is directly relevant to our project and should be acknowledged. |
| 4 | **`apiCommit` field on `Metadata` type** | `atlas_explore_pages_exploration.md` | Introspection shows 4 fields on `Metadata` (`serverName`, `ingestionCommit`, `ingestionDate`, `apiCommit`). Our docs only documented the first 3. |
| 5 | **`Banner` type full schema** | `atlas_explore_pages_exploration.md` | 6 fields: `bannerId`, `startTime`, `endTime`, `text`, `ctaText`, `ctaLink`. Our docs said "fields not documented." |
| 6 | **`CountryYearThresholds` statistical fields** | `explore_page_collection_guide.md` | 18 fields including percentiles (10/20/25/30/40/50/60/70/75/80/90), mean, median, min, max, std, variable. Our docs listed the return type but not the fields. |
| 7 | **`DataFlags` detailed fields** | `explore_page_collection_guide.md` | 20 fields including eligibility booleans (`rankingsEligible`, `countryProfilesEligible`), coverage booleans, and population/export thresholds. Our docs listed the return type but not the fields. |
| 8 | **`GroupYear` fields** | `explore_page_collection_guide.md` | 8 fields: `groupId`, `groupType`, `year`, `population`, `gdp`, `gdpPpp`, `exportValue`, `importValue`. Our docs mentioned the query but not its return type fields. |
| 9 | **`GroupGroupProductYear` fields** | `explore_page_collection_guide.md` | 11 fields including `groupType`, `partnerType`, `locationLevel`, `partnerLevel`. Not documented in our files. |
| 10 | **`CountryGroupProductYear` and `GroupCountryProductYear` fields** | `explore_page_collection_guide.md` | 9 fields each. Not documented in our files. |
| 11 | **`ConversionWeights` detailed schema** | `explore_page_collection_guide.md` | 19 fields with explicit weight field names (e.g., `weightSitc1962Sitc1976`, `weightHs2012Hs2017`). Our docs listed the query args but not the return fields. |
| 12 | **`DownloadsTable` full schema** | `explore_page_collection_guide.md` | 17+ fields including Harvard Dataverse fields (`dvFileId`, `dvFileName`, `dvFileSize`, `dvPublicationDate`, `doi`). Not documented. |
| 13 | **Support email** | General | `growthlabtools@hks.harvard.edu` for issues not covered in available resources. |
| 14 | **Data downloads page** | General | `https://atlas.hks.harvard.edu/data-downloads` explicitly recommended for bulk data instead of API. |
| 15 | **Growth Lab Viz Hub** | General | `https://growthlab.app` — additional resource link. |
| 16 | **Official citation format** | General | Specific citation format for academic use. |

### Information in our research that goes beyond official docs

Our research-based documentation provides extensive information **not covered** by the official docs:

- **Country Pages API** (`/api/countries/graphql`) — entirely undocumented officially, including `countryProfile` (46 fields), `treeMap` with facets, `countryLookback`, policy enums, etc.
- **Staging endpoint** (`staging.atlas.growthlab-dev.com`) — not mentioned in official docs
- **Internal product ID ↔ HS code mappings** — 8 verified mappings
- **URL parameter → API argument mappings** — how Atlas website URLs translate to API queries
- **API ↔ website component mappings** — which queries power which visualizations
- **Bilateral query selection logic** — how exporter/importer types determine query choice
- **ID format differences between APIs** — `countryId: 404` (Explore) vs `"location-404"` (Country Pages)
- **Broken queries** — 10 confirmed non-functional queries in the Country Pages API
- **Services behavior** — how `servicesClass: unilateral` affects results
- **Product space cluster labels** — 8 cluster labels vs 11 sector names
- **Implementation design** — full pipeline design for `atlas_graphql` tool
