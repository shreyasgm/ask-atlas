"""Unit tests for src/sql_validation.py — pure validation functions.

No LLM, no DB, no async needed.
"""

from src.sql_validation import validate_sql

# ---------------------------------------------------------------------------
# validate_sql — core validation logic
# ---------------------------------------------------------------------------


class TestValidateSql:
    """Tests for the main validate_sql function."""

    def test_valid_simple_select(self):
        sql = "SELECT country_id FROM hs92.country_year"
        result = validate_sql(sql)
        assert result.is_valid is True
        assert result.errors == []

    def test_syntax_error(self):
        sql = "SELEC country FORM table"
        result = validate_sql(sql)
        assert result.is_valid is False
        assert any("syntax" in e.lower() or "parse" in e.lower() for e in result.errors)

    def test_empty_sql(self):
        result = validate_sql("")
        assert result.is_valid is False
        assert any("empty" in e.lower() for e in result.errors)

    def test_whitespace_only_sql(self):
        result = validate_sql("   \n\t  ")
        assert result.is_valid is False
        assert any("empty" in e.lower() for e in result.errors)

    def test_select_star_warning(self):
        sql = "SELECT * FROM hs92.country_year"
        result = validate_sql(sql)
        assert result.is_valid is True
        assert any("SELECT *" in w for w in result.warnings)

    def test_leading_like_wildcard_warning(self):
        sql = "SELECT country_id FROM hs92.country_year WHERE name LIKE '%cotton'"
        result = validate_sql(sql)
        assert result.is_valid is True
        assert any("leading wildcard" in w.lower() for w in result.warnings)

    def test_no_warnings_for_normal_like(self):
        sql = "SELECT country_id FROM hs92.country_year WHERE name LIKE 'cotton%'"
        result = validate_sql(sql)
        assert result.is_valid is True
        assert not any("leading wildcard" in w.lower() for w in result.warnings)

    def test_multiple_tables_in_join(self):
        sql = (
            "SELECT c.country_id, p.product_id "
            "FROM hs92.country_year c "
            "JOIN hs92.country_product_year_4 p ON c.country_id = p.country_id"
        )
        result = validate_sql(sql)
        assert result.is_valid is True
        assert result.errors == []

    def test_subquery_valid(self):
        sql = "SELECT * FROM (SELECT country_id FROM hs92.country_year) sub"
        result = validate_sql(sql)
        assert result.is_valid is True

    # --- Write-operation blocking ---

    def test_insert_blocked(self):
        sql = "INSERT INTO hs92.country_year (country_id) VALUES (1)"
        result = validate_sql(sql)
        assert result.is_valid is False
        assert any("write" in e.lower() or "insert" in e.lower() for e in result.errors)

    def test_update_blocked(self):
        sql = "UPDATE hs92.country_year SET export_value = 0 WHERE country_id = 1"
        result = validate_sql(sql)
        assert result.is_valid is False
        assert any("write" in e.lower() or "update" in e.lower() for e in result.errors)

    def test_delete_blocked(self):
        sql = "DELETE FROM hs92.country_year WHERE country_id = 1"
        result = validate_sql(sql)
        assert result.is_valid is False
        assert any("write" in e.lower() or "delete" in e.lower() for e in result.errors)

    def test_drop_blocked(self):
        sql = "DROP TABLE hs92.country_year"
        result = validate_sql(sql)
        assert result.is_valid is False
        assert any("write" in e.lower() or "drop" in e.lower() for e in result.errors)

    def test_alter_blocked(self):
        sql = "ALTER TABLE hs92.country_year ADD COLUMN foo integer"
        result = validate_sql(sql)
        assert result.is_valid is False
        assert any("write" in e.lower() or "alter" in e.lower() for e in result.errors)

    def test_truncate_blocked(self):
        sql = "TRUNCATE TABLE hs92.country_year"
        result = validate_sql(sql)
        assert result.is_valid is False
        assert any(
            "write" in e.lower() or "truncate" in e.lower() for e in result.errors
        )

    def test_create_blocked(self):
        sql = "CREATE TABLE foo.bar (id integer)"
        result = validate_sql(sql)
        assert result.is_valid is False
        assert any("write" in e.lower() or "create" in e.lower() for e in result.errors)

    def test_grant_blocked(self):
        sql = "GRANT SELECT ON hs92.country_year TO public"
        result = validate_sql(sql)
        assert result.is_valid is False
        assert any("write" in e.lower() or "grant" in e.lower() for e in result.errors)

    def test_revoke_blocked(self):
        sql = "REVOKE SELECT ON hs92.country_year FROM public"
        result = validate_sql(sql)
        assert result.is_valid is False
        assert any("write" in e.lower() or "revoke" in e.lower() for e in result.errors)

    def test_select_still_works_after_write_blocking(self):
        """Regression: SELECT queries must still pass after adding write checks."""
        sql = "SELECT country_id, export_value FROM hs92.country_year WHERE year = 2022"
        result = validate_sql(sql)
        assert result.is_valid is True
        assert result.errors == []

    def test_cte_query_valid(self):
        """CTE queries should pass validation."""
        sql = (
            "WITH latest_year AS ("
            "  SELECT MAX(year) AS max_year FROM hs92.country_year"
            ") SELECT max_year FROM latest_year"
        )
        result = validate_sql(sql)
        assert result.is_valid is True

    def test_window_function_valid(self):
        """Window function queries should pass validation."""
        sql = (
            "SELECT country_id, "
            "ROW_NUMBER() OVER (PARTITION BY year ORDER BY export_value DESC) AS rn "
            "FROM hs92.country_year"
        )
        result = validate_sql(sql)
        assert result.is_valid is True
