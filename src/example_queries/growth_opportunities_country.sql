-- Growth Opportunities: Strategy-Aware Two-Query Approach
-- Example: Kenya in 2022

-- QUERY 1: Determine country policy for Kenya
SELECT cy.coi, cy.eci
FROM hs92.country_year cy
JOIN classification.location_country loc ON cy.country_id = loc.country_id
WHERE loc.iso3_code = 'KEN'
  AND cy.year = 2022;
-- Result: COI < 0 → StrategicBets policy → weights: 0.50/0.15/0.35

-- QUERY 2: Top growth opportunities using StrategicBets weights
-- Composite score: 50% proximity + 15% complexity + 35% opportunity gain
-- Pre-computed normalized columns are z-scores per country-year (distance inverted)
SELECT
    p.code AS product_code,
    p.name_short_en AS product_name,
    cpy.export_rca,
    cpy.distance,
    cpy.cog,
    (cpy.normalized_distance * 0.50
     + cpy.normalized_pci * 0.15
     + cpy.normalized_cog * 0.35) AS composite_score
FROM hs92.country_product_year_4 cpy
JOIN classification.location_country loc ON cpy.country_id = loc.country_id
JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
WHERE loc.iso3_code = 'KEN'
  AND cpy.year = 2022
  AND cpy.export_rca < 1
  AND cpy.normalized_distance IS NOT NULL
ORDER BY composite_score DESC
LIMIT 20;
