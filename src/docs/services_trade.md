---
title: Services Trade Data
purpose: >
  Technical reference for services trade data in the Atlas of Economic Complexity
  — DB schemas, service category taxonomy, year and country coverage, key
  differences from goods trade, and how to write correct SQL queries.
keywords:
  - services trade
  - services exports
  - services imports
  - tourism
  - transport
  - ICT
  - financial services
  - EBOPS
  - services_unilateral
  - services_bilateral
  - Products vs Locations discrepancy
  - bilateral services
  - services RCA
  - services complexity
  - goods vs services
when_to_load: >
  Load when the user asks about services exports or imports (tourism, transport,
  ICT, financial services), why the Atlas treemap total changes when switching
  from "Products" to "Locations" view, whether bilateral services data is
  available between two countries, or why services lack RCA/ECI/product-space
  metrics. Also load when combining goods and services into a total export figure.
when_not_to_load: >
  Do not load for goods trade methodology (see trade_methodology.md).
related_docs:
  - trade_methodology.md
---

## Two Services Schemas

The Atlas stores services trade data in two separate PostgreSQL schemas.

| Schema | Purpose | Has bilateral (country-pair) tables? |
|---|---|---|
| `services_unilateral` | A single country's service exports/imports, broken down by service category and year | Partial — see note below |
| `services_bilateral` | Service trade flows between specific country pairs | Yes (`country_country_product_year_*`) |

**Which schema to use:**
- For questions about one country's total or category-level service exports/imports → `services_unilateral`
- For questions about services traded between two specific countries → `services_bilateral`
- `services_bilateral` data is sparser than goods bilateral data; many country pairs have no records

---

## Tables in Each Schema

Both schemas follow the same naming convention as goods schemas: `{schema}.{data_type}_{level}`.

### `services_unilateral` Tables

| Table | Description |
|---|---|
| `services_unilateral.country_year` | Country-level aggregate (total service exports/imports by year, no product breakdown) |
| `services_unilateral.country_product_year_1` | Country × service category × year, 1-digit level |
| `services_unilateral.country_product_year_2` | Country × service category × year, 2-digit level |
| `services_unilateral.country_product_year_4` | Country × service category × year, 4-digit level — **most commonly used** |
| `services_unilateral.country_product_year_6` | Country × service category × year, 6-digit level |
| `services_unilateral.product_year_1` | Global service category totals, 1-digit |
| `services_unilateral.product_year_2` | Global service category totals, 2-digit |
| `services_unilateral.product_year_4` | Global service category totals, 4-digit |
| `services_unilateral.product_year_6` | Global service category totals, 6-digit |
| `services_unilateral.country_country_year` | Country-to-country relatedness metrics |
| `services_unilateral.country_country_product_year_1` | Bilateral services by category, 1-digit |
| `services_unilateral.country_country_product_year_2` | Bilateral services by category, 2-digit |
| `services_unilateral.country_country_product_year_4` | Bilateral services by category, 4-digit |
| `services_unilateral.country_country_product_year_6` | Bilateral services by category, 6-digit |
| `services_unilateral.group_group_product_year_1` | Group-to-group bilateral services, 1-digit |
| `services_unilateral.group_group_product_year_2` | Group-to-group bilateral services, 2-digit |
| `services_unilateral.group_group_product_year_4` | Group-to-group bilateral services, 4-digit |
| `services_unilateral.group_group_product_year_6` | Group-to-group bilateral services, 6-digit |

### `services_bilateral` Tables

