WITH latest_year AS (
    SELECT MAX(year) AS max_year FROM hs92.country_product_year_4
)
SELECT
    loc.name_short_en AS country_name,
    COUNT(*) AS products_with_rca
FROM hs92.country_product_year_4 cpy
JOIN classification.location_country loc ON cpy.country_id = loc.country_id
WHERE loc.iso3_code = 'BRA'
  AND cpy.year = (SELECT max_year FROM latest_year)
  AND cpy.export_rca >= 1
GROUP BY loc.name_short_en;
