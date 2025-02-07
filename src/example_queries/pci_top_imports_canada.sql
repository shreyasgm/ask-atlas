-- Get latest year in the database
WITH latest_year AS (
    SELECT MAX(year) as max_year 
    FROM hs92.country_country_product_year_4
),
-- Get the top imported goods and services for Canada from USA in the latest year
top_imports AS (
    (SELECT 
        'Goods' as category,
        p.product_id,
        p.code,
        p.name_en as name,
        cpy.import_value,
        py.pci
    FROM hs92.country_product_year_4 cpy
    JOIN hs92.product_year_4 py
        ON cpy.product_id = py.product_id
        AND cpy.year = py.year
    JOIN classification.location_country loc_exp 
        ON cpy.country_id = loc_exp.country_id 
        AND loc_exp.iso3_code = 'CAN'
    JOIN classification.product_hs92 p 
        ON cpy.product_id = p.product_id
    WHERE cpy.year = (SELECT max_year FROM latest_year)
    ORDER BY cpy.import_value DESC
    LIMIT 5)
    
    UNION ALL
    
    (SELECT 
        'Services' as category,
        p.product_id,
        p.code,
        p.name_en as name,
        cpy.import_value,
        py.pci
    FROM services_unilateral.country_product_year_4 cpy
    JOIN services_unilateral.product_year_4 py
        ON cpy.product_id = py.product_id
        AND cpy.year = py.year
    JOIN classification.location_country loc_exp 
        ON cpy.country_id = loc_exp.country_id 
        AND loc_exp.iso3_code = 'CAN'
    JOIN classification.product_services_unilateral p 
        ON cpy.product_id = p.product_id
    WHERE cpy.year = (SELECT max_year FROM latest_year)
    ORDER BY cpy.import_value DESC
    LIMIT 5)
)
-- Get the PCI for the top imported goods and services
SELECT 
    ti.category,
    ti.code,
    ti.name,
    ti.import_value,
    ti.pci
FROM top_imports ti;