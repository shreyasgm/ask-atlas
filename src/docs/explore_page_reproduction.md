# Explore Page Reproduction Guide

**Purpose:** Reference for reproducing the 7 Atlas Explore page visualizations using GraphQL queries or equivalent SQL, including exact query names, field lists, argument mappings, and known data quirks.

**When to load this document:** Load when the agent is constructing a GraphQL query for the Explore API
(`/api/graphql`) and needs to know which query name and fields correspond to
a specific Explore page visualization (treemap, geomap, overtime, market share,
product space, feasibility scatter, or feasibility table). Prefer this API over
the Country Pages API when the same data is available in both. Do NOT load for
Country Pages API queries (see `country_page_reproduction.md`) or for
understanding what metrics mean (see `metrics_glossary.md`).

---

## General Notes

- **Endpoint:** `POST https://atlas.hks.harvard.edu/api/graphql` — no authentication required, rate limit 120 req/min.
- **Default classification:** HS92, 4-digit product level (`productLevel: 4`).
- **Country IDs:** Numeric integers corresponding to M49 codes (e.g., Kenya = 404, USA = 840, China = 156, Brazil = 76).
- **Year range:** HS92 1995–2024; HS12 2012–2024; HS22 2022–2024; SITC 1962–2024.
- **Services:** Included in Products mode (goods + services total), excluded from Locations mode (goods only — no bilateral services data). The services classification argument is `servicesClass: unilateral`.
- **All Explore pages require a country/group exporter.** There is no "World" default; the site defaults to an arbitrary country if none is specified.

---

## URL Structure

```
Base: https://atlas.hks.harvard.edu/explore/{vizType}
```

| vizType | Sidebar Label | URL Example |
|---------|--------------|-------------|
| `treemap` | TRADE COMPOSITION | `/explore/treemap?year=2024&exporter=country-404` |
| `geomap` | TRADE MAP | `/explore/geomap?year=2024&exporter=country-404` |
| `overtime` | TRADE OVER TIME | `/explore/overtime?startYear=1995&endYear=2024&exporter=country-404` |
| `marketshare` | GLOBAL SHARE | `/explore/marketshare?startYear=1995&endYear=2024&exporter=country-404` |
| `productspace` | PRODUCT SPACE | `/explore/productspace?year=2024&exporter=country-404` |
| `feasibility` | GROWTH OPPORTUNITY | `/explore/feasibility?year=2024&exporter=country-404` |
| `feasibility/table` | (Table View toggle) | `/explore/feasibility/table?year=2024&exporter=country-404&productLevel=4` |

### URL Parameter to GraphQL Argument Mapping

| URL Parameter | GraphQL Argument | Transformation |
|---------------|-----------------|----------------|
| `exporter=country-404` | `countryId: 404` | Strip `country-` prefix, parse int |
| `exporter=group-5` | `groupId: 5` | Strip `group-` prefix, parse int |
| `importer=country-840` | `partnerCountryId: 840` | Strip `country-` prefix, parse int |
| `importer=group-1` | *(omit partner filter)* | `group-1` = World = no partner constraint |
| `year=2024` | `yearMin: 2024, yearMax: 2024` | Single year → min=max |
| `startYear=1995` | `yearMin: 1995` | Direct mapping |
| `endYear=2024` | `yearMax: 2024` | Direct mapping |
| `product=product-HS92-726` | `productId: 726` | Strip `product-HS92-` prefix, parse int |
| `productLevel=4` | `productLevel: 4` | Direct mapping |
| `view=markets` | *(switch to `countryCountryYear`)* | Locations mode uses different query |
| `tradeDirection=imports` | *(no arg change)* | Changes which field to read: `importValue` instead of `exportValue` |

### Constructing an Explore URL from API Results

```
https://atlas.hks.harvard.edu/explore/{vizType}
  ?year={yearMax}
  &exporter=country-{countryId}
  [&importer=country-{partnerCountryId}]   # bilateral
  [&product=product-HS92-{productId}]      # product-filtered
  [&startYear={yearMin}&endYear={yearMax}] # time series pages
  [&productLevel={productLevel}]           # feasibility/table
  [&view=markets]                          # locations mode
  [&tradeDirection=imports]                # import flow
```

---

## 1. Treemap — Trade Composition (`/explore/treemap`)

**Question answered:** "What did {Country} export in {year}?" (Products mode) / "Where did {Country} export to in {year}?" (Locations mode)