| Table | Description |
|---|---|
| `services_bilateral.country_year` | Country aggregate service trade by year |
| `services_bilateral.country_product_year_1` | Country × category × year, 1-digit |
| `services_bilateral.country_product_year_2` | Country × category × year, 2-digit |
| `services_bilateral.country_product_year_4` | Country × category × year, 4-digit |
| `services_bilateral.country_product_year_6` | Country × category × year, 6-digit |
| `services_bilateral.country_country_year` | Country-to-country relatedness metrics |
| `services_bilateral.country_country_product_year_1` | Bilateral services by category, 1-digit |
| `services_bilateral.country_country_product_year_2` | Bilateral services by category, 2-digit |
| `services_bilateral.country_country_product_year_4` | Bilateral services by category, 4-digit |
| `services_bilateral.country_country_product_year_6` | Bilateral services by category, 6-digit |
| `services_bilateral.product_year_1` | Global service totals, 1-digit |
| `services_bilateral.product_year_2` | Global service totals, 2-digit |
| `services_bilateral.product_year_4` | Global service totals, 4-digit |
| `services_bilateral.product_year_6` | Global service totals, 6-digit |
| `services_bilateral.group_group_product_year_1` | Group bilateral services, 1-digit |
| `services_bilateral.group_group_product_year_2` | Group bilateral services, 2-digit |
| `services_bilateral.group_group_product_year_4` | Group bilateral services, 4-digit |
| `services_bilateral.group_group_product_year_6` | Group bilateral services, 6-digit |

---

## Key Columns in Services Trade Tables

The column structure mirrors goods trade tables. All tables use `country_id` and `product_id` as internal integer foreign keys; join to the classification tables to get names and codes.

| Column | Tables | Description |
|---|---|---|
| `country_id` | All | Internal integer ID — join to `classification.location_country` |
| `partner_id` | `country_country_*` | Partner country internal ID |
| `product_id` | `*product*` | Internal service category ID — join to `classification.product_services_unilateral` or `classification.product_services_bilateral` |
| `year` | All | Calendar year |
| `export_value` | All | USD export value |
| `import_value` | All | USD import value |
| `global_market_share` | `country_product_year_*` | Country's share of world exports for this service category |
| `export_rca` | `country_product_year_*` | Revealed Comparative Advantage (computed but interpret with caution — see Metrics section) |
| `pci` | `product_year_*` | Product Complexity Index (where computed) |
| `normalized_distance` | `country_product_year_*` | Distance metric (where computed) |

**Important:** `product_id` in services tables belongs to a completely separate ID space from goods `product_id` values. A services `product_id = 5` is not the same product as a goods `product_id = 5`.

---

## Service Category Classification

Services trade does **not** use HS or SITC codes. It uses the **Extended Balance of Payments Services (EBOPS 2010)** standard, sourced from IMF Balance of Payments Manual 6 (BPM6) data.

### Classification Reference Tables

| Table | Used with |
|---|---|
| `classification.product_services_unilateral` | `services_unilateral` schema tables |
| `classification.product_services_bilateral` | `services_bilateral` schema tables |

**Key columns in classification tables:**

| Column | Description |
|---|---|
| `product_id` | Internal integer ID — use for JOINs |
| `code` | Short alphanumeric code (e.g., `"travel"`) |
| `name_en` | Full English name (e.g., `"Travel"`) |
| `name_short_en` | Short display name (e.g., `"Travel & tourism"`) |
| `product_level` | Hierarchy level (1, 2, 4, 6) |
| `parent_id` | Parent category ID for hierarchy navigation |

### The 12 EBOPS 2010 Service Categories (1-digit level)

| Category Name (Atlas) | EBOPS 2010 Equivalent |
|---|---|
| Manufacturing services | Manufacturing services on physical inputs owned by others |
| Maintenance & repair | Maintenance and repair services n.i.e. |
| Transport | Transport |
| Travel | Travel (includes tourism) |
| Construction | Construction |
| Insurance & pension | Insurance and pension services |
| Financial | Financial services |
| Intellectual property | Charges for the use of intellectual property n.i.e. |
| Telecom/computer/IT | Telecommunications, computer, and information services |
| Other business | Other business services |
| Personal/cultural/recreational | Personal, cultural, and recreational services |
| Government | Government goods and services n.i.e. |

**Atlas display names** in the classification table use descriptive strings such as `"Travel & tourism"`, `"Business"`, and `"ICT"` — not EBOPS codes. Always look up the exact `name_en` or `name_short_en` from `classification.product_services_unilateral` when filtering by category.

---

## Data Source and Year Coverage

- **Source:** International Monetary Fund (IMF), Direction of Trade Statistics (DOTS) and Balance of Payments (BPM6)
- **Year range:** 1980–2024 (coverage begins in 1980; many countries have data only from the 1990s or 2000s onward)
- **Country coverage:** Approximately 50–75% of Atlas countries report services data. Services coverage is substantially lower than goods coverage.
- **Update cycle:** Annual, alongside goods data updates (typically April–June each year)

