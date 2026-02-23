-- Get the most recent year from the dataset
WITH latest_year AS (
    SELECT MAX(year) as max_year
    FROM hs92.country_product_year_4
)

-- Combine top 5 imports for both goods and services
SELECT
    category,
    code,
    name,
    import_value,
    pci
FROM (
    -- Get top 5 imported goods for Canada
    SELECT 'Goods' AS category, p.code, p.name_en AS name, cpy.import_value, py.pci
    FROM hs92.country_product_year_4 cpy
    JOIN hs92.product_year_4 py ON cpy.product_id = py.product_id AND cpy.year = py.year
    JOIN classification.location_country loc_exp ON cpy.country_id = loc_exp.country_id AND loc_exp.iso3_code = 'CAN'
    JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
    WHERE cpy.year = (SELECT max_year FROM latest_year)
    ORDER BY cpy.import_value DESC
    LIMIT 5
) goods

UNION ALL

SELECT
    category,
    code,
    name,
    import_value,
    pci
FROM (
    -- Get top 5 imported services for Canada
    SELECT 'Services' AS category, p.code, p.name_en AS name, cpy.import_value, py.pci
    FROM services_unilateral.country_product_year_4 cpy
    JOIN services_unilateral.product_year_4 py ON cpy.product_id = py.product_id AND cpy.year = py.year
    JOIN classification.location_country loc_exp ON cpy.country_id = loc_exp.country_id AND loc_exp.iso3_code = 'CAN'
    JOIN classification.product_services_unilateral p ON cpy.product_id = p.product_id
    WHERE cpy.year = (SELECT max_year FROM latest_year)
    ORDER BY cpy.import_value DESC
    LIMIT 5
) services;
