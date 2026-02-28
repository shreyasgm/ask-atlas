# API Capability Reference: SQL vs GraphQL

> Extracted from `docs/hybrid_backend_analysis.md` (2026-02-21).
> Maps eval questions to routing decisions between SQL and GraphQL backends.

---

## Comparative Analysis

### What SQL Can Do That GraphQL Cannot

| Capability | SQL Method | GraphQL Status |
|---|---|---|
| Arbitrary aggregation (SUM, AVG, GROUP BY) | Native SQL | Not possible |
| Window functions (RANK, ROW_NUMBER) | Native SQL | Not possible |
| Cross-country product ranking ("who exports most cars?") | Single query | Broken (CPY_P) |
| Regional aggregation ("total African exports") | JOIN with location_group | Broken (allGroups) |
| Product-level time series | Single query with year range | Broken (productYearRange) |
| 6-digit product granularity | `_6` table suffix | Not available |
| Multiple HS revisions (HS92, HS96, HS02, HS07, HS12) | Schema selection | Only generic "HS" |
| Product proximity (product-product relatedness) | `product_product_4` tables | Only x,y coordinates |
| Custom CAGR between arbitrary years | SQL computation | Only pre-computed 3/5/10/15yr |
| Product-level lookback tables | `country_product_lookback` tables | Only hs92 schema |
| Group-to-group trade | `group_group_product_year` tables | Broken |
| Complex multi-table JOINs | Native SQL | Not possible |
| SITC product-level trade data | SITC schema tables | Broken (SITC treeMap) |
| Custom derived metrics | SQL computation | Not possible |

### What GraphQL Can Do That SQL Cannot

| Capability | GraphQL Query | SQL Status |
|---|---|---|
| Policy recommendations | `countryProfile.policyRecommendation` | **Not in database** |
| Diversification grade (A+ to D-) | `countryProfile.diversificationGrade` | **Not in database** |
| Growth projection + classification | `countryProfile.growthProjection*` | **Not in database** |
| Structural transformation status | `countryProfile.structuralTransformation*` | **Not in database** |
| COI classification (low/medium/high) | `countryProfile.coiClassification` | **Not in database** |
| Market share main sector + direction | `countryProfile.marketShareMainSector*` | **Not in database** |
| New products income/growth comments | `countryProfile.newProducts*Comments` | **Not in database** |
| Growth projection relative to income | `countryProfile.growthProjectionRelativeToIncome` | **Not in database** |
| Comparison peer countries | `countryProfile.comparisonLocations` | **Not in database** |
| Pre-computed per-product CAGR | `countryProductLookback` | Available in lookback tables (hs92 only) |
| Export growth classification | `countryLookback.exportValueGrowthClassification` | **Not in database** |
| GDP growth vs regional avg | `countryLookback.gdpPcConstantCagrRegionalDifference` | **Not in database** |
| Decile classifications (opportunity/distance/PCI) | `allCountryProductYear` | Would need to compute |

**Key insight:** The ~12 derived metrics marked "Not in database" represent real analytical value computed by the Atlas team's algorithms. These cannot be reproduced from raw SQL queries without implementing those algorithms.

### Performance Comparison

| Aspect | SQL Backend | GraphQL API |
|---|---|---|
| Latency per query | ~200-500ms (DB query) + ~2-5s (LLM chain) | ~200-500ms (HTTP call) |
| LLM calls per question | 2-4 (extract, lookup, generate, agent) | 0 (deterministic routing) |
| Error rate | Moderate (SQL generation errors) | Low for working queries; 32% endpoints broken |
| Infrastructure | PostgreSQL database required | No infrastructure needed |
| Cost per query | ~$0.01-0.05 (LLM tokens) | Free |
| Concurrent capacity | Limited by DB pool (10+20 overflow) | Unknown (external) |

---

## Eval Question Mapping (All 60 Questions)

### Routing Legend

- **GQL**: Can be answered entirely via GraphQL API
- **SQL**: Requires SQL backend (GraphQL cannot answer or answers poorly)
- **EITHER**: Both can answer equally well
- **GQL+**: GraphQL provides better/richer answer than SQL (pre-computed metrics)
- **N/A**: No data access needed (LLM behavior test)

### Total Export Values (Q1-3)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 1 | Total exports Brazil 2018 | EITHER | `countryYear(location: "location-76", year: 2018)` → `exportValue` | `SELECT export_value FROM hs92.country_year WHERE iso3_code='BRA' AND year=2018` | Both return same value |
| 2 | Crude oil from Nigeria 2020 | EITHER | `treeMap(CPY_C, location: "location-566", product: "product-HS-910", year: 2020)` | `SELECT export_value FROM hs92.country_product_year_4 WHERE code='2709' AND iso3_code='NGA' AND year=2020` | GraphQL needs product ID lookup |
| 3 | Service exports Singapore 2018 | EITHER | `treeMap(CPY_C, section level)` filter for Services section | `SELECT export_value FROM services_unilateral.country_year WHERE iso3_code='SGP' AND year=2018` | |

