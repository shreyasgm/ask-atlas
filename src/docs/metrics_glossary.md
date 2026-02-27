# Metrics Glossary

**Purpose:** Technical reference for all economic complexity metrics used in the Atlas of Economic Complexity, including formulas, DB column names, GraphQL field names, and cross-year comparability warnings.

**When to load this document:** Load when a user asks what any metric means in more depth than a brief definition —
including ECI, PCI, RCA, COI, COG, distance, density, proximity, diversity, ubiquity,
or normalized variants. Also load when the agent needs the formula for a metric, the
specific DB column name for a metric variant (e.g., `normalized_pci_rcalt1`), an
explanation of why values are not comparable across years, or the 2026 continuous-M
update. This is the primary reference for all metric understanding questions.

---

## Computation Pipeline Overview

```
Trade Data (X_cp)
    |
    v
RCA = (X_cp / sum_p X_cp) / (sum_c X_cp / sum_cp X_cp)
    |
    v
Continuous M (2026+): M_cp = RCA / (1 + RCA)          [was: M_cp = 1 if RCA >= 1 else 0]
    |
    +-----------------------------+-----------------------------+
    |                             |                             |
    v                             v                             v
Diversity          Ubiquity               Proximity (phi_ij)
k_{c,0} = sum_p M_cp   k_{p,0} = sum_c M_cp   phi_ij = min(P(x_i|x_j), P(x_j|x_i))
    |                             |                             |
    +-----------------------------+                             |
    |                                                           |
    v                                                           v
ECI / PCI (eigenvector of M-tilde)           Distance, Density, COG, COI
```

---

## 1. Revealed Comparative Advantage (RCA)

**Definition level:** Country × product × year

**Formula:**

```
RCA_cp = (X_cp / sum_p X_cp) / (sum_c X_cp / sum_c sum_p X_cp)
```

Where `X_cp` is exports of product `p` by country `c`. The numerator is product `p`'s share in country `c`'s total exports; the denominator is product `p`'s share of world trade.

| RCA value | Meaning |
|-----------|---------|
| > 1 | Country exports more than its world-average share — comparative advantage |
| = 1 | Country's share exactly equals the world average |
| < 1 | Country exports less than its fair share — no comparative advantage |

**Example:** Cyprus cheese RCA = (6.55% of Cyprus exports) / (0.18% of world trade) = 33.4

**DB column:** `export_rca` (float8) — in `{schema}.country_product_year_{level}` tables

**GraphQL field:** `exportRca` on `CountryProductYear`

**Cross-year comparability:** RCA values are directly comparable across years — they are always measured on the same world-average = 1 scale.

---

## 2. The Presence Matrix (M)

### 2026 Atlas Standard: Continuous M

**Effective from 2026 onward, this is the current Atlas-standard method.**

```
M_cp = RCA_cp / (1 + RCA_cp)
```

Properties:
- Range: (0, 1) — never exactly 0 or 1
- M = 0.5 when RCA = 1
- Increases monotonically with RCA
- Approaches 1 as RCA → ∞; approaches 0 as RCA → 0

**Why continuous M replaced binary M:** The binary threshold treated RCA = 0.99 identically to RCA = 0.01 (both M = 0), and RCA = 1.01 identically to RCA = 100 (both M = 1). Continuous M captures degrees of competitiveness, recognizes emerging industries below the binary threshold, and produces more nuanced diversity/ubiquity measures.

The continuous M formula is confirmed in the Atlas glossary (2026 update to the RCA definition) and `atlas_docs/economic_complexity_metrics.md` Section 3.

### Pre-2026: Binary M (historical reference only)

```
M_cp = 1 if RCA_cp >= 1 else 0
```

### Normalized RCA columns (`*_rcalt1` suffix)

The `country_product_year` tables carry two sets of normalized metrics: one calibrated using continuous M (the current standard), and one using the older binary threshold (RCA >= 1). The `*_rcalt1` columns use the binary threshold as an alternative calibration. See the Normalized Variants section.

---

## 3. Diversity

**Definition level:** Country × year

**Formula:**

```
Diversity = k_{c,0} = sum_p M_cp
```

With binary M (historical): simple count of products with RCA >= 1.
With continuous M (2026+): weighted sum reflecting degrees of specialization.

**Interpretation:** Diversity measures how many different products a country exports competitively, which is a proxy for the breadth of its productive knowledge.

**DB column:** `diversity` (int4) — in `{schema}.country_year`

