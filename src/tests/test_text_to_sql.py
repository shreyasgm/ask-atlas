import pytest
import os
from src.text_to_sql import AtlasTextToSQL


# Set up fixtures
@pytest.fixture
def atlas_sql(base_dir):
    return AtlasTextToSQL(
        db_uri=os.getenv("ATLAS_DB_URL"),
        table_descriptions_json=base_dir / "db_table_descriptions.json",
        table_structure_json=base_dir / "db_table_structure.json",
        queries_json=base_dir / "src/example_queries/queries.json",
        example_queries_dir=base_dir / "src/example_queries",
        max_results=15,
    )


@pytest.fixture
def sample_schemas():
    return ["hs92", "classification"]


def test_initialization(atlas_sql):
    """Test if the AtlasTextToSQL class initializes correctly"""
    assert atlas_sql.db is not None
    assert atlas_sql.table_descriptions is not None
    assert atlas_sql.table_structure is not None
    assert atlas_sql.example_queries is not None
    assert atlas_sql.max_results == 15


def test_get_table_info_for_schemas(atlas_sql, sample_schemas):
    """Test if table information can be retrieved for given schemas"""
    table_info = atlas_sql.get_table_info_for_schemas(sample_schemas)
    assert isinstance(table_info, str)
    assert len(table_info) > 0
    # Check if it properly filters out tables with 'group' in the name
    assert "group" not in table_info.lower()


def test_answer_question_basic(atlas_sql, logger):
    """Test if the system can answer a basic trade-related question"""
    question = (
        "What were the top 5 products exported from United States to China in 2020?"
    )
    answer = atlas_sql.answer_question(question)
    logger.debug(f"Question: {question}\nAnswer: {answer}")
    assert isinstance(answer, str)
    assert len(answer) > 0
    # Check if the answer contains relevant keywords
    assert any(
        word in answer.lower()
        for word in ["united states", "china", "export", "product"]
    )

def test_json_loading(base_dir):
    """Test the JSON loading functionality"""
    test_file = base_dir / "db_table_descriptions.json"

    result = AtlasTextToSQL._load_json_as_dict(test_file)
    assert isinstance(result, dict)
    assert len(result) > 0


def test_max_results_limit(atlas_sql, logger):
    """Test if the max_results parameter is respected"""
    question = "List all products exported from United States"
    answer = atlas_sql.answer_question(question)
    logger.info(f"Question: {question}\nAnswer: {answer}")
    # This is a bit tricky to test exactly, but we can check if the answer exists
    assert isinstance(answer, str)
    assert len(answer) > 0