### Sectoral Export Composition (Q4-16)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 4 | Germany automotive % of total | EITHER | `treeMap(CPY_C, section)` → compute Vehicles/total | SQL can do this in one query | GraphQL needs client-side computation |
| 5 | Top 5 Canada sectors 2021 | GQL | `treeMap(CPY_C, section, location: "location-124", year: 2021)` → sort | Single query, pre-sorted | Verified working |
| 6 | Top 3 India products 2020 | GQL | `treeMap(CPY_C, fourDigit, location: "location-356", year: 2020)` → top 3 | Returns all ~1,248 products | Client sorts |
| 7 | Mineral share South Africa 2019 | GQL | `treeMap(CPY_C, section)` → Minerals / total | Simple computation | |
| 8 | Services share France 2022 | GQL | `treeMap(CPY_C, section)` → Services / total | SQL needs UNION ALL goods+services | GraphQL simpler |
| 9 | Mineral vs service share Chile 2017 | GQL | `treeMap(CPY_C, section, year: 2017)` | One call gives both | |
| 10 | Top 3 mineral products Peru 2016 | GQL | `treeMap(CPY_C, fourDigit)` → filter by topLevelParent=Minerals → top 3 | SQL with JOIN to parent | |
| 11 | Top 3 UK service sectors 2019 | GQL | `treeMap(CPY_C, section)` → filter Services sub-products | Services have 5 categories | |
| 12 | Switzerland services % 2022 | GQL | `treeMap(CPY_C, section)` → Services / total | | |
| 13 | Travel share Thailand services 2022 | GQL | `treeMap(CPY_C, fourDigit)` → travel / sum(services) | Filter by productType=Service | |
| 14 | Transport share Netherlands 2017 | GQL | Same approach as Q13 | | |
| 15 | ICT vs Travel growth Ireland 2015-2021 | SQL | Needs 2 treeMap calls (2015 + 2021); no product-level time series | Single query with year range | SQL more efficient |
| 16 | Service trends Spain over decade | SQL | Would need ~10 treeMap calls (one per year) | Single query | SQL far more efficient for time series |

### Trade Partners and Market Position (Q17-24)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 17 | Japan export destinations 2021 | GQL | `treeMap(CCY_C, location: "location-392", year: 2021)` | SQL JOIN country_country_year | Verified working |
| 18 | Australia % to SE Asia | SQL | `treeMap(CCY_C)` gives partners but no group info; must hardcode SE Asia countries | SQL JOIN with location_group | SQL has group tables |
| 19 | Kenya trade balance top 10 partners | GQL | `treeMap(CCY_C)` has both exportValue and importValue per partner | Single call | |
| 20 | Singapore re-exports | SQL | Neither has re-export data directly | Neither can fully answer | Both need LLM interpretation |
| 21 | Australia iron ore market share Asia vs global | SQL | `globalMarketShare` is global only; no regional market share | SQL can JOIN with regional groups | GraphQL lacks regional breakdown |
| 22 | China electronics market share | GQL | `treeMap(CPY_C)` with Electronics products → `globalMarketShare` | | Verified working |
| 23 | South Korea semiconductor share over decade | SQL | Need ~10 treeMap calls; no product time series | Single query | SQL much more efficient |
| 24 | Germany automotive market share | GQL | `treeMap(CPY_C)` → Cars(8703) → `globalMarketShare: 20.17%` | | Verified: 20.17% |

### Growth and Performance (Q25-31)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 25 | Spain export change 2016-2021 | EITHER | `countryYear` for 2016 + 2021, compute % change | Single query | GraphQL needs 2 calls |
| 26 | Vietnam CAGR 2010-2020 | GQL+ | `countryLookback(yearRange: TenYears)` → `exportValueConstGrowthCagr` | SQL must compute CAGR | GraphQL has pre-computed CAGR |
| 27 | Netherlands vs Sweden growth 2012-2022 | GQL+ | `countryLookback` for each → compare CAGRs | SQL for both | GraphQL has pre-computed |
| 28 | Mineral fuel share Russia 2015-2021 | EITHER | 2 treeMap calls (2015 + 2021) | Single query | |
| 29 | Sectors driving Turkey growth 2015-2020 | GQL+ | `countryProductLookback(FiveYears, section)` → sort by exportValueConstGrowth | SQL with lookback tables or computation | GraphQL pre-computed |
| 30 | Pharma growth Switzerland 2010-2020 | EITHER | 2 treeMap calls with product filter | Single query | |
| 31 | Colombia market share change over 10 years | SQL | Need global totals + country totals over time; multiple calls | Single query with computation | SQL much more efficient |