**GraphQL field:** `diversity` on `CountryYear`

---

## 4. Ubiquity

**Definition level:** Product × year

**Formula:**

```
Ubiquity = k_{p,0} = sum_c M_cp
```

**Interpretation:** Ubiquity measures how many countries export a product with comparative advantage. Low-ubiquity products are produced by few, typically highly capable countries. High-ubiquity products are simple commodities nearly every country can make.

**DB column:** Not stored directly — derivable as count of countries with `export_rca >= 1` per product-year.

**GraphQL field:** Not exposed directly in the Explore API.

---

## 5. Economic Complexity Index (ECI)

**Definition level:** Country × year

### Derivation

ECI is the second eigenvector of the normalized country-country similarity matrix:

```
M_tilde^C_{cc'} = sum_p (M_cp * M_{c'p}) / (k_{c,0} * k_{p,0})
```

The matrix M-tilde^C satisfies the eigenvector equation:

```
M_tilde^C * k_vec = lambda * k_vec
```

ECI is standardized to z-scores using the second eigenvector K:

```
ECI_c = (K_c - mean(K)) / std(K)
```

The first eigenvector (lambda = 1) is trivial — it assigns equal value to all countries. The second eigenvector captures the primary axis of variation in export basket complexity.

### Key properties

- Standardized: mean ≈ 0, std ≈ 1 across countries in any given year
- Positive ECI: more complex than average (exports diverse, rare, sophisticated products)
- Negative ECI: less complex than average (exports concentrated in common products)
- Strongly correlated with GDP per capita; **predicts future growth** better than most macroeconomic indicators
- A 1 SD increase in ECI is associated with a 1.6% per year acceleration in long-run growth

### CRITICAL: Cross-year comparability warning

**ECI is NOT directly comparable across years as a level.** Each year's ECI is computed independently via eigenvalue decomposition of that year's M matrix and standardized relative to that year's country distribution. A country's ECI can rise or fall not because its productive capabilities changed, but because the reference set of countries changed.

**Correct use:** Compare within-year ECI rankings. For trend analysis, compare ECI rank trajectories (not level differences). The Atlas rankings page shows rank evolution over time, which is the methodologically valid way to track complexity trends.

### DB columns (in `{schema}.country_year`)

| Column | Type | Notes |
|--------|------|-------|
| `eci` | float8 | ECI value for that year |

### Additional country-year columns of interest

| Column | Type | Notes |
|--------|------|-------|
| `coi` | float8 | Complexity Outlook Index (see Section 10) |
| `diversity` | int4 | Count of products with comparative advantage |
| `growth_proj` | float8 | 10-year GDP per capita growth projection |
| `gdppc` | float8 | GDP per capita, current USD |
| `gdppc_ppp` | float8 | GDP per capita, PPP-adjusted |
| `gdppc_const` | float8 | GDP per capita, constant USD |
| `gdppc_ppp_const` | float8 | GDP per capita, constant PPP |
| `gdp` | float8 | Total GDP, current USD |
| `population` | float8 | Population |
| `export_value` | int8 | Total export value |
| `import_value` | int8 | Total import value |

### ECI rank

ECI rank is available via the GraphQL API but not stored as a separate DB column. It is derived from ordering countries by `eci` within a year.

**GraphQL fields on `CountryYear`:** `eci`, `coi`, `eciRank`

---

## 6. Product Complexity Index (PCI)

**Definition level:** Product × year

### Derivation

PCI uses the same eigenvector approach as ECI but from the product side. The product-product similarity matrix is:

```
M_tilde^P_{pp'} = sum_c (M_cp * M_{cp'}) / (k_{c,0} * k_{p,0})
```

PCI is the second eigenvector of M-tilde^P, standardized using **ECI's** mean and standard deviation (not PCI's own). This preserves the relationship:

```
ECI_c = (1 / k_{c,0}) * sum_p M_cp * PCI_p
```

A country's ECI equals the average PCI of the products where it has comparative advantage.

### Categorical variant: `complexity_enum`

The DB stores a categorical version of PCI: `complexity_enum ENUM(low, moderate, high)`. This is a derived bin assignment, not a direct quotient.

### DB columns

**In `{schema}.product_year_{level}` tables:**

| Column | Type | Notes |
|--------|------|-------|
| `pci` | float8 | Raw PCI for product × year |
| `complexity_enum` | ENUM(low, moderate, high) | Categorical complexity tier |
| `export_value` | int8 | World export value of product |
| `export_value_cagr_5` | float8 | 5-year compound annual growth rate |
| `export_value_growth_5` | float8 | 5-year cumulative growth |

