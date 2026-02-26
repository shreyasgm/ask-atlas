"""Tests for the Atlas link generation module.

Covers:
- AtlasLink dataclass construction
- Product classification URL formatting (all 4 classifications)
- Country page URL builders (all subpage types)
- Explore page URL builders (all 7 viz types + variants)
- Frontier country fallback behavior
- generate_atlas_links dispatch for all query types
- No links for unmapped query types
- Resolution notes propagation
- Edge cases (missing params, defaults)
- Multiple links per query type
- ProductClassificationRegistry lookup by code and name
"""

import pytest

from src.atlas_links import (
    ATLAS_BASE_URL,
    DEFAULT_PRODUCT_LEVEL,
    DEFAULT_START_YEAR,
    DEFAULT_YEAR,
    FRONTIER_COUNTRY_IDS,
    PRODUCT_CLASSIFICATION_PREFIXES,
    AtlasLink,
    ProductClassificationRegistry,
    ProductRecord,
    country_page_url,
    explore_feasibility_table_url,
    explore_feasibility_url,
    explore_geomap_url,
    explore_marketshare_url,
    explore_overtime_url,
    explore_productspace_url,
    explore_treemap_url,
    generate_atlas_links,
    is_frontier_country,
)

# ---------------------------------------------------------------------------
# AtlasLink dataclass
# ---------------------------------------------------------------------------


class TestAtlasLink:
    def test_basic_construction(self):
        link = AtlasLink(
            url="https://atlas.hks.harvard.edu/countries/404",
            label="Kenya - Country Profile",
            link_type="country_page",
        )
        assert link.url == "https://atlas.hks.harvard.edu/countries/404"
        assert link.label == "Kenya - Country Profile"
        assert link.link_type == "country_page"
        assert link.resolution_notes == []

    def test_with_resolution_notes(self):
        notes = ["Country 'Turkey' resolved to Turkiye (792)"]
        link = AtlasLink(
            url="https://atlas.hks.harvard.edu/countries/792",
            label="Turkiye - Country Profile",
            link_type="country_page",
            resolution_notes=notes,
        )
        assert link.resolution_notes == notes

    def test_explore_page_type(self):
        link = AtlasLink(
            url="https://atlas.hks.harvard.edu/explore/treemap?year=2024",
            label="Treemap",
            link_type="explore_page",
        )
        assert link.link_type == "explore_page"

    def test_frozen_dataclass(self):
        link = AtlasLink(
            url="https://example.com",
            label="Test",
            link_type="country_page",
        )
        with pytest.raises(AttributeError):
            link.url = "https://other.com"


# ---------------------------------------------------------------------------
# Product classification URL formatting
# ---------------------------------------------------------------------------


class TestProductClassificationFormatting:
    """Test product parameter formatting for all 4 classification systems."""

    def test_hs92_product_param(self):
        url = explore_treemap_url(
            year=2024, product_classification="HS92", product_id=726
        )
        assert "product=product-HS92-726" in url

    def test_hs12_product_param(self):
        url = explore_treemap_url(
            year=2024, product_classification="HS12", product_id=725
        )
        assert "product=product-HS12-725" in url

    def test_hs22_product_param(self):
        url = explore_treemap_url(
            year=2024, product_classification="HS22", product_id=727
        )
        assert "product=product-HS22-727" in url

    def test_sitc_product_param(self):
        url = explore_treemap_url(
            year=2024, product_classification="SITC", product_id=726
        )
        assert "product=product-SITC-726" in url

    def test_case_insensitive_classification(self):
        url = explore_treemap_url(
            year=2024, product_classification="hs92", product_id=726
        )
        assert "product=product-HS92-726" in url

    def test_invalid_classification_raises(self):
        with pytest.raises(ValueError, match="Unknown product classification"):
            explore_treemap_url(
                year=2024, product_classification="INVALID", product_id=1
            )

    def test_all_four_classifications_registered(self):
        assert set(PRODUCT_CLASSIFICATION_PREFIXES) == {"HS92", "HS12", "HS22", "SITC"}