---

## How Services Differ from Goods Data

| Dimension | Goods (HS92/SITC) | Services |
|---|---|---|
| Product categories | ~1,200 at 4-digit (HS92) | ~12–15 at 1-digit level |
| Product codes | Numeric HS or SITC codes | Descriptive names only (no standard code) |
| Data source | UN Comtrade | IMF DOTS / BPM6 |
| Year range | 1962–2024 (SITC), 1995–2024 (HS92) | 1980–2024 |
| Country coverage | ~250 countries | ~50–75% of Atlas countries |
| Bilateral coverage | Comprehensive | Sparser; many pairs missing |
| RCA computation | Yes | Computed structurally, but services RCA is less standard |
| PCI / ECI / COG | Yes (goods only) | **Not available** |
| Product space | Yes (goods only) | **Not available** — services not in product-product proximity tables |
| Distance metric | Yes (goods only) | Not meaningful — omit from services queries |

---

## Complexity Metrics and Services: Why They Are Absent

Economic complexity metrics (ECI, PCI, COG, distance, product space) require bilateral, product-level trade data to compute the bipartite network of countries and products. Services data does not provide the bilateral product-level granularity needed for this computation:

1. **No bilateral product-level data in standard form** — services categories are too broad and too few to form a meaningful bipartite network
2. **No product space for services** — the `product_product` proximity tables (used for distance and COG) contain only goods products; services products are absent
3. **PCI is structurally undefined for services** — PCI requires knowing which diverse countries export a product; with only ~12 categories and limited country coverage, the computation is unreliable

**Consequence:** When a user asks for the PCI of "Travel & tourism" or the distance from a service category, the correct answer is that these metrics do not exist for services in the Atlas. Do not attempt to return NULL values from services tables as a PCI or distance figure.

---

## Critical: Products vs. Locations Treemap Total Discrepancy

This is a frequent source of user confusion. The Atlas Explore treemap shows **different export totals** depending on the view selected:

| View | What it shows | Export total includes services? |
|---|---|---|
| **Products mode** (default) | Goods + services by product/category | **Yes** — includes services |
| **Locations mode** (`view=markets`) | Trade partners (bilateral), goods only | **No** — goods only |

**Example:** Kenya in 2024 — Products mode shows ~$16B total; Locations mode shows ~$8.2B. The ~$7.8B difference represents Kenya's service exports, which are included in the Products treemap but absent from the Locations treemap because bilateral services data is not available for the Locations view.

**This is not a data error.** It reflects a real difference in data availability: bilateral services trade flows between specific country pairs are not comprehensively available, so the Locations/geomap view uses only goods bilateral data.

**Country Pages treemap:** The export basket treemap on Country Profile pages (`/countries/{id}/export-basket`) **does include services** in its total. Service categories appear as pink/red rectangles labeled with names like "Travel & tourism" alongside goods products. The total export value displayed in the top bar includes both goods and services.

**Agent guidance:** When a user notices a total discrepancy between Products and Locations mode, explain: "The Products view includes both goods and services exports; the Locations (partner) view shows only goods, because detailed bilateral services data by trading partner is not available in the same way as goods."

---

## SQL Query Patterns

### Total service exports for a country in a given year

```sql
SELECT cy.export_value
FROM services_unilateral.country_year cy
JOIN classification.location_country lc ON cy.country_id = lc.country_id
WHERE lc.iso3_code = 'KEN'
  AND cy.year = 2022;
```

### Service exports by category (4-digit level)

```sql
SELECT
    p.name_en AS service_category,
    p.code,
    cpy.export_value
FROM services_unilateral.country_product_year_4 cpy
JOIN classification.location_country lc ON cpy.country_id = lc.country_id
JOIN classification.product_services_unilateral p ON cpy.product_id = p.product_id
WHERE lc.iso3_code = 'GBR'
  AND cpy.year = 2022
  AND cpy.export_value > 0
ORDER BY cpy.export_value DESC;
```