### Products Mode (default)

**Primary query:** `countryProductYear`

```graphql
{
  countryProductYear(
    countryId: 404        # Kenya
    productClass: HS92
    productLevel: 4
    yearMin: 2024
    yearMax: 2024
  ) {
    productId
    exportValue
    importValue
    exportRca
    distance
    normalizedPci
    globalMarketShare
    cog
    productStatus
    isNew
  }
}
```

**Supporting queries at page load:** `productHs92(productLevel: 4)` for product names + sector grouping, `locationCountry` for country names, `year` for deflators.

**Key fields:**
- Rectangle size: `exportValue`
- Sector color: derived from `productHs92.topParent` (11 sectors: Services, Textiles, Agriculture, Stone, Minerals, Metals, Chemicals, Vehicles, Machinery, Electronics, Other)
- Tooltip expanded: `exportRca`, `distance`, `normalizedPci`
- Total shown includes goods AND services

**SQL equivalent:**
```sql
SELECT p.name_short_en, p.code, p.top_parent_id,
       cpy.export_value, cpy.export_rca, cpy.distance, cpy.normalized_pci
FROM hs92.country_product_year_4 cpy
JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
WHERE cpy.country_id = 404 AND cpy.year = 2024
ORDER BY cpy.export_value DESC;
```

### Locations Mode (`?view=markets`)

**Primary query:** `countryCountryYear`

```graphql
{
  countryCountryYear(
    countryId: 404
    yearMin: 2024
    yearMax: 2024
  ) {
    partnerCountryId
    exportValue
    importValue
  }
}
```

**Key difference from Products mode:** Total shown is **goods only** — the bilateral data excludes services. For Kenya, this is approximately $8.2B vs $16B in Products mode.

**SQL equivalent:**
```sql
SELECT lc.name_short_en, ccy.export_value
FROM hs92.country_country_year ccy
JOIN classification.location_country lc ON ccy.partner_country_id = lc.country_id
WHERE ccy.country_id = 404 AND ccy.year = 2024
ORDER BY ccy.export_value DESC;
```

---

## 2. Geomap — Trade Map (`/explore/geomap`)

**Question answered:** "Where did {Country} export to?" (choropleth world map)

**Primary query:** `countryCountryYear` — identical to Treemap Locations mode.

```graphql
{
  countryCountryYear(
    countryId: 404
    yearMin: 2024
    yearMax: 2024
  ) {
    partnerCountryId
    exportValue
    importValue
  }
}
```

**Key characteristics:**
- Locations mode only — Products mode is disabled on this page.
- Color scale encodes trade value intensity (continuous gradient from ~$10k to $1B).
- Total is goods only (same caveat as Treemap Locations mode).
- No Products/Locations toggle — single view.
- Product filter available: user can filter map to a specific product, which switches the underlying query to `countryCountryProductYear(productId: {id})`.

---

## 3. Trade Over Time (`/explore/overtime`)

**Question answered:** "What did {Country} export, {startYear}–{endYear}?" (stacked area chart)

### Products Mode

**Primary query:** `countryProductYear` across full year range

```graphql
{
  countryProductYear(
    countryId: 404
    productClass: HS92
    productLevel: 4
    yearMin: 1995
    yearMax: 2024
  ) {
    productId
    year
    exportValue
    importValue
  }
}
```

**Supporting queries:** `countryYear(countryId: 404)` for population (per-capita metrics), `year(yearMin: 1995, yearMax: 2024) { year deflator }` for constant-dollar conversion.

### Y-axis Metric Options

| Display Label | Calculation | Required Fields |
|---------------|-------------|-----------------|
| Current Gross Exports | `exportValue` (raw) | `countryProductYear.exportValue` |
| Constant (2024 USD) | `exportValue / year.deflator` | + `year.deflator` |
| Per Capita | `exportValue / population` | + `countryYear.population` |
| Per Capita Constant (2024 USD) | `(exportValue / deflator) / population` | + both above |

**Grouping:** Areas stacked by sector using `productHs92.topParent`.

### Locations Mode

**Primary query:** `countryCountryYear` across year range:

```graphql
{
  countryCountryYear(
    countryId: 404
    yearMin: 1995
    yearMax: 2024
  ) {
    partnerCountryId
    year
    exportValue
  }
}
```

Areas stacked by region. Note the goods-only total caveat applies.