# ---------------------------------------------------------------------------
# Country page URL builders
# ---------------------------------------------------------------------------


class TestCountryPageURLs:
    """Test all 12 country page subpage URL patterns."""

    def test_country_profile(self):
        assert country_page_url(404) == f"{ATLAS_BASE_URL}/countries/404"

    def test_export_basket(self):
        url = country_page_url(404, "export-basket")
        assert url == f"{ATLAS_BASE_URL}/countries/404/export-basket"

    def test_export_complexity(self):
        url = country_page_url(404, "export-complexity")
        assert url == f"{ATLAS_BASE_URL}/countries/404/export-complexity"

    def test_growth_dynamics(self):
        url = country_page_url(404, "growth-dynamics")
        assert url == f"{ATLAS_BASE_URL}/countries/404/growth-dynamics"

    def test_market_share(self):
        url = country_page_url(404, "market-share")
        assert url == f"{ATLAS_BASE_URL}/countries/404/market-share"

    def test_new_products(self):
        url = country_page_url(404, "new-products")
        assert url == f"{ATLAS_BASE_URL}/countries/404/new-products"

    def test_product_space(self):
        url = country_page_url(404, "product-space")
        assert url == f"{ATLAS_BASE_URL}/countries/404/product-space"

    def test_paths(self):
        url = country_page_url(404, "paths")
        assert url == f"{ATLAS_BASE_URL}/countries/404/paths"

    def test_strategic_approach(self):
        url = country_page_url(404, "strategic-approach")
        assert url == f"{ATLAS_BASE_URL}/countries/404/strategic-approach"

    def test_growth_opportunities(self):
        url = country_page_url(404, "growth-opportunities")
        assert url == f"{ATLAS_BASE_URL}/countries/404/growth-opportunities"

    def test_product_table(self):
        url = country_page_url(404, "product-table")
        assert url == f"{ATLAS_BASE_URL}/countries/404/product-table"

    def test_summary(self):
        url = country_page_url(404, "summary")
        assert url == f"{ATLAS_BASE_URL}/countries/404/summary"

    def test_different_country_ids(self):
        assert "/countries/840" in country_page_url(840)
        assert "/countries/76" in country_page_url(76)
        assert "/countries/276" in country_page_url(276)


# ---------------------------------------------------------------------------
# Explore page URL builders
# ---------------------------------------------------------------------------


class TestExploreTreemapURL:
    def test_basic_treemap(self):
        url = explore_treemap_url(year=2024, country_id=404)
        assert url == f"{ATLAS_BASE_URL}/explore/treemap?year=2024&exporter=country-404"

    def test_treemap_with_partner(self):
        url = explore_treemap_url(year=2024, country_id=404, partner_id=840)
        assert "exporter=country-404" in url
        assert "importer=country-840" in url

    def test_treemap_with_product(self):
        url = explore_treemap_url(
            year=2024, product_classification="HS92", product_id=726
        )
        assert "product=product-HS92-726" in url

    def test_treemap_markets_view(self):
        url = explore_treemap_url(year=2024, country_id=404, view="markets")
        assert "view=markets" in url

    def test_treemap_with_group(self):
        url = explore_treemap_url(year=2024, group_id=5)
        assert "exporter=group-5" in url

    def test_group_takes_precedence_over_country(self):
        url = explore_treemap_url(year=2024, country_id=404, group_id=5)
        assert "exporter=group-5" in url
        assert "country-404" not in url


class TestExploreGeomapURL:
    def test_basic_geomap(self):
        url = explore_geomap_url(year=2024, country_id=404)
        assert url == f"{ATLAS_BASE_URL}/explore/geomap?year=2024&exporter=country-404"


