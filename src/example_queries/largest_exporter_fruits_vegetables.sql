WITH latest_year AS (
    SELECT MAX(year) as max_year 
    FROM hs92.country_product_year_4
),
combined_trade AS (
    -- Fruits trade (4-digit)
    SELECT 
        loc.iso3_code,
        SUM(cpy.export_value) as export_value
    FROM hs92.country_product_year_4 cpy
    JOIN classification.location_country loc 
        ON cpy.country_id = loc.country_id
    JOIN classification.product_hs92 p 
        ON cpy.product_id = p.product_id
    WHERE p.code IN ('0801', '0802', '0803', '0804', '0805', '0806', '0807', '0808', '0809', '0810', '0811', '0812', '0813', '0814')
        AND cpy.year = (SELECT max_year FROM latest_year)
    GROUP BY loc.iso3_code

    UNION ALL

    -- Vegetables trade (2-digit)
    SELECT 
        loc.iso3_code,
        SUM(cpy.export_value) as export_value
    FROM hs92.country_product_year_2 cpy
    JOIN classification.location_country loc 
        ON cpy.country_id = loc.country_id
    JOIN classification.product_hs92 p 
        ON cpy.product_id = p.product_id
    WHERE p.code = '07'
        AND cpy.year = (SELECT max_year FROM latest_year)
    GROUP BY loc.iso3_code
)
SELECT 
    iso3_code,
    SUM(export_value) as total_export_value
FROM combined_trade
GROUP BY iso3_code
ORDER BY total_export_value DESC
LIMIT 10;