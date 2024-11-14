import pytest
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
import os
from src.product_lookup import (
    ProductLookupTool,
    ProductMention,
    ProductCodeMapping,
    format_product_codes_for_prompt,
)
import logging


logger = logging.getLogger("test_logger")


@pytest.fixture
def llm():
    """Initialize the language model."""
    return ChatOpenAI(model="gpt-4-turbo-preview", temperature=0)


@pytest.fixture
def embeddings():
    """Initialize embeddings model."""
    return OpenAIEmbeddings()


@pytest.fixture
def product_lookup(llm, embeddings, base_dir):
    """Initialize the ProductLookupTool with actual database connection."""
    connection_string = os.getenv("ATLAS_DB_URL")
    return ProductLookupTool(
        llm=llm,
        connection_string=connection_string,
        collection_name="product_embeddings",
        embeddings=embeddings,
    )


@pytest.mark.integration
def test_extract_product_mentions(product_lookup):
    """Test product mention extraction from questions."""
    logger.info("Running test_extract_product_mentions")
    # Test question with product mentions
    question1 = "How much cotton and wheat did Brazil export in 2021?"
    result1 = product_lookup._extract_product_mentions(question1)
    logger.info(f"Question 1: {question1}")
    logger.info(f"Result 1: {result1}")
    assert isinstance(result1, ProductMention)
    assert result1.has_mentions
    assert "cotton" in result1.product_names
    assert "wheat" in result1.product_names

    # Test question with HS codes (should be ignored)
    question2 = "What were US exports of cars and vehicles (HS 87) in 2020?"
    result2 = product_lookup._extract_product_mentions(question2)
    logger.info(f"Question 2: {question2}")
    logger.info(f"Result 2: {result2}")
    assert not result2.has_mentions
    assert len(result2.product_names) == 0

    # Test question with no product mentions
    question3 = "What were the top 5 products exported from United States to China?"
    result3 = product_lookup._extract_product_mentions(question3)
    logger.info(f"Question 3: {question3}")
    logger.info(f"Result 3: {result3}")
    assert not result3.has_mentions
    assert len(result3.product_names) == 0


@pytest.mark.integration
def test_search_product_codes(product_lookup):
    """Test vector similarity search for product codes."""
    logger.info("Running test_search_product_codes")
    results = product_lookup._search_product_codes("cotton")
    logger.info("Testing product codes search for 'cotton'")
    logger.info(f"Results: {results}")
    assert len(results) > 0
    # Check that results contain relevant information
    cotton_related = any("cotton" in doc.page_content.lower() for doc in results)
    assert cotton_related
    # Check that results include HS codes
    has_hs_codes = any(
        any(char.isdigit() for char in doc.page_content) for doc in results
    )
    assert has_hs_codes


@pytest.mark.integration
def test_full_product_lookup_flow(product_lookup):
    """Test the complete product lookup flow."""
    question = "How much cotton and wheat did Brazil export in 2021?"
    result = product_lookup.lookup_product_codes(question)
    logger.info(f"Question: {question}")
    logger.info(f"Result: {result}")
    assert isinstance(result, ProductCodeMapping)
    assert len(result.mappings) > 0

    # Check that we got mappings for both products
    product_names = set()
    for mapping in result.mappings:
        for name in mapping.keys():
            product_names.add(name.lower())

    assert "cotton" in product_names
    assert "wheat" in product_names

    # Verify each mapping has a valid HS code (should be numeric)
    for mapping in result.mappings:
        for code in mapping.values():
            assert any(char.isdigit() for char in code)


@pytest.mark.integration
def test_format_product_codes(product_lookup):
    """Test formatting of product codes for prompt inclusion."""
    logger.info("Running test_format_product_codes")
    question = "How much cotton did Brazil export in 2021?"
    mappings = product_lookup.lookup_product_codes(question)
    logger.info(f"Question: {question}")
    logger.info(f"Mappings: {mappings}")

    formatted = format_product_codes_for_prompt(mappings)
    assert isinstance(formatted, str)
    assert "cotton" in formatted.lower()
    assert ":" in formatted  # Should contain product:code mapping

    # Test with no mappings
    empty_formatted = format_product_codes_for_prompt(None)
    assert empty_formatted == ""


@pytest.mark.integration
def test_edge_cases(product_lookup):
    """Test edge cases and potential error conditions."""
    # Test with question containing no product mentions
    question1 = "What were the top 5 products exported from United States to China?"
    result1 = product_lookup.lookup_product_codes(question1)
    logger.info(f"Question 1: {question1}")
    logger.info(f"Result 1: {result1}")
    assert result1 is None

    # Test with ambiguous/generic product mention
    question2 = "How many goods did USA export?"
    result2 = product_lookup.lookup_product_codes(question2)
    logger.info(f"Question 2: {question2}")
    logger.info(f"Result 2: {result2}")
    assert result2 is None or len(result2.mappings) == 0
