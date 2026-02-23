-- Goods exports (HS92)
SELECT
    'Goods' as category,
    p.name_en as product_name,
    p.code as product_code,
    cpy.export_value,
    cpy.global_market_share
FROM hs92.country_product_year_1 cpy
JOIN classification.location_country lc ON cpy.country_id = lc.country_id
JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
WHERE cpy.year = 2022
    AND cpy.export_value > 0
    AND lc.iso3_code = 'USA'

UNION ALL

-- Services exports
SELECT
    'Services' as category,
    p.name_en as product_name,
    p.code as product_code,
    cpy.export_value,
    cpy.global_market_share
FROM services_unilateral.country_product_year_1 cpy
JOIN classification.location_country lc ON cpy.country_id = lc.country_id
JOIN classification.product_services_unilateral p ON cpy.product_id = p.product_id
WHERE cpy.year = 2022
    AND cpy.export_value > 0
    AND lc.iso3_code = 'USA'

ORDER BY
    category,
    export_value DESC;
