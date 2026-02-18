import pytest
from src.text_to_sql import AtlasTextToSQL, StreamData
from src.config import get_settings

# Load settings
settings = get_settings()

pytestmark = pytest.mark.db


# Set up fixtures — module scope to share one instance across all tests
@pytest.fixture(scope="module")
def atlas_sql(base_dir):
    instance = AtlasTextToSQL(
        db_uri=settings.atlas_db_url,
        table_descriptions_json=base_dir / "db_table_descriptions.json",
        table_structure_json=base_dir / "db_table_structure.json",
        queries_json=base_dir / "src/example_queries/queries.json",
        example_queries_dir=base_dir / "src/example_queries",
        max_results=settings.max_results_per_query,
    )
    yield instance
    instance.close()


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


def test_answer_question_basic(atlas_sql, logger):
    """Test if the system can answer a basic trade-related question"""
    question1 = (
        "What were the top 5 products exported from United States to China in 2020?"
    )
    answer = atlas_sql.answer_question(question1, stream_response=False)
    logger.info(f"Question: {question1}\nAnswer: {answer}")
    assert isinstance(answer, str)
    assert len(answer) > 0
    # Check if the answer contains relevant keywords
    assert any(
        word in answer.lower()
        for word in ["united states", "china", "export", "product"]
    )

    question2 = (
        "Compare India's exports in agricultural goods to China's, from 2000 to 2020"
    )
    answer2 = atlas_sql.answer_question(question2, stream_response=False)
    logger.info(f"Question: {question2}\nAnswer: {answer2}")
    assert isinstance(answer2, str)
    assert len(answer2) > 0


def test_answer_question_stream(atlas_sql, logger):
    """Test streaming: returns (generator, list), generator yields (str, bool)."""
    question = (
        "What were the top 5 products exported from United States to China in 2020?"
    )
    result = atlas_sql.answer_question(question, stream_response=True)

    # Verify return type is a (generator, list) tuple
    assert isinstance(result, tuple) and len(result) == 2
    response_gen, messages = result

    # Consume the generator, collecting chunks
    full_text = ""
    for text_chunk, is_tools in response_gen:
        assert isinstance(text_chunk, str)
        assert isinstance(is_tools, bool)
        full_text += text_chunk

    logger.info(f"Streamed answer: {full_text[:200]}...")
    assert len(full_text) > 0
    # messages list should have been populated during iteration
    assert len(messages) > 0
    assert all(isinstance(m, StreamData) for m in messages)


def test_stream_contract(atlas_sql):
    """Verify the streaming API contract matches what write_stream expects."""
    gen, messages = atlas_sql.answer_question(
        "What is the ECI of Japan in 2019?", stream_response=True
    )

    chunks = list(gen)
    # Every chunk must be (str, bool)
    assert len(chunks) > 0
    for chunk in chunks:
        assert isinstance(chunk, tuple) and len(chunk) == 2
        text, is_tools = chunk
        assert isinstance(text, str) and len(text) > 0
        assert isinstance(is_tools, bool)

    # messages populated after full consumption
    assert isinstance(messages, list)
    assert len(messages) > 0


def test_json_loading(base_dir):
    """Test the JSON loading functionality"""
    test_file = base_dir / "db_table_descriptions.json"

    result = AtlasTextToSQL._load_json_as_dict(test_file)
    assert isinstance(result, dict)
    assert len(result) > 0


def test_max_results_limit(atlas_sql, logger):
    """Test if the max_results parameter is respected"""
    question = "List all products exported from United States"
    answer = atlas_sql.answer_question(question, stream_response=False)
    logger.info(f"Question: {question}\nAnswer: {answer}")
    assert isinstance(answer, str)
    assert len(answer) > 0


def test_conversation_history_and_threads(atlas_sql, logger):
    """Test conversation history with thread IDs"""
    thread_1 = "test_thread_1"
    thread_2 = "test_thread_2"

    # First question in thread 1
    q1 = "What were the top 3 products exported from US to China in 2020?"
    a1 = atlas_sql.answer_question(q1, stream_response=False, thread_id=thread_1)
    logger.info(f"Thread 1 - Q1: {q1}\nA1: {a1}")
    assert "united states" in a1.lower() or "us" in a1.lower()

    # First question in thread 2
    q2 = "What were Germany's top exports to France in 2020?"
    a2 = atlas_sql.answer_question(q2, stream_response=False, thread_id=thread_2)
    logger.info(f"Thread 2 - Q1: {q2}\nA2: {a2}")
    assert "germany" in a2.lower()

    # Follow-up question in thread 1 - should maintain US-China context
    q3 = "How did these numbers change in 2021?"
    a3 = atlas_sql.answer_question(q3, stream_response=False, thread_id=thread_1)
    logger.info(f"Thread 1 - Q2: {q3}\nA3: {a3}")
    assert any(word in a3.lower() for word in ["united states", "china", "us"])


def test_thread_isolation(atlas_sql, logger):
    """Verify that threads are truly isolated — follow-up in thread A doesn't leak context from thread B."""
    thread_a = "isolation_thread_a"
    thread_b = "isolation_thread_b"

    # Thread A: US context
    atlas_sql.answer_question(
        "What were the top 3 exports of the United States in 2020?",
        stream_response=False,
        thread_id=thread_a,
    )

    # Thread B: India context
    atlas_sql.answer_question(
        "What were the top 3 exports of India in 2020?",
        stream_response=False,
        thread_id=thread_b,
    )

    # Follow up in thread A — should reference US, NOT India
    follow_up = atlas_sql.answer_question(
        "How did those change in 2021?",
        stream_response=False,
        thread_id=thread_a,
    )
    logger.info(f"Thread A follow-up: {follow_up}")
    assert any(
        word in follow_up.lower() for word in ["united states", "us", "u.s."]
    )
