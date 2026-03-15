---
title: Strategic Approaches to Economic Diversification
purpose: >
  Technical reference for the Atlas's country classification framework — four
  strategic approaches, diversification grades, growth projections, and the
  complexity-income relationship.
keywords:
  - strategic approach
  - Light Touch
  - Parsimonious Industrial Policy
  - Strategic Bets
  - Technological Frontier
  - diversification grade
  - growth projection
  - complexity-income relationship
  - COI
  - ECI
  - policy recommendation
  - countryProfile
  - allCountryProfiles
  - frontier countries
  - growth opportunities unavailable
when_to_load: >
  Load whenever a question involves economic diversification — whether about a
  country's recommended strategic approach (Light Touch, Parsimonious IP,
  Strategic Bets, Technological Frontier), what diversification grades mean, how
  10-year income growth projections are modeled, or how complexity relates to
  growth potential. This is the primary reference for any country-level
  diversification question.
when_not_to_load: >
  Do not load for product-level opportunity analysis (where specific products to
  target; also load product_space_and_relatedness.md) or for historical realized
  growth rates (see growth_dynamics.md).
related_docs:
  - product_space_and_relatedness.md
  - growth_dynamics.md
---

## The Four Strategic Approaches: Light Touch, Parsimonious Industrial Policy, Strategic Bets, and Technological Frontier

Countries are assigned one of four strategic approaches based on two diagnostic dimensions:

- **Complexity adequacy**: "Is the country complex enough for its income to grow?" — measured by `eciNatResourcesGdpControlled` (ECI adjusted for natural resources and GDP per capita). Positive values mean the country is more complex than its income level predicts.
- **Opportunity connectedness**: "Is the country well-connected to many new opportunities?" — measured by COI (Complexity Outlook Index). Higher COI means more nearby complex products are within reach.

These two dimensions define four policy classifications, each mapped to a strategic approach. The classification is displayed at `/countries/{id}/strategic-approach`.

### Classification Map

```
High COI │ PARSIMONIOUS INDUSTRIAL │  LIGHT TOUCH
(COI≥0)  │ POLICY                  │
─────────┼─────────────────────────┼──────────────
Low COI  │ STRATEGIC BETS          │  TECHNOLOGICAL
(COI<0)  │                         │  FRONTIER (hardcoded list)
         └─────────────────────────────────────────
              ECI* < 0                ECI* ≥ 0
              (* = eciNatResourcesGdpControlled)
```

### Assignment Algorithm

The strategic approach is determined as follows:

1. **Technological Frontier** countries are a **hardcoded list** (16 countries). These are the world's most complex economies where COI-based heuristics don't cleanly apply. The list is maintained server-side and returned via the `policyRecommendation` field. Current TechFrontier countries: Austria, Canada, China, Czechia, Finland, France, Germany, Italy, Japan, Netherlands, Singapore, South Korea, Sweden, Switzerland, United Kingdom, United States.

2. For all other countries, the assignment follows two numeric thresholds using **COI** (from `countryYear` or `allCountryYear`) and **`eciNatResourcesGdpControlled`** (from `countryProfile`):

| Condition | Approach | API Enum |
|---|---|---|
| COI ≥ 0 AND ECI* ≥ 0 | **Light Touch** | `LightTouch` |
| COI ≥ 0 AND ECI* < 0 | **Parsimonious Industrial Policy** | `ParsimoniousIndustrial` |
| COI < 0 | **Strategic Bets** | `StrategicBets` |

Where ECI* = `eciNatResourcesGdpControlled` (ECI adjusted for natural resource rents and GDP per capita via partial correlation). Note that COI < 0 always yields Strategic Bets regardless of ECI* — the Technological Frontier quadrant (low COI + high ECI*) is occupied only by the hardcoded list above.

### Approach Definitions