**In `{schema}.country_product_year_{level}` tables:**

`normalized_pci` and `normalized_pci_rcalt1` (see Normalized Variants section) — PCI is stored redundantly here for query convenience.

**GraphQL fields on `ProductYear`:** `pci`, `exportValue`, `complexityEnum`

### Cross-year comparability warning

Same as ECI: PCI is standardized relative to each year's product distribution. Do not compare PCI levels across years. Use PCI rank comparisons within a year.

---

## 7. Normalized Variants

The `country_product_year` tables in the HS92, HS12, and SITC schemas carry a full set of normalized columns that re-express raw metrics as percentile ranks or normalized scores for display purposes (diamond ratings, scatter plot axes). There are two parallel families:

- **Standard columns** — calibrated using the continuous M (current Atlas standard)
- **`*_rcalt1` columns** — calibrated using the binary RCA >= 1 threshold (alternative/historical)

### Full column inventory (present in all `country_product_year_{level}` tables)

| Column | Type | Description |
|--------|------|-------------|
| `export_rca` | float8 | Raw RCA value (see Section 1) |
| `export_rpop` | float8 | Population-adjusted RCA (RPOP); see Note below |
| `normalized_export_rca` | float8 | Normalized RCA (continuous M calibration) |
| `normalized_export_rca_rcalt1` | float8 | Normalized RCA (binary RCA >= 1 calibration) |
| `normalized_pci` | float8 | Normalized PCI (continuous M calibration) |
| `normalized_pci_rcalt1` | float8 | Normalized PCI (binary threshold calibration) |
| `normalized_cog` | float8 | Normalized COG (continuous M calibration) |
| `normalized_cog_rcalt1` | float8 | Normalized COG (binary threshold calibration) |
| `normalized_distance` | float8 | Normalized distance (continuous M calibration) |
| `normalized_distance_rcalt1` | float8 | Normalized distance (binary threshold calibration) |
| `cog` | float8 | Raw Complexity Outlook Gain (see Section 9) |
| `distance` | float8 | Raw distance (see Section 8) |
| `global_market_share` | float8 | Country's share of world exports in this product |
| `is_new` | bool | True if product_status == 'new' |
| `product_status` | ENUM(new, absent, lost, present) | Export status relative to lookback period |

**Note on `export_rpop`:** This is a population-adjusted specialization measure — the country's share of world exports in a product divided by the country's share of world population. It addresses a bias in standard RCA for large countries. See the Advanced section for the formula.

**Note on the `*_rcalt1` columns:** These are provided for analytical comparability with pre-2026 data and for researchers who prefer the binary threshold approach. For current Atlas-standard analysis, use the columns without the `*_rcalt1` suffix.

**GraphQL equivalents on `CountryProductYear`:**

| DB column | GraphQL field |
|-----------|--------------|
| `export_rca` | `exportRca` |
| `normalized_pci` | `normalizedPci` |
| `normalized_cog` | `normalizedCog` |
| `normalized_distance` | `normalizedDistance` |
| `normalized_export_rca` | `normalizedExportRca` |
| `cog` | `cog` |
| `distance` | `distance` |

---

## 8. Proximity (phi)

**Definition level:** Product pair (globally fixed)

**Formula:**

```
phi_ij = min( P(x_i | x_j), P(x_j | x_i) )
```

Where:
- `P(x_i | x_j)` = probability a country exports product i given it exports product j
- `P(x_j | x_i)` = probability a country exports product j given it exports product i

**Computation:**

```
C_ij = sum_c M_ci * M_cj          # countries co-exporting both
P(x_i | x_j) = C_ij / k_{j,0}    # divide by ubiquity of j
phi_ij = min(C_ij / k_{i,0}, C_ij / k_{j,0})
```

Taking the minimum prevents inflated proximity when one product is much more ubiquitous than the other.

**Product space edge threshold:** Only pairs with `phi >= 0.3` appear as edges in the product space visualization.

**Example values:**

| Pair | phi |
|------|-----|
| Passenger cars ↔ Vehicle parts | 0.61 |
| Wine ↔ Vermouth | 0.58 |
| Men's shirts ↔ Women's blouses | 0.64 |
| Coffee ↔ Integrated circuits | 0.03 |
| Crude petroleum ↔ Medicaments | 0.04 |

