WITH latest_year AS (
    SELECT MAX(year) as max_year
    FROM hs12.country_product_year_4
),
-- Germany's automotive exports and global automotive total
germany_auto AS (
    SELECT
        SUM(cpy.export_value) as germany_export_value
    FROM hs12.country_product_year_4 cpy
    JOIN classification.location_country loc
        ON cpy.country_id = loc.country_id
        AND loc.iso3_code = 'DEU'
    JOIN classification.product_hs12 p
        ON cpy.product_id = p.product_id
    WHERE cpy.year = (SELECT max_year FROM latest_year)
        AND p.code IN ('8703', '8704', '8708')
),
global_auto AS (
    SELECT
        SUM(py.export_value) as global_export_value
    FROM hs12.product_year_4 py
    JOIN classification.product_hs12 p
        ON py.product_id = p.product_id
    WHERE py.year = (SELECT max_year FROM latest_year)
        AND p.code IN ('8703', '8704', '8708')
)
SELECT
    ga.germany_export_value,
    gla.global_export_value,
    ROUND(ga.germany_export_value * 100.0 / NULLIF(gla.global_export_value, 0), 2) as market_share_pct
FROM germany_auto ga
CROSS JOIN global_auto gla;