class TestExploreOvertimeURL:
    def test_basic_overtime(self):
        url = explore_overtime_url(
            year=2024, start_year=1995, end_year=2024, country_id=404
        )
        assert "startYear=1995" in url
        assert "endYear=2024" in url
        assert "exporter=country-404" in url
        assert url.startswith(f"{ATLAS_BASE_URL}/explore/overtime?")

    def test_overtime_markets_view(self):
        url = explore_overtime_url(
            year=2024, start_year=2000, end_year=2024, country_id=404, view="markets"
        )
        assert "view=markets" in url


class TestExploreMarketshareURL:
    def test_basic_marketshare(self):
        url = explore_marketshare_url(
            year=2024, start_year=1995, end_year=2024, country_id=404
        )
        assert url.startswith(f"{ATLAS_BASE_URL}/explore/marketshare?")
        assert "exporter=country-404" in url
        assert "startYear=1995" in url


class TestExploreProductspaceURL:
    def test_basic_productspace(self):
        url = explore_productspace_url(year=2024, country_id=404)
        assert (
            url
            == f"{ATLAS_BASE_URL}/explore/productspace?year=2024&exporter=country-404"
        )


class TestExploreFeasibilityURL:
    def test_basic_feasibility(self):
        url = explore_feasibility_url(year=2024, country_id=404)
        assert (
            url
            == f"{ATLAS_BASE_URL}/explore/feasibility?year=2024&exporter=country-404"
        )

    def test_feasibility_table(self):
        url = explore_feasibility_table_url(year=2024, country_id=404, product_level=4)
        assert "productLevel=4" in url
        assert url.startswith(f"{ATLAS_BASE_URL}/explore/feasibility/table?")

    def test_feasibility_table_different_levels(self):
        for level in (2, 4, 6):
            url = explore_feasibility_table_url(
                year=2024, country_id=404, product_level=level
            )
            assert f"productLevel={level}" in url

    def test_feasibility_table_default_level(self):
        url = explore_feasibility_table_url(year=2024, country_id=404)
        assert f"productLevel={DEFAULT_PRODUCT_LEVEL}" in url


# ---------------------------------------------------------------------------
# Frontier country logic
# ---------------------------------------------------------------------------


class TestFrontierCountries:
    def test_usa_is_frontier(self):
        assert is_frontier_country(840)

    def test_germany_is_frontier(self):
        assert is_frontier_country(276)

    def test_japan_is_frontier(self):
        assert is_frontier_country(392)

    def test_kenya_is_not_frontier(self):
        assert not is_frontier_country(404)

    def test_brazil_is_not_frontier(self):
        assert not is_frontier_country(76)

    def test_ethiopia_is_not_frontier(self):
        assert not is_frontier_country(231)

    def test_frontier_set_is_nonempty(self):
        assert len(FRONTIER_COUNTRY_IDS) > 0


# ---------------------------------------------------------------------------
# generate_atlas_links dispatch — Country page query types
# ---------------------------------------------------------------------------


class TestGenerateLinksCountryProfile:
    def test_produces_country_page_link(self):
        links = generate_atlas_links(
            "country_profile",
            {"country_id": 404, "country_name": "Kenya"},
        )
        assert len(links) == 1
        assert links[0].link_type == "country_page"
        assert links[0].url == f"{ATLAS_BASE_URL}/countries/404"
        assert "Kenya" in links[0].label

    def test_resolution_notes_propagated(self):
        notes = ["Year not specified in question - defaulted to 2024"]
        links = generate_atlas_links(
            "country_profile",
            {"country_id": 404, "country_name": "Kenya", "resolution_notes": notes},
        )
        assert links[0].resolution_notes == notes


class TestGenerateLinksCountryLookback:
    def test_produces_primary_and_supplementary(self):
        links = generate_atlas_links(
            "country_lookback",
            {"country_id": 404, "country_name": "Kenya", "year_max": 2024},
        )
        assert len(links) == 2
        # Primary: growth-dynamics country page
        assert links[0].link_type == "country_page"
        assert "growth-dynamics" in links[0].url
        # Supplementary: overtime explore page
        assert links[1].link_type == "explore_page"
        assert "/explore/overtime" in links[1].url


