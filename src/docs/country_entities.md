# Country Entities and Coverage

**Purpose:** Reference for how countries, territories, historical entities, and country groups are represented in Atlas data, including identifier formats across SQL and GraphQL interfaces.

**When to load this document:** Load when a country name might not match Atlas UN Comtrade naming conventions
(e.g., "Taiwan" = "Chinese Taipei"), when country identifier format matters
for API query construction (M49 integer vs. ISO alpha-3 vs. `"location-404"`
string), when asking about country group membership (regions, income levels,
trade blocs), or when historical entities (Soviet Union, Yugoslavia) are
involved. Also load when the user asks why a country is excluded from rankings —
this doc covers `in_rankings` eligibility criteria.

---

## Total Coverage

- **Atlas Explore** (the full database): data for all countries and territories covered by UN Comtrade (~250 countries and territories)
- **Country Profiles and Rankings**: restricted to ~145 countries that meet minimum coverage and quality thresholds (see `in_rankings` flag below)
- **Country selector on the Atlas website**: 145 countries shown in dropdown, each listed with name + ISO alpha-3 code (e.g., "Afghanistan (AFG)")

## Rankings Eligibility: `in_rankings` Flag

**~145 countries** appear in ECI/PCI rankings and have Country Profile pages (vs. ~250 total in the database). A country is included when it meets all four criteria:

1. **Population ≥ 1 million** — flagged by `min_population` (bool) in `public.data_flags`
2. **Average annual trade ≥ $1 billion** — flagged by `min_avg_export` (bool); actual 3-year average stored in `avg_export_3`
3. **Verified complexity data for current year** — flagged by `complexity_current_year_coverage` (float, threshold ~0.8)
4. **Sufficient lookback coverage for growth metrics** — flagged by `complexity_lookback_years_coverage` (float)

The `in_rankings` boolean in `classification.location_country` (mirrored in `public.data_flags`) is the definitive indicator. Countries excluded from rankings still have trade data rows — only their eligibility flag differs.

### Override Columns

Two columns in `classification.location_country` allow manual inclusion/exclusion regardless of automatic criteria:

| Column | Type | Effect |
|---|---|---|
| `rankings_override` | bool | If `true`, include in ECI/PCI rankings regardless of automatic criteria |
| `cp_override` | bool | If `true`, include in Country Profile pages regardless of automatic criteria |

Overrides are set administratively for special cases (e.g., Growth Lab project countries, territories with incomplete but important data).

### `public.data_flags` Key Columns

| Column | Type | Description |
|---|---|---|
| `country_id` | int4 | Links to `classification.location_country` |
| `in_rankings` | bool | Definitive rankings inclusion flag |
| `former_country` | bool | Historical entity no longer in existence |
| `rankings_eligible` | bool | Meets all automatic criteria (before override) |
| `country_profiles_eligible` | bool | Eligible for Country Profile page |
| `in_cp` | bool | Currently has a Country Profile page |
| `in_mv` | bool | Included in market visualization |
| `min_population` | bool | Population ≥ 1M threshold met |
| `population` | int8 | Latest available population figure |
| `min_avg_export` | bool | Average annual trade ≥ $1B threshold met |
| `avg_export_3` | int8 | 3-year average annual export value (USD) |
| `complexity_current_year_coverage` | float | Share of current-year data available for ECI computation |
| `complexity_lookback_years_coverage` | float | Share of lookback years available for growth metrics |
| `services_any_coverage` | bool | Has any services trade data |
| `imf_any_coverage` | bool | Has any IMF data |

## Country Naming Conventions

Country names in the Atlas follow official names as provided to **UN Comtrade**. This explains several non-obvious names:

| Atlas Name | Colloquial Name | ISO alpha-3 |
|---|---|---|
| Chinese Taipei | Taiwan | TWN |
| Türkiye | Turkey | TUR |
| Korea, Republic of | South Korea | KOR |
| Côte d'Ivoire | Ivory Coast | CIV |
| Kosovo | Kosovo | XKX |
| Lao PDR | Laos | LAO |
| Syrian Arab Republic | Syria | SYR |
| Viet Nam | Vietnam | VNM |

"Chinese Taipei" uses the UN Comtrade convention for Taiwan. Kosovo (XKX) has limited or no data in some schemas depending on UN reporting status. When a user asks about "Taiwan," the Atlas entity is "Chinese Taipei" with `iso3_code = 'TWN'`.

## Historical Entities

Countries that no longer exist appear in the data with `former_country = true` in `classification.location_country`. Trade data for historical entities is only available in the **SITC** schema (which covers 1962–2024), not in HS92 (starts 1995):

