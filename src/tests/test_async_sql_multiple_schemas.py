"""Tests for AsyncSQLDatabaseWithSchemas — async-native multi-schema DB class.

Phase 0 of TDD: all tests written before implementation.

Test categories:
  A. Unit tests (mocked async engine) — no DB needed
  B. Integration tests (real Docker DB) — @pytest.mark.db
  C. Async helper tests (for sql_pipeline / sql_subagent async variants)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import MetaData, create_engine, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings

settings = get_settings()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def event_loop():
    """Module-scoped event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def sync_db_engine():
    """Sync engine for the Docker test DB — used as comparison oracle."""
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
def async_db_engine():
    """Async engine for the Docker test DB (port 5433)."""
    if not settings.atlas_db_url:
        pytest.skip("ATLAS_DB_URL not configured")
    url = make_url(settings.atlas_db_url).set(drivername="postgresql+psycopg")
    engine = create_async_engine(
        url,
        execution_options={"postgresql_readonly": True},
        connect_args={"connect_timeout": 10},
    )
    yield engine
    # Cleanup happens sync; disposal is best-effort here
    return engine


# ---------------------------------------------------------------------------
# Helpers for building mocked async engines
# ---------------------------------------------------------------------------


def _make_mock_async_engine(
    schemas: set[str] | None = None,
    tables_per_schema: dict[str, list[str]] | None = None,
    dialect_name: str = "postgresql",
):
    """Build a MagicMock that quacks like an AsyncEngine for unit tests.

    The mock supports:
    - conn.run_sync(callable) by calling the callable with a sync mock connection
      that returns the right inspector when inspect(sync_conn) is called
    - engine.dialect.name -> dialect_name
    """

    schemas = schemas or {"public", "hs92", "classification"}
    tables_per_schema = tables_per_schema or {
        "hs92": ["country_year", "country_product_year_4"],
        "classification": ["location_country", "product_hs92"],
    }

    mock_engine = MagicMock()
    mock_engine.dialect = MagicMock()
    mock_engine.dialect.name = dialect_name

    # Build a mock inspector
    mock_inspector = MagicMock()
    mock_inspector.get_schema_names.return_value = list(schemas)
    mock_inspector.get_table_names = lambda schema=None: tables_per_schema.get(
        schema, []
    )
    mock_inspector.get_view_names = lambda schema=None: []

    # Mock sync connection that returns our inspector when inspect() is called
    mock_sync_conn = MagicMock()
    # SQLAlchemy's inspect() dispatches based on type; we patch it in run_sync
    mock_sync_conn._mock_inspector = mock_inspector

    # run_sync: calls the given function with the mock sync connection,
    # patching inspect() to return our mock inspector and MetaData.reflect to no-op
    async def _mock_run_sync(fn, *args, **kwargs):
        with (
            patch("src.sql_multiple_schemas.inspect", return_value=mock_inspector),
            patch.object(MetaData, "reflect"),
        ):
            return fn(mock_sync_conn, *args, **kwargs)

    # Mock the connect() context manager
    mock_conn = AsyncMock()
    mock_conn.run_sync = _mock_run_sync
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    # Make engine.connect() return the mock connection context manager
    mock_engine.connect = MagicMock(return_value=mock_conn)

    # Store refs for test assertions
    mock_engine._mock_inspector = mock_inspector
    mock_engine._mock_tables_per_schema = tables_per_schema
    mock_engine._mock_schemas = schemas

    return mock_engine


# ===================================================================
# A. Unit Tests (mocked async engine) — no DB needed
# ===================================================================


class TestAsyncCreateValidation:
    """Tests for factory validation logic."""

    @pytest.mark.asyncio
    async def test_create_rejects_missing_schemas(self):
        """Mock engine where inspector reports schemas {public, hs92} but
        we ask for 'nonexistent' — should raise ValueError."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        mock_engine = _make_mock_async_engine(schemas={"public", "hs92"})

        with pytest.raises(ValueError, match="schemas were not found"):
            await AsyncSQLDatabaseWithSchemas.create(
                mock_engine, schemas=["nonexistent"]
            )

    @pytest.mark.asyncio
    async def test_create_rejects_include_and_ignore_tables(self):
        """Cannot specify both include_tables and ignore_tables."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        mock_engine = _make_mock_async_engine()

        with pytest.raises(ValueError, match="Cannot specify both"):
            await AsyncSQLDatabaseWithSchemas.create(
                mock_engine,
                schemas=["hs92"],
                include_tables=["hs92.country_year"],
                ignore_tables=["hs92.country_product_year_4"],
            )


