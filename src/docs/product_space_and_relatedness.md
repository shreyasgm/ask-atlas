---
title: Product Space and Relatedness
purpose: >
  Technical reference for product proximity, distance, density, COG/COI metrics,
  the 8-cluster product space visualization, and the Growth Opportunity
  feasibility scatter — all as implemented in the Atlas of Economic Complexity.
keywords:
  - product space
  - proximity
  - distance
  - density
  - COG
  - COI
  - opportunity gain
  - feasibility scatter
  - diversification
  - product clusters
  - product_product
  - productProduct
  - relatedness
  - adjacent possible
  - stepping stone
  - core vs periphery
when_to_load: >
  Load when the user asks which products a country should diversify into, how
  nearby or feasible a product is for a country, what proximity/distance/density
  means as a strategic concept, how the Growth Opportunity feasibility scatter is
  constructed, or what the 8 product space clusters represent. Also load for
  `productProduct` query usage or `product_product_4` table structure. For
  country-level diversification strategy context, also load
  strategic_approaches.md.
when_not_to_load: >
  Do not load for metric formula derivations alone (see metrics_glossary.md).
related_docs:
  - strategic_approaches.md
  - metrics_glossary.md
---

## Conceptual Foundation: Why Countries Diversify Into Nearby Products

### The Adjacent Possible and Capability-Based Diversification

The product space encodes the insight that **countries diversify by moving into products that require similar capabilities to what they already produce** — the "adjacent possible." Products that share underlying know-how, infrastructure, institutions, or factor endowments tend to be co-exported by the same countries. The product space network makes this latent capability structure visible.

### Why the Product Space Is Persistent Over Time

The capabilities underlying the product space — skilled labor pools, institutional quality, infrastructure networks, supplier ecosystems, tacit industrial knowledge — change slowly. A country's position in the product space reflects its accumulated capability stock built over decades. This is why diversification patterns are highly path-dependent: today's export basket is the strongest predictor of tomorrow's, and radical jumps across the product space are rare.

### Why Diversification Follows the Product Space: Empirical Evidence

Moving into a new product requires assembling the right combination of capabilities — worker skills, supply chains, regulatory frameworks, quality standards, distribution networks. Products that are "nearby" in the product space share most of these requirements with products the country already makes successfully. The further the jump, the more new capabilities must be built simultaneously, and the probability of successful entry drops sharply. Hidalgo et al. (2007) showed that the probability of developing RCA in a new product is near-zero when the closest existing product is at proximity φ ≈ 0.1, but rises to approximately 15% when the closest product is at φ ≈ 0.8 (measured over 5-year windows). For 79% of products, countries that successfully transitioned into them had higher density ratios than countries that did not.

Recent work provides a micro-foundation for this pattern: Diodato, Hausmann & Schetter (2022) show that industries share occupational inputs, and entry probability into the nearest industry (by occupational overlap) is approximately 4x higher than at maximum occupational distance. The original product space was purely phenomenological (inferred from co-export patterns); the occupational input structure explains *why* proximity predicts diversification.

The pipeline: compute proximity (φ) → prune edges → assign fixed 2D coordinates → overlay per-country RCA to determine which products are actively exported.

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
| Threshold for network edges | Pre-pruned to top-5 connections per product (Explore frontend: 4,316 edges / 865 nodes; Country Profiles: 2,532 edges / 866 nodes). Edge data is served from static JSON files, NOT from the `productProduct` GraphQL query. |
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

## 2. The Product Space Network: Construction, Layout, and Core vs. Periphery

### Construction

1. Compute the ~865×865 (HS4 level) proximity matrix from historical co-export patterns across ~123 reliable countries.
2. Prune edges: retain the **top 5 connections per product** (by proximity strength). The pruning is done at data generation time. Minimum proximity in the pruned set: ~0.17 (Country Profiles) or ~0.48 (Explore). The pruned network contains ~865 nodes and ~4,316 edges. No runtime edge filtering occurs.
3. Project to 2D using UMAP. The resulting coordinates are **fixed globally** — node positions do not change per country. Pre-computed UMAP layouts are generated during data ingestion.

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

Rich countries preferentially occupy the dense core. Poor countries tend to be concentrated at the periphery.

### The Periphery Trap

Countries at the periphery of the product space face a structural disadvantage that compounds over time. This is one of the most important findings in the complexity literature, with both empirical and theoretical underpinnings.

**Empirical evidence from simulations (Hidalgo et al., *Science* 2007):** The original product space paper tested what happens when countries can "move" to products within a proximity threshold over 20 iterations:

