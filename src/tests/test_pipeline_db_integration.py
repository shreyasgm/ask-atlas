"""DB integration tests: real Docker DB + deterministic (fake) LLM.

These tests fill the "middle tier" of the testing pyramid — they validate
that pipeline nodes interact correctly with a real PostgreSQL schema
without requiring any LLM API calls.

Requires: Docker test DB on port 5433.
Run with:
    ATLAS_DB_URL=postgresql://postgres:testpass@localhost:5433/atlas_test \
    PYTHONPATH=$(pwd) pytest -m "db" src/tests/test_pipeline_db_integration.py -v
"""

import pytest
from sqlalchemy import create_engine, text

from src.config import get_settings
from src.generate_query import (
    execute_sql_node,
    get_table_info_node,
    get_table_info_for_schemas,
    load_table_descriptions,
)
from src.sql_multiple_schemas import SQLDatabaseWithSchemas

settings = get_settings()

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def db_engine():
    """Create a SQLAlchemy engine connected to the Docker test DB."""
    if not settings.atlas_db_url:
        pytest.skip("ATLAS_DB_URL not configured")
    engine = create_engine(
        settings.atlas_db_url,
        execution_options={"postgresql_readonly": True},
        connect_args={"connect_timeout": 10},
    )
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def db_instance(db_engine):
    """SQLDatabaseWithSchemas backed by the Docker test DB."""
    return SQLDatabaseWithSchemas(
        engine=db_engine,
        schemas=["hs92", "classification"],
        sample_rows_in_table_info=3,
    )


@pytest.fixture(scope="module")
def table_descriptions(base_dir):
    """Load real table descriptions JSON."""
    return load_table_descriptions(base_dir / "db_table_descriptions.json")


# ---------------------------------------------------------------------------
# 1. execute_sql_node — known-good SQL
# ---------------------------------------------------------------------------


async def test_execute_sql_node_real_db(db_engine):
    """Known-good SQL runs against real schema and returns expected data."""
    state = {
        "messages": [],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        "pipeline_question": "",
        "pipeline_products": None,
        "pipeline_codes": "",
        "pipeline_table_info": "",
        "pipeline_sql": (
            "SELECT loc.iso3_code, loc.name_en "
            "FROM classification.location_country loc "
            "WHERE loc.iso3_code = 'BOL' LIMIT 1"
        ),
        "pipeline_result": "",
    }

    result = await execute_sql_node(state, async_engine=db_engine)

    assert result["last_error"] == ""
    assert "BOL" in result["pipeline_result"]
    assert "Bolivia" in result["pipeline_result"]


# ---------------------------------------------------------------------------
# 2. execute_sql_node — invalid SQL
# ---------------------------------------------------------------------------


async def test_execute_sql_node_invalid_sql(db_engine):
    """Bad SQL populates last_error with a meaningful message."""
    state = {
        "messages": [],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        "pipeline_question": "",
        "pipeline_products": None,
        "pipeline_codes": "",
        "pipeline_table_info": "",
        "pipeline_sql": "SELECT FROM WHERE INVALID SYNTAX",
        "pipeline_result": "",
    }

    result = await execute_sql_node(state, async_engine=db_engine)

    assert result["pipeline_result"] == ""
    assert result["last_error"] != ""


# ---------------------------------------------------------------------------
# 3. execute_sql_node — non-existent table
# ---------------------------------------------------------------------------


async def test_execute_sql_node_bad_table_ref(db_engine):
    """Non-existent table reference returns an error."""
    state = {
        "messages": [],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        "pipeline_question": "",
        "pipeline_products": None,
        "pipeline_codes": "",
        "pipeline_table_info": "",
        "pipeline_sql": "SELECT * FROM nonexistent_schema.fake_table LIMIT 1",
        "pipeline_result": "",
    }

    result = await execute_sql_node(state, async_engine=db_engine)

    assert result["pipeline_result"] == ""
    assert result["last_error"] != ""


# ---------------------------------------------------------------------------
# 4. get_table_info_node — real DB returns expected columns
# ---------------------------------------------------------------------------


async def test_get_table_info_node_real_db(db_instance, table_descriptions):
    """Real DB returns table_info string with expected column names."""
    from src.product_and_schema_lookup import SchemasAndProductsFound

    products = SchemasAndProductsFound(
        classification_schemas=["hs92"],
        products=[],
        requires_product_lookup=False,
    )
    state = {
        "messages": [],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        "pipeline_question": "",
        "pipeline_products": products,
        "pipeline_codes": "",
        "pipeline_table_info": "",
        "pipeline_sql": "",
        "pipeline_result": "",
    }

    result = await get_table_info_node(
        state, db=db_instance, table_descriptions=table_descriptions
    )

    table_info = result["pipeline_table_info"]
    assert len(table_info) > 0
    # The hs92 schema should have trade-related columns
    assert "export_value" in table_info.lower()
    assert "country_id" in table_info.lower()


# ---------------------------------------------------------------------------
# 5. get_table_info_for_schemas — sync helper with real DB
# ---------------------------------------------------------------------------


def test_get_table_info_for_schemas_real_db(db_instance, table_descriptions):
    """Sync helper returns non-empty info for known schemas."""
    table_info = get_table_info_for_schemas(
        db=db_instance,
        table_descriptions=table_descriptions,
        classification_schemas=["hs92"],
    )

    assert isinstance(table_info, str)
    assert len(table_info) > 0
    assert "CREATE TABLE" in table_info
