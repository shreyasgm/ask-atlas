-- Get latest year in the database
WITH latest_year AS (
    SELECT MAX(year) as max_year 
    FROM hs92.country_country_product_year_4
),
-- Get the top 5 imported products for Canada in the latest year
top_imports AS (
    SELECT 
        p.product_id,
        p.code as product_code,
        p.name_en as product_name,
        SUM(ccpy.import_value) as total_import_value
    FROM hs92.country_country_product_year_4 ccpy
    JOIN classification.location_country loc_exp 
        ON ccpy.country_id = loc_exp.country_id 
        AND loc_exp.iso3_code = 'CAN'
    JOIN classification.location_country loc_imp 
        ON ccpy.partner_id = loc_imp.country_id 
        AND loc_imp.iso3_code = 'USA'
    JOIN classification.product_hs92 p 
        ON ccpy.product_id = p.product_id
    WHERE ccpy.year = (SELECT max_year FROM latest_year)
    GROUP BY p.product_id, p.code, p.name_en
    ORDER BY total_import_value DESC
    LIMIT 5
)
-- Get the PCI for the top 5 imported products for Canada in the latest year
SELECT 
    tis.product_code,
    tis.product_name,
    py4.pci
FROM top_imports tis
JOIN hs92.product_year_4 py4 
    ON tis.product_id = py4.product_id
    AND py4.year = (SELECT max_year FROM latest_year)
ORDER BY py4.pci DESC
LIMIT 15;
