"""Unit tests for src/sql_validation.py — pure validation functions.

No LLM, no DB, no async needed.
"""

from src.sql_validation import extract_table_names_from_ddl, validate_sql

# ---------------------------------------------------------------------------
# extract_table_names_from_ddl
# ---------------------------------------------------------------------------


class TestExtractTableNamesFromDDL:
    """Tests for the DDL → table-name extraction helper."""

    def test_single_schema_qualified_table(self):
        ddl = "CREATE TABLE hs92.country_year (\n  country_id integer\n);\n"
        assert extract_table_names_from_ddl(ddl) == {"hs92.country_year"}

    def test_multiple_tables(self):
        ddl = (
            "Table: hs92.country_year\nDescription: Year-level data\n"
            "CREATE TABLE hs92.country_year (\n  country_id integer\n);\n\n"
            "Table: hs92.country_product_year_4\nDescription: 4-digit product data\n"
            "CREATE TABLE hs92.country_product_year_4 (\n  product_id integer\n);\n"
        )
        result = extract_table_names_from_ddl(ddl)
        assert result == {"hs92.country_year", "hs92.country_product_year_4"}

    def test_if_not_exists_variant(self):
        ddl = "CREATE TABLE IF NOT EXISTS hs92.product_year_4 (\n  id integer\n);\n"
        assert extract_table_names_from_ddl(ddl) == {"hs92.product_year_4"}

    def test_empty_ddl(self):
        assert extract_table_names_from_ddl("") == set()


# ---------------------------------------------------------------------------
# validate_sql — core validation logic
# ---------------------------------------------------------------------------


class TestValidateSql:
    """Tests for the main validate_sql function."""

    VALID_TABLES = {
        "hs92.country_year",
        "hs92.country_product_year_4",
        "hs92.product_year_4",
    }

    def test_valid_simple_select(self):
        sql = "SELECT country_id FROM hs92.country_year"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is True
        assert result.errors == []

    def test_syntax_error(self):
        sql = "SELEC country FORM table"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("syntax" in e.lower() or "parse" in e.lower() for e in result.errors)

    def test_empty_sql(self):
        result = validate_sql("", self.VALID_TABLES)
        assert result.is_valid is False
        assert any("empty" in e.lower() for e in result.errors)

    def test_whitespace_only_sql(self):
        result = validate_sql("   \n\t  ", self.VALID_TABLES)
        assert result.is_valid is False
        assert any("empty" in e.lower() for e in result.errors)

    def test_unknown_table(self):
        sql = "SELECT * FROM nonexistent.fake_table"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("nonexistent.fake_table" in e.lower() for e in result.errors)

    def test_select_star_warning(self):
        sql = "SELECT * FROM hs92.country_year"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is True
        assert any("SELECT *" in w for w in result.warnings)

    def test_leading_like_wildcard_warning(self):
        sql = "SELECT country_id FROM hs92.country_year WHERE name LIKE '%cotton'"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is True
        assert any("leading wildcard" in w.lower() for w in result.warnings)

    def test_multiple_tables_in_join(self):
        sql = (
            "SELECT c.country_id, p.product_id "
            "FROM hs92.country_year c "
            "JOIN hs92.country_product_year_4 p ON c.country_id = p.country_id"
        )
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is True
        assert result.errors == []

    def test_join_with_unknown_table(self):
        sql = (
            "SELECT c.country_id "
            "FROM hs92.country_year c "
            "JOIN nonexistent.bad_table b ON c.id = b.id"
        )
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("nonexistent.bad_table" in e.lower() for e in result.errors)

    def test_subquery_table_validated(self):
        sql = "SELECT * FROM (" "  SELECT country_id FROM hs92.country_year" ") sub"
        result = validate_sql(sql, self.VALID_TABLES)
        # Inner table is valid; SELECT * on subquery alias is fine
        assert result.is_valid is True

    def test_no_warnings_for_normal_like(self):
        sql = "SELECT country_id FROM hs92.country_year WHERE name LIKE 'cotton%'"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is True
        assert not any("leading wildcard" in w.lower() for w in result.warnings)

    def test_empty_valid_tables_rejects_any_table(self):
        sql = "SELECT 1 FROM hs92.country_year"
        result = validate_sql(sql, set())
        assert result.is_valid is False
        assert any("hs92.country_year" in e.lower() for e in result.errors)

    def test_classification_tables_accepted_when_in_valid_set(self):
        """Classification tables should pass validation when included in valid_tables."""
        valid = {
            "hs92.country_year",
            "hs92.country_product_year_4",
            "classification.location_country",
            "classification.product_hs92",
        }
        sql = (
            "SELECT cy.country_id, lc.name_en "
            "FROM hs92.country_year cy "
            "JOIN classification.location_country lc ON cy.country_id = lc.country_id"
        )
        result = validate_sql(sql, valid)
        assert result.is_valid is True
        assert result.errors == []

    def test_classification_product_table_in_join(self):
        """Joining classification.product_hs92 should pass when it's in valid_tables."""
        valid = {
            "hs92.country_product_year_4",
            "classification.product_hs92",
        }
        sql = (
            "SELECT p.name_short_en, cy.export_value "
            "FROM hs92.country_product_year_4 cy "
            "JOIN classification.product_hs92 p ON cy.product_id = p.product_id"
        )
        result = validate_sql(sql, valid)
        assert result.is_valid is True
        assert result.errors == []