| Approach | API Enum | Quadrant | Policy Logic |
|---|---|---|---|
| **Light Touch** | `LightTouch` | COI ≥ 0, ECI* ≥ 0 (44 countries) | Country is complex and well-connected to opportunities. Ample space to diversify by leveraging existing successes. Minimal government intervention needed — markets are functioning. |
| **Parsimonious Industrial Policy** | `ParsimoniousIndustrial` | COI ≥ 0, ECI* < 0 (22 countries) | Many opportunities nearby but current basket is simpler than income predicts. Targeted support for specific promising sectors. Easiest path to complexity growth. |
| **Strategic Bets** | `StrategicBets` | COI < 0 (63 countries) | Few nearby opportunities and simple current basket. Must make deliberate, concentrated investments in specific sectors. Highest-risk approach — necessary for countries far from the complexity frontier. |
| **Technological Frontier** | `TechFrontier` | Hardcoded list (16 countries) | Already at the frontier. Few unexploited nearby opportunities because most have been captured. Growth comes from innovation, not product diversification. |

**Country examples:** Kenya → `LightTouch`; USA → `TechFrontier`; Spain → `ParsimoniousIndustrial`; Nigeria → `StrategicBets`.

### Narrative Decision Tree

The quadrant assignment can also be understood through two diagnostic questions:

1. **Can the country grow using its existing knowhow?** If **yes** — the country is already complex relative to its income (ECI* ≥ 0) and well-connected to opportunities (COI ≥ 0) → **Light Touch**.

2. If **no**, **how easy is it to diversify into new products?** This is assessed by COI — how many complex products are near the country's current capabilities.
   - **COI ≥ 0** (many nearby opportunities) → **Parsimonious Industrial Policy**. Remove specific bottlenecks to help firms move into closely related products.
   - **COI < 0** (few nearby opportunities) → **Strategic Bets**. The country faces a sparse opportunity landscape and must make coordinated investments to leap into strategic areas that open future diversification paths.
   - **Technological Frontier** countries are an exception — they have high complexity but low COI because they've already captured most nearby opportunities. These are identified by a hardcoded list rather than the COI/ECI* thresholds.

---

## GraphQL API: Strategic Approach, COI, and ECI Fields (countryProfile and countryYear)

### Primary query: `countryProfile` (Country Pages API)

Endpoint: `POST https://atlas.hks.harvard.edu/api/countries/graphql`
Location ID format: `"location-{M49}"` (e.g., `"location-404"` for Kenya)

```graphql
query {
  countryProfile(location: "location-404") {
    policyRecommendation          # PolicyRecommendation enum
    eciNatResourcesGdpControlled  # Float — x-axis of strategic approach scatter
    latestEci                     # Float — raw ECI value
    latestEciRank                 # Int — rank out of 145
    latestCoi                     # Float — raw COI value
    latestCoiRank                 # Int — rank out of 145
    coiClassification             # COIClassification enum: low | medium | high
    growthProjection              # Float — annualized 10-year GDP/capita growth rate
    growthProjectionRank          # Int — rank out of 145
    growthProjectionClassification       # GrowthProjectionClassification: rapid | moderate | slow
    growthProjectionRelativeToIncome     # GrowthProjectionRelativeToIncome enum
    diversificationGrade          # DiversificationGrade enum
    diversityRank                 # Int — rank out of 145
    diversity                     # Int — count of products with RCA >= 1
  }
}
```

### Secondary query: `allCountryProfiles` — all countries at once

```graphql
query {
  allCountryProfiles {
    location { id shortName code }
    diversificationGrade          # DiversificationGrade enum
    eciNatResourcesGdpControlled  # Float — used as x-axis for scatter
    policyRecommendation          # PolicyRecommendation enum
  }
}
```

Use `allCountryProfiles` when comparing strategic approaches across multiple countries.

### Explore API: `countryYear` (for raw ECI/COI time series)

Endpoint: `POST https://atlas.hks.harvard.edu/api/graphql`

