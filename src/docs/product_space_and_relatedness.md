# Product Space and Relatedness

**Purpose:** Technical reference for product proximity, distance, density, COG/COI metrics, the 8-cluster product space visualization, and the Growth Opportunity feasibility scatter — all as implemented in the Atlas of Economic Complexity.

**When to load this document:** Load when the user asks which products a country should diversify into, how
nearby or feasible a product is for a country, what proximity/distance/density
means as a strategic concept, how the Growth Opportunity feasibility scatter
is constructed, or what the 8 product space clusters represent. Also load for
`productProduct` query usage or `product_product_4` table structure. For
country-level diversification strategy context, also load `strategic_approaches.md`.
Do NOT load for metric formula derivations alone (see `metrics_glossary.md`) or
for reproducing the product space visualization (see `explore_page_reproduction.md`).

---

## Conceptual Foundation

The product space encodes the insight that **countries diversify by moving into products that require similar capabilities to what they already produce** — the "adjacent possible." Products that share underlying know-how, infrastructure, institutions, or factor endowments tend to be co-exported by the same countries. The product space network makes this latent capability structure visible.

The pipeline: compute proximity (φ) → threshold → lay out in 2D → overlay per-country RCA to color nodes.

---

## 1. Proximity (φ) — Product-to-Product Relatedness

### Definition

Proximity between products i and j is the **minimum conditional co-export probability**:

$$\phi_{ij} = \min\left\{ P(\text{RCA}_i \geq 1 \mid \text{RCA}_j \geq 1),\; P(\text{RCA}_j \geq 1 \mid \text{RCA}_i \geq 1) \right\}$$

Equivalently, in terms of the binary RCA matrix M (where M_cp = 1 if country c exports product p with RCA ≥ 1):

$$\phi_{ij} = \frac{\sum_c M_{ci} \cdot M_{cj}}{\max\left(\sum_c M_{ci},\; \sum_c M_{cj}\right)} = \frac{C_{ij}}{\max(k_{i,0},\; k_{j,0})}$$

where C_ij is the count of countries that export both i and j, and k_{p,0} is the ubiquity of product p.

**Why the minimum?** If product A is exported by 100 countries and product B by only 10, then P(A|B) may be high while P(B|A) is low. Taking the minimum prevents artificially inflated proximity driven by asymmetric ubiquity, and ensures symmetry: φ_ij = φ_ji.

**Note:** The Atlas glossary states proximity uses the minimum conditional probability. The formula above (dividing by the maximum ubiquity) is the computational equivalent of taking that minimum.

### Properties

| Property | Value |
|---|---|
| Range | 0 to 1 |
| Symmetry | φ_ij = φ_ji (symmetric) |
| Interpretation | Higher = more likely to be co-exported = more related capabilities |
| Threshold for network edges | φ ≥ 0.3 (standard; retains ~2,000 edges across ~865 nodes at HS4 level) |
| Fixed globally | Yes — computed from many countries' export histories; does not change per country viewed |

### Example Values

| Pair | Proximity (φ) | Explanation |
|---|---|---|
| Men's shirts ↔ Women's blouses | 0.64 | Nearly always co-exported; same textile capabilities |
| Passenger cars ↔ Vehicle parts | 0.61 | Shared automotive industry capabilities |
| Wine ↔ Vermouth | 0.58 | Same wine-producing regions and knowledge |
| Coffee ↔ Integrated circuits | 0.03 | Essentially unrelated capabilities |
| Bananas ↔ Aircraft | 0.02 | No shared productive know-how |

### Database and API

| Source | Location | Key Fields |
|---|---|---|
| SQL table | `{schema}.product_product_4` (e.g., `hs92.product_product_4`) | `product_id`, `target_id`, `strength`, `product_level` |
| GraphQL query | `productProduct(productClass: HS92, productLevel: 4)` | Returns `productId`, `targetId`, `strength`, `productLevel` |
| Schema | `hs92`, `sitc` (proximity is not available in `hs12` or `hs22` schemas) |
| Level | Only available at 4-digit product level |

```graphql
# Example: get all product-product relatedness strengths
{
  productProduct(productClass: HS92, productLevel: 4) {
    productId
    targetId
    strength
    productLevel
  }
}
```

The field `strength` in the API corresponds to proximity φ in the formulas. No country filter is available — the query always returns all pairs globally.

---

