-- Export value CAGR for coffee (HS 0901) across top exporting countries
-- Uses the pre-computed lookback table for growth metrics
SELECT
    loc.iso3_code,
    loc.name_en as country_name,
    lb.export_value_cagr,
    lb.export_value_percent_change,
    lb.global_market_share_change
FROM hs92.country_product_lookback_4 lb
JOIN classification.location_country loc
    ON lb.country_id = loc.country_id
JOIN classification.product_hs92 p
    ON lb.product_id = p.product_id
WHERE p.code = '0901'
    AND lb.lookback = 5
ORDER BY lb.export_value_cagr DESC
LIMIT 10;
