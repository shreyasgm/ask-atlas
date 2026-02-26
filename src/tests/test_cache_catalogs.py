"""Tests for GraphQL catalog caches — lazy-loaded, TTL-based, dual-indexed.

These verify the business-critical behaviors for the GraphQL pipeline's
``resolve_ids`` node:

- Catalogs must NOT be fetched at import/startup (lazy loading)
- Lookups by standard codes (ISO alpha-3, HS codes) return correct entries
- Product catalog supports dual indexing (by HS code AND by name)
- TTL expiry triggers re-fetch from source
- Concurrent first-access triggers only ONE fetch (stampede prevention)
- Registry clear_all() resets catalog caches (test isolation)
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.cache import (
    CatalogCache,
    country_catalog,
    product_catalog,
    registry,
    services_catalog,
    wire_catalog_fetchers,
)

# ---------------------------------------------------------------------------
# Sample catalog data (mirrors real GraphQL API responses)
# ---------------------------------------------------------------------------

SAMPLE_COUNTRIES = [
    {
        "countryId": 404,
        "nameEn": "Kenya",
        "nameShortEn": "Kenya",
        "iso3Code": "KEN",
        "incomelevelEnum": "LOW_INCOME",
    },
    {
        "countryId": 724,
        "nameEn": "Spain",
        "nameShortEn": "Spain",
        "iso3Code": "ESP",
        "incomelevelEnum": "HIGH_INCOME",
    },
    {
        "countryId": 840,
        "nameEn": "United States of America",
        "nameShortEn": "USA",
        "iso3Code": "USA",
        "incomelevelEnum": "HIGH_INCOME",
    },
]

SAMPLE_PRODUCTS = [
    {
        "productId": 726,
        "code": "0901",
        "nameEn": "Coffee, not roasted, not decaffeinated",
        "nameShortEn": "Coffee",
        "productType": "goods",
        "naturalResource": False,
        "greenProduct": False,
    },
    {
        "productId": 1234,
        "code": "8703",
        "nameEn": "Motor cars and vehicles for transport of persons",
        "nameShortEn": "Cars",
        "productType": "goods",
        "naturalResource": False,
        "greenProduct": False,
    },
    {
        "productId": 5678,
        "code": "5201",
        "nameEn": "Cotton, not carded or combed",
        "nameShortEn": "Cotton",
        "productType": "goods",
        "naturalResource": True,
        "greenProduct": False,
    },
]

SAMPLE_SERVICES = [
    {
        "productId": 100,
        "code": "S01",
        "nameEn": "Travel & tourism",
        "nameShortEn": "Travel & tourism",
    },
    {
        "productId": 101,
        "code": "S02",
        "nameEn": "Transport",
        "nameShortEn": "Transport",
    },
    {
        "productId": 102,
        "code": "S03",
        "nameEn": "ICT services",
        "nameShortEn": "ICT services",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_catalog(
    *,
    ttl: int = 3600,
    timer: object | None = None,
) -> CatalogCache:
    """Create a standalone CatalogCache with a country-like index setup."""
    kwargs: dict = {"ttl": ttl}
    if timer is not None:
        kwargs["timer"] = timer
    cache = CatalogCache("test_catalog", **kwargs)
    cache.add_index(
        "iso3",
        key_fn=lambda e: e.get("iso3Code", "").upper(),
        normalize_query=lambda q: q.strip().upper(),
    )
    cache.add_index(
        "name",
        key_fn=lambda e: (e.get("nameShortEn") or e.get("nameEn", "")).strip().lower(),
        normalize_query=lambda q: q.strip().lower(),
    )
    return cache


# ---------------------------------------------------------------------------
# Lazy fetching
# ---------------------------------------------------------------------------


class TestCatalogCacheLazyFetching:
    """Catalogs must not fetch data until the first lookup or get_all."""

    async def test_fetcher_not_called_at_construction(self):
        """Creating a CatalogCache and setting a fetcher must NOT trigger a fetch."""
        fetcher = AsyncMock(return_value=SAMPLE_COUNTRIES)
        cache = _make_catalog()
        cache.set_fetcher(fetcher)

        fetcher.assert_not_called()

    async def test_first_lookup_triggers_fetch(self):
        """The first lookup populates the cache from the fetcher."""
        fetcher = AsyncMock(return_value=SAMPLE_COUNTRIES)
        cache = _make_catalog()
        cache.set_fetcher(fetcher)

        result = await cache.lookup("iso3", "KEN")

        fetcher.assert_called_once()
        assert result is not None
        assert result["countryId"] == 404

    async def test_second_lookup_does_not_refetch(self):
        """Subsequent lookups use cached data — no additional fetch calls."""
        fetcher = AsyncMock(return_value=SAMPLE_COUNTRIES)
        cache = _make_catalog()
        cache.set_fetcher(fetcher)

        await cache.lookup("iso3", "KEN")
        await cache.lookup("iso3", "ESP")
        await cache.lookup("name", "usa")

        fetcher.assert_called_once()

    async def test_get_all_triggers_fetch(self):
        """get_all() also triggers lazy fetch if cache is empty."""
        fetcher = AsyncMock(return_value=SAMPLE_COUNTRIES)
        cache = _make_catalog()
        cache.set_fetcher(fetcher)

        entries = await cache.get_all()

        fetcher.assert_called_once()
        assert len(entries) == 3

    async def test_lookup_without_fetcher_or_data_raises(self):
        """Accessing an unpopulated cache without a fetcher is a programming error."""
        cache = _make_catalog()

        with pytest.raises(RuntimeError, match="no fetcher"):
            await cache.lookup("iso3", "KEN")

    async def test_direct_populate_bypasses_fetcher(self):
        """populate() loads data directly — no fetcher needed."""
        cache = _make_catalog()
        cache.populate(SAMPLE_COUNTRIES)

        result = await cache.lookup("iso3", "KEN")
        assert result is not None
        assert result["countryId"] == 404


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


class TestCatalogCacheTTLExpiry:
    """Cache must re-fetch from source after TTL expires."""

    async def test_refetches_after_ttl(self):
        """After TTL elapses, the next access triggers a fresh fetch."""
        call_count = 0
        current_time = 1000.0

        def mock_timer():
            return current_time

        async def mock_fetcher():
            nonlocal call_count
            call_count += 1
            return SAMPLE_COUNTRIES

        cache = _make_catalog(ttl=60, timer=mock_timer)
        cache.set_fetcher(mock_fetcher)

        # First access — triggers fetch
        await cache.lookup("iso3", "KEN")
        assert call_count == 1

        # Within TTL — no re-fetch
        current_time = 1050.0
        await cache.lookup("iso3", "KEN")
        assert call_count == 1

        # After TTL — triggers re-fetch
        current_time = 1061.0
        await cache.lookup("iso3", "KEN")
        assert call_count == 2

    async def test_stale_data_served_during_refetch_does_not_corrupt(self):
        """Re-fetch replaces data atomically — indexes are consistent."""
        current_time = 1000.0
        updated_countries = [
            {
                "countryId": 404,
                "nameEn": "Kenya",
                "nameShortEn": "Kenya",
                "iso3Code": "KEN",
                "incomelevelEnum": "LOWER_MIDDLE_INCOME",
            },
        ]

        def mock_timer():
            return current_time

        call_count = 0

        async def mock_fetcher():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return SAMPLE_COUNTRIES
            return updated_countries

        cache = _make_catalog(ttl=60, timer=mock_timer)
        cache.set_fetcher(mock_fetcher)

        # Initial fetch — 3 countries
        entries = await cache.get_all()
        assert len(entries) == 3

        # After TTL — re-fetch returns 1 country
        current_time = 1061.0
        entries = await cache.get_all()
        assert len(entries) == 1
        result = await cache.lookup("iso3", "KEN")
        assert result["incomelevelEnum"] == "LOWER_MIDDLE_INCOME"

        # ESP no longer exists after re-fetch
        assert await cache.lookup("iso3", "ESP") is None


# ---------------------------------------------------------------------------
# Stampede prevention
# ---------------------------------------------------------------------------


class TestCatalogCacheStampedePrevention:
    """Concurrent first-accesses must trigger only one fetch."""

    async def test_concurrent_lookups_trigger_single_fetch(self):
        """10 concurrent lookups on a cold cache should fire exactly 1 fetch."""
        call_count = 0

        async def slow_fetcher():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # simulate network latency
            return SAMPLE_COUNTRIES

        cache = _make_catalog()
        cache.set_fetcher(slow_fetcher)

        results = await asyncio.gather(
            *[cache.lookup("iso3", "KEN") for _ in range(10)]
        )

        assert call_count == 1
        for r in results:
            assert r is not None
            assert r["countryId"] == 404

    async def test_concurrent_get_all_triggers_single_fetch(self):
        """Multiple concurrent get_all calls also deduplicate."""
        call_count = 0

        async def slow_fetcher():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return SAMPLE_PRODUCTS

        cache = CatalogCache("test", ttl=3600)
        cache.set_fetcher(slow_fetcher)

        results = await asyncio.gather(*[cache.get_all() for _ in range(5)])

        assert call_count == 1
        for r in results:
            assert len(r) == 3


# ---------------------------------------------------------------------------
# Index lookups (exact match)
# ---------------------------------------------------------------------------


class TestCatalogCacheIndexLookup:
    """Exact-match lookups via named indexes."""

    async def test_lookup_normalizes_query(self):
        """ISO3 lookup should be case-insensitive."""
        cache = _make_catalog()
        cache.populate(SAMPLE_COUNTRIES)

        assert (await cache.lookup("iso3", "ken"))["countryId"] == 404
        assert (await cache.lookup("iso3", "KEN"))["countryId"] == 404
        assert (await cache.lookup("iso3", " Ken "))["countryId"] == 404

    async def test_lookup_miss_returns_none(self):
        """Non-existent key returns None, not an error."""
        cache = _make_catalog()
        cache.populate(SAMPLE_COUNTRIES)

        assert await cache.lookup("iso3", "XXX") is None

    async def test_lookup_nonexistent_index_raises(self):
        """Using an unregistered index name is a programming error."""
        cache = _make_catalog()
        cache.populate(SAMPLE_COUNTRIES)

        with pytest.raises(KeyError, match="no_such_index"):
            await cache.lookup("no_such_index", "KEN")

    async def test_name_index_lookup(self):
        """Name index normalizes to lowercase for exact match."""
        cache = _make_catalog()
        cache.populate(SAMPLE_COUNTRIES)

        result = await cache.lookup("name", "Kenya")
        assert result is not None
        assert result["iso3Code"] == "KEN"

        result = await cache.lookup("name", "usa")
        assert result is not None
        assert result["countryId"] == 840


# ---------------------------------------------------------------------------
# Text search (substring match)
# ---------------------------------------------------------------------------


class TestCatalogCacheSearch:
    """Case-insensitive substring search across all entries."""

    async def test_search_by_partial_name(self):
        """Substring of a name should match entries."""
        cache = _make_catalog()
        cache.populate(SAMPLE_COUNTRIES)

        results = await cache.search("nameShortEn", "ken")
        assert len(results) >= 1
        assert any(c["iso3Code"] == "KEN" for c in results)

    async def test_search_is_case_insensitive(self):
        """Search should match regardless of case."""
        cache = _make_catalog()
        cache.populate(SAMPLE_COUNTRIES)

        results_lower = await cache.search("nameShortEn", "spain")
        results_upper = await cache.search("nameShortEn", "SPAIN")
        assert len(results_lower) == len(results_upper) == 1

    async def test_search_respects_limit(self):
        """Search returns at most `limit` entries."""
        cache = _make_catalog()
        cache.populate(SAMPLE_COUNTRIES)

        results = await cache.search("nameShortEn", "", limit=2)  # "" matches all
        assert len(results) == 2

    async def test_search_no_match_returns_empty(self):
        cache = _make_catalog()
        cache.populate(SAMPLE_COUNTRIES)

        results = await cache.search("nameShortEn", "zzzzz")
        assert results == []


# ---------------------------------------------------------------------------
# Concrete catalog instances: country
# ---------------------------------------------------------------------------


class TestCountryCatalog:
    """The module-level country_catalog has correct indexes for resolve_ids."""

    def setup_method(self):
        country_catalog.populate(SAMPLE_COUNTRIES)

    async def test_lookup_by_iso3(self):
        """resolve_ids verifies LLM's ISO alpha-3 guesses via this index."""
        result = await country_catalog.lookup("iso3", "KEN")
        assert result is not None
        assert result["countryId"] == 404

    async def test_lookup_by_name(self):
        """resolve_ids searches country names via this index."""
        result = await country_catalog.lookup("name", "Spain")
        assert result is not None
        assert result["iso3Code"] == "ESP"

    async def test_lookup_by_id(self):
        """Reverse lookup from API response countryId to entry."""
        result = await country_catalog.lookup("id", "840")
        assert result is not None
        assert result["nameShortEn"] == "USA"

    async def test_search_countries_by_name(self):
        """Text search for partial country names."""
        results = await country_catalog.search("nameEn", "United")
        assert len(results) >= 1
        assert any(c["iso3Code"] == "USA" for c in results)