**DB table:** `{schema}.product_product_4` — columns: `product_id`, `target_id`, `strength` (= phi), `product_level`

**GraphQL query:** `productProduct` — fields: `productId`, `targetId`, `strength`, `productLevel`

**Note:** Proximity values are globally fixed — they do not change per country. Only the country's position (which products are colored vs. grey) changes.

---

## 9. Distance

**Definition level:** Country × product × year

**Formula (Atlas glossary form — distance to products NOT currently exported):**

```
d_cp = sum_{p'} (1 - M_{cp'}) * phi_{p,p'} / sum_{p'} phi_{p,p'}
```

**Equivalent formulation (complement-of-density form):**

```
d_cp = 1 - [ sum_{p'} M_{cp'} * phi_{p,p'} / sum_{p'} phi_{p,p'} ]
     = 1 - density_cp
```

**Interpretation:**
- Range: [0, 1]
- Low distance (close to 0): country already has most of the related capabilities — high probability of successful entry
- High distance (close to 1): country lacks most related capabilities — high risk, requires building many new capabilities
- Typical values on the Atlas feasibility scatter: 0.65–0.95 for opportunity products

**Important:** The two formula variants are numerically different. The Atlas glossary uses the version that sums over products the country does NOT currently make (the "missing" proximity). The alternative sums over what the country DOES make. In practice, the DB stores the final normalized score and both formulas yield equivalent rankings.

**DB column:** `distance` (float8) — in `{schema}.country_product_year_{level}`

**GraphQL field:** `distance` on `CountryProductYear`

**Feasibility scatter axis:** X-axis, labeled "More Nearby ◄ → Less Nearby ►". Lower distance = left side = more feasible.

---

## 10. Density

**Definition level:** Country × product × year

**Formula:**

```
density_cp = sum_{p'} M_{cp'} * phi_{p,p'} / sum_{p'} phi_{p,p'}
```

**Interpretation:** Density measures how connected a product is to what the country already makes. Range [0, 1]. High density = high feasibility. Density = 1 − Distance.

**DB storage:** Density is not stored separately — it is the complement of `distance`. Compute as `1 - distance` if needed.

**GraphQL field:** Not exposed directly; use `1 - distance`.

---

## 11. Opportunity Gain / Complexity Outlook Gain (COG)

**Definition level:** Country × product × year

**Formula:**

```
OG_cp = sum_{p'} [ phi_{p,p'} / sum_{p''} phi_{p'',p'} ] * (1 - M_{cp'}) * PCI_{p'}
```

Where:
- `phi_{p,p'}` = proximity between candidate product `p` and other product `p'`
- `sum_{p''} phi_{p'',p'}` = total connectivity of product `p'` to all products (normalizer)
- `(1 - M_{cp'})` = counts only products the country is NOT currently producing
- `PCI_{p'}` = product complexity of `p'`

**Interpretation:**
- High COG: candidate product is a "hub" — gaining it opens bridges to many complex products the country doesn't yet make
- Low COG: product is isolated; gaining it doesn't open many new paths
- Products with high COG have outsized strategic value as stepping stones

**DB column:** `cog` (float8) — in `{schema}.country_product_year_{level}`

**GraphQL field:** `cog` on `CountryProductYear`

**Feasibility scatter axis:** Y-axis upper half, labeled "More Complex / More Strategic ▲"

---

## 12. Complexity Outlook Index (COI)

**Definition level:** Country × year

**Formula:**

```
COI_c = sum_p (1 - d_cp) * (1 - M_cp) * PCI_p
```

Equivalently (using density):

```
COI_c = sum_p density_cp * (1 - M_cp) * PCI_p
```

Where `(1 - M_cp)` ensures only products the country does NOT currently export are counted.

**Interpretation:**
- High COI: many complex products are nearby — country is well-positioned for complexity-driven growth
- Low COI: isolated from complex products — diversification will be harder
- **Inverted-U pattern:** Mid-complexity countries (Spain, India, Turkey) often have the highest COI. The most complex economies (Japan, Germany) have low COI because they have already captured most opportunities. The least complex have low COI because they are too far from complex products.

**DB column:** `coi` (float8) — in `{schema}.country_year`

**GraphQL field:** `coi` on `CountryYear`

---

## 13. Product Status and New Products

**Definition level:** Country × product × year (relative to a lookback base year)

### `product_status` ENUM