class TestGenerateLinksNewProducts:
    def test_produces_new_products_link(self):
        links = generate_atlas_links(
            "new_products",
            {"country_id": 404, "country_name": "Kenya"},
        )
        assert len(links) == 1
        assert "new-products" in links[0].url
        assert links[0].link_type == "country_page"


class TestGenerateLinksCountryYear:
    def test_produces_country_profile_link(self):
        links = generate_atlas_links(
            "country_year",
            {"country_id": 404, "country_name": "Kenya", "year": 2020},
        )
        assert len(links) == 1
        assert links[0].url == f"{ATLAS_BASE_URL}/countries/404"


# ---------------------------------------------------------------------------
# generate_atlas_links dispatch — Explore page query types
# ---------------------------------------------------------------------------


class TestGenerateLinksTreemapProducts:
    def test_produces_treemap_and_country_page(self):
        links = generate_atlas_links(
            "treemap_products",
            {"country_id": 404, "country_name": "Kenya", "year": 2024},
        )
        assert len(links) == 2
        # Primary: explore treemap
        assert links[0].link_type == "explore_page"
        assert "/explore/treemap" in links[0].url
        assert "year=2024" in links[0].url
        assert "exporter=country-404" in links[0].url
        # Supplementary: country export basket
        assert links[1].link_type == "country_page"
        assert "export-basket" in links[1].url


class TestGenerateLinksTreemapPartners:
    def test_produces_markets_view_link(self):
        links = generate_atlas_links(
            "treemap_partners",
            {"country_id": 404, "country_name": "Kenya", "year": 2024},
        )
        assert len(links) == 1
        assert "view=markets" in links[0].url


class TestGenerateLinksTreemapBilateral:
    def test_produces_bilateral_link(self):
        links = generate_atlas_links(
            "treemap_bilateral",
            {
                "country_id": 404,
                "country_name": "Kenya",
                "partner_id": 840,
                "partner_name": "USA",
                "year": 2024,
            },
        )
        assert len(links) == 1
        assert "exporter=country-404" in links[0].url
        assert "importer=country-840" in links[0].url


class TestGenerateLinksProductInfo:
    def test_produces_product_treemap_link(self):
        links = generate_atlas_links(
            "product_info",
            {
                "product_id": 726,
                "product_classification": "HS92",
                "product_name": "Coffee",
                "year": 2024,
            },
        )
        assert len(links) == 1
        assert "product=product-HS92-726" in links[0].url
        assert "Coffee" in links[0].label


class TestGenerateLinksExploreBilateral:
    def test_produces_bilateral_link(self):
        links = generate_atlas_links(
            "explore_bilateral",
            {
                "country_id": 76,
                "country_name": "Brazil",
                "partner_id": 156,
                "partner_name": "China",
                "year": 2023,
            },
        )
        assert len(links) == 1
        assert "exporter=country-76" in links[0].url
        assert "importer=country-156" in links[0].url


class TestGenerateLinksExploreGroup:
    def test_produces_group_exporter_link(self):
        links = generate_atlas_links(
            "explore_group",
            {"group_id": 5, "group_name": "BRICS", "year": 2024},
        )
        assert len(links) == 1
        assert "exporter=group-5" in links[0].url

    def test_no_link_when_group_id_missing(self):
        links = generate_atlas_links(
            "explore_group",
            {"year": 2024},
        )
        assert links == []