# ---------------------------------------------------------------------------
# Concrete catalog instances: product (dual indexing)
# ---------------------------------------------------------------------------


class TestProductCatalog:
    """Product catalog is dual-indexed by HS code AND by name."""

    def setup_method(self):
        product_catalog.populate(SAMPLE_PRODUCTS)

    async def test_lookup_by_hs_code(self):
        """resolve_ids verifies LLM's HS code guesses via this index."""
        result = await product_catalog.lookup("code", "0901")
        assert result is not None
        assert result["nameShortEn"] == "Coffee"
        assert result["productId"] == 726

    async def test_lookup_by_name(self):
        """resolve_ids looks up products by name when LLM has no code guess."""
        result = await product_catalog.lookup("name", "coffee")
        assert result is not None
        assert result["code"] == "0901"

    async def test_lookup_by_product_id(self):
        """Reverse lookup from API response productId to entry."""
        result = await product_catalog.lookup("id", "726")
        assert result is not None
        assert result["code"] == "0901"

    async def test_search_products_by_name(self):
        """Text search finds products by partial name."""
        results = await product_catalog.search("nameEn", "Motor cars")
        assert len(results) >= 1
        assert any(p["code"] == "8703" for p in results)

    async def test_dual_index_both_paths_resolve_same_entry(self):
        """Looking up by code or by name should find the same product."""
        by_code = await product_catalog.lookup("code", "5201")
        by_name = await product_catalog.lookup("name", "cotton")
        assert by_code == by_name
        assert by_code["productId"] == 5678


