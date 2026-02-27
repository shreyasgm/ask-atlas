---
title: Data Coverage
purpose: >
  Reference for what trade data is available in the Atlas of Economic Complexity
  — which classification systems, year ranges, country sets, and data quality
  flags exist, and where each dataset can be accessed (SQL database vs. GraphQL
  API).
keywords:
  - data coverage
  - year range
  - classification system
  - HS92
  - HS12
  - HS22
  - SITC
  - services
  - country inclusion
  - data quality flags
  - data_flags
  - in_rankings
  - in_cp
  - missing data
  - 6-digit
  - product granularity
  - schema
  - SQL database
  - GraphQL API
  - data update cycle
when_to_load: >
  Load when a user explicitly asks about data coverage caveats or limitations —
  e.g., "why is my query returning no data?", "does Atlas have data before 1995?",
  "why is [country] missing from ECI rankings?", "what data quality flags exist?".
  Also load when a proposed query would fail due to year or classification boundary
  constraints.
when_not_to_load: >
  Do not load for routine classification questions (see classification_systems.md)
  or for country naming and eligibility mechanics (see country_entities.md).
related_docs:
  - classification_systems.md
  - country_entities.md
---

## Classification Systems and Year Coverage

| Classification | Full Name | Year Range | ~# Products (4-digit) | Available via SQL DB? | Available via Explore API (GraphQL)? | Available via Country Pages API? |
|---|---|---|---|---|---|---|
| **HS92** | Harmonized System 1992 | 1995–2024 | ~1,200 | Yes (default) | Yes (`HS92`) | Yes (as `HS`) |
| **HS12** | Harmonized System 2012 | 2012–2024 | ~1,200 | Yes | Yes (`HS12`) | No |
| **HS22** | Harmonized System 2022 | 2022–2024 | ~1,200 | **No — Explore API only** | Yes (`HS22`) | No |
| **SITC** | Standard International Trade Classification Rev. 2 | 1962–2024 | ~700 | Yes | Yes (`SITC`) | Yes (as `SITC`) |
| **Services** | IMF DOTS services trade | 1980–2024 | ~12–15 categories | Yes (`services_unilateral`, `services_bilateral` schemas) | Yes (`servicesClass: unilateral`) | Yes (bundled) |

**Critical note on HS22:** HS22 data exists only in the Explore GraphQL API (`/api/graphql`). There is no `hs22` schema in the SQL database and no HS22 support in the Country Pages API. Any SQL query requesting HS22 data will fail. Redirect HS22 requests to the GraphQL pipeline.

**Critical note on 6-digit products:** 6-digit product granularity is supported in the Explore API (`productLevel: 6`) but complexity metrics (ECI, PCI, RCA, COG, distance) are not available at the 6-digit level in either API or SQL. The SQL test seed skips 6-digit tables entirely because they are extremely large.

## Product Granularity Levels

| Level | Approximate Count (HS92) | Complexity Metrics Available? | Available in SQL? | Available via Explore API? |
|---|---|---|---|---|
| 1-digit | ~20 | Yes | Yes | Yes |
| 2-digit | ~97 | Yes | Yes | Yes |
| 4-digit | ~1,200 | Yes | Yes | Yes |
| 6-digit | ~5,000 | **No** | Yes (tables exist but very large) | Yes (`productLevel: 6`) |

## Services Trade

- **Coverage:** Approximately 50–75% of Atlas countries report services data.
- **Year range:** 1980–2024 (coverage varies by country; many countries start later).
- **Categories:** ~12–15 broad categories (e.g., Travel & tourism, Transport, ICT, Business, Financial, Insurance, Government, Construction, Personal/cultural, Manufacturing on physical inputs, Other).
- **Complexity metrics:** Not available for services (no standard RCA, PCI, or COG for services products).
- **SQL schemas:** `services_unilateral` and `services_bilateral`.
- **Important discrepancy:** The Atlas Explore treemap **Products mode** includes services in its total export value. The **Locations mode** (bilateral) is goods-only — services bilateral data is excluded. This is why Products mode and Locations mode totals differ for the same country and year.
- **Country Pages:** Services are included in total export values shown in treemaps.

## Country Inclusion Criteria

Not all countries with trade data appear in all Atlas features.

| Feature | Inclusion Criteria |
|---|---|
| **Atlas Explore** (treemap, geomap, etc.) | All countries and territories in UN Comtrade — no minimum thresholds |
| **Country Profiles** (`in_cp = true`) | Population ≥ 1 million, average annual trade ≥ $1 billion, verified GDP and export data, consistent reporting history |
| **ECI Rankings** (`in_rankings = true`) | Same as Country Profiles, plus additional reliability checks |

Countries can exist in the SQL database and Explore API without appearing in Country Profiles or Rankings. The `data_flags` table and `classification.location_country` table both carry the `in_rankings` and `in_cp` boolean flags.

## `data_flags` Table (SQL: `public.data_flags`)

Per-country quality indicators used to determine rankings and profile eligibility.

