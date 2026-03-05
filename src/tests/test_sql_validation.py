"""Unit tests for src/sql_validation.py — pure validation functions.

No LLM, no DB, no async needed.
"""

from unittest.mock import patch

from src.sql_validation import (
    build_schema_from_ddl,
    extract_table_names_from_ddl,
    validate_sql,
)

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

    # --- Write-operation blocking (Tier 1A) ---

    def test_insert_blocked(self):
        sql = "INSERT INTO hs92.country_year (country_id) VALUES (1)"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("write" in e.lower() or "insert" in e.lower() for e in result.errors)

    def test_update_blocked(self):
        sql = "UPDATE hs92.country_year SET export_value = 0 WHERE country_id = 1"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("write" in e.lower() or "update" in e.lower() for e in result.errors)

    def test_delete_blocked(self):
        sql = "DELETE FROM hs92.country_year WHERE country_id = 1"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("write" in e.lower() or "delete" in e.lower() for e in result.errors)

    def test_drop_blocked(self):
        sql = "DROP TABLE hs92.country_year"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("write" in e.lower() or "drop" in e.lower() for e in result.errors)

    def test_alter_blocked(self):
        sql = "ALTER TABLE hs92.country_year ADD COLUMN foo integer"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("write" in e.lower() or "alter" in e.lower() for e in result.errors)

    def test_truncate_blocked(self):
        sql = "TRUNCATE TABLE hs92.country_year"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any(
            "write" in e.lower() or "truncate" in e.lower() for e in result.errors
        )

    def test_create_blocked(self):
        sql = "CREATE TABLE foo.bar (id integer)"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("write" in e.lower() or "create" in e.lower() for e in result.errors)

    def test_grant_blocked(self):
        sql = "GRANT SELECT ON hs92.country_year TO public"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("write" in e.lower() or "grant" in e.lower() for e in result.errors)

    def test_revoke_blocked(self):
        sql = "REVOKE SELECT ON hs92.country_year FROM public"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is False
        assert any("write" in e.lower() or "revoke" in e.lower() for e in result.errors)

    def test_select_still_works_after_write_blocking(self):
        """Regression: SELECT queries must still pass after adding write checks."""
        sql = "SELECT country_id, export_value FROM hs92.country_year WHERE year = 2022"
        result = validate_sql(sql, self.VALID_TABLES)
        assert result.is_valid is True
        assert result.errors == []

    # --- Schema-mismatch detection ---

    def test_schema_mismatch_detected(self):
        """SQL using hs12 tables when expected schema is hs92 should error."""
        valid = {"hs12.country_product_year_4", "classification.location_country"}
        sql = "SELECT * FROM hs12.country_product_year_4"
        result = validate_sql(sql, valid, expected_schemas={"hs92"})
        assert result.is_valid is False
        assert any("schema mismatch" in e.lower() for e in result.errors)
        assert any("hs12" in e for e in result.errors)

    def test_classification_always_allowed(self):
        """classification.* tables should never trigger schema-mismatch errors."""
        valid = {
            "hs92.country_year",
            "classification.location_country",
            "classification.product_hs92",
        }
        sql = (
            "SELECT * FROM hs92.country_year cy "
            "JOIN classification.location_country lc ON cy.country_id = lc.country_id"
        )
        result = validate_sql(sql, valid, expected_schemas={"hs92"})
        assert result.is_valid is True
        assert not any("schema mismatch" in e.lower() for e in result.errors)

    def test_multi_schema_union_all_passes(self):
        """When expected_schemas contains both schemas, UNION ALL across them should pass."""
        valid = {
            "hs92.country_product_year_4",
            "services_unilateral.country_product_year_4",
        }
        sql = (
            "SELECT * FROM hs92.country_product_year_4 "
            "UNION ALL "
            "SELECT * FROM services_unilateral.country_product_year_4"
        )
        result = validate_sql(
            sql, valid, expected_schemas={"hs92", "services_unilateral"}
        )
        assert result.is_valid is True

    def test_schema_check_skipped_when_none(self):
        """When expected_schemas is None, no schema-mismatch check runs."""
        valid = {"hs12.country_product_year_4"}
        sql = "SELECT * FROM hs12.country_product_year_4"
        result = validate_sql(sql, valid, expected_schemas=None)
        assert result.is_valid is True

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


# ---------------------------------------------------------------------------
# build_schema_from_ddl
# ---------------------------------------------------------------------------


