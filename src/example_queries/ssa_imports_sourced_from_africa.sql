-- What share of Sub-Saharan African imports could be sourced from within Africa?
-- A product is "sourceable from Africa" if at least one African country has
-- revealed comparative advantage (RCA >= 1) in it.
--
-- Two-step logic:
--   1. Aggregate all imports by Sub-Saharan African countries at the 4-digit product level.
--   2. Check which of those products are competitively exported (RCA >= 1) by any
--      African country. Calculate the share of import value and product count.
--
-- Uses classification.location_group_member to identify:
--   - Sub-Saharan Africa (group_id = 947) for the importing side
--   - Africa (group_id = 2) for the exporting/sourcing side

WITH ssa_imports AS (
    SELECT cpy.product_id,
           SUM(cpy.import_value) AS import_value
    FROM hs92.country_product_year_4 cpy
    JOIN classification.location_group_member gm
        ON gm.country_id = cpy.country_id
        AND gm.group_id = 947
    WHERE cpy.year = 2024
    GROUP BY cpy.product_id
),
african_competitive_products AS (
    SELECT DISTINCT cpy.product_id
    FROM hs92.country_product_year_4 cpy
    JOIN classification.location_group_member gm
        ON gm.country_id = cpy.country_id
        AND gm.group_id = 2
    WHERE cpy.year = 2024
      AND cpy.export_rca >= 1
)
SELECT
    COUNT(DISTINCT si.product_id)                         AS total_products_imported,
    COUNT(DISTINCT acp.product_id)                        AS products_sourceable_from_africa,
    SUM(si.import_value)                                  AS total_import_value,
    SUM(CASE WHEN acp.product_id IS NOT NULL
             THEN si.import_value ELSE 0 END)             AS sourceable_import_value,
    ROUND(100.0 * COUNT(DISTINCT acp.product_id)::numeric
          / COUNT(DISTINCT si.product_id), 1)             AS pct_products_sourceable,
    ROUND(100.0 * SUM(CASE WHEN acp.product_id IS NOT NULL
                            THEN si.import_value ELSE 0 END)::numeric
          / SUM(si.import_value), 1)                      AS pct_value_sourceable
FROM ssa_imports si
LEFT JOIN african_competitive_products acp
    ON acp.product_id = si.product_id;