| Historical Entity | ISO alpha-3 | Approx. Data Range | Notes |
|---|---|---|---|
| Soviet Union | SUN | through ~1991 | Pre-Soviet republics not separately tracked |
| Yugoslavia | YUG | through ~1991 | |
| Czechoslovakia | CSK | through ~1992 | |

For pre-1995 queries about these entities, use the `sitc` schema. Exact year ranges available in the DB may differ; query `sitc.country_year WHERE country_id = (SELECT country_id FROM classification.location_country WHERE iso3_code = 'SUN')` to verify.

## Small and Micro States

Some countries (e.g., Tuvalu, Liechtenstein, San Marino) exist in the data but have very sparse trade records. They typically have `in_rankings = false` and may return few or no rows for many trade queries. The agent should warn about data limitations when querying these countries.

## Country-Specific Data Availability Notes

- **South Sudan (SSD)**: Independence 2011; data from ~2012 onward
- **Timor-Leste (TLS)**: Independence 2002; data from ~2002 onward
- **Kosovo (XKX)**: Coverage varies by schema; may have limited rows

## DB Tables for Country Lookup

### `classification.location_country` — primary reference table

| Column | Type | Description |
|---|---|---|
| `country_id` | int4 | Numeric country ID; coincides with M49 / ISO 3166-1 numeric code for most countries |
| `location_level` | ENUM(country, group) | Always `country` for individual countries |
| `iso3_code` | bpchar(3) | ISO alpha-3 code (e.g., `KEN`, `USA`, `DEU`) |
| `iso2_code` | bpchar(2) | ISO alpha-2 code (e.g., `KE`, `US`, `DE`) |
| `name_en` | text | Full official English name |
| `name_short_en` | text | Shorter display name |
| `name_es` | text | Spanish name |
| `name_abbr_en` | text | Abbreviated name |
| `incomelevel_enum` | ENUM(high, upper middle, lower middle, low) | World Bank income classification |
| `in_rankings` | bool | True = included in ECI/PCI rankings and Country Profile |
| `in_cp` | bool | True = has a Country Profile page |
| `in_mv` | bool | True = included in market visualization |
| `is_trusted` | bool | True = data quality considered reliable |
| `former_country` | bool | True = historical entity no longer existing |
| `country_project` | bool | Special Growth Lab project country |
| `rankings_override` | bool | Admin override for ranking inclusion |
| `cp_override` | bool | Admin override for Country Profile inclusion |
| `reported_serv` | bool | True = has any services trade data |
| `reported_serv_recent` | bool | True = has recent services trade data |
| `the_prefix` | bool | Whether "the" is used before the country name |
| `legacy_country_id` | int4 | Legacy internal ID |

### `classification.location_group` — country group definitions

| Column | Type | Description |
|---|---|---|
| `group_id` | int4 | Numeric group ID |
| `location_level` | ENUM(country, group) | Always `group` |
| `group_type` | ENUM(continent, political, region, rock_song, subregion, trade, wdi_income_level, wdi_region, world) | Type of grouping |
| `group_name` | text | Group name (e.g., "Sub-Saharan Africa", "ASEAN", "High income") |
| `parent_id` | int4 | Parent group ID |
| `parent_type` | same ENUM | Type of parent group |
| `parent_name` | text | Parent group name |
| `gdp_mean` | int8 | Mean GDP across member countries (latest year) |
| `gdp_sum` | int8 | Total GDP |
| `export_value_mean` | int8 | Mean export value |
| `export_value_sum` | int8 | Total export value |
| `export_value_cagr_3/5/10/15` | float8 | Export value compound annual growth rates |
| `export_value_non_oil_cagr_3/5/10/15` | float8 | Non-oil export CAGR |
| `gdp_cagr_3/5/10/15` | float8 | GDP CAGR |
| `gdp_const_cagr_3/5/10/15` | float8 | Constant-dollar GDP CAGR |
| `gdppc_const_cagr_3/5/10/15` | float8 | Constant-dollar GDP per capita CAGR |

### `classification.location_group_member` — country-to-group membership mapping

| Column | Type | Description |
|---|---|---|
| `group_id` | int4 | References `location_group.group_id` |
| `group_type` | same ENUM | Type of group |
| `group_name` | text | Group name |
| `country_id` | int4 | References `location_country.country_id` |

### `public.data_flags` — per-country data quality metadata

Key columns: `country_id`, `in_rankings`, `former_country`, `rankings_eligible`, `country_profiles_eligible`, `in_cp`, `in_mv`, `min_population` (bool), `population` (int8), `min_avg_export` (bool), `avg_export_3` (int8), `complexity_current_year_coverage`, `complexity_lookback_years_coverage`, `services_any_coverage`, `imf_any_coverage`.