---

## 4. Global Market Share (`/explore/marketshare`)

**Question answered:** "{Country}'s global market share by sector, {startYear}–{endYear}" (multi-line chart)

**Requires two queries joined by product and year:**

```graphql
# Query 1 — country's sector exports over time
{
  countryProductYear(
    countryId: 404
    productClass: HS92
    productLevel: 4
    yearMin: 1995
    yearMax: 2024
  ) {
    productId
    year
    exportValue
  }
}

# Query 2 — world sector totals over time
{
  productYear(
    productClass: HS92
    productLevel: 4
    yearMin: 1995
    yearMax: 2024
  ) {
    productId
    year
    exportValue
  }
}
```

**Calculation:**

```
market_share(sector, year) =
  SUM(countryProductYear.exportValue WHERE topParent = sector)
  / SUM(productYear.exportValue WHERE topParent = sector)
  × 100%
```

**SQL equivalent:**

```sql
SELECT p.top_parent_id AS sector,
       cpy.year,
       SUM(cpy.export_value) / NULLIF(SUM(py.export_value), 0) AS market_share
FROM hs92.country_product_year_4 cpy
JOIN hs92.product_year_4 py
  ON cpy.product_id = py.product_id AND cpy.year = py.year
JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
WHERE cpy.country_id = 404
GROUP BY p.top_parent_id, cpy.year
ORDER BY cpy.year, market_share DESC;
```

**Key characteristics:**
- No Products/Locations toggle — single-perspective view.
- No Importer dropdown.
- One line per sector, colored by the same 11-sector palette.
- `productYear` query has no `countryId` filter — it returns global totals.

---

## 5. Product Space (`/explore/productspace`)

**Question answered:** "{Country} in the Product Space, {year}" (network graph of product relatedness)

**Three query types required:**

```graphql
# 1. Country's RCA per product (for node coloring)
{
  countryProductYear(
    countryId: 404
    productClass: HS92
    productLevel: 4
    yearMin: 2024
    yearMax: 2024
  ) {
    productId
    exportRca
    exportValue
  }
}

# 2. Product-to-product relatedness (for network edges)
{
  productProduct(
    productClass: HS92
    productLevel: 4
  ) {
    productId
    targetId
    strength
  }
}

# 3. Product catalog (for node positions)
{
  productHs92(productLevel: 4) {
    productId
    nameShortEn
    code
    clusterId
    productSpaceX
    productSpaceY
  }
}
```

**Node rendering logic:**
- **Colored node** (sector color): `exportRca >= 1` — country has comparative advantage
- **Grey node:** `exportRca < 1` — no comparative advantage
- **Node size:** Based on `productYear.exportValue` (global trade value of that product)
- **Node position:** Fixed coordinates from `productHs92.productSpaceX` and `productHs92.productSpaceY`

**Edge rendering:** `productProduct.strength` — higher strength = stronger connection drawn between nodes. Edges represent co-export proximity (probability that countries exporting one product also export the other).

**Cluster labels (8 clusters in product space, different from the 11 treemap sectors):**
Agricultural Goods, Construction Goods, Electronics, Chemicals and Basic Metals, Metalworking and Machinery, Minerals, Textile and Home Goods, Apparel

**Important distinction:** The product space uses 8 `clusterId` clusters for color/grouping, while the treemap uses 11 `topParent` sectors. These are different classification axes.

**SQL equivalent (nodes + RCA):**
```sql
SELECT p.product_id, p.name_short_en, p.cluster_id,
       p.product_space_x, p.product_space_y,
       cpy.export_rca
FROM classification.product_hs92 p
LEFT JOIN hs92.country_product_year_4 cpy
  ON p.product_id = cpy.product_id
  AND cpy.country_id = 404
  AND cpy.year = 2024
WHERE p.product_level = 4;

-- Edges:
SELECT product_id, target_id, strength
FROM hs92.product_product_4;
```

---

## 6. Growth Opportunity — Scatter (`/explore/feasibility`)

**Question answered:** "Growth Opportunities for {Country}, {year}" (scatter plot of unexploited products)

**Filter:** Only products where `exportRca < 1` (country does NOT have comparative advantage — these are the "opportunity" products).

**Requires two queries:**

