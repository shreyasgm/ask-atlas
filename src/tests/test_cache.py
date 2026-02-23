"""Tests for src/cache.py — cache behavior that matters for correctness."""

import pytest

from src.cache import (
    CacheRegistry,
    product_details_key,
    table_info_key,
    text_search_key,
)

# --- Key normalization: equivalent queries must share cache entries ---


class TestKeyNormalization:
    """These test the invariants that matter for cache hit rates."""

    def test_product_codes_in_different_order_produce_same_key(self):
        """Users may mention "5202, 5201" or "5201, 5202" — both should hit cache."""
        k1 = product_details_key(["5201", "5202"], "hs92")
        k2 = product_details_key(["5202", "5201"], "hs92")
        assert k1 == k2

    def test_text_search_is_case_insensitive(self):
        """LLM might output 'Cotton' one time and 'cotton' the next."""
        k1 = text_search_key("Cotton", "hs92")
        k2 = text_search_key("cotton", "hs92")
        assert k1 == k2

    def test_text_search_ignores_leading_trailing_whitespace(self):
        """LLM outputs sometimes have trailing spaces."""
        k1 = text_search_key("  coffee beans  ", "hs92")
        k2 = text_search_key("coffee beans", "hs92")
        assert k1 == k2

    def test_different_schemas_produce_different_keys(self):
        """'cotton' in hs92 vs hs12 are different product tables."""
        k1 = text_search_key("cotton", "hs92")
        k2 = text_search_key("cotton", "hs12")
        assert k1 != k2

    def test_table_info_schema_order_does_not_matter(self):
        """Pipeline may list schemas in any order."""
        k1 = table_info_key(["hs92", "services_bilateral"])
        k2 = table_info_key(["services_bilateral", "hs92"])
        assert k1 == k2


# --- Registry: only the behaviors needed for observability ---


class TestCacheStats:
    """Stats are the observability mechanism — verify they reflect real usage."""

    def test_stats_reflect_actual_cache_usage(self):
        """After puts and gets, stats should report correct hit rate."""
        r = CacheRegistry()
        cache = r.create("test", maxsize=10, ttl=60)
        cache["key1"] = "value1"
        r.record_miss("test")  # first lookup was a miss
        r.record_hit("test")  # second lookup was a hit
        stats = r.stats()["test"]
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1
        assert stats["hit_rate"] == pytest.approx(0.5)

    def test_clear_all_resets_caches_and_counters(self):
        """Tests must be isolated — clear_all is the mechanism for that."""
        r = CacheRegistry()
        c = r.create("x", maxsize=10, ttl=60)
        c["a"] = 1
        r.record_hit("x")
        r.clear_all()
        assert len(c) == 0
        assert r.stats()["x"]["hits"] == 0

    def test_clear_single_cache(self):
        """Clearing one cache should not affect others."""
        r = CacheRegistry()
        c1 = r.create("a", maxsize=10, ttl=60)
        c2 = r.create("b", maxsize=10, ttl=60)
        c1["k"] = 1
        c2["k"] = 2
        r.record_hit("a")
        r.record_hit("b")
        r.clear("a")
        assert len(c1) == 0
        assert r.stats()["a"]["hits"] == 0
        assert len(c2) == 1
        assert r.stats()["b"]["hits"] == 1

    def test_hit_rate_zero_when_no_accesses(self):
        """Avoid division by zero — hit rate should be 0.0 with no accesses."""
        r = CacheRegistry()
        r.create("empty", maxsize=10, ttl=60)
        assert r.stats()["empty"]["hit_rate"] == 0.0