# ---------------------------------------------------------------------------
# Concrete catalog instances: services
# ---------------------------------------------------------------------------


class TestServicesCatalog:
    """Services catalog caches service category names and IDs."""

    def setup_method(self):
        services_catalog.populate(SAMPLE_SERVICES)

    async def test_lookup_service_by_name(self):
        """resolve_ids matches LLM's service category guess against catalog."""
        result = await services_catalog.lookup("name", "Travel & tourism")
        assert result is not None
        assert result["productId"] == 100

    async def test_get_all_for_prompt_injection(self):
        """Full catalog injected into extraction prompt for services questions."""
        all_services = await services_catalog.get_all()
        assert len(all_services) == 3
        names = [s["nameShortEn"] for s in all_services]
        assert "Transport" in names
        assert "ICT services" in names


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestCatalogRegistryIntegration:
    """Catalog caches integrate with the CacheRegistry for observability and test isolation."""

    async def test_clear_all_resets_catalog_caches(self):
        """conftest._clear_caches calls registry.clear_all() — catalogs must be reset."""
        country_catalog.populate(SAMPLE_COUNTRIES)
        assert await country_catalog.lookup("iso3", "KEN") is not None

        registry.clear_all()

        # After clear, the catalog should be empty (unpopulated)
        assert not country_catalog.is_populated

    async def test_catalog_stats_in_registry(self):
        """Registry stats include catalog cache info."""
        country_catalog.populate(SAMPLE_COUNTRIES)

        stats = registry.stats()
        assert "country_catalog" in stats
        assert stats["country_catalog"]["size"] == 3
        assert stats["country_catalog"]["populated"] is True

    async def test_unpopulated_catalog_stats(self):
        """Unpopulated catalog reports size=0 and populated=False."""
        stats = registry.stats()
        assert "country_catalog" in stats
        assert stats["country_catalog"]["size"] == 0
        assert stats["country_catalog"]["populated"] is False