class TestGenerateLinksOvertimeProducts:
    def test_produces_overtime_and_treemap_links(self):
        links = generate_atlas_links(
            "overtime_products",
            {
                "country_id": 404,
                "country_name": "Kenya",
                "year_min": 2000,
                "year_max": 2024,
            },
        )
        assert len(links) == 2
        # Primary: overtime
        assert "/explore/overtime" in links[0].url
        assert "startYear=2000" in links[0].url
        assert "endYear=2024" in links[0].url
        # Supplementary: treemap snapshot
        assert "/explore/treemap" in links[1].url


class TestGenerateLinksOvertimePartners:
    def test_produces_overtime_markets_link(self):
        links = generate_atlas_links(
            "overtime_partners",
            {
                "country_id": 404,
                "country_name": "Kenya",
                "year_min": 1995,
                "year_max": 2024,
            },
        )
        assert len(links) == 1
        assert "view=markets" in links[0].url
        assert "/explore/overtime" in links[0].url


class TestGenerateLinksMarketshare:
    def test_produces_marketshare_link(self):
        links = generate_atlas_links(
            "marketshare",
            {
                "country_id": 404,
                "country_name": "Kenya",
                "year_min": 1995,
                "year_max": 2024,
            },
        )
        assert len(links) == 1
        assert "/explore/marketshare" in links[0].url
        assert "startYear=1995" in links[0].url


class TestGenerateLinksProductSpace:
    def test_produces_productspace_and_complexity_links(self):
        links = generate_atlas_links(
            "product_space",
            {"country_id": 404, "country_name": "Kenya", "year": 2024},
        )
        assert len(links) == 2
        # Primary: product space
        assert "/explore/productspace" in links[0].url
        assert links[0].link_type == "explore_page"
        # Supplementary: export complexity
        assert "export-complexity" in links[1].url
        assert links[1].link_type == "country_page"


class TestGenerateLinksFeasibility:
    def test_produces_feasibility_and_table_links(self):
        links = generate_atlas_links(
            "feasibility",
            {"country_id": 404, "country_name": "Kenya", "year": 2024},
        )
        assert len(links) == 2
        assert "/explore/feasibility?" in links[0].url
        assert "/explore/feasibility/table?" in links[1].url


class TestGenerateLinksFeasibilityTable:
    def test_produces_feasibility_table_link(self):
        links = generate_atlas_links(
            "feasibility_table",
            {
                "country_id": 404,
                "country_name": "Kenya",
                "year": 2024,
                "product_level": 6,
            },
        )
        assert len(links) == 1
        assert "productLevel=6" in links[0].url

    def test_default_product_level(self):
        links = generate_atlas_links(
            "feasibility_table",
            {"country_id": 404, "country_name": "Kenya", "year": 2024},
        )
        assert f"productLevel={DEFAULT_PRODUCT_LEVEL}" in links[0].url


# ---------------------------------------------------------------------------
# Frontier country fallback
# ---------------------------------------------------------------------------


class TestFrontierFallback:
    def test_growth_opportunities_non_frontier_uses_country_page(self):
        links = generate_atlas_links(
            "growth_opportunities",
            {"country_id": 404, "country_name": "Kenya", "year": 2024},
        )
        assert len(links) == 1
        assert links[0].link_type == "country_page"
        assert "growth-opportunities" in links[0].url

    def test_growth_opportunities_frontier_falls_back_to_feasibility(self):
        links = generate_atlas_links(
            "growth_opportunities",
            {"country_id": 840, "country_name": "USA", "year": 2024},
        )
        assert len(links) == 1
        assert links[0].link_type == "explore_page"
        assert "/explore/feasibility?" in links[0].url
        assert "exporter=country-840" in links[0].url

    def test_product_table_non_frontier_uses_country_page(self):
        links = generate_atlas_links(
            "product_table",
            {"country_id": 404, "country_name": "Kenya", "year": 2024},
        )
        assert len(links) == 1
        assert links[0].link_type == "country_page"
        assert "product-table" in links[0].url

    def test_product_table_frontier_falls_back_to_feasibility_table(self):
        links = generate_atlas_links(
            "product_table",
            {"country_id": 276, "country_name": "Germany", "year": 2024},
        )
        assert len(links) == 1
        assert links[0].link_type == "explore_page"
        assert "/explore/feasibility/table?" in links[0].url
        assert "exporter=country-276" in links[0].url

    def test_product_table_frontier_fallback_uses_product_level(self):
        links = generate_atlas_links(
            "product_table",
            {
                "country_id": 392,
                "country_name": "Japan",
                "year": 2024,
                "product_level": 6,
            },
        )
        assert "productLevel=6" in links[0].url


