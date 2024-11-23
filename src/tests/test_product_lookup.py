import pytest
from langchain_openai import ChatOpenAI
import os
from src.product_lookup import (
    ProductLookupTool,
    ProductMention,
    ProductCodeMapping,
    ProductSearchResult,
)


@pytest.fixture
def llm():
    """Initialize the language model."""
    return ChatOpenAI(model="gpt-4o", temperature=0)


@pytest.fixture
def product_lookup(llm):
    """Initialize the ProductLookupTool with actual database connection."""
    connection_string = os.getenv("ATLAS_DB_URL")
    return ProductLookupTool(
        llm=llm,
        connection_string=connection_string,
        engine_args={
            "execution_options": {"postgresql_readonly": True},
            "connect_args": {"connect_timeout": 10},
        },
        products_table="classification.product_hs92",
    )


@pytest.mark.integration
def test_extract_product_mentions(product_lookup, logger):
    """Test product mention extraction and initial LLM code suggestions."""
    logger.debug("Running test_extract_product_mentions")

    # Test question with product mentions
    question1 = "How much cotton and wheat did Brazil export in 2021?"
    result1 = product_lookup._extract_product_mentions().invoke({"question": question1})
    logger.debug(f"Question 1: {question1}")
    logger.debug(f"Result 1: {result1}")
    assert isinstance(result1, ProductMention)
    assert result1.has_mentions
    product_names = [item.product_name for item in result1.product_names_with_codes]
    assert "cotton" in product_names
    assert "wheat" in product_names
    assert all(
        isinstance(item.codes, list) for item in result1.product_names_with_codes
    )
    assert any(len(item.codes) > 0 for item in result1.product_names_with_codes)

    # Test question with HS codes (should be ignored)
    question2 = "What were US exports of cars and vehicles (HS 87) in 2020?"
    result2 = product_lookup._extract_product_mentions().invoke({"question": question2})
    logger.debug(f"Question 2: {question2}")
    logger.debug(f"Result 2: {result2}")
    assert not result2.has_mentions
    assert len(result2.product_names_with_codes) == 0

    # Test question with no product mentions
    question3 = "What were the top 5 products exported from United States to China?"
    result3 = product_lookup._extract_product_mentions().invoke({"question": question3})
    logger.debug(f"Question 3: {question3}")
    logger.debug(f"Result 3: {result3}")
    assert not result3.has_mentions
    assert len(result3.product_names_with_codes) == 0


@pytest.mark.integration
def test_verify_product_codes(product_lookup, logger):
    """Test verification of LLM-suggested codes against database."""
    logger.debug("Running test_verify_product_codes")

    # Test with valid codes
    valid_codes = ["5201", "5202"]
    results1 = product_lookup._verify_product_codes(valid_codes)
    assert len(results1) > 0
    for result in results1:
        assert "product_code" in result
        assert "product_name" in result
        assert "product_id" in result
        assert "product_level" in result

    # Test with invalid codes
    invalid_codes = ["abcd"]
    results2 = product_lookup._verify_product_codes(invalid_codes)
    assert len(results2) == 0, f"Expected 0 results, got {len(results2)}: {results2}"

    # Test with mixed valid/invalid codes
    mixed_codes = ["5201", "abcd"]
    results3 = product_lookup._verify_product_codes(mixed_codes)
    assert len(results3) > 0, f"Expected >0 results, got {len(results3)}: {results3}"
    assert len(results3) < len(
        mixed_codes
    ), f"Expected <{len(mixed_codes)} results, got {len(results3)}: {results3}"


@pytest.mark.integration
def test_direct_text_search(product_lookup, logger):
    """Test the direct text search functionality with full-text and trigram fallback."""
    logger.debug("Running test_direct_text_search")

    # Test exact match (should use full-text search)
    results1 = product_lookup._direct_text_search("cotton")
    logger.debug(f"Direct text search results for 'cotton': {results1}")
    assert len(results1) > 0
    assert any("cotton" in result["product_name"].lower() for result in results1)

    # Test partial match (might use trigram)
    results2 = product_lookup._direct_text_search("cott")
    logger.debug(f"Direct text search results for partial 'cott': {results2}")
    assert len(results2) > 0

    # Test with misspelling (should use trigram)
    results3 = product_lookup._direct_text_search("cottin")
    logger.debug(f"Direct text search results for misspelled 'cottin': {results3}")
    assert len(results3) > 0

    # Test with nonsense term
    results4 = product_lookup._direct_text_search("xyzabc123")
    logger.debug(f"Direct text search results for nonsense term: {results4}")
    assert len(results4) == 0


@pytest.mark.integration
def test_select_final_codes(product_lookup, logger):
    """Test the LLM-based final code selection process."""
    logger.debug("Running test_select_final_codes")

    # Create test search results
    search_results = [
        ProductSearchResult(
            product_name="cotton",
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
    result = product_lookup._select_final_codes(search_results).invoke(
        {"question": question}
    )

    assert isinstance(result, ProductCodeMapping)
    assert len(result.mappings) > 0
    assert "cotton" in str(result.mappings).lower()
    assert any("5201" in str(mapping) for mapping in result.mappings)


@pytest.mark.integration
def test_full_product_lookup_flow(product_lookup, logger):
    """Test the complete product lookup flow including LLM suggestions and text search."""
    logger.debug("Running test_full_product_lookup_flow")

    # Test with simple products
    question1 = "How much cotton and wheat did Brazil export in 2021?"
    result1 = product_lookup.lookup_product_codes().invoke({"question": question1})
    logger.debug(f"Results for simple products: {result1}")
    assert isinstance(result1, ProductCodeMapping)
    assert len(result1.mappings) > 0

    # Test with more specific product
    question2 = "What were exports of raw cotton fiber?"
    result2 = product_lookup.lookup_product_codes().invoke({"question": question2})
    logger.debug(f"Results for specific product: {result2}")
    assert isinstance(result2, ProductCodeMapping)
    assert len(result2.mappings) > 0

    # Test with no product mentions
    question3 = "What were the top 5 products exported from United States to China?"
    result3 = product_lookup.lookup_product_codes().invoke({"question": question3})
    logger.debug(f"Results for no product mentions: {result3}")
    assert isinstance(result3, ProductCodeMapping)
    assert len(result3.mappings) == 0

    # Verify mappings structure
    for mapping in result1.mappings + result2.mappings:
        assert isinstance(mapping.product_name, str)
        assert isinstance(mapping.hs_codes, list)
        assert len(mapping.hs_codes) > 0
        for code in mapping.hs_codes:
            assert isinstance(code, str)
            assert any(char.isdigit() for char in code)

    # Test with 6-digit product
    question4 = "What were exports of cotton seeds in 2021?"
    result4 = product_lookup.lookup_product_codes().invoke({"question": question4})
    logger.debug(f"Results for 6-digit product: {result4}")
    assert isinstance(result4, ProductCodeMapping)
    assert len(result4.mappings) > 0
