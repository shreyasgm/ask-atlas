from unittest.mock import MagicMock, patch

import pytest

from src.product_and_schema_lookup import (
    ProductAndSchemaLookup,
    ProductDetails,
    ProductCodesMapping,
    ProductSearchResult,
    SchemasAndProductsFound,
    format_product_codes_for_prompt,
)
from src.config import get_settings, create_llm

# Load settings
settings = get_settings()


@pytest.fixture
def llm():
    """Initialize the language model using configured model."""
    return create_llm(
        settings.frontier_model, settings.frontier_model_provider, temperature=0
    )


@pytest.fixture
def product_lookup(llm):
    """Initialize the ProductLookupTool with actual database connection."""
    return ProductAndSchemaLookup(
        llm=llm,
        connection=settings.atlas_db_url,
        engine_args={
            "execution_options": {"postgresql_readonly": True},
            "connect_args": {"connect_timeout": 10},
        },
    )


@pytest.mark.integration
def test_extract_schemas_and_product_mentions(product_lookup, logger):
    """Test product mention extraction and initial LLM code suggestions."""
    logger.debug("Running test_extract_schemas_and_product_mentions")

    # Test question with product mentions
    question1 = "How much cotton and wheat did Brazil export in 2021?"
    result1 = product_lookup.extract_schemas_and_product_mentions().invoke(
        {"question": question1}
    )
    logger.debug(f"Question 1: {question1}")
    logger.debug(f"Result 1: {result1}")
    assert isinstance(result1, SchemasAndProductsFound)
    assert result1.products
    assert result1.requires_product_lookup
    product_names = [item.name for item in result1.products]
    assert "cotton" in product_names
    assert "wheat" in product_names
    assert all(isinstance(item.codes, list) for item in result1.products)
    assert any(len(item.codes) > 0 for item in result1.products)

    # Test question with HS codes (should be ignored)
    question2 = "What were US exports of cars and vehicles (HS 87) in 2020?"
    result2 = product_lookup.extract_schemas_and_product_mentions().invoke(
        {"question": question2}
    )
    logger.debug(f"Question 2: {question2}")
    logger.debug(f"Result 2: {result2}")
    assert not result2.products
    assert len(result2.products) == 0
    assert (
        not result2.requires_product_lookup
    ), "Should not require product lookup - HS codes already provided"

    # Test question with no product mentions
    question3 = (
        "What were the top 5 products exported from United States to China in 2020?"
    )
    result3 = product_lookup.extract_schemas_and_product_mentions().invoke(
        {"question": question3}
    )
    logger.debug(f"Question 3: {question3}")
    logger.debug(f"Result 3: {result3}")
    assert not result3.products
    assert len(result3.products) == 0
    assert (
        not result3.requires_product_lookup
    ), "Should not require product lookup - no product mentions"


@pytest.mark.db
def test_get_official_product_details(product_lookup, logger):
    """Test verification of LLM-suggested codes against database."""
    logger.debug("Running test_get_official_product_details")

    # Test with valid codes
    valid_codes = ["5201", "5202"]
    results1 = product_lookup._get_official_product_details(
        valid_codes, classification_schema="hs92"
    )
    assert len(results1) > 0
    for result in results1:
        assert "product_code" in result
        assert "product_name" in result
        assert "product_id" in result
        assert "product_level" in result

    # Test with invalid codes
    invalid_codes = ["abcd"]
    results2 = product_lookup._get_official_product_details(
        invalid_codes, classification_schema="hs92"
    )
    assert len(results2) == 0, f"Expected 0 results, got {len(results2)}: {results2}"

    # Test with mixed valid/invalid codes
    mixed_codes = ["5201", "abcd"]
    results3 = product_lookup._get_official_product_details(
        mixed_codes, classification_schema="hs12"
    )
    assert len(results3) > 0, f"Expected >0 results, got {len(results3)}: {results3}"
    assert len(results3) < len(
        mixed_codes
    ), f"Expected <{len(mixed_codes)} results, got {len(results3)}: {results3}"

    # TODO: Add test with services codes


@pytest.mark.db
def test_direct_text_search(product_lookup, logger):
    """Test the direct text search functionality with full-text and trigram fallback."""
    logger.debug("Running test_direct_text_search")

    # Test exact match (should use full-text search)
    results1 = product_lookup._direct_text_search(
        "cotton", classification_schema="hs92"
    )
    logger.debug(f"Direct text search results for 'cotton': {results1}")
    assert len(results1) > 0
    assert any("cotton" in result["product_name"].lower() for result in results1)

    # Test partial match (might use trigram)
    results2 = product_lookup._direct_text_search("cott", classification_schema="hs92")
    logger.debug(f"Direct text search results for partial 'cott': {results2}")
    assert len(results2) > 0

    # Test with misspelling (should use trigram)
    results3 = product_lookup._direct_text_search(
        "cottin", classification_schema="hs92"
    )
    logger.debug(f"Direct text search results for misspelled 'cottin': {results3}")
    assert len(results3) > 0

    # Test with nonsense term
    results4 = product_lookup._direct_text_search(
        "xyzabc123", classification_schema="hs92"
    )
    logger.debug(f"Direct text search results for nonsense term: {results4}")
    assert len(results4) == 0