```graphql
# 1. Country-level feasibility data
{
  countryProductYear(
    countryId: 404
    productClass: HS92
    productLevel: 4
    yearMin: 2024
    yearMax: 2024
  ) {
    productId
    exportRca
    distance
    cog
    normalizedPci
    exportValue
  }
}

# 2. Global product trade value (for bubble sizing)
{
  productYear(
    productClass: HS92
    productLevel: 4
    yearMin: 2024
    yearMax: 2024
  ) {
    productId
    exportValue
    pci
  }
}
```

**Plus `countryYear` for the ECI reference line:**
```graphql
{
  countryYear(countryId: 404, yearMin: 2024, yearMax: 2024) {
    eci
  }
}
```

**Axis mapping:**
- **X-axis (Distance):** `countryProductYear.distance` — labeled "More Nearby ◄" to "Less Nearby ►". Lower distance = product is closer to country's existing capabilities.
- **Y-axis (dual):** `countryProductYear.cog` (Opportunity Gain, top) AND `countryProductYear.normalizedPci` (Product Complexity, bottom). Both plotted on the same vertical axis. Range approximately -3.5 to 2.5.
- **Bubble size:** `productYear.exportValue` (global trade value of the product)
- **Bubble color:** Sector from `productHs92.topParent`
- **Reference line:** Dashed horizontal at `countryYear.eci` value

**"Total value" header:** Sum of `exportValue` for opportunity products only (RCA < 1), not total country exports. For Kenya 2024, this is approximately $947M.

**SQL equivalent:**
```sql
SELECT p.name_short_en, p.code, p.top_parent_id,
       cpy.distance, cpy.cog, cpy.normalized_pci,
       py.export_value AS global_size,
       cy.eci
FROM hs92.country_product_year_4 cpy
JOIN hs92.product_year_4 py
  ON cpy.product_id = py.product_id AND cpy.year = py.year
JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
CROSS JOIN (
  SELECT eci FROM hs92.country_year
  WHERE country_id = 404 AND year = 2024
) cy
WHERE cpy.country_id = 404
  AND cpy.year = 2024
  AND cpy.export_rca < 1
ORDER BY cpy.normalized_pci DESC;
```