- **At φ ≥ 0.55:** Most countries can diffuse through to the core of the product space, though countries starting in the core do so much faster.
- **At φ ≥ 0.60:** Countries starting in the periphery (e.g., Chile) spread slowly; countries in the core (e.g., Korea) still populate it after just a few rounds.
- **At φ ≥ 0.65:** Peripheral countries **cannot diffuse at all** — they lack any close-enough products to transition to. Core countries still make some progress but slowly. The world "maintains a degree of inequality similar to its current state."

The system undergoes an **abrupt transition** around φ = 0.65: convergence is possible only if countries can jump to products located at proximity above this threshold. The sparsity of the product space makes this threshold binding for many countries — 65% of all proximity values are below 0.2, and 32% are below 0.1.

**The compounding mechanism:**

1. **Limited capability spillover per step.** Peripheral products (agriculture, raw materials, simple textiles) share few capabilities with the complex products at the core. When a peripheral country successfully enters a new product, that product is likely to be another peripheral product — the step provides limited "capability spillover" toward the complex core.

2. **Each step is harder AND less valuable.** Not only are jumps from the periphery longer (higher distance), but the intermediate products encountered along the way tend to have low PCI and low COG. This means each diversification step is both more difficult to achieve and less strategically valuable compared to steps taken by countries already near the core.

3. **The gap compounds.** Countries in the dense core can diversify rapidly through market-driven processes because many complex products are nearby. Each new product they gain opens connections to more complex products (high COG). Meanwhile, peripheral countries must fight for each incremental step. Over time, core countries pull further ahead.

**Theoretical foundation — the quiescence trap (Hausmann & Hidalgo, 2011):** The capabilities model formalizes why the periphery trap is self-reinforcing. Products require specific *combinations* of capabilities, and a country can produce a product only if it holds *all* required capabilities. The returns to accumulating one more capability are *convex* — they increase as a power of the country's existing capability stock. In a calibrated model with 65-80 capabilities worldwide:

- A country with only 5 capabilities gets essentially zero return from accumulating one or two more, because the probability of completing the exact capability set required by even the simplest product is negligible.
- A country with 40+ capabilities gets large returns from any additional one, because it can combine the new capability with existing ones in many productive ways.

This creates a "quiescence equilibrium" — countries with few capabilities have no economic incentive to invest in acquiring more, while countries with many capabilities have strong incentives to continue accumulating. The depth of this trap increases as products become more complex (require more capabilities) and as the total number of capabilities in the world grows.

**Why this matters for policy:** The periphery trap explains why market forces alone are insufficient for many developing countries. Without deliberate, coordinated investment — what the Atlas calls "Strategic Bets" — peripheral countries cannot bridge the capability gap. Countries in the dense core can rely more on "Light Touch" market-driven diversification because the product space structure works in their favor. This structural insight underpins the Atlas's strategic approach framework (see `strategic_approaches.md`).

**Modern extensions:** The genotypic product space (Schetter, Diodato, Protzer, Neffke & Hausmann, 2024) constructs product proximity directly from observed capability requirements (occupational employment patterns) rather than co-export correlations. This approach can identify *which specific capabilities* a country is missing, making the periphery trap actionable for policy — rather than just saying "this product is far away," it can say "you are missing capabilities X, Y, and Z." The genotypic proximity is also asymmetric (industry A may be a stepping stone to B but not vice versa), capturing directionality that the symmetric classic product space misses. The Atlas currently uses the classic (phenotypic) product space.

---

## 3. The 8 Product Space Clusters: Sector Groupings and Database Fields

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

## 4. Country Position in the Product Space: RCA Overlay and Capability Assessment

A country's position in the product space is defined by which products it exports with comparative advantage. Products with `export_rca ≥ 1` are part of the country's active capability set; products with `export_rca < 1` represent potential diversification targets. Each product belongs to one of the 8 clusters (see Section 3).

**Assessing a country's position:**
- A country concentrated in one or two clusters (e.g., only Agricultural Goods and Minerals) has a narrow capability base and faces higher diversification risk
- A country with RCA ≥ 1 products spread across multiple clusters has broader capabilities and more diversification pathways
- The density of a country's active products in the network core vs. periphery determines how easily it can reach complex products (see the periphery trap in Section 2)

**SQL query:**
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

**Country Pages API:** `productSpace(location: "location-{id}")` returns RCA, x/y coordinates, and edge connections per product for that country.

---

## 5. Distance (d_cp) — How Far a Country Is from a Product

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

**Key rule:** Lower distance = more feasible = lower risk. Distance ranges from 0 (nearby — country has most related capabilities) to 1 (distant — country lacks most related capabilities).

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

## 6. Density (ρ_cp) — Proximity to Existing Capabilities (1 − Distance)

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

## 7. Opportunity Gain (COG) — Strategic Value as a Diversification Stepping Stone

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

## 8. Complexity Outlook Index (COI) — Country-Level Proximity to Complex Products

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

COI is one of the five inputs to the Atlas growth projections (alongside ECI, log GDP per capita, natural resource export change, and the ECI×COI interaction term).

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

