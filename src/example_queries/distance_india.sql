WITH latest_year AS (
    SELECT MAX(year) as max_year 
    FROM hs92.country_product_year_4
),
combined_distances AS (
    (SELECT 
        'Goods' as category,
        p.code as product_code,
        p.name_en as product_name,
        cpy.normalized_distance,
        RANK() OVER (ORDER BY cpy.normalized_distance) as distance_rank
    FROM hs92.country_product_year_4 cpy
    JOIN classification.location_country loc 
        ON cpy.country_id = loc.country_id 
        AND loc.iso3_code = 'IND'
    JOIN classification.product_hs92 p 
        ON cpy.product_id = p.product_id
    WHERE cpy.year = (SELECT max_year FROM latest_year)
        AND cpy.normalized_distance IS NOT NULL
    LIMIT 10)
    
    UNION ALL
    
    (SELECT 
        'Services' as category,
        p.code as product_code,
        p.name_en as product_name,
        cpy.normalized_distance,
        RANK() OVER (ORDER BY cpy.normalized_distance) as distance_rank
    FROM services_unilateral.country_product_year_4 cpy
    JOIN classification.location_country loc 
        ON cpy.country_id = loc.country_id 
        AND loc.iso3_code = 'IND'
    JOIN classification.product_services_unilateral p 
        ON cpy.product_id = p.product_id
    WHERE cpy.year = (SELECT max_year FROM latest_year)
        AND cpy.normalized_distance IS NOT NULL
    LIMIT 10)
)
-- Best products by distance (lower is better)
SELECT 
    category,
    product_code,
    product_name,
    normalized_distance,
    distance_rank
FROM combined_distances
ORDER BY category, normalized_distance;