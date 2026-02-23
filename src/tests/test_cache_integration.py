"""Integration tests: caching actually prevents redundant work.

These tests verify that the cache wrappers in src/cache.py and the
call sites in product_and_schema_lookup.py / generate_query.py actually
prevent duplicate DB queries for identical lookups.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock


from src.cache import (
    cached_product_details,
    cached_text_search,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_ROWS_PRODUCT = [
    ("5201", "Cotton, not carded", "p5201", "4digit"),
]

_SAMPLE_ROWS_TEXT = [
    ("Cotton, not carded", "5201", "p5201", "4digit", 0.95),
]


class _AsyncContextManager:
    """Minimal async context manager wrapping a mock connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return False


def _make_async_engine(rows):
    """Build a mock AsyncEngine whose .connect() returns canned rows."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()
    mock_engine.connect.return_value = _AsyncContextManager(mock_conn)
    return mock_engine


# ---------------------------------------------------------------------------
# Product details caching
# ---------------------------------------------------------------------------


class TestProductDetailsCaching:
    """cached_product_details should cache DB results."""

    async def test_second_call_same_codes_skips_db(self):
        """Identical lookups should not hit the DB twice."""
        engine = _make_async_engine(_SAMPLE_ROWS_PRODUCT)

        r1 = await cached_product_details(("5201",), "hs92", engine)
        r2 = await cached_product_details(("5201",), "hs92", engine)

        assert r1 == r2
        # engine.connect() called only for the first (miss) call
        assert engine.connect.call_count == 1

    async def test_different_codes_still_hit_db(self):
        """Different product codes must produce separate DB queries."""
        engine = _make_async_engine(_SAMPLE_ROWS_PRODUCT)

        await cached_product_details(("5201",), "hs92", engine)
        await cached_product_details(("1001",), "hs92", engine)

        assert engine.connect.call_count == 2

    async def test_code_order_does_not_cause_extra_db_call(self):
        """("5201", "5202") and ("5202", "5201") are the same lookup."""
        engine = _make_async_engine(_SAMPLE_ROWS_PRODUCT)

        await cached_product_details(("5201", "5202"), "hs92", engine)
        await cached_product_details(("5202", "5201"), "hs92", engine)

        assert engine.connect.call_count == 1

    async def test_different_schema_causes_separate_db_call(self):
        """Same codes in hs92 vs hs12 are different products tables."""
        engine = _make_async_engine(_SAMPLE_ROWS_PRODUCT)

        await cached_product_details(("5201",), "hs92", engine)
        await cached_product_details(("5201",), "hs12", engine)

        assert engine.connect.call_count == 2


# ---------------------------------------------------------------------------
# Text search caching
# ---------------------------------------------------------------------------


class TestTextSearchCaching:
    """cached_text_search should cache search results."""

    async def test_case_variant_searches_share_cache(self):
        """'Cotton' and 'cotton' should produce only one DB query."""
        engine = _make_async_engine(_SAMPLE_ROWS_TEXT)

        await cached_text_search("Cotton", "hs92", engine)
        await cached_text_search("cotton", "hs92", engine)

        assert engine.connect.call_count == 1

    async def test_whitespace_variant_searches_share_cache(self):
        """' wheat ' and 'wheat' should share a cache entry."""
        engine = _make_async_engine(_SAMPLE_ROWS_TEXT)

        await cached_text_search(" wheat ", "hs92", engine)
        await cached_text_search("wheat", "hs92", engine)

        assert engine.connect.call_count == 1


# ---------------------------------------------------------------------------
# Table info caching
# ---------------------------------------------------------------------------


class TestTableInfoCaching:
    """get_table_info_for_schemas should cache DDL strings."""

    def test_second_call_same_schemas_skips_db_reflection(self):
        """Table DDL doesn't change — second call should be cached."""
        from src.generate_query import get_table_info_for_schemas

        mock_db = MagicMock()
        mock_db.get_table_info.return_value = "CREATE TABLE ..."
        table_descriptions = {
            "hs92": [
                {"table_name": "country_year", "context_str": "Trade by country-year"}
            ],
        }

        r1 = get_table_info_for_schemas(mock_db, table_descriptions, ["hs92"])
        call_count_after_first = mock_db.get_table_info.call_count
        r2 = get_table_info_for_schemas(mock_db, table_descriptions, ["hs92"])

        assert r1 == r2
        # No additional get_table_info calls on the second invocation
        assert mock_db.get_table_info.call_count == call_count_after_first

    def test_schema_order_does_not_cause_extra_reflection(self):
        """["hs92", "sitc"] and ["sitc", "hs92"] should share cache."""
        from src.generate_query import get_table_info_for_schemas

        mock_db = MagicMock()
        mock_db.get_table_info.return_value = "CREATE TABLE ..."
        table_descriptions = {
            "hs92": [{"table_name": "country_year", "context_str": "desc1"}],
            "sitc": [{"table_name": "country_year", "context_str": "desc2"}],
        }

        get_table_info_for_schemas(mock_db, table_descriptions, ["hs92", "sitc"])
        call_count_after_first = mock_db.get_table_info.call_count
        get_table_info_for_schemas(mock_db, table_descriptions, ["sitc", "hs92"])

        assert mock_db.get_table_info.call_count == call_count_after_first


# ---------------------------------------------------------------------------
# Stampede prevention
# ---------------------------------------------------------------------------


class TestStampedePrevention:
    """With 20 concurrent users, identical lookups must not all hit the DB."""

    async def test_concurrent_identical_lookups_produce_single_db_call(self):
        """10 concurrent calls for the same product should fire only 1 DB query."""
        engine = _make_async_engine(_SAMPLE_ROWS_PRODUCT)

        results = await asyncio.gather(
            *[cached_product_details(("5201",), "hs92", engine) for _ in range(10)]
        )

        # All results should be identical
        for r in results:
            assert r == results[0]
        # Only one DB call should have been made
        assert engine.connect.call_count == 1