```graphql
query {
  countryYear(countryId: 404, yearMin: 2024, yearMax: 2024) {
    eci
    coi
    growthProj   # same growth projection value, no rank or classification
    gdppc
  }
}
```

Note: The Explore API `growthProj` field gives the numeric value only. For the rank, classification, and relative-to-income assessment, use the Country Pages API `countryProfile`.

### SQL equivalent (Atlas data DB)

The `{schema}.country_year` table (schemas: `hs92`, `hs12`, `sitc`) contains raw numeric values:

```sql
-- Growth projection and complexity metrics for a single country-year
SELECT
    cy.year,
    cy.eci,
    cy.coi,
    cy.growth_proj,   -- 10-year annualized GDP per capita growth forecast
    cy.diversity,     -- count of products with RCA >= 1
    cy.gdppc,
    cy.gdppc_ppp
FROM hs92.country_year cy
JOIN public.location l ON cy.country_id = l.id
WHERE l.code = 'KEN'            -- ISO alpha-3
  AND cy.year = 2023
ORDER BY cy.year;
```

**Important:** `growth_proj`, `eci`, and `coi` are all stored as `DOUBLE PRECISION`. The diversification grade, policy recommendation, and growth projection classification are **not stored in SQL** — they are derived and returned only by the Country Pages API `countryProfile` query.

---

## Diversification Grades: New Product Counts, Letter Grades, and Performance Interpretation

Displayed on the `/countries/{id}/new-products` page top bar as a letter grade (e.g., "B").

### How Diversification Grades Are Calculated: New Product Counting Methodology

Diversification grades rank countries by the number of new products they have successfully added to their export basket over an approximately 15-year window (default 2009–2024 as of the latest data). A "new product" is determined by recomputing RCA from 3-year averaged export values at each end of the window:

1. **Start period:** Average each country-product `export_value` over the first 3 years (e.g., 2009–2011). Compute RCA from those averages across all countries and all 4-digit products in the chosen classification.
2. **End period:** Repeat for the last 3 years (e.g., 2022–2024).
3. A product is **"new"** if its start-period RCA < 0.5 AND end-period RCA >= 1.0.

The Atlas Country Pages default to HS92 for this calculation, but the same method works with any classification (HS12, SITC). Note that HS12 data starts in 2012, so the maximum window is shorter (~11 years). All 4-digit products are eligible (including natural resources). No product filters are applied. This filters out noisy, one-off export spikes — only sustained new comparative advantages qualify.

### Grade thresholds

| Grade | Threshold |
|---|---|
| A+ | Top 10 countries by new product count |
| A | >= 30 new products |
| B | >= 13 new products |
| C | >= 6 new products |
| D | >= 3 new products |
| D- | < 3 new products |

### API enum

```
DiversificationGrade: APlus | A | B | C | D | DMinus
```

The documentation needs analysis (section 9.6) also notes a finer scale `A+, A, A-, B+, B, B-, C+, C, C-, D+, D, D-` but the API enum observed in live introspection has six values: `APlus`, `A`, `B`, `C`, `D`, `DMinus`.

### Querying diversification grades

```graphql
# Single country
query {
  countryProfile(location: "location-404") {
    diversificationGrade    # e.g., "B"
    diversityRank           # e.g., 38 (out of 145)
    diversity               # e.g., 226 (products with RCA > 1)
  }
}

# All countries (for comparisons)
query {
  allCountryProfiles {
    location { shortName code }
    diversificationGrade
  }
}
```

For the peer comparison table (comparing a country with 3 similar countries by new product count):

```graphql
query {
  newProductsComparisonCountries(location: "location-404") {
    location { shortName }
    newProductCount
    newProductExportValue
    newProductExportValuePerCapita
  }
}
```

### Interpreting Diversification Performance

Beyond the letter grade, several qualitative factors help assess whether a country's diversification is meaningful:

- **Composition of new products matters as much as count.** If a single low-complexity commodity accounts for over 70% of new export value, the country has not meaningfully broadened its productive capabilities — even if the total dollar value appears large. Ideally, new products should span multiple sectors and reflect movement into higher-complexity goods.

- **Share of total exports from new products.** A small share (e.g., only 4% of total exports) signals that the export structure has remained largely unchanged and diversification efforts have had limited impact on the overall economy. The API field `newProductExportValue / exportValue` captures this ratio.

- **Per-capita income contribution.** The number of new products and the income per capita they contribute (via `newProductExportValuePerCapita`) measures how successfully a country has translated diversification into broad-based income growth.

- **Peer comparison context.** A country that added 9 new products contributing $94 per capita looks markedly different from a peer that added 35 new products contributing $310 per capita. Use `newProductsComparisonCountries` to contextualize performance against similar economies.

---

## Growth Projections: 10-Year GDP Per Capita Forecast Model

### Overview: 10-Year GDP Per Capita Growth Forecast

The Atlas provides a **10-year GDP per capita growth forecast** for each of the 145 countries. Displayed as an annualized rate (e.g., "Kenya is expected to grow 3.4% per year over the next 10 years"). The current forecast horizon ends approximately 10 years from the most recent data year.

### Methodology: OLS regression model

The projection uses OLS regression with 5 features + decade dummies. Dependent variable: annualized 10-year constant GDP per capita growth.

| Factor | Variable | Direction |
|---|---|---|
| **Log GDP per capita** | `ln_gdppc_const` | Convergence: poorer countries (complex relative to income) grow faster |
| **Natural resource change** | `nr_growth_10` | 10-year change in real NR net exports per capita; resource-driven income is discounted |
| **ECI** | `eci` (SITC classification) | Higher ECI → faster growth |
| **COI** | `oppval` (Complexity Outlook Index) | Higher COI → faster growth |
| **ECI × COI interaction** | `eci_oppval` | Captures synergy between complexity level and opportunity connectedness |

**Procedure:** 10 separate cohort regressions are run (one per digit year), outliers > 2.5× RMSE are removed, crisis countries (VEN, LBN, YEM) are excluded from training, and high-growth Asian countries (CHN, KOR, SGP) are restricted to post-1989 data. Final GDP growth = `100 × ((1 + point_est) × (1 + pop_est) - 1)`.

The key empirical finding: countries whose ECI is higher than their income level would predict tend to grow faster, converging toward the income level their complexity "deserves." The inverse is true for countries propped up by resource rents with low underlying complexity.

### API fields

| Field | Type | Description |
|---|---|---|
| `growthProjection` | `Float` | Annualized 10-year GDP per capita growth rate (e.g., `0.034` = 3.4%/yr) |
| `growthProjectionRank` | `Int` | Rank out of 145 (1 = fastest projected growth) |
| `growthProjectionClassification` | `GrowthProjectionClassification` enum | `rapid` \| `moderate` \| `slow` |
| `growthProjectionRelativeToIncome` | `GrowthProjectionRelativeToIncome` enum | `More` \| `ModeratelyMore` \| `Same` \| `ModeratelyLess` \| `Less` |
| `growthProjectionPercentileClassification` | `GrowthProjectionPercentileClassification` enum | `TopDecile` \| `TopQuartile` \| `TopHalf` \| `BottomHalf` |

### SQL field

```sql
-- Raw growth projection (numeric value only — no classification)
SELECT cy.growth_proj
FROM hs92.country_year cy
JOIN public.location l ON cy.country_id = l.id
WHERE l.code = 'KEN' AND cy.year = 2023;
```

Column: `growth_proj DOUBLE PRECISION` in `{schema}.country_year`.

---

## Complexity-Income Relationship: Why ECI Predicts Growth and How Natural Resources Distort It

### ECI vs. Income Level: More or Less Complex Than Expected