## 2. The Product Space Network

### Construction

1. Compute the ~865×865 (HS4 level) proximity matrix from historical co-export patterns across 128 countries over 50 years.
2. Threshold: retain only edges where φ ≥ 0.3 (~2,000 edges remain).
3. Project to 2D using a force-directed layout (original Hidalgo et al. 2007 approach) or UMAP. The resulting coordinates are **fixed globally** — node positions do not change per country.

### Node Positions (API / DB)

Product catalog tables carry fixed 2D coordinates for the product space visualization:

| Column | Description |
|---|---|
| `product_space_x` / `productSpaceX` | X-coordinate in the 2D layout |
| `product_space_y` / `productSpaceY` | Y-coordinate in the 2D layout |
| `legacy_product_space_x` / `legacyProductSpaceX` | Older layout (preserved for backward compatibility) |
| `legacy_product_space_y` / `legacyProductSpaceY` | Older layout |

**SQL location:** `classification.product_hs92` (columns: `product_space_x`, `product_space_y`)
**GraphQL location:** `productHs92(productLevel: 4)` → fields `productSpaceX`, `productSpaceY`

### Core vs. Periphery Structure

| Zone | Products | Typical PCI | Diversification |
|---|---|---|---|
| Dense core | Electronics, Machinery, Chemicals, Metals | High (positive) | Easy — many nearby products |
| Sparse periphery | Agriculture, Raw materials, Petroleum, Simple textiles | Low (negative) | Hard — few stepping-stone products |

Rich countries preferentially occupy the dense core. Poor countries tend to be concentrated at the periphery. The "periphery trap": countries whose exports sit at the periphery face long, capability-building leaps to reach the core.

---

## 3. The 8 Product Space Clusters

The Atlas groups products into **8 clusters** based on network community detection. These differ from the 11 treemap sectors used in Trade Composition views.

| # | Cluster Name | `cluster_id` | Color in Atlas | Typical Products |
|---|---|---|---|---|
| 1 | Agricultural Goods | 1 | Yellow | Food, beverages, animal/vegetable products |
| 2 | Construction Goods | 2 | Orange | Building materials, wood, cement, glass |
| 3 | Electronics | 3 | Light blue | Computers, telecom equipment, semiconductors |
| 4 | Chemicals and Basic Metals | 4 | Purple | Chemical compounds, basic metal products |
| 5 | Metalworking and Machinery | 5 | Red/Pink | Industrial machinery, tools, vehicle parts |
| 6 | Minerals | 6 | Brown | Petroleum, ores, mining products |
| 7 | Textile and Home Goods | 7 | Grey/Dark | Fabrics, home furnishings, paper |
| 8 | Apparel | 8 | Green | Garments, footwear, accessories |

**SQL:** `classification.product_hs92.cluster_id`
**GraphQL:** `productHs92(productLevel: 4)` → field `clusterId`

**Important distinction:** These 8 clusters are used in the product space visualization. The 11 treemap sectors (Services, Textiles, Agriculture, Stone, Minerals, Metals, Chemicals, Vehicles, Machinery, Electronics, Other) are used in the Trade Composition treemap and are based on `top_parent_id`. Do not conflate these two classification systems.

### Additional Product-Level Flags

| Flag | SQL Column | GraphQL Field | Meaning |
|---|---|---|---|
| Natural resource | `natural_resource` | `naturalResource` | True for commodities, extractives, raw materials |
| Green product | — | `greenProduct` | True for environmentally relevant products |
| Show feasibility | `show_feasibility` | `showFeasibility` | Whether this product appears in the growth opportunity scatter |

---

## 4. Country Position in the Product Space

A country's position is determined by its current export basket overlaid on the fixed network:

| Visual element | Condition | Metric |
|---|---|---|
| Colored node | Country exports product with RCA ≥ 1 | `export_rca ≥ 1` in `country_product_year` |
| Grey node | Country does not have comparative advantage | `export_rca < 1` |
| Node size | Proportional to global trade value of that product | `product_year.export_value` |
| Node color | Cluster membership | `cluster_id` |

**SQL for coloring:**
```sql
SELECT
  p.product_id,
  p.product_space_x,
  p.product_space_y,
  p.cluster_id,
  cpy.export_rca
FROM classification.product_hs92 p
JOIN hs92.country_product_year_4 cpy
  ON p.product_id = cpy.product_id
WHERE cpy.country_id = 404   -- Kenya
  AND cpy.year = 2023;
```

