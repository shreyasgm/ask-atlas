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

## The Four Strategic Approaches

Countries are assigned one of four strategic approaches based on their position on a scatter plot with two axes:

- **X-axis**: "Is the country complex enough for its income to grow?" — measured by `eciNatResourcesGdpControlled` (ECI adjusted for natural resources and GDP per capita). Countries to the right are more complex than their income level predicts.
- **Y-axis**: "Is the country well-connected to many new opportunities?" — measured by COI (Complexity Outlook Index). Countries higher up have more nearby complex products.

The scatter plot appears at `/countries/{id}/strategic-approach`. All 145 countries are plotted. The selected country is highlighted in its quadrant.

### Quadrant Map

```
High COI │ PARSIMONIOUS INDUSTRIAL │  LIGHT TOUCH
         │ POLICY (top-left)       │  (top-right)
─────────┼─────────────────────────┼──────────────
Low COI  │ STRATEGIC BETS          │  TECHNOLOGICAL
         │ (bottom-left)           │  FRONTIER (bottom-right)
         └─────────────────────────────────────────
                Low rel. complexity   High rel. complexity
```

### Approach Definitions

| Approach | API Enum | Quadrant | Condition | Policy Logic |
|---|---|---|---|---|
| **Light Touch** | `LightTouch` | Top-right | High COI + High relative complexity | Country is complex and well-connected to opportunities. Ample space to diversify by leveraging existing successes. Minimal government intervention needed — markets are functioning. |
| **Parsimonious Industrial Policy** | `ParsimoniousIndustrial` | Top-left | High COI + Low relative complexity | Many opportunities nearby but current basket is simpler than income predicts. Targeted support for specific promising sectors. Easiest path to complexity growth. |
| **Strategic Bets** | `StrategicBets` | Bottom-left | Low COI + Low relative complexity | Few nearby opportunities and simple current basket. Must make deliberate, concentrated investments in specific sectors. Highest-risk approach — necessary for countries far from the complexity frontier. |
| **Technological Frontier** | `TechFrontier` | Bottom-right | Low COI + High relative complexity | Already at the frontier. Few unexploited nearby opportunities because most have been captured. Growth comes from innovation, not product diversification. Examples: USA, Germany, Japan. |

**Country examples:** Kenya → `LightTouch`; USA → `TechFrontier`; Spain and Turkiye → `LightTouch`.

---

## GraphQL API: Strategic Approach Fields

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

## Diversification Grades

Displayed on the `/countries/{id}/new-products` page top bar as a letter grade (e.g., "B").

### What diversification grades measure

Diversification grades rank countries by the number of new products they have successfully added to their export basket over an approximately 18-year window. A "new product" is one where:
- The first 3 years of the window all had RCA < 0.5 (not meaningfully exported)
- The last 3 years all had RCA >= 1.0 (now firmly exported)

This filters out noisy, one-off export spikes — only sustained new comparative advantages qualify.

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

---

## Growth Projections

### What they are

The Atlas provides a **10-year GDP per capita growth forecast** for each of the 145 countries. Displayed as an annualized rate (e.g., "Kenya is expected to grow 3.4% per year over the next 10 years"). The current forecast horizon ends approximately 10 years from the most recent data year.

### Methodology: four-factor model

The projection model is based on complexity-growth regressions from the Growth Lab (Hausmann, Hidalgo et al.). Four factors predict 10-year GDP per capita growth:

| Factor | Description | Direction |
|---|---|---|
| **ECI** | Economic Complexity Index — current productive capabilities | Higher ECI → faster growth |
| **COI** | Complexity Outlook Index — connectedness to new complex products | Higher COI → faster growth |
| **Current income level** | GDP per capita | Countries complex *relative to income* grow faster (convergence effect) |
| **Natural resource export share** | Expected natural resource exports per capita | Resource-driven income is "discounted" — it doesn't reflect productive capabilities |

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

## Complexity-Income Relationship

### What it means

When the Atlas says a country is "more complex than expected for its income level," it uses an OLS regression of ECI rank on GDP per capita rank across all 145 countries. The country's observed ECI rank is compared against the prediction interval:

- **More complex than expected** (`growthProjectionRelativeToIncome: More` or `ModeratelyMore`): ECI rank is better (lower number) than the lower prediction bound → predicted to grow faster
- **In line with expectations** (`Same`): ECI rank is within the prediction interval
- **Less complex than expected** (`Less` or `ModeratelyLess`): ECI rank is worse (higher number) than the upper prediction bound → predicted to grow more slowly (often indicates resource-driven income)

This is the fundamental tension the Atlas identifies: complexity should determine income, but resource rents allow some countries to have high income without complex exports. Those countries typically receive a `TechFrontier` classification *not* because of innovation capacity but because their income level puts them above what their ECI would suggest.

### The scatter chart x-axis

The field `eciNatResourcesGdpControlled` (available in both `countryProfile` and `allCountryProfiles`) represents the **natural-resource-and-GDP-controlled ECI** — the x-axis of the strategic approach scatter. It is the residual from a regression of ECI on GDP per capita and natural resource export share. A positive value means the country is more complex than its income and resource wealth would predict; a negative value means less complex.

---

## Frontier Countries: Growth Opportunity Pages Unavailable

For highest-complexity countries (those assigned `TechFrontier`), the Atlas does not show growth opportunity pages:

- `/countries/{id}/growth-opportunities` → "Visualization not available for highest complexity countries"
- `/countries/{id}/product-table` → same message

**Why:** These countries have already developed comparative advantage in most nearby products. The distance × complexity scatter has no meaningful "missing nearby opportunities" to recommend.

**Workaround:** The Explore API feasibility page (`/explore/feasibility?year={year}&exporter=country-{id}`) is available for all countries including frontier countries. It shows the same underlying distance × COG data in a different visualization context.

---

## Strategic Approach Subpage: Data Sources

URL: `/countries/{id}/strategic-approach`

| Data element | API query | Key fields |
|---|---|---|
| This country's assigned approach | `countryProfile` | `policyRecommendation` |
| All countries' positions (scatter dots) | `allCountryProfiles` + `allCountryYear` | `eciNatResourcesGdpControlled`, `coi` |
| Approach description text | Derived from `policyRecommendation` enum | — |

The page title asks two questions that map directly to the two axes:
1. "Is the {country} complex enough for its income to grow?" → x-axis (`eciNatResourcesGdpControlled`)
2. "Is the {country} well-connected to many new opportunities (COI)?" → y-axis (`latestCoi`)

---

## Peer Country Comparisons (New Products Page)

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

## Quick Reference: All Enum Values

| Enum | Values |
|---|---|
| `PolicyRecommendation` | `LightTouch`, `ParsimoniousIndustrial`, `StrategicBets`, `TechFrontier` |
| `DiversificationGrade` | `APlus`, `A`, `B`, `C`, `D`, `DMinus` |
| `COIClassification` | `low`, `medium`, `high` |
| `GrowthProjectionClassification` | `rapid`, `moderate`, `slow` |
| `GrowthProjectionRelativeToIncome` | `More`, `ModeratelyMore`, `Same`, `ModeratelyLess`, `Less` |
| `GrowthProjectionPercentileClassification` | `TopDecile`, `TopQuartile`, `TopHalf`, `BottomHalf` |

---

## API Routing Summary

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