# ---------------------------------------------------------------------------
# No links for certain query types
# ---------------------------------------------------------------------------


class TestNoLinkQueryTypes:
    def test_global_datum_produces_no_links(self):
        links = generate_atlas_links("global_datum", {})
        assert links == []

    def test_explore_data_availability_produces_no_links(self):
        links = generate_atlas_links("explore_data_availability", {})
        assert links == []

    def test_unknown_query_type_produces_no_links(self):
        links = generate_atlas_links("nonexistent_query_type", {})
        assert links == []


# ---------------------------------------------------------------------------
# Resolution notes propagation
# ---------------------------------------------------------------------------


class TestResolutionNotes:
    def test_empty_notes_when_clean(self):
        links = generate_atlas_links(
            "country_profile",
            {"country_id": 404, "country_name": "Kenya"},
        )
        assert links[0].resolution_notes == []

    def test_notes_propagated_to_all_links(self):
        notes = ["Country 'Turkey' resolved to Turkiye (792)"]
        links = generate_atlas_links(
            "treemap_products",
            {
                "country_id": 792,
                "country_name": "Turkiye",
                "year": 2024,
                "resolution_notes": notes,
            },
        )
        # treemap_products produces 2 links; both should carry the notes
        assert len(links) == 2
        for link in links:
            assert link.resolution_notes == notes

    def test_multiple_resolution_notes(self):
        notes = [
            "Product 'chips' resolved to Electronic integrated circuits (8542)",
            "Year not specified - defaulted to 2024",
        ]
        links = generate_atlas_links(
            "product_info",
            {
                "product_id": 100,
                "product_classification": "HS92",
                "product_name": "Electronic integrated circuits",
                "year": 2024,
                "resolution_notes": notes,
            },
        )
        assert links[0].resolution_notes == notes
        assert len(links[0].resolution_notes) == 2


# ---------------------------------------------------------------------------
# Default values and edge cases
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_default_year_when_missing(self):
        links = generate_atlas_links(
            "treemap_products",
            {"country_id": 404, "country_name": "Kenya"},
        )
        assert f"year={DEFAULT_YEAR}" in links[0].url

    def test_default_start_year_for_overtime(self):
        links = generate_atlas_links(
            "overtime_products",
            {"country_id": 404, "country_name": "Kenya"},
        )
        assert f"startYear={DEFAULT_START_YEAR}" in links[0].url

    def test_country_name_falls_back_to_id(self):
        links = generate_atlas_links(
            "country_profile",
            {"country_id": 404},
        )
        assert "404" in links[0].label

    def test_year_max_used_as_year_for_overtime(self):
        links = generate_atlas_links(
            "overtime_products",
            {
                "country_id": 404,
                "country_name": "Kenya",
                "year_min": 2000,
                "year_max": 2022,
            },
        )
        assert "year=2022" in links[0].url
        assert "endYear=2022" in links[0].url
        assert "startYear=2000" in links[0].url


# ---------------------------------------------------------------------------
# ProductClassificationRegistry
# ---------------------------------------------------------------------------


