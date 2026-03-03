WITH latest_year AS (
    SELECT MAX(year) as max_year
    FROM hs12.country_country_year
)
-- Germany's exports to USA (export_value) and imports from USA (import_value)
-- Trade balance = exports - imports from Germany's perspective
SELECT
    ccy.year,
    ccy.export_value as germany_exports_to_usa,
    ccy.import_value as germany_imports_from_usa,
    (ccy.export_value - ccy.import_value) as trade_balance
FROM hs12.country_country_year ccy
JOIN classification.location_country exporter
    ON ccy.country_id = exporter.country_id
    AND exporter.iso3_code = 'DEU'
JOIN classification.location_country importer
    ON ccy.partner_id = importer.country_id
    AND importer.iso3_code = 'USA'
WHERE ccy.year = (SELECT max_year FROM latest_year);