### Economic Complexity (Q32-39)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 32 | Brazil ECI trend 15 years | GQL | `countryYearRange(minYear: 2009, maxYear: 2024)` → eci array | SQL from country_year | Both work; GraphQL returns clean time series |
| 33 | Vietnam complexity vs regional peers | SQL | Need to identify peers (no group queries), then compare | SQL JOIN with location_group | GraphQL lacks group queries |
| 34 | Top 5 products for SA complexity | GQL | `treeMap(CPY_C, fourDigit)` with `pci` + `rca` fields → filter RCA>1, sort by PCI | SQL with JOIN | GraphQL has PCI on treeMap |
| 35 | Indonesia high-complexity export proportion | SQL | Need PCI thresholds + time series; multiple calls | Single query with PCI JOIN | SQL more efficient for time series |
| 36 | Poland high-tech evolution 20 years | SQL | ~20 treeMap calls | Single query | SQL far more efficient |
| 37 | Egypt COI vs regional competitors | SQL | `countryProfile` for each country, but need to identify competitors (groups broken) | SQL JOIN with groups | |
| 38 | South Korea complexity growth industries | SQL | Complex multi-dimensional; would need many calls | Multiple SQL queries, but more efficient | |
| 39 | Malaysia export complexity vs global average | SQL | Need global average PCI computation | SQL computation | |

### Diversification Strategies (Q40-44)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 40 | Mexico diversification vs middle-income | SQL | Would need all middle-income countries (groups broken) | SQL with group tables | |
| 41 | Portugal Product Space opportunities | GQL+ | `treeMap(CPY_C)` with distance + opportunityGain + RCA < 1 → sort by opportunityGain | SQL with distance/COG | GraphQL has pre-computed fields |
| 42 | Argentina diversification risks | SQL | Complex multi-factor analysis | Multiple queries | Both need LLM interpretation |
| 43 | Ghana diversification potential | GQL+ | `treeMap(CPY_C)` with distance + opportunityGain + `countryProfile.diversificationGrade` | SQL + manual grade | GraphQL has diversification grade |
| 44 | Kazakhstan resource-based complexity | SQL | Need time series + resource identification + complexity analysis | Multiple queries | |

### Edge Cases (Q45-52)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 45 | Tuvalu exports 2020 | EITHER | `countryYear(location: "location-798", year: 2020)` | SQL query | Check data availability |
| 46 | Soviet Union 1990 | EITHER | `location(id: ...)` to check if exists | SQL query | Historical entity |
| 47 | Germany to Germany | GQL | `treeMap(CCY_C, location: "location-276", partner: "location-276")` | SQL bilateral self-trade | |
| 48 | Brazil 2030 | EITHER | `countryYear(year: 2030)` → returns error | SQL returns empty | Both should explain data limits |
| 49 | Products > $1T single country | SQL | Would need to scan all countries | Single SQL query with MAX | SQL much more efficient |
| 50 | Liechtenstein products + ECI | EITHER | `location` to check if in country pages; `countryProfile` if available | SQL query | Small country edge case |
| 51 | Japan RCA in bananas | GQL | `productSpace(location: "location-392")` → find banana code → `rca` | SQL query | GraphQL has RCA |
| 52 | South Sudan top 5 exports 2015 | GQL | `treeMap(CPY_C, fourDigit, location: "location-728", year: 2015)` → top 5 | SQL query | |

### Out-of-Scope Refusals (Q53-56)

| ID | Question | Route | Notes |
|---|---|---|---|
| 53 | Capital of France | N/A | LLM should refuse; no data access needed |
| 54 | SQL injection attempt | N/A | LLM should refuse; system should not execute |
| 55 | Nigeria protectionist policy | N/A | LLM should decline normative policy; may offer factual data |
| 56 | Python scraping script | N/A | LLM should refuse code generation |

### Data Availability Boundaries (Q57-60)

| ID | Question | Route | GraphQL Method | SQL Method | Notes |
|---|---|---|---|---|---|
| 57 | Bilateral services US-China | EITHER | `treeMap(CPY_C, partner)` with service products | `services_bilateral` schema | GraphQL integrates services |
| 58 | UK exports 1960 | EITHER | `countryYear(year: 1960)` → check if data exists | SQL query | Both should report data limits |
| 59 | Taiwan vs Chinese Taipei | EITHER | `allLocations` to find representation | SQL classification lookup | Entity resolution |
| 60 | Germany service imports | EITHER | `treeMap(CPY_C)` has importValue field | SQL services tables | Both have import data |

### Coverage Summary

| Route | Count | Questions |
|---|---|---|
| **GQL** (GraphQL sufficient) | 18 | 5,6,7,8,9,10,11,12,13,14,17,19,22,24,32,34,47,51 |
| **GQL+** (GraphQL provides richer answer) | 6 | 26,27,29,41,43,52 |
| **EITHER** (both work equally) | 13 | 1,2,3,4,25,28,30,45,48,50,57,58,60 |
| **SQL** (SQL required) | 15 | 15,16,18,20,21,23,31,33,35,36,37,38,39,40,44 |
| **N/A** (no data access) | 6 | 46,49,53,54,55,56 |
| **Entity resolution** | 2 | 42,59 |

**Conclusion:** ~37 of 60 questions (62%) can be handled by GraphQL alone or with GraphQL providing a richer answer. ~15 questions (25%) require SQL. The SQL-required questions are disproportionately the harder, more analytically interesting ones.