class TestBuildSchemaFromDDL:
    """Tests for DDL → nested schema dict extraction."""

    def test_basic_schema_extraction(self):
        ddl = (
            "CREATE TABLE hs92.country_year (\n"
            "  country_id INTEGER,\n"
            "  year INTEGER,\n"
            "  export_value FLOAT\n);\n"
        )
        result = build_schema_from_ddl(ddl)
        assert "hs92" in result
        assert "country_year" in result["hs92"]
        assert result["hs92"]["country_year"] == {
            "country_id": "INTEGER",
            "year": "INTEGER",
            "export_value": "FLOAT",
        }

    def test_multiple_schemas(self):
        ddl = (
            "CREATE TABLE hs92.country_year (\n"
            "  country_id INTEGER,\n"
            "  year INTEGER\n);\n\n"
            "CREATE TABLE classification.location_country (\n"
            "  country_id INTEGER,\n"
            "  name_en TEXT,\n"
            "  iso3_code CHAR(3)\n);\n"
        )
        result = build_schema_from_ddl(ddl)
        assert "hs92" in result
        assert "classification" in result
        assert "country_id" in result["classification"]["location_country"]
        assert "iso3_code" in result["classification"]["location_country"]

    def test_empty_ddl(self):
        assert build_schema_from_ddl("") == {}

    def test_constraint_keywords_excluded(self):
        ddl = (
            "CREATE TABLE hs92.t (\n" "  col_a INTEGER,\n" "  PRIMARY KEY (col_a)\n);\n"
        )
        result = build_schema_from_ddl(ddl)
        cols = result["hs92"]["t"]
        assert "col_a" in cols
        assert "primary" not in cols

    def test_columns_after_char_type_not_truncated(self):
        """Regression: CHAR(3) paren must not truncate remaining columns."""
        ddl = (
            "CREATE TABLE classification.location_country (\n"
            "\tcountry_id SERIAL NOT NULL,\n"
            "\tiso3_code CHAR(3) NOT NULL,\n"
            "\tname_en TEXT NOT NULL,\n"
            "\tname_short_en TEXT NOT NULL,\n"
            "\tCONSTRAINT location_country_pkey PRIMARY KEY (country_id)\n"
            ");\n"
        )
        result = build_schema_from_ddl(ddl)
        cols = result["classification"]["location_country"]
        assert "iso3_code" in cols
        assert "name_en" in cols, "name_en missing — CHAR(3) paren truncated the block"
        assert "name_short_en" in cols


# ---------------------------------------------------------------------------
# Qualify-based column validation
# ---------------------------------------------------------------------------


class TestQualifyColumnValidation:
    """Tests for scope-aware column validation via sqlglot qualify()."""

    VALID_TABLES = {"hs92.country_year", "classification.location_country"}

    SCHEMA = {
        "hs92": {
            "country_year": {
                "country_id": "INT",
                "year": "INT",
                "export_value": "FLOAT",
            },
            "country_product_year_4": {
                "country_id": "INT",
                "product_id": "INT",
                "year": "INT",
                "export_value": "FLOAT",
                "export_rca": "FLOAT",
            },
        },
        "classification": {
            "location_country": {
                "country_id": "INT",
                "name_en": "TEXT",
                "name_short_en": "TEXT",
                "iso3_code": "TEXT",
            },
            "product_hs92": {
                "product_id": "INT",
                "name_en": "TEXT",
                "name_short_en": "TEXT",
                "code": "TEXT",
            },
        },
    }

    def test_valid_columns_pass(self):
        sql = "SELECT country_id, year FROM hs92.country_year"
        result = validate_sql(sql, self.VALID_TABLES, column_schema=self.SCHEMA)
        assert result.is_valid is True
        assert result.errors == []

    def test_unknown_column_flagged(self):
        sql = "SELECT nonexistent_col FROM hs92.country_year"
        result = validate_sql(sql, self.VALID_TABLES, column_schema=self.SCHEMA)
        assert result.is_valid is False
        assert any("nonexistent_col" in e.lower() for e in result.errors)

    def test_cte_alias_not_flagged(self):
        sql = (
            "WITH latest_year AS ("
            "  SELECT MAX(year) AS max_year FROM hs92.country_year"
            ") SELECT max_year FROM latest_year"
        )
        result = validate_sql(sql, self.VALID_TABLES, column_schema=self.SCHEMA)
        assert result.is_valid is True
        assert not any("max_year" in e.lower() for e in result.errors)

    def test_aggregation_alias_in_order_by(self):
        sql = (
            "SELECT SUM(export_value) AS total_exports "
            "FROM hs92.country_year "
            "ORDER BY total_exports"
        )
        result = validate_sql(sql, self.VALID_TABLES, column_schema=self.SCHEMA)
        assert result.is_valid is True
        assert not any("total_exports" in e.lower() for e in result.errors)

    def test_subquery_derived_column(self):
        sql = (
            "SELECT sub.total "
            "FROM (SELECT SUM(export_value) AS total FROM hs92.country_year) sub"
        )
        result = validate_sql(sql, self.VALID_TABLES, column_schema=self.SCHEMA)
        assert result.is_valid is True

    def test_cross_schema_join(self):
        valid = {
            "hs92.country_year",
            "classification.location_country",
        }
        sql = (
            "SELECT cy.country_id, lc.name_en "
            "FROM hs92.country_year cy "
            "JOIN classification.location_country lc ON cy.country_id = lc.country_id"
        )
        result = validate_sql(sql, valid, column_schema=self.SCHEMA)
        assert result.is_valid is True
        assert result.errors == []

    def test_window_function_columns(self):
        sql = (
            "SELECT country_id, "
            "ROW_NUMBER() OVER (PARTITION BY year ORDER BY export_value DESC) AS rn "
            "FROM hs92.country_year"
        )
        result = validate_sql(sql, self.VALID_TABLES, column_schema=self.SCHEMA)
        assert result.is_valid is True

    def test_qualify_error_fails_open(self):
        """Internal qualify error should log warning and return no errors."""
        sql = "SELECT country_id FROM hs92.country_year"
        with patch(
            "src.sql_validation.qualify",
            side_effect=RuntimeError("internal bug"),
        ):
            result = validate_sql(sql, self.VALID_TABLES, column_schema=self.SCHEMA)
        assert result.is_valid is True
        assert result.errors == []

    def test_column_check_skipped_when_none(self):
        sql = "SELECT any_column FROM hs92.country_year"
        result = validate_sql(sql, self.VALID_TABLES, column_schema=None)
        assert result.is_valid is True