| Value | Meaning |
|-------|---------|
| `present` | Country had RCA >= 1 in base year AND current year |
| `new` | Country had RCA < 1 in base year, RCA >= 1 in current year |
| `lost` | Country had RCA >= 1 in base year, RCA < 1 in current year |
| `absent` | Country had RCA < 1 in both base year and current year |

**DB column:** `product_status` — in `{schema}.country_product_year_{level}`

**DB column:** `is_new` (bool) — shorthand for `product_status = 'new'`

**Lookback tables:** `hs92.country_product_lookback_{level}` — pre-computed lookback data containing `lookback` (years back), `lookback_year`, `export_value_cagr`, `export_value_growth`, `export_value_change`, `export_rpop_change`, `global_market_share_change`. Available only in the HS92 schema.

---

## 14. Growth Projections

**Definition level:** Country × year

**Variables:** The Atlas 10-year GDP per capita growth projection uses four explanatory factors:
1. Economic Complexity Index (ECI)
2. Complexity Outlook Index (COI)
3. Current income level (log GDP per capita)
4. Expected natural resource exports per capita

Countries whose ECI is high relative to their income level are predicted to grow faster; countries whose ECI is low relative to income (often natural-resource-dependent) are predicted to grow slower.

**DB column:** `growth_proj` (float8) — in `{schema}.country_year`

**GraphQL field:** `growthProjection` on `CountryYear` (Explore API), and on `CountryProfile` (Country Pages API)

---

## 15. Product Space Coordinates and Cluster

**Definition level:** Product (globally fixed)

**DB columns in `classification.product_hs92` and `classification.product_hs12`:**

| Column | Type | Description |
|--------|------|-------------|
| `product_space_x` | float8 | Fixed 2D X coordinate for product space layout |
| `product_space_y` | float8 | Fixed 2D Y coordinate for product space layout |
| `cluster_id` | int4 | Product space cluster assignment (8 clusters) |
| `natural_resource` | bool | True if product classified as natural resource |
| `code` | text | HS code (e.g., "0901" for coffee) |
| `name_en` | text | Full English product name |
| `name_short_en` | text | Short English product name |

**The 8 product space clusters:**

| Cluster name | Description |
|-------------|-------------|
| Agricultural Goods | Food, beverages, animal/vegetable products |
| Construction Goods | Building materials, wood, cement |
| Electronics | Computers, telecom, semiconductors |
| Chemicals and Basic Metals | Chemical compounds, basic metal products |
| Metalworking and Machinery | Industrial machinery, tools, vehicle parts |
| Minerals | Petroleum, ores, mining products |
| Textile and Home Goods | Fabrics, home furnishings, paper |
| Apparel | Garments, footwear, accessories |

Note: These 8 product space clusters differ from the 11 treemap sectors used in trade composition views.

**GraphQL fields on product catalog queries** (`productHs92`, `productHs12`): `clusterId`, `productSpaceX`, `productSpaceY`

---

## 16. Thresholds Table

`{schema}.country_year_thresholds` stores per-country, per-year distributional statistics for variables (used for percentile normalization of the `normalized_*` columns):

| Column | Type |
|--------|------|
| `year` | int4 |
| `variable` | varchar |
| `mean`, `median`, `min`, `max`, `std` | float8 |
| `percentile_10` through `percentile_90` | float8 |

---

## 17. GraphQL Field Reference Summary

| Metric | GraphQL Query | Field(s) |
|--------|--------------|---------|
| ECI | `countryYear` | `eci`, `eciRank` |
| COI | `countryYear` | `coi` |
| Diversity | `countryYear` | `diversity` |
| Growth projection | `countryYear` | `growthProjection` |
| RCA | `countryProductYear` | `exportRca` |
| Distance | `countryProductYear` | `distance` |
| COG | `countryProductYear` | `cog` |
| Normalized PCI | `countryProductYear` | `normalizedPci` |
| Normalized distance | `countryProductYear` | `normalizedDistance` |
| Normalized COG | `countryProductYear` | `normalizedCog` |
| Normalized RCA | `countryProductYear` | `normalizedExportRca` |
| PCI (raw) | `productYear` | `pci` |
| complexity_enum | `productYear` | `complexityEnum` |
| Product space coords | `productHs92` / `productHs12` | `productSpaceX`, `productSpaceY`, `clusterId` |
| Proximity (strength) | `productProduct` | `strength`, `productId`, `targetId` |

---

## 18. SQL Query Examples

### ECI for a country over time