## Country Group Types

The `group_type` ENUM in `location_group` covers these grouping categories:

| Group Type | Description | Example Groups |
|---|---|---|
| `continent` | Continental groupings | Africa, Asia, Europe, Americas, Oceania |
| `region` | Broad geographic regions | Eastern Europe, Southeast Asia |
| `subregion` | More granular geographic regions | Sub-Saharan Africa, Caribbean |
| `wdi_region` | World Bank regional groupings | East Asia & Pacific, South Asia |
| `wdi_income_level` | World Bank income classifications | High income, Upper middle income, Lower middle income, Low income |
| `trade` | Trade blocs and economic unions | ASEAN, EU, MERCOSUR, NAFTA |
| `political` | Political groupings | G20, G7 |
| `world` | Entire world as one group | World (group_id typically 1) |
| `rock_song` | Internal classification (ignore) | — |

## Identifier Formats

Country IDs use M49 codes (UN standard), which coincide with ISO 3166-1 numeric codes for most countries.

| Context | Format | Example (Kenya) |
|---|---|---|
| SQL `country_id` column | Integer | `404` |
| SQL WHERE clause by name | `iso3_code` string | `WHERE iso3_code = 'KEN'` |
| Atlas website URL | `country-{numeric_id}` | `/countries/404` |
| Country Pages API (GraphQL) | `"location-{numeric_id}"` | `"location-404"` |
| Explore API (GraphQL) | Integer argument | `countryId: 404` |

### Common country IDs

| Country | ISO alpha-3 | Numeric ID (M49) |
|---|---|---|
| USA | USA | 840 |
| China | CHN | 156 |
| Germany | DEU | 276 |
| Brazil | BRA | 76 |
| India | IND | 356 |
| Japan | JPN | 392 |
| Kenya | KEN | 404 |
| Ethiopia | ETH | 231 |
| Spain | ESP | 724 |
| Türkiye | TUR | 792 |

Full list: `SELECT country_id, iso3_code, name_short_en FROM classification.location_country ORDER BY name_en`

## SQL Patterns

### Look up a country by common name

```sql
SELECT country_id, iso3_code, iso2_code, name_en, name_short_en,
       incomelevel_enum, in_rankings, former_country, is_trusted
FROM classification.location_country
WHERE name_en ILIKE '%turkey%'
   OR name_short_en ILIKE '%turkey%'
   OR iso3_code = 'TUR';
```

### List all countries included in rankings

```sql
SELECT country_id, iso3_code, name_short_en, incomelevel_enum
FROM classification.location_country
WHERE in_rankings = true
ORDER BY name_en;
```

### Find group membership for a country

```sql
SELECT lgm.group_name, lgm.group_type
FROM classification.location_group_member lgm
WHERE lgm.country_id = (
    SELECT country_id FROM classification.location_country
    WHERE iso3_code = 'KEN'
)
ORDER BY lgm.group_type;
```

### List all members of a specific group (e.g., ASEAN)

```sql
SELECT lc.iso3_code, lc.name_short_en
FROM classification.location_group_member lgm
JOIN classification.location_country lc ON lgm.country_id = lc.country_id
WHERE lgm.group_name = 'ASEAN'
ORDER BY lc.name_en;
```

### Check historical entity year range (SITC only)

```sql
SELECT MIN(year), MAX(year), COUNT(*)
FROM sitc.country_year
WHERE country_id = (
    SELECT country_id FROM classification.location_country
    WHERE iso3_code = 'SUN'  -- Soviet Union
);
```

## GraphQL: Country ID Usage

### Explore API (`/api/graphql`) — integer IDs

```graphql
{
  countryYear(countryId: 404, yearMin: 2020, yearMax: 2022) {
    year
    exportValue
    eci
  }
}
```

### Country Pages API (`/api/countries/graphql`) — prefixed string IDs

```graphql
{
  countryProfile(location: "location-404") {
    eci
    eciRank
    latestCoi
    policyRecommendation
  }
}
```

The `code` field returned by Country Pages API `Location` type is the ISO alpha-3 code (e.g., `"KEN"`).

To retrieve all country IDs and codes via GraphQL:

```graphql
{
  allLocations {
    id
    code
    shortName
  }
}
```

## Self-Trade Entries

In bilateral trade tables (`country_country_year`, `country_country_product_year_*`), rows where `country_id = partner_id` represent self-trade entries. These should be zero in practice and can be excluded with `WHERE country_id != partner_id` when aggregating bilateral totals.
