-- Export value CAGR for coffee (HS 0901) across top exporting countries
-- Computes CAGR manually from export values over a 5-year window
-- Filters to top 20 exporters by latest-year value first, then ranks by CAGR
WITH max_yr AS (
    SELECT MAX(year) AS yr FROM hs92.country_product_year_4
),
coffee_exports AS (
    SELECT
        loc.iso3_code,
        loc.name_short_en AS country_name,
        cpy.year,
        cpy.export_value
    FROM hs92.country_product_year_4 cpy
    JOIN classification.location_country loc ON cpy.country_id = loc.country_id
    JOIN classification.product_hs92 p ON cpy.product_id = p.product_id
    WHERE p.code = '0901'
        AND cpy.year IN ((SELECT yr FROM max_yr) - 5, (SELECT yr FROM max_yr))
        AND cpy.export_value > 0
),
pivoted AS (
    SELECT
        iso3_code,
        country_name,
        MAX(CASE WHEN year = (SELECT yr - 5 FROM max_yr) THEN export_value END) AS start_value,
        MAX(CASE WHEN year = (SELECT yr FROM max_yr) THEN export_value END) AS end_value
    FROM coffee_exports
    GROUP BY iso3_code, country_name
    HAVING MAX(CASE WHEN year = (SELECT yr - 5 FROM max_yr) THEN export_value END) > 0
       AND MAX(CASE WHEN year = (SELECT yr FROM max_yr) THEN export_value END) > 0
),
top_exporters AS (
    SELECT * FROM pivoted ORDER BY end_value DESC LIMIT 20
)
SELECT
    iso3_code,
    country_name,
    start_value,
    end_value,
    ROUND((POWER(end_value::numeric / start_value::numeric, 1.0 / 5) - 1) * 100, 2) AS cagr_pct
FROM top_exporters
ORDER BY cagr_pct DESC
LIMIT 10;
