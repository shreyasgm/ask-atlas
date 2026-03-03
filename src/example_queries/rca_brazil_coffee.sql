WITH latest_year AS (
    SELECT MAX(year) as max_year
    FROM hs12.country_product_year_4
)
SELECT
    p.code as product_code,
    p.name_en as product_name,
    cpy.export_rca,
    cpy.export_value,
    cpy.global_market_share
FROM hs12.country_product_year_4 cpy
JOIN classification.location_country loc
    ON cpy.country_id = loc.country_id
    AND loc.iso3_code = 'BRA'
JOIN classification.product_hs12 p
    ON cpy.product_id = p.product_id
WHERE cpy.year = (SELECT max_year FROM latest_year)
    AND p.code IN ('0901')
ORDER BY cpy.export_rca DESC;