When the Atlas says a country is "more complex than expected for its income level," it uses an OLS regression of ECI rank on GDP per capita rank across all 145 countries. The country's observed ECI rank is compared against the prediction interval:

- **More complex than expected** (`growthProjectionRelativeToIncome: More` or `ModeratelyMore`): ECI rank is better (lower number) than the lower prediction bound → predicted to grow faster
- **In line with expectations** (`Same`): ECI rank is within the prediction interval
- **Less complex than expected** (`Less` or `ModeratelyLess`): ECI rank is worse (higher number) than the upper prediction bound → predicted to grow more slowly (often indicates resource-driven income)

This is the fundamental tension the Atlas identifies: complexity should determine income, but resource rents allow some countries to have high income without complex exports. Those countries typically receive a `TechFrontier` classification *not* because of innovation capacity but because their income level puts them above what their ECI would suggest.

### Why Natural Resources Distort the Complexity-Income Picture

Natural resource rents (oil, minerals, agricultural commodities) inflate national income without building the productive capabilities that ECI measures. A country can be wealthy from oil but have a narrow, low-complexity export basket. This creates a fundamental mismatch:

- **High income + low ECI** = income is sustained by resource extraction, not by productive knowledge. This is inherently fragile — when resource revenues decline (depletion, price drops, energy transition), income falls toward the level that the country's actual capabilities can support.
- **The growth projection model accounts for this** by including natural resource export changes (`nr_growth_10`) as a separate predictor. Resource-driven income growth is effectively discounted relative to complexity-driven growth.
- **For strategic approach assignment**, raw ECI would misclassify resource-rich countries. A Gulf state with high GDP per capita but low ECI might appear to be "underperforming" on complexity when in fact its income is resource-driven, not complexity-driven. The adjusted metric corrects for this.

### The `eciNatResourcesGdpControlled` Metric

The field `eciNatResourcesGdpControlled` (available in both `countryProfile` and `allCountryProfiles`) represents the **natural-resource-and-GDP-controlled ECI** — the x-axis of the strategic approach scatter. It is the residual from a regression of ECI on GDP per capita and natural resource export share. A positive value means the country is more complex than its income and resource wealth would predict; a negative value means less complex.

This adjusted metric isolates the "genuine" productive complexity of the economy — the capabilities that exist independent of resource wealth. It answers the question: "Given this country's income level and resource endowment, is it more or less complex than expected?"

**Why the strategic approach algorithm uses this metric instead of raw ECI:** A resource-rich country with low raw ECI but high income would be classified as needing dramatic intervention. But its actual challenge is different — it needs to build productive capabilities to sustain income as resource revenues eventually decline. The adjusted metric correctly identifies this as a low-complexity position (negative `eciNatResourcesGdpControlled`), leading to appropriate policy guidance (typically Strategic Bets focused on building non-resource productive capabilities).

**Implications for growth projections:** Resource-rich countries with low adjusted complexity face slower projected long-term growth. Their current income exceeds what their productive capabilities can sustain, so the growth projection model predicts convergence downward — income declining toward the complexity-implied level. This is the mirror image of the complexity-income convergence story: just as high-complexity/low-income countries grow faster, low-complexity/high-income countries grow slower.

**The energy transition adds urgency:** The Growth Lab's Greenplexity Index (2024-2025) highlights that fossil-fuel-dependent countries face a compounding challenge. Their current export baskets are dominated by low-complexity peripheral products (oil, minerals), and many of the green products they would need to transition into (batteries, EVs, solar equipment, wind turbines) sit in the complex core of the product space. The periphery trap (see `product_space_and_relatedness.md`) makes these jumps structurally difficult. Countries that have not built non-resource productive capabilities face both declining resource revenues *and* large capability gaps to the emerging green economy.

**Product space position of resource products:** In the product space, petroleum, minerals, and raw agricultural goods are located in the sparse periphery with low proximity to complex products (Hidalgo et al., 2007). This means resource-rich countries face particularly large structural transformation challenges — the distance from resource products to complex manufactures is large, and the intermediate products along the way offer limited capability spillover.

