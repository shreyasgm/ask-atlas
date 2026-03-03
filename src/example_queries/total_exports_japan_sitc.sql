WITH latest_year AS (
    SELECT MAX(year) as max_year
    FROM sitc.country_year
)
-- Country-level aggregate exports and ECI for Japan (SITC classification)
SELECT
    cy.year,
    cy.export_value,
    cy.import_value,
    cy.eci,
    cy.diversity
FROM sitc.country_year cy
JOIN classification.location_country loc
    ON cy.country_id = loc.country_id
    AND loc.iso3_code = 'JPN'
WHERE cy.year = (SELECT max_year FROM latest_year);