**Key characteristics:**
- Services are excluded — legend shows "PRODUCT SECTORS" without Services.
- Available for ALL countries (unlike Country Pages' growth-opportunities, which is hidden for the highest-complexity frontier economies).
- No Products/Locations toggle.

---

## 7. Growth Opportunity — Table (`/explore/feasibility/table`)

**Question answered:** Same as scatter — ranked list of growth opportunity products.

**URL:** `/explore/feasibility/table?year=2024&exporter=country-404&productLevel=4`

**Same underlying queries as the scatter** (`countryProductYear` + `productYear`). The table is the only Explore visualization rendered as DOM-accessible HTML (not canvas).

### Table Columns

| Column | API Source Field | Notes |
|--------|-----------------|-------|
| Product Name + HS code | `productHs92.nameEn` + `productHs92.code` | e.g., "Photographic film, developed (3705 HS)" |
| "Nearby" Distance | `countryProductYear.distance` | Diamond rating (7 diamonds); inverted display — more diamonds = closer (lower distance) |
| Opportunity Gain | `countryProductYear.cog` | Diamond rating (7 diamonds) |
| Product Complexity | `countryProductYear.normalizedPci` | Diamond rating (7 diamonds) |
| Global Size (USD) | `productYear.exportValue` | Dollar amount of global trade in that product |
| Global Growth 5 YR | `productYear.exportValueConstCagr5` | Percentage with ↑/↓ arrow; 5-year constant-dollar CAGR |

**Default sort:** Product Complexity (`normalizedPci` descending)

**Scope:** All products where `exportRca < 1`. Not limited — shows all opportunity products (unlike Country Pages' product-table which caps at Top 50 and is unavailable for frontier economies).

---

## Settings That Affect All Visualizations

| Setting | GraphQL Argument | Options | Default |
|---------|-----------------|---------|---------|
| Detail Level | `productLevel` | `2`, `4`, `6` | `4` |
| Trade Flow | *(read field)* | Gross (`exportValue`), Net (`exportValue - importValue`) | Gross |
| Product Class | `productClass` | `HS92`, `HS12`, `HS22`, `SITC` | `HS92` |
| Color by | *(client-side)* | Sector, Complexity, Entry Year | Sector |

Note: `HS22` has data only from 2022–2024. `SITC` extends back to 1962.

---

## Bilateral Query Selection Logic

When both an exporter and importer are specified, the query type depends on entity types:

| Exporter | Importer | Query |
|----------|----------|-------|
| Country | World (`group-1`) | `countryProductYear` (no partner filter) |
| Country | Country | `countryCountryProductYear` |
| Country | Group | `countryGroupProductYear(partnerGroupId: {id}!)` |
| Group | Country | `groupCountryProductYear(groupId: {id}!)` |
| Group | Group | `groupGroupProductYear` |

---

## Known Discrepancies and Quirks

### Goods vs. Goods+Services Totals

The most common source of confusion: the total export value shown on Explore pages varies by mode and visualization.

| Page / Mode | Total Value Includes | Example (Kenya 2024) |
|-------------|---------------------|----------------------|
| Treemap — Products mode | Goods + services | ~$16B |
| Treemap — Locations mode | Goods only | ~$8.2B |
| Geomap | Goods only | ~$8.2B |
| Product Space | Goods only | ~$7.9B |
| Feasibility graph | Goods only (opportunity products) | ~$947M |

The discrepancy between Products and Locations totals is not an error — bilateral trade data (`countryCountryYear`) does not include services, while the product trade data (`countryProductYear`) does when `servicesClass: unilateral` is included.

### Feasibility "Total Value" Is Not Export Total

The header value on the feasibility pages shows the **sum of opportunity product values**, not total country exports. Only products with `exportRca < 1` are included.

### Product IDs Are Internal, Not HS Codes

URL parameter `product-HS92-726` refers to internal Atlas product ID 726, not HS code 726. The mapping must be resolved via `productHs92 { productId code }`. Known examples:

| HS92 Code | Internal Product ID | Product |
|-----------|--------------------|---------|
| 0901 | 726 | Coffee |
| 0902 | 727 | Tea |
| 2601 | 1506 | Iron ores |
| 2710 | 1584 | Petroleum oils, refined |
| 3004 | 1748 | Medicaments |
| 6109 | 2801 | T-shirts |
| 8542 | 3595 | Electronic integrated circuits |
| 8703 | 3667 | Cars |

### COG vs. normalizedPci on the Feasibility Y-axis

The feasibility scatter labels its Y-axis as both "Opportunity Gain" (top) and "Product Complexity" (bottom). These are two separate fields:
- **Opportunity Gain = `cog`** (Complexity Outlook Gain) — strategic value of adding the product to the country's basket
- **Product Complexity = `normalizedPci`** — complexity of the product itself

Both are plotted on the same vertical axis.

### HS22 Availability

HS22 (`productClass: HS22`) is supported in the Explore GraphQL API but is **not available** in the SQL database or the Country Pages API. Restricts to 2022–2024 data only.

### Canvas vs. DOM Rendering

Treemap, product space, geomap, and feasibility graph are canvas-rendered — no programmatic access to individual data points via browser DOM. Only the feasibility **table** view (`/explore/feasibility/table`) renders as accessible HTML. All data is available via the GraphQL API regardless.

---

## CountryProductYear Full Field Reference

The richest query type in the Explore API — 22 fields returned per country × product × year row:

```
countryId            # M49 integer ID
locationLevel        # "country" or "group"
productId            # Internal Atlas product ID
productLevel         # 2, 4, or 6
year                 # Integer year
exportValue          # Current USD
importValue          # Current USD
globalMarketShare    # Country's share of world exports for this product
exportRca            # Revealed Comparative Advantage (Balassa index)
exportRpop           # RCA relative to population
isNew                # Boolean: gained RCA >= 1 vs lookback year
productStatus        # "absent" | "lost" | "new" | "present"
cog                  # Complexity Outlook Gain (strategic value)
distance             # Distance from country's capability frontier (0=close, 1=far)
normalizedPci        # Normalized Product Complexity Index (within-year)
normalizedCog        # Normalized COG
normalizedDistance   # Normalized distance
normalizedExportRca  # Normalized RCA
normalizedPciRcalt1          # normalizedPci computed only for RCA < 1 products
normalizedCogRcalt1          # normalizedCog for RCA < 1 products
normalizedDistanceRcalt1     # normalizedDistance for RCA < 1 products
normalizedExportRcaRcalt1    # normalizedExportRca for RCA < 1 products
```

The `*Rcalt1` variants are computed only over the subset of products where `exportRca < 1`, providing within-feasibility-set normalization.