# ---------------------------------------------------------------------------
# wire_catalog_fetchers
# ---------------------------------------------------------------------------


class TestWireCatalogFetchersSetsAll:
    """wire_catalog_fetchers must wire fetchers to all three module-level catalogs."""

    async def test_wire_catalog_fetchers_sets_fetchers(self):
        """After calling wire_catalog_fetchers, all three catalogs have a non-None _fetcher."""
        mock_client = AsyncMock()
        mock_client.execute = AsyncMock(return_value={})

        # Ensure fetchers are initially None (clean state)
        country_catalog._fetcher = None
        product_catalog._fetcher = None
        services_catalog._fetcher = None

        try:
            wire_catalog_fetchers(mock_client)

            assert country_catalog._fetcher is not None
            assert product_catalog._fetcher is not None
            assert services_catalog._fetcher is not None
        finally:
            # Reset fetchers to None to avoid leaking state to other tests
            country_catalog._fetcher = None
            product_catalog._fetcher = None
            services_catalog._fetcher = None

    async def test_wired_fetcher_calls_client_execute(self):
        """The wired fetchers delegate to AtlasGraphQLClient.execute with correct queries."""
        mock_client = AsyncMock()
        mock_client.execute = AsyncMock(
            return_value={"locationCountry": SAMPLE_COUNTRIES}
        )

        country_catalog._fetcher = None
        country_catalog.clear()

        try:
            wire_catalog_fetchers(mock_client)

            # Trigger the fetcher by accessing data
            result = await country_catalog.lookup("iso3", "KEN")
            assert result is not None
            assert result["countryId"] == 404

            # Verify execute was called with a query containing locationCountry
            mock_client.execute.assert_called()
            call_args = mock_client.execute.call_args[0][0]
            assert "locationCountry" in call_args
        finally:
            country_catalog._fetcher = None
            country_catalog.clear()


@pytest.mark.integration
class TestWireCatalogFetchersIntegration:
    """Integration test: wire_catalog_fetchers populates real data from the Atlas API."""

    async def test_wire_catalog_fetchers_populates_real_data(self):
        """Real AtlasGraphQLClient populates country catalog with Kenya."""
        from src.graphql_client import AtlasGraphQLClient

        client = AtlasGraphQLClient(
            base_url="https://atlas.hks.harvard.edu/api/graphql",
            timeout=15.0,
        )

        # Clear state before test
        country_catalog._fetcher = None
        country_catalog.clear()

        try:
            wire_catalog_fetchers(client)

            result = await country_catalog.lookup("iso3", "KEN")
            assert result is not None
            assert "countryId" in result
            assert "Kenya" in (result.get("nameShortEn") or result.get("nameEn", ""))
        finally:
            # Reset catalog state to avoid leaking to other tests
            country_catalog._fetcher = None
            country_catalog.clear()