### Combined goods + services total exports (for a complete picture)

```sql
-- Goods total
(
    SELECT 'Goods' AS category, cy.export_value
    FROM hs92.country_year cy
    JOIN classification.location_country lc ON cy.country_id = lc.country_id
    WHERE lc.iso3_code = 'BRA' AND cy.year = 2022
)
UNION ALL
-- Services total
(
    SELECT 'Services' AS category, cy.export_value
    FROM services_unilateral.country_year cy
    JOIN classification.location_country lc ON cy.country_id = lc.country_id
    WHERE lc.iso3_code = 'BRA' AND cy.year = 2022
);
-- Sum both values in application code, or wrap in a CTE to compute the total
```

### Bilateral services between two countries

```sql
SELECT
    p.name_en AS service_category,
    SUM(ccpy.export_value) AS export_value
FROM services_bilateral.country_country_product_year_4 ccpy
JOIN classification.location_country lc_exp ON ccpy.country_id = lc_exp.country_id
JOIN classification.location_country lc_imp ON ccpy.partner_id = lc_imp.country_id
JOIN classification.product_services_bilateral p ON ccpy.product_id = p.product_id
WHERE lc_exp.iso3_code = 'USA'
  AND lc_imp.iso3_code = 'CHN'
  AND ccpy.year = 2022
  AND ccpy.export_value > 0
GROUP BY p.name_en
ORDER BY export_value DESC;
```

### Compute services share of total exports

```sql
WITH goods AS (
    SELECT cy.export_value AS goods_exports
    FROM hs92.country_year cy
    JOIN classification.location_country lc ON cy.country_id = lc.country_id
    WHERE lc.iso3_code = 'CHE' AND cy.year = 2022
),
services AS (
    SELECT cy.export_value AS services_exports
    FROM services_unilateral.country_year cy
    JOIN classification.location_country lc ON cy.country_id = lc.country_id
    WHERE lc.iso3_code = 'CHE' AND cy.year = 2022
)
SELECT
    goods_exports,
    services_exports,
    goods_exports + services_exports AS total_exports,
    services_exports::FLOAT / (goods_exports + services_exports) AS services_share
FROM goods, services;
```

### Find a specific service category by name

```sql
-- Use name_en for exact matching; name_short_en for display names
SELECT product_id, code, name_en, name_short_en, product_level
FROM classification.product_services_unilateral
WHERE name_en ILIKE '%travel%'
   OR name_short_en ILIKE '%travel%'
ORDER BY product_level;
```

---

## GraphQL API: Services Trade

In the Explore API (`https://atlas.hks.harvard.edu/api/graphql`), services data is accessed by passing `servicesClass: unilateral` to trade queries.

```graphql
{
  countryProductYear(
    countryId: 404
    productClass: HS92
    servicesClass: unilateral
    productLevel: 4
    yearMin: 2022
    yearMax: 2022
  ) {
    productId
    exportValue
    importValue
  }
}
```

To fetch the services product catalog via GraphQL:

```graphql
{
  productHs92(servicesClass: unilateral) {
    productId
    nameShortEn
    code
    productLevel
  }
}
```

**Note:** The `ServicesClass` enum has only one value: `unilateral`. There is no `bilateral` enum value in the GraphQL API — bilateral services data is only accessible via SQL.

---

## Common Pitfalls

- **Joining across schema product IDs:** Never join `services_unilateral.country_product_year_4.product_id` directly to `classification.product_hs92`. Services product IDs only join to `classification.product_services_unilateral` (for unilateral) or `classification.product_services_bilateral` (for bilateral).
- **Filtering by product name:** Use `name_en` or `name_short_en` from the classification table — never hardcode a numeric product code for services (they do not use numeric HS/SITC codes).
- **Querying complexity metrics for services:** `pci`, `cog`, and distance columns in services tables may be NULL or absent. Do not return these as valid complexity scores.
- **Missing data:** If a country reports no services data, the query returns no rows — this is expected for countries outside the ~50–75% coverage range.
- **Using iso3_code in WHERE:** Never filter `country_product_year_*` directly on a text country code. Always JOIN to `classification.location_country` and filter on `lc.iso3_code`.