| Column | Type | Meaning |
|---|---|---|
| `country_id` | INTEGER | UN M49 / ISO numeric country identifier |
| `former_country` | BOOLEAN | Historical entity (e.g., Soviet Union, Yugoslavia) |
| `min_population` | BOOLEAN | Meets population ≥ 1M threshold |
| `population` | BIGINT | Actual population value |
| `min_avg_export` | BOOLEAN | Meets average annual trade ≥ $1B threshold |
| `avg_export_3` | BIGINT | 3-year average export value (USD) |
| `complexity_current_year_coverage` | BOOLEAN | Sufficient data for current-year ECI/PCI |
| `complexity_lookback_years_coverage` | BOOLEAN | Sufficient data for multi-year complexity trends |
| `services_any_coverage` | BOOLEAN | Country has any services trade data |
| `services_current_years_coverage` | BOOLEAN | Country has recent services data |
| `imf_any_coverage` | BOOLEAN | Country has any IMF economic indicator data |
| `imf_current_years_coverage` | BOOLEAN | Country has recent IMF data |
| `imf_lookback_years_coverage` | BOOLEAN | Country has historical IMF data |
| `rankings_eligible` | BOOLEAN | Meets all criteria to appear in rankings |
| `country_profiles_eligible` | BOOLEAN | Meets all criteria for a Country Profile |
| `in_rankings` | BOOLEAN | Currently included in ECI rankings (may override eligibility) |
| `in_cp` | BOOLEAN | Currently has a Country Profile |
| `in_mv` | BOOLEAN | Included in "market view" features |

`rankings_override` and `cp_override` columns allow manual inclusion/exclusion independent of computed eligibility.

## SQL Database Schemas

The Atlas SQL database contains these top-level schemas:

| Schema | Contents |
|---|---|
| `public` | `data_flags`, `year` (deflators) |
| `classification` | `location_country`, `product_hs92`, `product_hs12`, `product_sitc`, `product_services_unilateral`, `product_services_bilateral`, `product_hs92_ps_clusters`, `product_hs92_ps_edges` |
| `hs92` | HS 1992 trade tables (country_product_year, country_country_product_year, etc.) at 1/2/4-digit levels |
| `hs12` | HS 2012 trade tables at 1/2/4-digit levels |
| `sitc` | SITC trade tables at 1/2/4-digit levels |
| `services_unilateral` | Unilateral services trade |
| `services_bilateral` | Bilateral services trade |

**No `hs22` schema exists in the SQL database.**

Table naming convention: `{schema}.{facet}_{digit_level}` — e.g., `hs92.country_product_year_4` = HS92, country × product × year, 4-digit level.

## GraphQL API vs. SQL: Data Access Summary

| Data | SQL DB | Explore API (`/api/graphql`) | Country Pages API (`/api/countries/graphql`) |
|---|---|---|---|
| HS92 trade (1995–2024) | Yes | Yes | Yes (generic `HS`) |
| HS12 trade (2012–2024) | Yes | Yes | No |
| HS22 trade (2022–2024) | **No** | Yes | No |
| SITC trade (1962–2024) | Yes | Yes | Yes |
| Services trade (1980–2024) | Yes | Yes | Yes |
| 6-digit product detail | Yes (large tables) | Yes | No |
| Complexity metrics (ECI, PCI, RCA, COG) | Yes (1–4 digit) | Yes (1–4 digit) | Yes |
| 6-digit complexity metrics | No | No | No |
| Growth projections, strategic approach | No | No | Yes |
| Bilateral goods trade | Yes | Yes | No |
| Bilateral services trade | Partial | No | No |
| Group/regional aggregates | No | Yes | Partial |
| Product space edges (proximity) | Yes (`product_hs92_ps_edges`) | Yes (`productProduct`) | No |

## Data Update Cycle

- **Annual update:** ~95% of data updated once per year, typically April–June.
- **Lag:** Country reporting to UN Comtrade requires 12–18 months. For example, most 2024 trade data becomes available in the Atlas between April–June 2026.
- **Current latest year:** 2024 (for HS92, HS12, HS22, and SITC in the Explore API).
- **Historical revisions:** Annual releases may incorporate corrections to prior years.
- **Interim updates:** Ongoing throughout the year for newly submitted country data.

## Complexity Data Availability Constraints

- Complexity metrics (ECI, PCI, RCA, COG, distance, product_status) are **included** for unilateral and product trade datasets at 1–4 digit levels.
- Complexity metrics are **not available** for:
  - 6-digit granularity
  - Bilateral trade data
  - Services trade

## Data Sources

| Data Type | Raw Source |
|---|---|
| Goods trade | UN Comtrade (reconciled using Growth Lab reliability-weighted mirroring methodology) |
| Services trade | IMF Direction of Trade Statistics (DOTS) |
| Economic indicators (GDP, population) | IMF World Economic Outlook (WEO) |
| Inflation deflators | Federal Reserve Economic Data (FRED), Producer Price Index for Industrial Commodities |

All trade values are in **current USD** (nominal). Constant-dollar values use the `public.year` table deflators, with the base year set to the most recent Atlas data year (currently 2024).

## Common Data Boundary Questions

| User Question | Answer |
|---|---|
| "Does Atlas have data from 1960?" | No. Earliest is SITC at 1962; HS92 starts 1995. |
| "Can I get HS22 data from SQL?" | No. HS22 is Explore API (GraphQL) only. |
| "Why is [small country] missing from rankings?" | Must meet population ≥ 1M and avg. trade ≥ $1B thresholds. Check `data_flags.in_rankings`. |
| "Why does my Products mode total differ from Locations mode?" | Products mode includes services; Locations (bilateral) mode is goods only. |
| "Is 2024 data available?" | Yes, for HS92/HS12/HS22/SITC in the Explore API. Annual release typically April–June 2026. |
| "Can I get product complexity at 6-digit level?" | No. Complexity metrics stop at 4-digit. |
| "Does Atlas cover services bilateral trade?" | Partially. `services_bilateral` schema exists in SQL. Explore API Locations mode does not include services. |
