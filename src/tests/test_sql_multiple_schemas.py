import pytest
from sqlalchemy import create_engine, text, MetaData
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.config import get_settings

# Load settings
settings = get_settings()
ATLAS_DB_URL = settings.atlas_db_url

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def check_atlas_db_url():
    """Fixture to check if ATLAS_DB_URL is set before running tests."""
    if not settings.atlas_db_url:
        pytest.xfail("ATLAS_DB_URL not configured in settings")


@pytest.fixture
def db_engine():
    """Fixture to create a database engine."""
    engine = create_engine(
        ATLAS_DB_URL,
        execution_options={"postgresql_readonly": True},
        connect_args={"connect_timeout": 10},
    )
    yield engine
    engine.dispose()


@pytest.fixture
def db_instance(db_engine):
    """Fixture to create a SQLDatabaseWithSchemas instance."""
    return SQLDatabaseWithSchemas(
        engine=db_engine,
        schemas=["hs92", "sitc", "classification"],
        sample_rows_in_table_info=5,
    )


def test_database_connection(db_engine):
    """Test basic database connectivity"""
    with db_engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        assert result.scalar() == 1


def test_database_initialization(db_instance):
    """Test initialization of SQLDatabaseWithSchemas"""
    # Verify schemas initialization
    assert "hs92" in db_instance._schemas
    assert "sitc" in db_instance._schemas
    assert "classification" in db_instance._schemas

    # Verify tables existence
    assert "hs92.country_country_product_year_4" in db_instance._all_tables
    assert "classification.product_hs92" in db_instance._all_tables

    # Verify metadata reflection
    assert db_instance._metadata is not None
    assert len(db_instance._metadata.tables) > 0


def test_initialization_with_custom_settings(db_engine):
    """Test initialization with custom settings"""
    custom_metadata = MetaData()
    custom_table_info = {
        "hs92.country_country_product_year_4": "Custom info for trade data"
    }

    db = SQLDatabaseWithSchemas(
        engine=db_engine,
        schemas=["hs92"],
        metadata=custom_metadata,
        sample_rows_in_table_info=10,
        indexes_in_table_info=True,
        custom_table_info=custom_table_info,
        max_string_length=500,
    )

    assert db._sample_rows_in_table_info == 10
    assert db._indexes_in_table_info is True
    assert db._max_string_length == 500
    assert db._custom_table_info == custom_table_info


def test_invalid_schema(db_engine):
    """Test initialization with invalid schema"""
    with pytest.raises(ValueError) as exc_info:
        SQLDatabaseWithSchemas(engine=db_engine, schemas=["nonexistent_schema"])
    assert "schemas were not found in the database" in str(exc_info.value)


def test_invalid_table_settings(db_engine):
    """Test initialization with invalid table settings"""
    with pytest.raises(ValueError):
        SQLDatabaseWithSchemas(
            engine=db_engine,
            schemas=["hs92"],
            include_tables=["table1"],
            ignore_tables=["table2"],
        )


def test_get_table_info_basic(db_instance):
    """Test basic table info retrieval"""
    table_info = db_instance.get_table_info(
        table_names=["hs92.country_country_product_year_4"]
    )
    assert "CREATE TABLE" in table_info
    assert "country_id" in table_info.lower()
    assert "export_value" in table_info.lower()


def test_get_table_info_with_all_options(db_instance):
    """Test table info retrieval with all options enabled"""
    table_info = db_instance.get_table_info(
        table_names=["hs92.country_country_product_year_4"],
        include_comments=True,
        include_foreign_keys=True,
        include_indexes=True,
        include_sample_rows=True,
    )

    # Basic structure checks
    assert "CREATE TABLE" in table_info

    # Check for optional sections if they exist
    if "/*" in table_info:
        if "Foreign Keys:" in table_info:
            assert "->" in table_info
        if "Indexes:" in table_info:
            assert "INDEX" in table_info.upper() or "UNIQUE" in table_info.upper()
        if "Sample Rows:" in table_info:
            assert "rows from hs92.country_country_product_year_4 table" in table_info