---

## Frontier Countries: Growth Opportunities Unavailable, Subpage Data Sources, and Peer Comparisons

### Frontier Countries: Growth Opportunity Pages Unavailable

For highest-complexity countries (those assigned `TechFrontier`), the Atlas does not show growth opportunity pages:

- `/countries/{id}/growth-opportunities` → "Visualization not available for highest complexity countries"
- `/countries/{id}/product-table` → same message

**Why:** These countries have already developed comparative advantage in most nearby products. The distance × complexity scatter has no meaningful "missing nearby opportunities" to recommend.

**Workaround:** The Explore API feasibility page (`/explore/feasibility?year={year}&exporter=country-{id}`) is available for all countries including frontier countries. It shows the same underlying distance × COG data in a different visualization context.

### Strategic Approach Subpage: Data Sources

URL: `/countries/{id}/strategic-approach`

| Data element | API query | Key fields |
|---|---|---|
| This country's assigned approach | `countryProfile` | `policyRecommendation` |
| All countries' positions (scatter dots) | `allCountryProfiles` + `allCountryYear` | `eciNatResourcesGdpControlled`, `coi` |
| Approach description text | Derived from `policyRecommendation` enum | — |

The page title asks two questions that map directly to the two axes:
1. "Is the {country} complex enough for its income to grow?" → x-axis (`eciNatResourcesGdpControlled`)
2. "Is the {country} well-connected to many new opportunities (COI)?" → y-axis (`latestCoi`)

### Peer Country Comparisons (New Products Page)

On `/countries/{id}/new-products`, the Atlas compares the selected country with 3 peer countries in a table showing new product count, total value, and per-capita value.

Peers are selected by the Country Pages API based on similarity (income level, region, complexity). The selection is not configurable by the user and is opaque (no documented algorithm). The comparison is returned by:

```graphql
query {
  newProductsComparisonCountries(location: "location-404", quantity: 3) {
    location { shortName code }
    newProductCount
    newProductExportValue
    newProductExportValuePerCapita
  }
}
```

Example output for Kenya (M49: 404): Uganda (28 products/$211M), Kenya (24/$260M), Ethiopia (21/$181M), Tanzania (17/$468M).

---

## Quick Reference: Strategic Approach Enums, Diversification Enums, and API Routing

### All Enum Values

| Enum | Values |
|---|---|
| `PolicyRecommendation` | `LightTouch`, `ParsimoniousIndustrial`, `StrategicBets`, `TechFrontier` |
| `DiversificationGrade` | `APlus`, `A`, `B`, `C`, `D`, `DMinus` |
| `COIClassification` | `low`, `medium`, `high` |
| `GrowthProjectionClassification` | `rapid`, `moderate`, `slow` |
| `GrowthProjectionRelativeToIncome` | `More`, `ModeratelyMore`, `Same`, `ModeratelyLess`, `Less` |
| `GrowthProjectionPercentileClassification` | `TopDecile`, `TopQuartile`, `TopHalf`, `BottomHalf` |

### API Routing Summary

| User question | Query type | API |
|---|---|---|
| "What is Kenya's strategic approach?" | `country_profile_complexity` | Country Pages |
| "What does 'Strategic Bets' mean?" | No query needed — use this doc | — |
| "What is Kenya's growth projection?" | `country_profile_complexity` | Country Pages |
| "How does Kenya's growth projection compare to Tanzania's?" | `country_profile_complexity` (both) | Country Pages |
| "What is Kenya's diversification grade?" | `country_profile_complexity` | Country Pages |
| "Why can't I see growth opportunities for the USA?" | No query needed — use this doc | — |
| "List all countries with Strategic Bets approach" | `allCountryProfiles` | Country Pages |
| "Kenya's ECI time series" | `country_year` | Explore API |