class TestAsyncGetUsableTableNames:
    """Tests for the sync, in-memory get_usable_table_names method."""

    @pytest.mark.asyncio
    async def test_returns_sorted_schema_qualified(self):
        """get_usable_table_names() returns sorted schema.table names."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        tables = {
            "s1": ["b_table", "a_table"],
            "s2": ["c_table"],
        }
        mock_engine = _make_mock_async_engine(
            schemas={"s1", "s2"}, tables_per_schema=tables
        )
        db = await AsyncSQLDatabaseWithSchemas.create(mock_engine, schemas=["s1", "s2"])
        names = db.get_usable_table_names()
        assert names == ["s1.a_table", "s1.b_table", "s2.c_table"]

    @pytest.mark.asyncio
    async def test_respects_include_tables(self):
        """Only included tables are usable."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        tables = {
            "s1": ["a_table", "b_table"],
        }
        mock_engine = _make_mock_async_engine(schemas={"s1"}, tables_per_schema=tables)
        db = await AsyncSQLDatabaseWithSchemas.create(
            mock_engine, schemas=["s1"], include_tables=["s1.a_table"]
        )
        assert db.get_usable_table_names() == ["s1.a_table"]

    @pytest.mark.asyncio
    async def test_respects_ignore_tables(self):
        """Ignored tables are excluded."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        tables = {
            "s1": ["a_table", "b_table"],
        }
        mock_engine = _make_mock_async_engine(schemas={"s1"}, tables_per_schema=tables)
        db = await AsyncSQLDatabaseWithSchemas.create(
            mock_engine, schemas=["s1"], ignore_tables=["s1.b_table"]
        )
        names = db.get_usable_table_names()
        assert "s1.b_table" not in names
        assert "s1.a_table" in names


class TestAsyncDialect:
    """Tests for dialect property."""

    @pytest.mark.asyncio
    async def test_dialect_property(self):
        """dialect property returns engine dialect name."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        mock_engine = _make_mock_async_engine(dialect_name="postgresql")
        db = await AsyncSQLDatabaseWithSchemas.create(mock_engine, schemas=["hs92"])
        assert db.dialect == "postgresql"


class TestAsyncGetTableInfoErrors:
    """Tests for error paths in aget_table_info."""

    @pytest.mark.asyncio
    async def test_aget_table_info_raises_for_unknown_table(self):
        """Requesting info for a non-existent table raises ValueError."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        mock_engine = _make_mock_async_engine()
        db = await AsyncSQLDatabaseWithSchemas.create(mock_engine, schemas=["hs92"])
        with pytest.raises(ValueError, match="not found"):
            await db.aget_table_info(table_names=["nonexistent.table"])


class TestAsyncGetTableInfoNoThrow:
    """Tests for aget_table_info_no_throw."""

    @pytest.mark.asyncio
    async def test_returns_error_string_for_unknown_table(self):
        """Should return 'Error: ...' string instead of raising."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        mock_engine = _make_mock_async_engine()
        db = await AsyncSQLDatabaseWithSchemas.create(mock_engine, schemas=["hs92"])
        result = await db.aget_table_info_no_throw(table_names=["nonexistent.table"])
        assert result.startswith("Error:")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_returns_normal_info_on_success(self):
        """Should return normal table info when no error occurs."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        mock_engine = _make_mock_async_engine()
        db = await AsyncSQLDatabaseWithSchemas.create(mock_engine, schemas=["hs92"])
        # With mocked metadata (no real tables reflected), this returns empty string
        result = await db.aget_table_info_no_throw()
        assert not result.startswith("Error:")


class TestAsyncGetContext:
    """Tests for aget_context."""

    @pytest.mark.asyncio
    async def test_returns_expected_keys(self):
        """aget_context() must return dict with table_info, table_names, schemas."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        mock_engine = _make_mock_async_engine()
        db = await AsyncSQLDatabaseWithSchemas.create(mock_engine, schemas=["hs92"])
        ctx = await db.aget_context()
        assert "table_info" in ctx
        assert "table_names" in ctx
        assert "schemas" in ctx
        assert "hs92" in ctx["schemas"]

    @pytest.mark.asyncio
    async def test_table_names_are_comma_separated(self):
        """table_names value should be a comma-separated string."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        tables = {"s1": ["a_table", "b_table"]}
        mock_engine = _make_mock_async_engine(schemas={"s1"}, tables_per_schema=tables)
        db = await AsyncSQLDatabaseWithSchemas.create(mock_engine, schemas=["s1"])
        ctx = await db.aget_context()
        assert "s1.a_table" in ctx["table_names"]
        assert "s1.b_table" in ctx["table_names"]
        assert ", " in ctx["table_names"]


class TestAsyncExecuteOptions:
    """Tests for execution_options parameter in _aexecute."""

    @pytest.mark.asyncio
    async def test_aexecute_accepts_execution_options(self):
        """_aexecute should accept execution_options without error."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        mock_engine = _make_mock_async_engine()
        db = await AsyncSQLDatabaseWithSchemas.create(mock_engine, schemas=["hs92"])

        # Mock the begin() context manager for _aexecute
        mock_cursor = MagicMock()
        mock_cursor.returns_rows = True
        mock_cursor.keys.return_value = ["col1"]
        mock_cursor.fetchall.return_value = [("val1",)]

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_conn)

        result = await db._aexecute(
            "SELECT 1 AS col1",
            execution_options={"postgresql_readonly": True},
        )
        assert result == [{"col1": "val1"}]
        # Verify execution_options was passed through
        call_kwargs = mock_conn.execute.call_args
        assert call_kwargs.kwargs.get("execution_options") == {
            "postgresql_readonly": True
        }