class TestProductClassificationRegistry:
    @pytest.fixture()
    def registry(self):
        reg = ProductClassificationRegistry()
        reg.add(
            ProductRecord(
                product_id=726,
                hs_code="0901",
                name="Coffee",
                classification="HS92",
                product_level=4,
            )
        )
        reg.add(
            ProductRecord(
                product_id=725,
                hs_code="0901",
                name="Coffee",
                classification="HS12",
                product_level=4,
            )
        )
        reg.add(
            ProductRecord(
                product_id=100,
                hs_code="8542",
                name="Electronic integrated circuits",
                classification="HS92",
                product_level=4,
            )
        )
        return reg

    def test_lookup_by_code(self, registry):
        record = registry.lookup_by_code("HS92", "0901")
        assert record is not None
        assert record.product_id == 726
        assert record.name == "Coffee"

    def test_lookup_by_code_different_classification(self, registry):
        record = registry.lookup_by_code("HS12", "0901")
        assert record is not None
        assert record.product_id == 725

    def test_lookup_by_code_not_found(self, registry):
        record = registry.lookup_by_code("HS92", "9999")
        assert record is None

    def test_lookup_by_name(self, registry):
        records = registry.lookup_by_name("HS92", "Coffee")
        assert len(records) == 1
        assert records[0].product_id == 726

    def test_lookup_by_name_case_insensitive(self, registry):
        records = registry.lookup_by_name("HS92", "coffee")
        assert len(records) == 1
        assert records[0].product_id == 726

    def test_lookup_by_name_not_found(self, registry):
        records = registry.lookup_by_name("HS92", "Bananas")
        assert records == []

    def test_lookup_by_code_case_insensitive_classification(self, registry):
        record = registry.lookup_by_code("hs92", "0901")
        assert record is not None
        assert record.product_id == 726

    def test_empty_registry(self):
        reg = ProductClassificationRegistry()
        assert reg.lookup_by_code("HS92", "0901") is None
        assert reg.lookup_by_name("HS92", "Coffee") == []

    def test_multiple_products_same_name(self):
        """Different products can share a name in different classifications."""
        reg = ProductClassificationRegistry()
        reg.add(
            ProductRecord(
                product_id=726,
                hs_code="0901",
                name="Coffee",
                classification="HS92",
                product_level=4,
            )
        )
        reg.add(
            ProductRecord(
                product_id=725,
                hs_code="0901",
                name="Coffee",
                classification="HS12",
                product_level=4,
            )
        )
        hs92_records = reg.lookup_by_name("HS92", "Coffee")
        hs12_records = reg.lookup_by_name("HS12", "Coffee")
        assert len(hs92_records) == 1
        assert hs92_records[0].product_id == 726
        assert len(hs12_records) == 1
        assert hs12_records[0].product_id == 725

    def test_product_record_frozen(self):
        record = ProductRecord(
            product_id=726,
            hs_code="0901",
            name="Coffee",
            classification="HS92",
            product_level=4,
        )
        with pytest.raises(AttributeError):
            record.product_id = 999


# ---------------------------------------------------------------------------
# End-to-end: SSE-emitted fields coverage
# ---------------------------------------------------------------------------


class TestSSEFieldsCoverage:
    """Verify all fields needed for SSE emission are present and correct."""

    def test_all_sse_fields_present(self):
        links = generate_atlas_links(
            "country_profile",
            {"country_id": 404, "country_name": "Kenya"},
        )
        link = links[0]
        # All 4 SSE fields
        assert isinstance(link.url, str)
        assert isinstance(link.label, str)
        assert link.link_type in ("country_page", "explore_page")
        assert isinstance(link.resolution_notes, list)

    def test_serializable_to_dict(self):
        """AtlasLink should be convertible to a dict matching the SSE schema."""
        from dataclasses import asdict

        links = generate_atlas_links(
            "treemap_products",
            {
                "country_id": 404,
                "country_name": "Kenya",
                "year": 2024,
                "resolution_notes": ["Test note"],
            },
        )
        d = asdict(links[0])
        assert set(d.keys()) == {"url", "label", "link_type", "resolution_notes"}
        assert d["resolution_notes"] == ["Test note"]