def test_get_usable_table_names(db_instance):
    """Test retrieval of usable table names"""
    table_names = db_instance.get_usable_table_names()
    assert isinstance(table_names, list)
    assert len(table_names) > 0
    assert "hs92.country_country_product_year_4" in table_names
    assert all("." in table for table in table_names)


def test_get_context(db_instance):
    """Test context retrieval"""
    context = db_instance.get_context()
    assert isinstance(context, dict)
    assert "table_info" in context
    assert "table_names" in context
    assert "schemas" in context
    assert all(
        schema in context["schemas"] for schema in ["hs92", "sitc", "classification"]
    )


def test_real_world_query(db_instance):
    """Test execution of a real-world complex query"""
    query = """
    SELECT
        loc_exp.iso3_code as exporter,
        loc_imp.iso3_code as importer,
        p.code as product_code,
        p.name_en as product_name,
        SUM(ccpy.export_value) as total_export_value
    FROM hs92.country_country_product_year_4 ccpy
    JOIN classification.location_country loc_exp
        ON ccpy.country_id = loc_exp.country_id
        AND loc_exp.iso3_code = 'BOL'
    JOIN classification.location_country loc_imp
        ON ccpy.partner_id = loc_imp.country_id
        AND loc_imp.iso3_code = 'MAR'
    JOIN classification.product_hs92 p
        ON ccpy.product_id = p.product_id
    WHERE ccpy.year BETWEEN 2010 AND 2022
        AND ccpy.export_value > 0
        AND ccpy.location_level = 'country'
        AND ccpy.partner_level = 'country'
    GROUP BY
        p.code,
        p.name_en,
        loc_exp.iso3_code,
        loc_imp.iso3_code
    ORDER BY
        total_export_value DESC
    LIMIT 10;
    """

    result = db_instance._execute(query)

    # Verify result structure
    assert isinstance(result, list)
    if len(result) > 0:
        first_row = result[0]
        assert isinstance(first_row, dict)
        assert all(
            key in first_row
            for key in [
                "exporter",
                "importer",
                "product_code",
                "product_name",
                "total_export_value",
            ]
        )
        assert first_row["exporter"] == "BOL"
        assert first_row["importer"] == "MAR"


def test_execute_with_parameters(db_instance):
    """Test query execution with parameters"""
    query = """
    SELECT loc.iso3_code, loc.name_en
    FROM classification.location_country loc
    WHERE loc.iso3_code = :country_code;
    """

    result = db_instance._execute(query, parameters={"country_code": "BOL"})

    assert len(result) == 1
    assert result[0]["iso3_code"] == "BOL"


def test_execute_different_fetch_modes(db_instance):
    """Test different fetch modes in query execution"""
    query = "SELECT DISTINCT iso3_code FROM classification.location_country LIMIT 5;"

    # Test 'all' fetch mode
    result_all = db_instance._execute(query, fetch="all")
    assert isinstance(result_all, list)
    assert len(result_all) <= 5

    # Test 'one' fetch mode
    result_one = db_instance._execute(query, fetch="one")
    assert isinstance(result_one, list)
    assert len(result_one) <= 1

    # Test 'cursor' fetch mode
    result_cursor = db_instance._execute(query, fetch="cursor")
    assert hasattr(result_cursor, "fetchall")


def test_from_uri_construction():
    """Test construction from URI"""
    db = SQLDatabaseWithSchemas.from_uri(
        ATLAS_DB_URL,
        schemas=["hs92", "classification"],
        engine_args={
            "execution_options": {"postgresql_readonly": True},
            "connect_args": {"connect_timeout": 10},
        },
    )

    assert isinstance(db, SQLDatabaseWithSchemas)
    assert "hs92" in db._schemas
    assert "classification" in db._schemas