@pytest.mark.integration
def test_select_final_codes(product_lookup, logger):
    """Test the LLM-based final code selection process."""
    logger.debug("Running test_select_final_codes")

    # Create test search results
    search_results = [
        ProductSearchResult(
            name="cotton",
            classification_schema="hs92",
            llm_suggestions=[
                {
                    "product_code": "5201",
                    "product_name": "Cotton, not carded or combed",
                    "product_id": "1234",
                    "product_level": "4",
                }
            ],
            db_suggestions=[
                {
                    "product_code": "5201",
                    "product_name": "Cotton, not carded or combed",
                    "product_id": "1234",
                    "product_level": "4",
                },
                {
                    "product_code": "5203",
                    "product_name": "Cotton, carded or combed",
                    "product_id": "1235",
                    "product_level": "4",
                },
            ],
        )
    ]

    question = "How much raw cotton did Brazil export?"
    result = product_lookup.select_final_codes(search_results).invoke(
        {"question": question}
    )

    assert isinstance(result, ProductCodesMapping)
    assert len(result.mappings) > 0
    assert "cotton" in str(result.mappings).lower()
    assert any("5201" in str(mapping) for mapping in result.mappings)


@pytest.mark.integration
def test_full_product_lookup_flow(product_lookup, logger):
    """Test the complete product lookup flow including LLM suggestions and text search."""
    logger.debug("Running test_full_product_lookup_flow")

    # Test with simple products
    question1 = "How much cotton and wheat did Brazil export in 2021?"
    result1 = product_lookup.get_product_details().invoke({"question": question1})
    logger.debug(f"Results for simple products: {result1}")
    assert isinstance(result1, ProductCodesMapping)
    assert len(result1.mappings) > 0

    # Test with no product mentions
    question3 = "What were the top 5 products exported from United States to China?"
    result3 = product_lookup.get_product_details().invoke({"question": question3})
    logger.debug(f"Results for no product mentions: {result3}")
    assert isinstance(result3, ProductCodesMapping)
    assert len(result3.mappings) == 0

    # Verify mappings structure
    for mapping in result1.mappings:
        assert isinstance(mapping.name, str)
        assert isinstance(mapping.classification_schema, str)
        assert isinstance(mapping.codes, list)
        assert len(mapping.codes) > 0
        for code in mapping.codes:
            assert isinstance(code, str)
            assert any(char.isdigit() for char in code)


def test_six_digit_product_code_pipeline():
    """Verify 6-digit product codes flow through get_candidate_codes correctly.

    Mocks DB calls to validate that the candidate-gathering step preserves
    6-digit codes from both LLM suggestions and text search results.
    This covers the same scenario as the removed integration test for
    "cotton seeds" without requiring real LLM or DB calls.
    """
    mock_llm = MagicMock()
    mock_engine = MagicMock()
    lookup = ProductAndSchemaLookup(llm=mock_llm, connection=mock_engine)

    # Simulate extraction result: LLM identified "cotton seeds" with a 6-digit code
    extraction_result = SchemasAndProductsFound(
        classification_schemas=["hs92"],
        products=[
            ProductDetails(
                name="cotton seeds",
                classification_schema="hs92",
                codes=["120720"],
            )
        ],
        requires_product_lookup=True,
    )

    # Mock DB verification of LLM-suggested codes (returns 6-digit match)
    llm_verified = [
        {
            "product_code": "120720",
            "product_name": "Cotton seeds, whether or not broken",
            "product_id": "5001",
            "product_level": "6",
        },
    ]

    # Mock text search results (also 6-digit codes)
    db_search_results = [
        {
            "product_code": "120720",
            "product_name": "Cotton seeds, whether or not broken",
            "product_id": "5001",
            "product_level": "6",
        },
        {
            "product_code": "120740",
            "product_name": "Sesame seeds, whether or not broken",
            "product_id": "5002",
            "product_level": "6",
        },
    ]

    with (
        patch.object(
            lookup,
            "_get_official_product_details",
            return_value=llm_verified,
        ) as mock_official,
        patch.object(
            lookup,
            "_direct_text_search",
            return_value=db_search_results,
        ) as mock_search,
    ):
        candidates = lookup.get_candidate_codes(extraction_result)

    # Should produce exactly one ProductSearchResult for "cotton seeds"
    assert len(candidates) == 1
    assert candidates[0].name == "cotton seeds"
    assert candidates[0].classification_schema == "hs92"

    # All suggestion codes should be 6-digit
    all_codes = [
        s["product_code"]
        for s in candidates[0].llm_suggestions + candidates[0].db_suggestions
    ]
    assert "120720" in all_codes
    assert all(
        len(c) == 6 for c in all_codes
    ), f"Expected all 6-digit codes, got: {all_codes}"

    # Verify DB methods were called with the right args
    mock_official.assert_called_once_with(
        codes=["120720"], classification_schema="hs92"
    )
    mock_search.assert_called_once_with("cotton seeds", "hs92")


def test_format_product_codes_for_prompt():
    """Test formatting of product codes for prompt inclusion."""
    # Test with empty mappings
    result2 = format_product_codes_for_prompt(ProductCodesMapping(mappings=[]))
    assert result2 == ""

    # Test with single mapping
    mapping = ProductCodesMapping(
        mappings=[
            ProductDetails(name="cotton", codes=["5201"], classification_schema="hs92")
        ]
    )
    result3 = format_product_codes_for_prompt(mapping)
    assert "cotton" in result3
    assert "5201" in result3
    assert "hs92" in result3

    # Test with multiple mappings
    multiple_mappings = ProductCodesMapping(
        mappings=[
            ProductDetails(name="cotton", codes=["5201"], classification_schema="hs92"),
            ProductDetails(name="wheat", codes=["1001"], classification_schema="hs92"),
        ]
    )
    result4 = format_product_codes_for_prompt(multiple_mappings)
    assert "cotton" in result4
    assert "wheat" in result4
    assert "5201" in result4
    assert "1001" in result4
    assert result4.count("hs92") == 2