```sql
SELECT year, eci, coi, diversity, growth_proj
FROM hs92.country_year
JOIN classification.location_country USING (country_id)
WHERE iso3_code = 'KEN'
ORDER BY year;
```

### Top products by PCI for a given year (product complexity ranking)

```sql
SELECT p.name_en, py.pci, py.complexity_enum, py.export_value
FROM hs92.product_year_4 py
JOIN classification.product_hs92 p USING (product_id)
WHERE py.year = 2024
  AND py.product_level = 4
ORDER BY py.pci DESC
LIMIT 20;
```

### Growth opportunity products for a country (low distance, high COG, RCA < 1)

```sql
SELECT p.name_en, cpy.distance, cpy.cog, cpy.normalized_pci, cpy.export_rca
FROM hs92.country_product_year_4 cpy
JOIN classification.location_country lc USING (country_id)
JOIN classification.product_hs92 p USING (product_id)
WHERE lc.iso3_code = 'KEN'
  AND cpy.year = 2024
  AND cpy.export_rca < 1
ORDER BY cpy.distance ASC, cpy.cog DESC
LIMIT 20;
```

### New products (gained RCA in last N years)

```sql
SELECT p.name_en, cpy.export_rca, cpy.export_value
FROM hs92.country_product_year_4 cpy
JOIN classification.location_country lc USING (country_id)
JOIN classification.product_hs92 p USING (product_id)
WHERE lc.iso3_code = 'KEN'
  AND cpy.year = 2024
  AND cpy.product_status = 'new'
ORDER BY cpy.export_value DESC;
```

---

## Advanced / Non-Standard Methods

> **IMPORTANT:** The methods in this section are **frontier research extensions NOT currently implemented in the Atlas of Economic Complexity**. They are documented here for completeness and to support advanced methodology questions. Do not present these as Atlas-standard metrics.

### RPOP (Population-Adjusted RCA / Revealed per Capita Advantage)

**Purpose:** Addresses a systematic bias in standard RCA for large countries. A large country may have high absolute exports in a product but low per-capita specialization.

**Formula:**

```
RPOP_cp = (X_cp / sum_c X_cp) / (Pop_c / Pop_W)
```

Where `Pop_c` is country c's population and `Pop_W` is world population. This is equivalent to: country's world-market share in the product divided by country's share of world population.

**Combined specialization score (tunable blend):**

```
Specialization = (nRCA)^alpha * (nRPOP)^(1-alpha)
```

Where `nRCA = RCA/(1+RCA)`, `nRPOP = RPOP/(1+RPOP)`, and `alpha` ∈ [0,1] (alpha=0.6 is a typical default). At alpha=1 this reduces to pure nRCA; at alpha=0, pure nRPOP.

**Note:** `export_rpop` is stored in the Atlas `country_product_year` tables (as `export_rpop` float8). This makes RPOP the one advanced metric that IS present in the Atlas DB, though it is not used to construct the primary ECI/PCI rankings.

### HHI-Corrected Presence Matrix

**Purpose:** In highly concentrated domains (patents, niche technologies), standard RCA thresholding can exclude dominant producers with RCA slightly below 1 due to scale effects.

**Formula:**

```
M^H_cp = 1 if (RCA_cp >= 1) OR (rank_cp <= n_p) else 0
```

Where `n_p = 1 / HHI_p` is the effective number of competing countries in product `p` (inverse Herfindahl-Hirschman Index), and `rank_cp` is country `c`'s rank by export value in product `p`.

**Not in Atlas.** Primarily used for patent and innovation system analyses.

### Statistical Significance Testing for Proximity

**Purpose:** Standard proximity can produce spurious relatedness from chance co-occurrence, particularly for low-ubiquity products.

**Approach:** Observed co-occurrence count `C_ij` is tested against the expected count under independence (`k_{i,0} * k_{j,0} / N`, where N = total countries). A z-score filter zeroes out connections below a significance threshold (e.g., z > 1.96 for p < 0.05). This yields cleaner relatedness networks with fewer false edges.

**Not in Atlas.** The Atlas product space uses a fixed `phi >= 0.3` threshold rather than significance testing.

### Alternative Proximity Measures for Innovation Domains

Beyond trade co-occurrence, research literature uses:
- **Citation-based proximity:** Flows between patent or publication classes
- **Co-inventor / co-author proximity:** Network overlap across institutional boundaries
- **Multi-classification proximity:** Patents spanning multiple IPC/CPC classes

These are used in subnational complexity, patent complexity, and scientific publication complexity studies. None are present in the Atlas.