## 9. Strategic Approaches: COI/ECI Thresholds and Technological Frontier Countries

COI and ECI together determine a country's recommended **strategic approach** to diversification. The Atlas assigns each country one of four approaches, displayed at `/countries/{id}/strategic-approach`:

### Assignment Algorithm

**Technological Frontier** countries are a **hardcoded list** of 16 economies. These are the world's most complex economies where the standard COI/ECI heuristics don't cleanly apply — they have already captured most nearby product opportunities and growth must come from innovation rather than diversification into existing product classes.

**Current TechFrontier countries:** Austria, Canada, China, Czechia, Finland, France, Germany, Italy, Japan, Netherlands, Singapore, South Korea, Sweden, Switzerland, United Kingdom, United States.

For all other countries, the assignment uses two numeric thresholds on **COI** (from `countryYear`) and **`eciNatResourcesGdpControlled`** (ECI adjusted for natural resource rents and GDP per capita, from `countryProfile`):

| Condition | Approach | API Enum | Policy Logic |
|---|---|---|---|
| COI ≥ 0 AND ECI* ≥ 0 | **Light Touch** | `LightTouch` | Country is complex and well-connected to opportunities. Leverage existing successes with minimal intervention. |
| COI ≥ 0 AND ECI* < 0 | **Parsimonious Industrial Policy** | `ParsimoniousIndustrial` | Many opportunities nearby but current basket is simpler than income predicts. Targeted support for promising sectors. |
| COI < 0 | **Strategic Bets** | `StrategicBets` | Few nearby opportunities. Must make deliberate, concentrated investments in strategic sectors. |

Note: COI < 0 always yields Strategic Bets regardless of ECI*. The bottom-right quadrant (high complexity, low COI) is occupied only by the hardcoded TechFrontier list above.

### Growth opportunities unavailable for frontier countries

The Atlas **does not display growth opportunity products** for TechFrontier countries. The Country Pages growth opportunities page (`/countries/{id}/growth-opportunities`) is hidden for these 16 economies. This is because the standard distance/COG/PCI framework is designed for countries that can diversify into existing product classes — frontier countries need innovation to expand the frontier itself and create new product classes, which the product space framework cannot capture.

If a user asks about growth opportunities for a TechFrontier country, explain that the Atlas does not provide product-level diversification recommendations for these economies because they have already captured most existing nearby opportunities and their growth path depends on innovation rather than diversification into known product categories.

### GraphQL API

```graphql
query {
  countryProfile(location: "location-404") {
    policyRecommendation   # LightTouch | ParsimoniousIndustrial | StrategicBets | TechFrontier
    eciNatResourcesGdpControlled  # Float — x-axis of strategic approach scatter
    latestCoi                     # Float — y-axis
  }
}
```

---

## 10. Feasibility Assessment: Growth Opportunity Scoring and Product Selection

### Three Dimensions of Feasibility

Growth opportunity products (those with `exportRca < 1`) are assessed along three dimensions:

| Dimension | Metric | Source Field | Principle |
|---|---|---|---|
| **Proximity** | Distance | `countryProductYear.distance` | Products closer to existing capabilities are more feasible — lower distance means the country already has most related capabilities |
| **Strategic value** | Opportunity Gain (COG) | `countryProductYear.cog` | Products that open bridges to many complex products the country doesn't yet make have higher strategic value |
| **Complexity payoff** | Product Complexity (PCI) | `countryProductYear.normalizedPci` | More complex products contribute more to long-run income growth |
| **Market size** | Global trade value | `productYear.exportValue` | Larger global markets offer more export revenue potential |

### Strategic Evaluation Framework

| Distance | PCI/COG | Strategic Implication |
|---|---|---|
| Low (nearby) | High | **Low-Hanging Fruit** — feasible AND complex/strategic. Highest priority targets. |
| High (distant) | High | **Long Jumps** — high payoff but require building many new capabilities. High risk. |
| Low (nearby) | Low | Nearby but low-value — easy to enter but limited strategic benefit |
| High (distant) | Low | Far AND low-value — least attractive targets |

**The key principle:** Products closer to a country's existing capabilities (low distance) that also have high complexity or high opportunity gain represent the most attractive diversification targets.

### Country Pages vs. Explore API Differences

| Feature | Explore API (`/explore/feasibility`) | Country Pages (`/countries/{id}/growth-opportunities`) |
|---|---|---|
| Availability | All countries | Hidden for TechFrontier countries (16 economies — see Section 9) |
| Y-axis numeric labels | Yes (e.g., -3.5 to 2.5) | No — uses qualitative categories |
| Strategy selector | No | Yes (Low-Hanging Fruit / Balanced Portfolio / Long Jumps radio buttons) |
| Table view | `/explore/feasibility/table` | `/countries/{id}/product-table` (top 50 only) |
| Diamond ratings | Max 5 diamonds (0.5–5.0 scale), all products | Max 5 diamonds, top 50 |