# ===================================================================
# B. Integration Tests (real Docker DB) — @pytest.mark.db
# ===================================================================


@pytest.mark.db
class TestAsyncCreateReflection:
    """Test async factory with real Docker DB."""

    @pytest.mark.asyncio
    async def test_create_reflects_schemas_and_tables(self, async_db_engine):
        """Verify async reflection discovers schemas and tables."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=["hs92", "classification"]
        )
        assert "hs92" in db._schemas
        assert "classification" in db._schemas
        assert "hs92.country_country_product_year_4" in db._all_tables
        assert "classification.product_hs92" in db._all_tables
        assert db._metadata is not None
        assert len(db._metadata.tables) > 0


@pytest.mark.db
class TestAsyncMatchesSync:
    """Behavioral equivalence tests: async output must match sync oracle."""

    @pytest.mark.asyncio
    async def test_async_matches_sync_table_names(
        self, sync_db_engine, async_db_engine
    ):
        """CRITICAL: async and sync must discover identical table names."""
        from src.sql_multiple_schemas import (
            AsyncSQLDatabaseWithSchemas,
            SQLDatabaseWithSchemas,
        )

        schemas = ["hs92", "sitc", "classification"]
        sync_db = SQLDatabaseWithSchemas(engine=sync_db_engine, schemas=schemas)
        async_db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=schemas
        )

        assert async_db.get_usable_table_names() == sync_db.get_usable_table_names()

    @pytest.mark.asyncio
    async def test_async_matches_sync_table_info_ddl(
        self, sync_db_engine, async_db_engine
    ):
        """CRITICAL: DDL output must be identical between sync and async."""
        from src.sql_multiple_schemas import (
            AsyncSQLDatabaseWithSchemas,
            SQLDatabaseWithSchemas,
        )

        schemas = ["hs92", "classification"]
        table = "hs92.country_country_product_year_4"

        sync_db = SQLDatabaseWithSchemas(engine=sync_db_engine, schemas=schemas)
        async_db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=schemas
        )

        sync_ddl = sync_db.get_table_info(table_names=[table])
        async_ddl = await async_db.aget_table_info(table_names=[table])

        assert sync_ddl == async_ddl

    @pytest.mark.asyncio
    async def test_async_matches_sync_table_info_with_sample_rows(
        self, sync_db_engine, async_db_engine
    ):
        """DDL + sample rows output must match."""
        from src.sql_multiple_schemas import (
            AsyncSQLDatabaseWithSchemas,
            SQLDatabaseWithSchemas,
        )

        schemas = ["hs92", "classification"]
        table = "classification.location_country"

        sync_db = SQLDatabaseWithSchemas(
            engine=sync_db_engine, schemas=schemas, sample_rows_in_table_info=3
        )
        async_db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=schemas, sample_rows_in_table_info=3
        )

        sync_info = sync_db.get_table_info(
            table_names=[table], include_sample_rows=True
        )
        async_info = await async_db.aget_table_info(
            table_names=[table], include_sample_rows=True
        )

        assert sync_info == async_info


@pytest.mark.db
class TestAsyncNullTypeFiltering:
    """Test NullType column filtering in DDL output."""

    @pytest.mark.asyncio
    async def test_nulltype_columns_excluded_from_ddl(self, async_db_engine):
        """Columns with NullType should not appear as 'NULL' type in DDL.

        The Atlas DB has a 'vector' column (embedding) on some tables that
        SQLAlchemy doesn't recognize, producing NullType. Our filtering should
        strip these from DDL output.
        """
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=["hs92", "classification"]
        )
        ddl = await db.aget_table_info()
        # NullType columns render as "column_name NULL" in DDL — this pattern
        # should not appear for the embedding column specifically
        for line in ddl.split("\n"):
            stripped = line.strip()
            # A NullType column renders as e.g. "embedding NULL" or
            # "embedding NULL,"  — column name followed by NULL as the type
            if "embedding" in stripped.lower():
                # If the column appears, it should not have NULL as its type
                assert (
                    "NULL" not in stripped.split("embedding")[-1].split(",")[0]
                ), f"NullType column found in DDL: {stripped}"


@pytest.mark.db
class TestAsyncContextMatchesSync:
    """Test aget_context matches sync get_context."""

    @pytest.mark.asyncio
    async def test_aget_context_matches_sync(self, sync_db_engine, async_db_engine):
        """Async aget_context() should match sync get_context()."""
        from src.sql_multiple_schemas import (
            AsyncSQLDatabaseWithSchemas,
            SQLDatabaseWithSchemas,
        )

        schemas = ["hs92", "classification"]
        sync_db = SQLDatabaseWithSchemas(engine=sync_db_engine, schemas=schemas)
        async_db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=schemas
        )

        sync_ctx = sync_db.get_context()
        async_ctx = await async_db.aget_context()

        assert async_ctx["table_names"] == sync_ctx["table_names"]
        assert async_ctx["table_info"] == sync_ctx["table_info"]
        assert set(async_ctx["schemas"].split(", ")) == set(
            sync_ctx["schemas"].split(", ")
        )


@pytest.mark.db
class TestAsyncExecute:
    """Test async SQL execution."""

    @pytest.mark.asyncio
    async def test_aexecute_returns_correct_results(self, async_db_engine):
        """Execute a known query and verify correct results."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=["classification"]
        )
        result = await db._aexecute(
            "SELECT iso3_code, name_en FROM classification.location_country "
            "WHERE iso3_code = 'BOL'"
        )
        assert len(result) == 1
        assert result[0]["iso3_code"] == "BOL"

    @pytest.mark.asyncio
    async def test_aexecute_fetch_modes(self, async_db_engine):
        """Validate all fetch modes work."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=["classification"]
        )
        query = "SELECT DISTINCT iso3_code FROM classification.location_country LIMIT 5"

        # fetch="all"
        result_all = await db._aexecute(query, fetch="all")
        assert len(result_all) == 5

        # fetch="one"
        result_one = await db._aexecute(query, fetch="one")
        assert len(result_one) == 1

    @pytest.mark.asyncio
    async def test_aexecute_with_parameters(self, async_db_engine):
        """Validate parameter binding in async context."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=["classification"]
        )
        result = await db._aexecute(
            "SELECT iso3_code FROM classification.location_country "
            "WHERE iso3_code = :code",
            parameters={"code": "BOL"},
        )
        assert len(result) == 1
        assert result[0]["iso3_code"] == "BOL"


