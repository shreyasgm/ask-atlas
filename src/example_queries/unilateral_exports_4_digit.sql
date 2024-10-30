SELECT 
    p.code as hs_code,
    p.name_en as product_name,
    cpy.export_value,
    cpy.global_market_share
FROM hs92.country_product_year_4 cpy
JOIN classification.location_country lc ON cpy.country_id = lc.country_id
JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
WHERE cpy.year = 2022
    AND cpy.export_value > 0
    AND lc.iso3_code = 'USA'
ORDER BY 
    cpy.export_value DESC
LIMIT 10;