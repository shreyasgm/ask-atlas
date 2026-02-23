WITH latest_year AS (
    SELECT MAX(year) as max_year
    FROM hs92.country_product_year_4
)
-- Best products by COG (higher is better)
SELECT
    p.code as product_code,
    p.name_en as product_name,
    cpy.normalized_cog,
    RANK() OVER (ORDER BY cpy.normalized_cog DESC) as cog_rank
FROM hs92.country_product_year_4 cpy
JOIN classification.location_country loc
    ON cpy.country_id = loc.country_id
    AND loc.iso3_code = 'KEN'
JOIN classification.product_hs92 p
    ON cpy.product_id = p.product_id
WHERE cpy.year = (SELECT max_year FROM latest_year)
    AND cpy.normalized_cog IS NOT NULL
ORDER BY normalized_cog DESC
LIMIT 15;