@pytest.mark.db
class TestAsyncMultiTableInfo:
    """Test multi-table info assembly."""

    @pytest.mark.asyncio
    async def test_aget_table_info_multiple_tables(
        self, sync_db_engine, async_db_engine
    ):
        """Multi-table info should contain both CREATE TABLE statements
        and match sync output."""
        from src.sql_multiple_schemas import (
            AsyncSQLDatabaseWithSchemas,
            SQLDatabaseWithSchemas,
        )

        schemas = ["hs92", "classification"]
        tables = ["hs92.country_year", "classification.location_country"]

        sync_db = SQLDatabaseWithSchemas(engine=sync_db_engine, schemas=schemas)
        async_db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=schemas
        )

        sync_info = sync_db.get_table_info(table_names=tables)
        async_info = await async_db.aget_table_info(table_names=tables)

        assert "CREATE TABLE" in async_info
        assert sync_info == async_info


@pytest.mark.db
class TestAsyncNoThreads:
    """Verify the async class uses run_sync, not asyncio.to_thread."""

    @pytest.mark.asyncio
    async def test_create_and_aget_table_info_no_to_thread(self, async_db_engine):
        """CRITICAL: asyncio.to_thread must never be called internally."""
        from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

        async def _fail_to_thread(*args, **kwargs):
            raise AssertionError(
                "asyncio.to_thread was called — should use run_sync instead"
            )

        with patch("asyncio.to_thread", side_effect=_fail_to_thread):
            db = await AsyncSQLDatabaseWithSchemas.create(
                async_db_engine, schemas=["hs92", "classification"]
            )
            await db.aget_table_info(
                table_names=["hs92.country_country_product_year_4"]
            )
            # If we got here, no to_thread was used — test passes


