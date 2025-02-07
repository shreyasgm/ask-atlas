-- Goods exports
(SELECT 
    'Goods' as category,
    loc_exp.iso3_code as exporter,
    loc_imp.iso3_code as importer,
    p.code as product_code,
    p.name_en as product_name,
    SUM(ccpy.export_value) as total_export_value
FROM hs92.country_country_product_year_4 ccpy
JOIN classification.location_country loc_exp 
    ON ccpy.country_id = loc_exp.country_id 
    AND loc_exp.iso3_code = 'BOL'
JOIN classification.location_country loc_imp 
    ON ccpy.partner_id = loc_imp.country_id 
    AND loc_imp.iso3_code = 'MAR'
JOIN classification.product_hs92 p 
    ON ccpy.product_id = p.product_id
WHERE ccpy.year BETWEEN 2010 AND 2022
    AND ccpy.export_value > 0
GROUP BY 
    p.code,
    p.name_en,
    loc_exp.iso3_code,
    loc_imp.iso3_code
ORDER BY 
    total_export_value DESC
LIMIT 10)

UNION ALL

-- Services exports
(SELECT 
    'Services' as category,
    loc_exp.iso3_code as exporter,
    loc_imp.iso3_code as importer,
    p.code as product_code,
    p.name_en as product_name,
    SUM(ccpy.export_value) as total_export_value
FROM services_bilateral.country_country_product_year_4 ccpy
JOIN classification.location_country loc_exp
    ON ccpy.country_id = loc_exp.country_id
    AND loc_exp.iso3_code = 'BOL'
JOIN classification.location_country loc_imp
    ON ccpy.partner_id = loc_imp.country_id
    AND loc_imp.iso3_code = 'MAR'
JOIN classification.product_services_bilateral p
    ON ccpy.product_id = p.product_id
WHERE ccpy.year BETWEEN 2010 AND 2022
    AND ccpy.export_value > 0
GROUP BY 
    p.code,
    p.name_en,
    loc_exp.iso3_code,
    loc_imp.iso3_code
ORDER BY 
    total_export_value DESC
LIMIT 10);