import pytest
from select_schema_and_tables import create_schema_selection_chain
from langchain_openai import ChatOpenAI


@pytest.fixture
def schema_selection_chain():
    """Fixture to create and return the schema selection chain."""
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    return create_schema_selection_chain(llm)


# Test cases as tuples of (question, expected_schemas)
TEST_CASES = [
    (
        "What did India export to China in 2021? I want the top 10 products in terms of export value, in the HS 2012 product classification for goods, and I also want services data.",
        ["classification", "hs12", "services_bilateral"],
    ),
    (
        "What were Brazil's top exports in 2020?",
        ["classification", "hs92"],  # Default to hs92 when no classification specified
    ),
    (
        "Show me the trade in services between USA and Germany in 2022.",
        ["classification", "services_bilateral"],
    ),
    (
        "What services did Japan export in 2021?",
        ["classification", "services_unilateral"],
    ),
    (
        "Compare USA's exports in SITC and HS92 classifications for 2019.",
        ["classification", "sitc", "hs92"],
    ),
]


@pytest.mark.parametrize("question,expected_schemas", TEST_CASES)
def test_schema_selection(schema_selection_chain, question, expected_schemas):
    """
    Test schema selection for various types of questions.

    Args:
        schema_selection_chain: The chain fixture
        question: Input question
        expected_schemas: List of expected schema names
    """
    response = schema_selection_chain.invoke({"question": question})
    assert sorted(response) == sorted(
        expected_schemas
    ), f"For question '{question}', expected {expected_schemas}, but got {response}"


def test_invalid_question():
    """Test handling of irrelevant or invalid questions."""
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    chain = create_schema_selection_chain(llm)
    response = chain.invoke({"question": "What is the weather like today?"})
    assert "classification" in response, "Should always include classification schema"
    assert (
        len(response) >= 2
    ), "Should return at least one schema besides classification"