# ===================================================================
# C. Async Helper Tests (for sql_pipeline / sql_subagent)
# ===================================================================


@pytest.mark.db
class TestAsyncSubagentHelpers:
    """Test async versions of sql_subagent helper functions."""

    @pytest.mark.asyncio
    async def test_alist_tables_in_schema(self, sync_db_engine, async_db_engine):
        """Async schema listing should match sync version."""
        from src.sql_subagent import _alist_tables_in_schema, _list_tables_in_schema

        sync_result = _list_tables_in_schema("hs92", sync_db_engine)
        async_result = await _alist_tables_in_schema("hs92", async_db_engine)
        assert async_result == sync_result

    @pytest.mark.asyncio
    async def test_aget_sample_rows(self, async_db_engine):
        """Async sample rows should contain expected headers."""
        from src.sql_subagent import _aget_sample_rows

        result = await _aget_sample_rows(
            "classification.location_country", async_db_engine, limit=3
        )
        assert "Sample rows from" in result
        assert "3 rows" in result


@pytest.mark.db
class TestAsyncGetTableInfoForSchemas:
    """Test aget_table_info_for_schemas helper."""

    @pytest.mark.asyncio
    async def test_aget_table_info_for_schemas_matches_sync(
        self, sync_db_engine, async_db_engine
    ):
        """Async table info assembly must match sync version."""
        from src.sql_multiple_schemas import (
            AsyncSQLDatabaseWithSchemas,
            SQLDatabaseWithSchemas,
        )
        from src.sql_pipeline import (
            aget_table_info_for_schemas,
            get_table_info_for_schemas,
            load_table_descriptions,
        )

        table_desc = load_table_descriptions("src/schema/db_table_descriptions.json")
        schemas_list = ["hs92"]

        sync_db = SQLDatabaseWithSchemas(
            engine=sync_db_engine, schemas=["hs92", "classification"]
        )
        async_db = await AsyncSQLDatabaseWithSchemas.create(
            async_db_engine, schemas=["hs92", "classification"]
        )

        # Clear cache to force fresh computation
        from src.cache import table_info_cache

        table_info_cache.clear()

        sync_result = get_table_info_for_schemas(
            db=sync_db,
            table_descriptions=table_desc,
            classification_schemas=schemas_list,
        )

        table_info_cache.clear()

        async_result = await aget_table_info_for_schemas(
            db=async_db,
            table_descriptions=table_desc,
            classification_schemas=schemas_list,
        )

        assert async_result == sync_result


class TestAsyncGetTableInfoForSchemasCache:
    """Test caching behavior of aget_table_info_for_schemas."""

    @pytest.mark.asyncio
    async def test_aget_table_info_for_schemas_uses_cache(self):
        """Second call should hit cache — db.aget_table_info not called again."""
        from src.sql_pipeline import aget_table_info_for_schemas

        from src.cache import table_info_cache

        table_info_cache.clear()

        mock_db = AsyncMock()
        mock_db.aget_table_info = AsyncMock(return_value="CREATE TABLE mock ...")
        mock_db.get_usable_table_names = MagicMock(return_value=["hs92.country_year"])

        table_desc = {
            "hs92": [{"table_name": "country_year", "context_str": "Country-year data"}]
        }

        result1 = await aget_table_info_for_schemas(
            db=mock_db,
            table_descriptions=table_desc,
            classification_schemas=["hs92"],
        )

        # Record call count after first invocation
        calls_after_first = mock_db.aget_table_info.call_count
        assert calls_after_first > 0  # Should have been called at least once

        result2 = await aget_table_info_for_schemas(
            db=mock_db,
            table_descriptions=table_desc,
            classification_schemas=["hs92"],
        )

        # Second invocation should not have triggered any additional aget_table_info calls
        assert mock_db.aget_table_info.call_count == calls_after_first
        assert result1 == result2

        # Cleanup
        table_info_cache.clear()
