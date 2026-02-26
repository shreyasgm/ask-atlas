"""Atlas link generation module.

Deterministic URL builders for Atlas visualization pages.
Pure functions — no LLM calls, no HTTP, no graph dependencies.

Usage::

    from src.atlas_links import generate_atlas_links

    links = generate_atlas_links(
        "treemap_products",
        {"country_id": 404, "country_name": "Kenya", "year": 2024},
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

ATLAS_BASE_URL = "https://atlas.hks.harvard.edu"

# Year defaults when the user's question doesn't specify.
DEFAULT_YEAR = 2024
DEFAULT_START_YEAR = 1995
DEFAULT_PRODUCT_LEVEL = 4

# Product classification → URL prefix.  The numeric ID part is
# classification-specific (the same product has different IDs across
# classifications).
PRODUCT_CLASSIFICATION_PREFIXES: dict[str, str] = {
    "HS92": "HS92",
    "HS12": "HS12",
    "HS22": "HS22",
    "SITC": "SITC",
}

# Frontier countries where the Country Page subpages ``growth-opportunities``
# and ``product-table`` are unavailable.  For these countries the link
# generator falls back to the equivalent Explore feasibility pages.
FRONTIER_COUNTRY_IDS: frozenset[int] = frozenset(
    {
        40,  # Austria
        56,  # Belgium
        203,  # Czech Republic
        208,  # Denmark
        246,  # Finland
        250,  # France
        276,  # Germany
        372,  # Ireland
        380,  # Italy
        392,  # Japan
        410,  # South Korea
        528,  # Netherlands
        702,  # Singapore
        752,  # Sweden
        756,  # Switzerland
        826,  # United Kingdom
        840,  # USA
    }
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AtlasLink:
    """A deterministic link to an Atlas visualization page.

    Attributes:
        url: Full URL to the Atlas page.
        label: Human-readable label for the link pill.
        link_type: Whether this links to a country_page or explore_page.
        resolution_notes: Empty when resolution was clean; populated with
            human-readable notes when entities were ambiguously resolved.
    """

    url: str
    label: str
    link_type: Literal["country_page", "explore_page"]
    resolution_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProductRecord:
    """A product in the Atlas product catalog."""

    product_id: int
    hs_code: str
    name: str
    classification: str  # HS92, HS12, HS22, or SITC
    product_level: int


class ProductClassificationRegistry:
    """In-memory registry mapping HS codes and product names to Atlas product IDs.

    Populated externally (e.g., from a database query or cache).
    Supports lookup by HS code or product name.
    """

    def __init__(self) -> None:
        # (classification_upper, hs_code) → ProductRecord
        self._by_code: dict[tuple[str, str], ProductRecord] = {}
        # (classification_upper, name_lower) → list[ProductRecord]
        self._by_name: dict[tuple[str, str], list[ProductRecord]] = {}

    def add(self, record: ProductRecord) -> None:
        """Register a product record for lookup."""
        cls = record.classification.upper()
        self._by_code[(cls, record.hs_code)] = record
        name_key = (cls, record.name.lower())
        self._by_name.setdefault(name_key, []).append(record)

    def lookup_by_code(self, classification: str, hs_code: str) -> ProductRecord | None:
        """Look up a product by classification and HS code."""
        return self._by_code.get((classification.upper(), hs_code))

    def lookup_by_name(self, classification: str, name: str) -> list[ProductRecord]:
        """Look up products by classification and name (case-insensitive)."""
        return list(self._by_name.get((classification.upper(), name.lower()), []))


# ---------------------------------------------------------------------------
# URL parameter formatting helpers
# ---------------------------------------------------------------------------


def _product_param(classification: str, product_id: int) -> str:
    """Format product URL parameter (e.g., ``product-HS92-726``)."""
    cls_upper = classification.upper()
    if cls_upper not in PRODUCT_CLASSIFICATION_PREFIXES:
        raise ValueError(
            f"Unknown product classification '{classification}'. "
            f"Valid: {sorted(PRODUCT_CLASSIFICATION_PREFIXES)}"
        )
    return f"product-{PRODUCT_CLASSIFICATION_PREFIXES[cls_upper]}-{product_id}"


def _exporter_param(country_id: int) -> str:
    return f"country-{country_id}"


def _group_exporter_param(group_id: int) -> str:
    return f"group-{group_id}"


# ---------------------------------------------------------------------------
# Country page URL builder
# ---------------------------------------------------------------------------


def country_page_url(country_id: int, subpage: str | None = None) -> str:
    """Build a country page URL.

    Args:
        country_id: ISO 3166-1 numeric country code.
        subpage: Optional subpage slug (e.g., ``"export-basket"``).
    """
    base = f"{ATLAS_BASE_URL}/countries/{country_id}"
    if subpage:
        return f"{base}/{subpage}"
    return base


# ---------------------------------------------------------------------------
# Explore page URL builders
# ---------------------------------------------------------------------------


def explore_treemap_url(
    *,
    year: int,
    country_id: int | None = None,
    partner_id: int | None = None,
    product_classification: str | None = None,
    product_id: int | None = None,
    view: str | None = None,
    group_id: int | None = None,
) -> str:
    """Build an explore treemap URL."""
    params = [f"year={year}"]
    if group_id is not None:
        params.append(f"exporter={_group_exporter_param(group_id)}")
    elif country_id is not None:
        params.append(f"exporter={_exporter_param(country_id)}")
    if partner_id is not None:
        params.append(f"importer={_exporter_param(partner_id)}")
    if product_classification and product_id is not None:
        params.append(f"product={_product_param(product_classification, product_id)}")
    if view:
        params.append(f"view={view}")
    return f"{ATLAS_BASE_URL}/explore/treemap?{'&'.join(params)}"


def explore_overtime_url(
    *,
    year: int,
    start_year: int,
    end_year: int,
    country_id: int,
    view: str | None = None,
) -> str:
    """Build an explore overtime (trade over time) URL."""
    url = (
        f"{ATLAS_BASE_URL}/explore/overtime?"
        f"year={year}&startYear={start_year}&endYear={end_year}"
        f"&exporter={_exporter_param(country_id)}"
    )
    if view:
        url += f"&view={view}"
    return url


def explore_marketshare_url(
    *, year: int, start_year: int, end_year: int, country_id: int
) -> str:
    """Build an explore marketshare URL."""
    return (
        f"{ATLAS_BASE_URL}/explore/marketshare?"
        f"year={year}&startYear={start_year}&endYear={end_year}"
        f"&exporter={_exporter_param(country_id)}"
    )


def explore_productspace_url(*, year: int, country_id: int) -> str:
    """Build an explore product space URL."""
    return (
        f"{ATLAS_BASE_URL}/explore/productspace?"
        f"year={year}&exporter={_exporter_param(country_id)}"
    )


def explore_feasibility_url(*, year: int, country_id: int) -> str:
    """Build an explore feasibility (growth opportunity scatter) URL."""
    return (
        f"{ATLAS_BASE_URL}/explore/feasibility?"
        f"year={year}&exporter={_exporter_param(country_id)}"
    )


def explore_feasibility_table_url(
    *, year: int, country_id: int, product_level: int = DEFAULT_PRODUCT_LEVEL
) -> str:
    """Build an explore feasibility table URL."""
    return (
        f"{ATLAS_BASE_URL}/explore/feasibility/table?"
        f"year={year}&exporter={_exporter_param(country_id)}"
        f"&productLevel={product_level}"
    )


# ---------------------------------------------------------------------------
# Frontier country check
# ---------------------------------------------------------------------------


def is_frontier_country(country_id: int) -> bool:
    """Return True if the country is a frontier economy."""
    return country_id in FRONTIER_COUNTRY_IDS


# ---------------------------------------------------------------------------
# Internal helpers for the dispatch handlers
# ---------------------------------------------------------------------------


def _get_notes(params: dict) -> list[str]:
    """Extract resolution_notes from params, defaulting to empty list."""
    return list(params.get("resolution_notes", []))


def _get_year(params: dict) -> int:
    return params.get("year", DEFAULT_YEAR)


def _get_year_range(params: dict) -> tuple[int, int, int]:
    """Return ``(year, start_year, end_year)`` for time-series queries."""
    year_max = params.get("year_max", params.get("year", DEFAULT_YEAR))
    year_min = params.get("year_min", DEFAULT_START_YEAR)
    return year_max, year_min, year_max


# ---------------------------------------------------------------------------
# Query-type handler functions
# ---------------------------------------------------------------------------

# --- Country page handlers ---


def _handle_country_profile(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=country_page_url(cid),
            label=f"{name} \u2014 Country Profile",
            link_type="country_page",
            resolution_notes=notes,
        )
    ]


def _handle_country_lookback(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    notes = _get_notes(params)
    year, start_year, end_year = _get_year_range(params)
    return [
        AtlasLink(
            url=country_page_url(cid, "growth-dynamics"),
            label=f"{name} \u2014 Growth Dynamics",
            link_type="country_page",
            resolution_notes=notes,
        ),
        AtlasLink(
            url=explore_overtime_url(
                year=year,
                start_year=start_year,
                end_year=end_year,
                country_id=cid,
            ),
            label=f"{name} \u2014 Trade Over Time ({start_year}\u2013{end_year})",
            link_type="explore_page",
            resolution_notes=notes,
        ),
    ]


def _handle_new_products(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=country_page_url(cid, "new-products"),
            label=f"{name} \u2014 New Products",
            link_type="country_page",
            resolution_notes=notes,
        )
    ]


def _handle_country_year(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=country_page_url(cid),
            label=f"{name} \u2014 Country Profile",
            link_type="country_page",
            resolution_notes=notes,
        )
    ]


# --- Explore page handlers ---


def _handle_treemap_products(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    year = _get_year(params)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_treemap_url(year=year, country_id=cid),
            label=f"{name} \u2014 Export Basket ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        ),
        AtlasLink(
            url=country_page_url(cid, "export-basket"),
            label=f"{name} \u2014 Export Basket",
            link_type="country_page",
            resolution_notes=notes,
        ),
    ]


def _handle_treemap_partners(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    year = _get_year(params)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_treemap_url(year=year, country_id=cid, view="markets"),
            label=f"{name} \u2014 Trade Partners ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        )
    ]


def _handle_treemap_bilateral(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    pid = params["partner_id"]
    name = params.get("country_name", str(cid))
    partner_name = params.get("partner_name", str(pid))
    year = _get_year(params)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_treemap_url(year=year, country_id=cid, partner_id=pid),
            label=f"{name} \u2192 {partner_name} ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        )
    ]


def _handle_product_info(params: dict) -> list[AtlasLink]:
    prod_id = params["product_id"]
    cls = params.get("product_classification", "HS92")
    prod_name = params.get("product_name", str(prod_id))
    year = _get_year(params)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_treemap_url(
                year=year, product_classification=cls, product_id=prod_id
            ),
            label=f"{prod_name} \u2014 Global Trade ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        )
    ]


def _handle_explore_bilateral(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    pid = params["partner_id"]
    name = params.get("country_name", str(cid))
    partner_name = params.get("partner_name", str(pid))
    year = _get_year(params)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_treemap_url(year=year, country_id=cid, partner_id=pid),
            label=f"{name} \u2192 {partner_name} ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        )
    ]


def _handle_explore_group(params: dict) -> list[AtlasLink]:
    gid = params.get("group_id")
    if gid is None:
        return []
    year = _get_year(params)
    notes = _get_notes(params)
    group_name = params.get("group_name", f"Group {gid}")
    return [
        AtlasLink(
            url=explore_treemap_url(year=year, group_id=gid),
            label=f"{group_name} \u2014 Exports ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        )
    ]


def _handle_overtime_products(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    year, start_year, end_year = _get_year_range(params)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_overtime_url(
                year=year,
                start_year=start_year,
                end_year=end_year,
                country_id=cid,
            ),
            label=f"{name} \u2014 Trade Over Time ({start_year}\u2013{end_year})",
            link_type="explore_page",
            resolution_notes=notes,
        ),
        AtlasLink(
            url=explore_treemap_url(year=year, country_id=cid),
            label=f"{name} \u2014 Export Basket ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        ),
    ]


def _handle_overtime_partners(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    year, start_year, end_year = _get_year_range(params)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_overtime_url(
                year=year,
                start_year=start_year,
                end_year=end_year,
                country_id=cid,
                view="markets",
            ),
            label=(
                f"{name} \u2014 Partners Over Time " f"({start_year}\u2013{end_year})"
            ),
            link_type="explore_page",
            resolution_notes=notes,
        )
    ]


def _handle_marketshare(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    year, start_year, end_year = _get_year_range(params)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_marketshare_url(
                year=year,
                start_year=start_year,
                end_year=end_year,
                country_id=cid,
            ),
            label=(
                f"{name} \u2014 Global Market Share " f"({start_year}\u2013{end_year})"
            ),
            link_type="explore_page",
            resolution_notes=notes,
        )
    ]


def _handle_product_space(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    year = _get_year(params)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_productspace_url(year=year, country_id=cid),
            label=f"{name} \u2014 Product Space ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        ),
        AtlasLink(
            url=country_page_url(cid, "export-complexity"),
            label=f"{name} \u2014 Export Complexity",
            link_type="country_page",
            resolution_notes=notes,
        ),
    ]


def _handle_feasibility(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    year = _get_year(params)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_feasibility_url(year=year, country_id=cid),
            label=f"{name} \u2014 Growth Opportunities ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        ),
        AtlasLink(
            url=explore_feasibility_table_url(year=year, country_id=cid),
            label=f"{name} \u2014 Growth Opportunities Table ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        ),
    ]


def _handle_feasibility_table(params: dict) -> list[AtlasLink]:
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    year = _get_year(params)
    level = params.get("product_level", DEFAULT_PRODUCT_LEVEL)
    notes = _get_notes(params)
    return [
        AtlasLink(
            url=explore_feasibility_table_url(
                year=year, country_id=cid, product_level=level
            ),
            label=f"{name} \u2014 Growth Opportunities Table ({year})",
            link_type="explore_page",
            resolution_notes=notes,
        )
    ]


# --- Frontier country fallback handlers ---


def _handle_growth_opportunities(params: dict) -> list[AtlasLink]:
    """Country page growth-opportunities with frontier fallback."""
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    year = _get_year(params)
    notes = _get_notes(params)

    if is_frontier_country(cid):
        return [
            AtlasLink(
                url=explore_feasibility_url(year=year, country_id=cid),
                label=f"{name} \u2014 Growth Opportunities ({year})",
                link_type="explore_page",
                resolution_notes=notes,
            )
        ]
    return [
        AtlasLink(
            url=country_page_url(cid, "growth-opportunities"),
            label=f"{name} \u2014 Growth Opportunities",
            link_type="country_page",
            resolution_notes=notes,
        )
    ]


def _handle_product_table(params: dict) -> list[AtlasLink]:
    """Country page product-table with frontier fallback."""
    cid = params["country_id"]
    name = params.get("country_name", str(cid))
    year = _get_year(params)
    level = params.get("product_level", DEFAULT_PRODUCT_LEVEL)
    notes = _get_notes(params)

    if is_frontier_country(cid):
        return [
            AtlasLink(
                url=explore_feasibility_table_url(
                    year=year, country_id=cid, product_level=level
                ),
                label=f"{name} \u2014 Growth Opportunities Table ({year})",
                link_type="explore_page",
                resolution_notes=notes,
            )
        ]
    return [
        AtlasLink(
            url=country_page_url(cid, "product-table"),
            label=f"{name} \u2014 Product Table",
            link_type="country_page",
            resolution_notes=notes,
        )
    ]


# ---------------------------------------------------------------------------
# Query-type → handler dispatch table
# ---------------------------------------------------------------------------

_QUERY_TYPE_HANDLERS: dict[str, Callable[[dict], list[AtlasLink]]] = {
    # Country pages
    "country_profile": _handle_country_profile,
    "country_lookback": _handle_country_lookback,
    "new_products": _handle_new_products,
    "country_year": _handle_country_year,
    "growth_opportunities": _handle_growth_opportunities,
    "product_table": _handle_product_table,
    # Explore pages — treemap
    "treemap_products": _handle_treemap_products,
    "treemap_partners": _handle_treemap_partners,
    "treemap_bilateral": _handle_treemap_bilateral,
    "product_info": _handle_product_info,
    "explore_bilateral": _handle_explore_bilateral,
    "explore_group": _handle_explore_group,
    # Explore pages — time series
    "overtime_products": _handle_overtime_products,
    "overtime_partners": _handle_overtime_partners,
    "marketshare": _handle_marketshare,
    # Explore pages — network & opportunity
    "product_space": _handle_product_space,
    "feasibility": _handle_feasibility,
    "feasibility_table": _handle_feasibility_table,
    # No link generated (explicitly listed for documentation):
    # "global_datum"              → returns []
    # "explore_data_availability" → returns []
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_atlas_links(
    query_type: str,
    resolved_params: dict,
) -> list[AtlasLink]:
    """Generate Atlas links for a classified query.

    This is the main entry point.  It dispatches to the appropriate handler
    based on ``query_type`` and returns a list of :class:`AtlasLink` objects.

    Args:
        query_type: The classified query type string (e.g.,
            ``"treemap_products"``, ``"country_profile"``).
        resolved_params: Dict with resolved entity IDs and metadata.
            Expected keys vary by query_type but may include:

            - ``country_id`` (int): ISO numeric country code
            - ``country_name`` (str): Country display name
            - ``partner_id`` (int): Partner country ISO numeric code
            - ``partner_name`` (str): Partner display name
            - ``product_id`` (int): Atlas internal product numeric ID
            - ``product_classification`` (str): HS92, HS12, HS22, or SITC
            - ``product_name`` (str): Product display name
            - ``year`` (int): Display year
            - ``year_min`` (int): Time series start year
            - ``year_max`` (int): Time series end year
            - ``product_level`` (int): Product detail level (2, 4, or 6)
            - ``group_id`` (int): Group exporter ID
            - ``resolution_notes`` (list[str]): Entity resolution notes

    Returns:
        List of AtlasLink objects.  Empty for query types with no mapping
        (e.g., ``"global_datum"``, ``"explore_data_availability"``).
    """
    handler = _QUERY_TYPE_HANDLERS.get(query_type)
    if handler is None:
        return []
    return handler(resolved_params)