**Country Pages API:** The `/countries/{id}/paths` subpage (`productSpace(location: "location-{id}")`) returns RCA, x/y coordinates, and edge connections per product for that country.

---

## 5. Distance — How Far Is a Country from a Product?

### Formula (Atlas Glossary / Official Definition)

Distance measures how far a country's current capabilities are from a given product. The official Atlas formula sums proximity to products the country is **not** currently exporting:

$$d_{cp} = \frac{\sum_{p'} (1 - M_{cp'}) \cdot \phi_{p,p'}}{\sum_{p'} \phi_{p,p'}}$$

where:
- M_cp' = 1 if country c exports product p' with RCA ≥ 1 (0 otherwise)
- φ_pp' = proximity between the target product p and product p'
- The sum in the numerator runs over products the country does **not** export; the denominator normalizes by total proximity connecting product p to all other products

**Equivalent formulation:** d_cp = 1 − ρ_cp (distance equals 1 minus density; see Section 6).

### Interpretation

| Distance | Meaning | Typical situation |
|---|---|---|
| Close to 0 | Country already has nearly all related capabilities | Country is on the verge of gaining RCA in this product |
| Close to 1 | Country lacks most related capabilities | Product is far from the current export basket |
| 0.65–0.95 | Typical range on the Atlas feasibility scatter | Most opportunity products for most countries |

**Key rule:** Lower distance = more feasible = lower risk. The X-axis of the feasibility scatter is labeled "More Nearby ◄ → Less Nearby ►" (left is closer = lower distance value).

### Database and API

| Source | Column / Field |
|---|---|
| SQL table | `hs92.country_product_year_4.distance` |
| GraphQL | `countryProductYear.distance` |
| Normalized variant | `normalizedDistance` (rescaled 0–1 relative to other products for that country-year) |
| Normalized `Rcalt1` variant | `normalizedDistanceRcalt1` (computed using RCA < 1 as threshold) |

```graphql
# Distance for Brazil's opportunity products (2023)
{
  countryProductYear(
    countryId: 76
    productClass: HS92
    productLevel: 4
    yearMin: 2023
    yearMax: 2023
  ) {
    productId
    exportRca
    distance
    normalizedDistance
  }
}
```

---

## 6. Density — The Complement of Distance

### Formula

Density measures how connected a product is to what a country already exports:

$$\rho_{cp} = \frac{\sum_{p'} M_{cp'} \cdot \phi_{p,p'}}{\sum_{p'} \phi_{p,p'}}$$

- Numerator: sum of proximity to products the country **does** export (M_cp' = 1)
- Denominator: total proximity connecting product p to all other products
- Identity: ρ_cp = 1 − d_cp

### Interpretation

| Density | Meaning |
|---|---|
| High (close to 1) | Country already exports most related products — high feasibility |
| Low (close to 0) | Country lacks most related products — low feasibility |

Density is not stored as a separate column in the Atlas database; it can be derived as `1 - distance`. The term "density" appears in some Atlas explanatory text; the stored metric is `distance`.

---

## 7. Opportunity Gain (COG — Complexity Outlook Gain)

### Definition

Opportunity Gain (also called Complexity Outlook Gain, COG) measures the **strategic value** of a product as a stepping stone. Specifically: how much a country could benefit in opening future diversification paths by developing product p.

### Formula (from Atlas Glossary)

$$\text{OG}_{cp} = \sum_{p'} \frac{\phi_{p,p'}}{\sum_{p''} \phi_{p'',p'}} \cdot (1 - M_{cp'}) \cdot \text{PCI}_{p'}$$

where:
- φ_pp' = proximity between the candidate product p and each other product p'
- Σ_p'' φ_p''p' = total proximity connecting product p' to all products (normalization term)
- (1 − M_cp') = counts only products the country is **not** currently producing
- PCI_p' = Product Complexity Index of product p'

### Interpretation

| COG value | Meaning |
|---|---|
| High | Product is a "hub" — gaining it opens bridges to many complex products the country doesn't yet export |
| Low | Product is isolated — gaining it doesn't unlock many new capabilities |

**Analogy:** COG is like asking "if I step onto this stepping stone, how many more stepping stones become reachable?" Hub products with high COG unlock entire new neighborhoods of the product space.

**The inverted-U pattern for COI (country-level COG):** Countries at the middle of the ECI spectrum often have the highest COI. The most complex economies (Japan, Germany) have low COI because they've already captured nearby opportunities. The least complex have low COI because they're too far from complex products. The sweet spot: mid-complexity countries like Spain, Portugal, India, Turkey.

### Database and API

| Source | Column / Field |
|---|---|
| SQL table | `hs92.country_product_year_4.cog` |
| GraphQL | `countryProductYear.cog` |
| Normalized variant | `countryProductYear.normalizedCog` |

---

## 8. Complexity Outlook Index (COI) — Country-Level Strategic Position

### Definition

COI summarizes a country's overall strategic position: **how many complex products are near a country's current productive capabilities?**

### Formula (from Atlas Glossary)

$$\text{COI}_c = \sum_p (1 - d_{cp}) \cdot (1 - M_{cp}) \cdot \text{PCI}_p$$

which is equivalent to:

$$\text{COI}_c = \sum_p \rho_{cp} \cdot (1 - M_{cp}) \cdot \text{PCI}_p$$

where:
- (1 − d_cp) = ρ_cp = density (closeness to product p)
- (1 − M_cp) = counts only products the country does NOT yet export
- PCI_p = Product Complexity Index of product p

### Interpretation

| COI | Meaning |
|---|---|
| High | Country is well-positioned — many complex products are within reach |
| Low | Country is isolated from complex products — diversification will be harder |

COI is one of the four inputs to the Atlas growth projections (alongside ECI, GDP per capita, and expected natural resource exports per capita).

### Database and API

| Source | Column / Field |
|---|---|
| SQL table | `hs92.country_year.coi` |
| GraphQL | `countryYear.coi` |
| Country Pages top-bar | COI rank displayed on `/countries/{id}/paths` subpage (e.g., "8th of 145") |

```graphql
# Get COI and ECI for Kenya
{
  countryYear(countryId: 404, yearMin: 2023, yearMax: 2023) {
    year
    eci
    coi
    growthProj
  }
}
```

---

## 9. The Feasibility Scatter Plot (Growth Opportunity)

### Axes and Data Mapping

| Axis / Element | Metric | Source Field | Notes |
|---|---|---|---|
| X-axis | Distance | `countryProductYear.distance` | Lower = more nearby = more feasible |
| X-axis label | "More Nearby ◄ → Less Nearby ►" | — | Inverted: left = close |
| Y-axis (top half) | Opportunity Gain | `countryProductYear.cog` | Higher = more strategic value |
| Y-axis (bottom half) | Product Complexity (PCI) | `countryProductYear.normalizedPci` | Higher = more complex |
| Y-axis label | "Less Complex ▼ → More Complex ▲" | — | |
| Bubble size | Global trade value | `productYear.exportValue` | Size of global market for that product |
| Bubble color | Product sector | `productHs92.topParent` | 11 treemap sectors (not 8 PS clusters) |
| Reference line | Country's ECI | `countryYear.eci` | Dashed horizontal line |
| Filter | Only opportunity products | `export_rca < 1` | Products country does NOT yet export |

### Strategic Quadrants

| Quadrant | Distance | PCI/COG | Strategic Label |
|---|---|---|---|
| Top-left (most attractive) | Low (nearby) | High | Low-Hanging Fruit — feasible AND complex/strategic |
| Top-right | High (distant) | High | Long Jumps — complex but hard to reach |
| Bottom-left | Low (nearby) | Low | Nearby but low-value |
| Bottom-right (least attractive) | High (distant) | Low | Far AND low-value |

**The sweet spot is the top-left corner:** products that are nearby (low distance) AND have high complexity or high opportunity gain.

### Country Pages vs. Explore API Differences

| Feature | Explore API (`/explore/feasibility`) | Country Pages (`/countries/{id}/growth-opportunities`) |
|---|---|---|
| Availability | All countries | Hidden for highest-complexity frontier countries |
| Y-axis numeric labels | Yes (e.g., -3.5 to 2.5) | No — uses qualitative categories |
| Strategy selector | No | Yes (Low-Hanging Fruit / Balanced Portfolio / Long Jumps radio buttons) |
| Table view | `/explore/feasibility/table` | `/countries/{id}/product-table` (top 50 only) |
| Diamond ratings | 7 diamonds, all products | 7 diamonds, top 50 |

### GraphQL Query for Feasibility Scatter Data

```graphql
{
  countryProductYear(
    countryId: 404
    productClass: HS92
    productLevel: 4
    yearMin: 2023
    yearMax: 2023
  ) {
    productId
    year
    exportRca
    distance
    cog
    normalizedPci
    normalizedCog
    normalizedDistance
  }
}
```

Filter client-side to `exportRca < 1` to get opportunity products only.

### Feasibility Table View Columns

The table view (`/explore/feasibility/table`) presents the same data in sortable HTML columns:

| Column | API Field | Rating Display |
|---|---|---|
| Nearby Distance | `countryProductYear.distance` | 7 diamonds (inverted: more = closer) |
| Opportunity Gain | `countryProductYear.cog` | 7 diamonds |
| Product Complexity | `countryProductYear.normalizedPci` | 7 diamonds |
| Global Size (USD) | `productYear.exportValue` | Dollar amount |
| Global Growth 5 YR | `productYear.exportValueConstCagr5` | Percentage with ↑/↓ |

---

## 10. SQL Schema Reference

The product space data spans two schema layers:

### `classification` schema (global, static)

| Table | Key Columns | Purpose |
|---|---|---|
| `classification.product_hs92` | `product_id`, `code`, `name_en`, `cluster_id`, `product_space_x`, `product_space_y`, `legacy_product_space_x`, `legacy_product_space_y`, `natural_resource`, `green_product`, `show_feasibility` | Product catalog with space coordinates and flags |
| `classification.product_hs92_ps_clusters` | Cluster membership data | Used for cluster-level analysis |
| `classification.product_hs92_ps_edges` | Edge/connection data | Adjacency structure of product space |

### `hs92` schema (annual, per country-product)

| Table | Key Columns | Purpose |
|---|---|---|
| `hs92.product_product_4` | `product_id`, `target_id`, `strength`, `product_level` | Proximity matrix (product space edges) |
| `hs92.country_product_year_4` | `country_id`, `product_id`, `year`, `export_rca`, `distance`, `cog`, `normalized_pci`, `normalized_cog`, `normalized_distance` | Per-country per-product metrics |
| `hs92.country_year` | `country_id`, `year`, `eci`, `coi`, `growth_proj` | Country-level complexity aggregates |
| `hs92.product_year_4` | `product_id`, `year`, `pci`, `export_value`, `export_value_const_cagr5` | Global product-level data |

The `sitc` schema has parallel tables for SITC-classified data. `hs12` and `hs22` schemas do **not** contain proximity or product space data.

### Complete SQL Example: Top 10 Opportunity Products for Kenya (2023)

```sql
SELECT
  p.name_short_en                            AS product_name,
  p.code                                     AS hs_code,
  cpy.distance,
  cpy.cog                                    AS opportunity_gain,
  cpy.normalized_pci,
  py.export_value                            AS global_export_value,
  py.export_value_const_cagr5               AS global_growth_5yr
FROM hs92.country_product_year_4 cpy
JOIN classification.product_hs92 p  ON cpy.product_id = p.product_id
JOIN hs92.product_year_4 py
  ON cpy.product_id = py.product_id AND cpy.year = py.year
WHERE cpy.country_id = 404           -- Kenya
  AND cpy.year = 2023
  AND p.product_level = 4
  AND cpy.export_rca < 1             -- opportunity products only
  AND p.show_feasibility = TRUE
ORDER BY cpy.normalized_pci DESC     -- sort by complexity (default)
LIMIT 10;
```

---

## 11. GraphQL Queries Summary

| Query | Required Args | Returns | Use for |
|---|---|---|---|
| `productProduct` | `productClass: HS92`, `productLevel: 4` | `[ProductProduct]` (`productId`, `targetId`, `strength`, `productLevel`) | All product-product proximity values |
| `countryProductYear` | `productLevel: 4` | `[CountryProductYear]` | Distance, COG, normalized PCI per country-product pair |
| `countryYear` | — | `[CountryYear]` | ECI, COI per country-year |
| `productYear` | `productLevel: 4` | `[ProductYear]` | Global export value and PCI per product-year |
| `productHs92` | — | `[Product]` | Product catalog with `clusterId`, `productSpaceX/Y`, `naturalResource`, `greenProduct` |

---

## 12. Metric Relationships: Quick Reference

```
Proximity φ(i,j)  — product-to-product; symmetric; globally fixed
        ↓
Density ρ(c,p)    — country-to-product; fraction of product p's neighborhood
                    that country c already occupies
        ↓
Distance d(c,p)   — = 1 − ρ(c,p); stored in DB as `distance`; used in feasibility scatter X-axis
        ↓
COG OG(c,p)       — strategic value of gaining product p for country c;
                    weighted sum of PCI of unreached products unlocked by p
        ↓
COI(c)            — sum of ρ(c,p) × PCI_p over products c does not yet export;
                    country's overall strategic proximity to complex products
```

**Stored metrics vs. derived metrics:**
- `distance` — stored in `country_product_year`
- `density` — NOT stored; derived as `1 - distance`
- `proximity` — stored in `product_product_4` as `strength`
- `cog` — stored in `country_product_year`
- `coi` — stored in `country_year`

---

## Advanced / Non-Standard Methods

The following methods appear in the frontier research literature and are documented in `atlas_docs/economic_complexity_modern.md`. **They are NOT currently implemented in the Atlas of Economic Complexity** and are provided here as research extensions only.

### A. Statistical Significance Testing for Proximity

The standard proximity formula can produce spurious relatedness from chance co-occurrence, especially for common products. A research extension applies a z-score filter:

$$z_{ij} = \frac{C_{ij} - \mathbb{E}[C_{ij}]}{\sigma[C_{ij}]}$$

where the expected overlap under independence is:

$$\mathbb{E}[C_{ij}] = \frac{k_{i,0} \cdot k_{j,0}}{N_{\text{countries}}}$$

and the standard deviation is:

$$\sigma[C_{ij}] = \sqrt{\frac{k_{i,0} \cdot k_{j,0} \cdot (N - k_{i,0}) \cdot (N - k_{j,0})}{N^3}}$$

Connections with z < 1.96 (95% significance threshold) are zeroed out. This produces a cleaner, more defensible relatedness network but reduces connectivity for ubiquitous products. Not used in the Atlas; the Atlas applies φ ≥ 0.3 as its threshold instead.

### B. Population-Adjusted RCA (RPOP / RpCA)

Standard RCA normalizes by output share relative to world trade. RPOP normalizes by population share instead, correcting for the systematic bias that large countries have lower RCA simply because their denominator is large:

$$\text{RPOP}_{cp} = \frac{X_{cp} / \sum_c X_{cp}}{\text{Pop}_c / \text{Pop}_{\text{world}}}$$

A tunable combined specialization score blends nRCA and nRPOP:

$$\text{Specialization}_{cp} = (\text{nRCA}_{cp})^\alpha \times (\text{nRPOP}_{cp})^{1-\alpha}$$

with α typically 0.6 for a slight preference for RCA. The Atlas uses standard RCA (α = 1) exclusively. RPOP is useful for subnational or cross-domain applications (patents, publications) where population normalization is more natural than output-share normalization.

### C. HHI-Corrected Presence Matrix

In highly concentrated fields (niche technologies, rare patents), the top-ranked countries may have RCA slightly below 1 due to scale effects and still be clearly dominant. The HHI-corrected matrix uses a dual criterion:

$$M^H_{cp} = 1 \quad \text{if} \quad \text{RCA}_{cp} \geq 1 \;\text{ OR }\; \text{rank}_{cp} \leq n_p$$

where n_p = 1/HHI_p is the effective number of competing countries (inverse Herfindahl-Hirschman Index). This ensures that top-ranked countries in concentrated fields are included even if their RCA is marginally below 1. Not used in the Atlas; relevant for innovation-domain applications (patent classes, scientific fields) where winner-take-most dynamics are common.

### D. Cross-Domain Density

Density can be calculated within a single domain (technological capabilities predicting technological opportunities) or across domains (e.g., scientific publication capabilities predicting technological diversification). Cross-domain proximity matrices require careful construction and are not interchangeable with trade-based proximity. Not currently implemented in the Atlas.

### Validation Benchmarks (from research literature)

For any complexity implementation, expected ranges for stable results:
- Minimum sample size: 50+ countries, 100+ product fields
- Temporal stability: year-on-year rank correlation > 0.7
- ECI–GDP per capita correlation: 0.3–0.7 (higher with natural resource controls)
- Density predictive power: ~13–14% increase in diversification likelihood per standard deviation increase in density
