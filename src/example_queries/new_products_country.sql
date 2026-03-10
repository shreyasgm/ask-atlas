-- New products for Russia over the window 2009-2024 (using HS92)
-- A product is "new" if start-period RCA < 0.5 AND end-period RCA >= 1.0
-- RCA is recomputed from 3-year averaged export values at each end of the window
-- All 4-digit products are eligible (no filters applied)
-- This pattern works for any schema: swap hs92 -> hs12/sitc and classification.product_hs92 -> product_hs12/product_sitc
-- Note: hs12 data starts in 2012, so adjust the start-period years accordingly
WITH start_avg AS (
    SELECT
        country_id,
        product_id,
        AVG(export_value) AS avg_export
    FROM hs92.country_product_year_4
    WHERE year BETWEEN 2009 AND 2011
    GROUP BY country_id, product_id
),
start_country_total AS (
    SELECT country_id, SUM(avg_export) AS total_export
    FROM start_avg
    GROUP BY country_id
),
start_world_product AS (
    SELECT product_id, SUM(avg_export) AS world_product_export
    FROM start_avg
    GROUP BY product_id
),
start_world_total AS (
    SELECT SUM(avg_export) AS world_total FROM start_avg
),
start_rca AS (
    SELECT
        sa.country_id,
        sa.product_id,
        (sa.avg_export / NULLIF(sct.total_export, 0))
        / (swp.world_product_export / NULLIF(swt.world_total, 0)) AS rca
    FROM start_avg sa
    JOIN start_country_total sct ON sa.country_id = sct.country_id
    JOIN start_world_product swp ON sa.product_id = swp.product_id
    CROSS JOIN start_world_total swt
),
end_avg AS (
    SELECT
        country_id,
        product_id,
        AVG(export_value) AS avg_export
    FROM hs92.country_product_year_4
    WHERE year BETWEEN 2022 AND 2024
    GROUP BY country_id, product_id
),
end_country_total AS (
    SELECT country_id, SUM(avg_export) AS total_export
    FROM end_avg
    GROUP BY country_id
),
end_world_product AS (
    SELECT product_id, SUM(avg_export) AS world_product_export
    FROM end_avg
    GROUP BY product_id
),
end_world_total AS (
    SELECT SUM(avg_export) AS world_total FROM end_avg
),
end_rca AS (
    SELECT
        ea.country_id,
        ea.product_id,
        (ea.avg_export / NULLIF(ect.total_export, 0))
        / (ewp.world_product_export / NULLIF(ewt.world_total, 0)) AS rca
    FROM end_avg ea
    JOIN end_country_total ect ON ea.country_id = ect.country_id
    JOIN end_world_product ewp ON ea.product_id = ewp.product_id
    CROSS JOIN end_world_total ewt
)
SELECT
    p.code AS product_code,
    p.name_short_en AS product_name,
    ROUND(sr.rca::numeric, 4) AS start_rca,
    ROUND(er.rca::numeric, 4) AS end_rca
FROM start_rca sr
JOIN end_rca er ON sr.country_id = er.country_id AND sr.product_id = er.product_id
JOIN classification.location_country loc ON sr.country_id = loc.country_id
JOIN classification.product_hs92 p ON sr.product_id = p.product_id
WHERE loc.iso3_code = 'RUS'
  AND sr.rca < 0.5
  AND er.rca >= 1.0
ORDER BY er.rca DESC;
