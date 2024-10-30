import pytest
from sqlalchemy import create_engine, text
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
import os

ATLAS_DB_URL = os.getenv("ATLAS_DB_URL")


@pytest.fixture(autouse=True)
def check_atlas_db_url():
    """Fixture to check if ATLAS_DB_URL is set before running tests."""
    if not os.getenv("ATLAS_DB_URL"):
        pytest.xfail("ATLAS_DB_URL environment variable not set")


def test_database_connection(monkeypatch):
    """Test basic database connectivity"""
    # Initialize engine
    engine = create_engine(
        ATLAS_DB_URL,
        execution_options={"postgresql_readonly": True},
        connect_args={"connect_timeout": 10},
    )

    # Test basic connectivity
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        assert result.scalar() == 1


def test_database_with_schemas_initialization():
    """Test initialization of SQLDatabaseWithSchemas"""
    # Initialize engine
    engine = create_engine(
        ATLAS_DB_URL,
        execution_options={"postgresql_readonly": True},
        connect_args={"connect_timeout": 10},
    )

    # Test initialization with specific schemas
    db = SQLDatabaseWithSchemas(engine=engine, schemas=["sitc", "classification"])

    # Verify that the schemas were properly initialized
    assert "sitc" in db._schemas
    assert "classification" in db._schemas

    # Verify that the tables exist in the respective schemas
    assert "sitc.country_product_year_4" in db._all_tables
    assert "classification.product_hs12" in db._all_tables


def test_get_table_info():
    """Test get_table_info method"""
    # Initialize engine
    engine = create_engine(
        ATLAS_DB_URL,
        execution_options={"postgresql_readonly": True},
        connect_args={"connect_timeout": 10},
    )

    # Initialize database with schemas
    db = SQLDatabaseWithSchemas(engine=engine, schemas=["sitc", "classification"])

    # Test get_table_info for specific tables
    table_info = db.get_table_info(
        table_names=["sitc.country_product_year_4", "classification.product_hs12"]
    )

    # Verify that the table info contains CREATE TABLE statements
    assert "CREATE TABLE sitc.country_product_year_4" in table_info
    assert "CREATE TABLE classification.product_hs12" in table_info

    # Verify that some expected columns are present
    assert "country_id" in table_info.lower()
    assert "code" in table_info.lower()
    assert "year" in table_info.lower()


def test_invalid_schema():
    """Test initialization with invalid schema"""

    engine = create_engine(
        ATLAS_DB_URL,
        execution_options={"postgresql_readonly": True},
        connect_args={"connect_timeout": 10},
    )

    # Test initialization with invalid schema
    with pytest.raises(ValueError) as exc_info:
        SQLDatabaseWithSchemas(engine=engine, schemas=["nonexistent_schema"])

    assert "schemas were not found in the database" in str(exc_info.value)


def test_get_table_info_with_options():
    """Test get_table_info with various options enabled"""
    engine = create_engine(
        ATLAS_DB_URL,
        execution_options={"postgresql_readonly": True},
        connect_args={"connect_timeout": 10},
    )

    db = SQLDatabaseWithSchemas(engine=engine, schemas=["sitc", "classification"])

    # Test with all optional information
    table_info = db.get_table_info(
        table_names=["sitc.country_product_year_4"],
        include_comments=True,
        include_foreign_keys=True,
        include_indexes=True,
        include_sample_rows=True,
    )

    # Verify that optional sections are present when available
    assert "CREATE TABLE" in table_info

    # Note: These assertions might need to be adjusted based on whether
    # your actual database has these features
    if "/*" in table_info:  # If there are any additional sections
        if "Foreign Keys:" in table_info:
            assert "->" in table_info
        if "Indexes:" in table_info:
            assert "INDEX" in table_info.upper()
        if "Sample Rows:" in table_info:
            assert "rows from sitc.country_product_year_4 table" in table_info