### Product Selection Strategies (Country Pages Growth Opportunities)

The Country Pages growth opportunities page offers three product selection strategies, each a weighted combination of the three criteria (distance, complexity, opportunity gain):

**Country Profiles** scoring: `score = normalizedDistance × distanceWeight + normalizedPci × complexityWeight + normalizedCog × opportunityGainWeight`

The weights vary by strategy AND by policy recommendation (for Balanced Portfolio):

| Strategy | Policy Recommendation | Distance | Complexity (PCI) | Opportunity Gain (COG) |
|---|---|---|---|---|
| **Low-Hanging Fruit** | (any) | 60% | 15% | 25% |
| **Long Jump** | (any) | 45% | 20% | 35% |
| **Balanced Portfolio** | StrategicBets | 50% | 15% | 35% |
| **Balanced Portfolio** | ParsimoniousIndustrial | 55% | 20% | 25% |
| **Balanced Portfolio** | LightTouch | 60% | 20% | 20% |
| **Balanced Portfolio** | TechFrontier | N/A (no feasibility page) | | |

Products are sorted descending by score and the **top 50** are highlighted.

**PCI Ceiling Filter**: For countries with GDP per capita ≤ $6,000, product PCI must be < `countryECI + ceilingRange` (2.0 for Low-Hanging/Balanced, 2.5 for Long Jump; table view uses 1.75). Above $6k, ceiling is effectively unlimited.

**Explore page** uses a different, fixed formula: `score = 0.50 × normalizedDistance + 0.15 × normalizedPci + 0.35 × normalizedCog` (no strategy variation).

These strategies appear as radio buttons on `/countries/{id}/growth-opportunities`. The Explore API feasibility page (`/explore/feasibility`) does not offer these strategy presets but uses its own fixed weighting.

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
| Nearby Distance | `countryProductYear.distance` | max 5 diamonds (0.5–5.0 scale from 10 deciles) (inverted: more = closer) |
| Opportunity Gain | `countryProductYear.cog` | max 5 diamonds (0.5–5.0 scale from 10 deciles) |
| Product Complexity | `countryProductYear.normalizedPci` | max 5 diamonds (0.5–5.0 scale from 10 deciles) |
| Global Size (USD) | `productYear.exportValue` | Dollar amount |
| Global Growth 5 YR | `productYear.exportValueConstCagr5` | Percentage with ↑/↓ |

---

## 11. SQL Schema Reference: Product Space Tables and Columns

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

## 12. Quick Reference: Product Space GraphQL Queries and Metric Relationships

### GraphQL Queries Summary

| Query | Required Args | Returns | Use for |
|---|---|---|---|
| `productProduct` | `productClass: HS92`, `productLevel: 4` | `[ProductProduct]` (`productId`, `targetId`, `strength`, `productLevel`) | All product-product proximity values |
| `countryProductYear` | `productLevel: 4` | `[CountryProductYear]` | Distance, COG, normalized PCI per country-product pair |
| `countryYear` | — | `[CountryYear]` | ECI, COI per country-year |
| `productYear` | `productLevel: 4` | `[ProductYear]` | Global export value and PCI per product-year |
| `productHs92` | — | `[Product]` | Product catalog with `clusterId`, `productSpaceX/Y`, `naturalResource`, `greenProduct` |

### Metric Relationships

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

## Advanced Methods Not Implemented in the Atlas: Statistical Proximity, RPOP, HHI Correction

The following methods appear in the frontier research literature and are documented in `atlas_docs/economic_complexity_modern.md`. **They are NOT currently implemented in the Atlas of Economic Complexity** and are provided here as research extensions only.

### A. Statistical Significance Testing for Proximity

The standard proximity formula can produce spurious relatedness from chance co-occurrence, especially for common products. A research extension applies a z-score filter:

$$z_{ij} = \frac{C_{ij} - \mathbb{E}[C_{ij}]}{\sigma[C_{ij}]}$$

where the expected overlap under independence is:

$$\mathbb{E}[C_{ij}] = \frac{k_{i,0} \cdot k_{j,0}}{N_{\text{countries}}}$$

and the standard deviation is:

$$\sigma[C_{ij}] = \sqrt{\frac{k_{i,0} \cdot k_{j,0} \cdot (N - k_{i,0}) \cdot (N - k_{j,0})}{N^3}}$$

Connections with z < 1.96 (95% significance threshold) are zeroed out. This produces a cleaner, more defensible relatedness network but reduces connectivity for ubiquitous products. Not used in the Atlas; the Atlas uses a top-5-neighbors-per-product pruning strategy instead of a fixed threshold.

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
