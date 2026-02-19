import pytest
from src.text_to_sql import AtlasTextToSQL, StreamData
from src.config import get_settings

# Load settings
settings = get_settings()

pytestmark = [pytest.mark.db, pytest.mark.asyncio(loop_scope="module")]


# Set up fixtures — module scope to share one instance across all tests
@pytest.fixture(scope="module")
async def atlas_sql(base_dir):
    instance = await AtlasTextToSQL.create_async(
        db_uri=settings.atlas_db_url,
        table_descriptions_json=base_dir / "db_table_descriptions.json",
        table_structure_json=base_dir / "db_table_structure.json",
        queries_json=base_dir / "src/example_queries/queries.json",
        example_queries_dir=base_dir / "src/example_queries",
        max_results=settings.max_results_per_query,
    )
    yield instance
    await instance.aclose()


async def test_initialization(atlas_sql):
    """Test if the AtlasTextToSQL class initializes correctly via create_async."""
    assert atlas_sql.db is not None
    assert atlas_sql.table_descriptions is not None
    assert atlas_sql.table_structure is not None
    assert atlas_sql.example_queries is not None
    assert atlas_sql.max_results == 15


async def test_answer_question_basic(atlas_sql, logger):
    """Test if the system can answer a basic trade-related question"""
    question1 = (
        "What were the top 5 products exported from United States to China in 2020?"
    )
    answer = await atlas_sql.aanswer_question(question1)
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
    answer2 = await atlas_sql.aanswer_question(question2)
    logger.info(f"Question: {question2}\nAnswer: {answer2}")
    assert isinstance(answer2, str)
    assert len(answer2) > 0


async def test_answer_question_stream(atlas_sql, logger):
    """Test streaming: aanswer_question_stream yields StreamData objects."""
    question = (
        "What were the top 5 products exported from United States to China in 2020?"
    )

    full_text = ""
    messages = []
    async for stream_data in atlas_sql.aanswer_question_stream(question):
        assert isinstance(stream_data, StreamData)
        messages.append(stream_data)
        if stream_data.source == "agent" and stream_data.content:
            full_text += stream_data.content

    logger.info(f"Streamed answer: {full_text[:200]}...")
    assert len(full_text) > 0
    assert len(messages) > 0
    assert all(isinstance(m, StreamData) for m in messages)


async def test_stream_contract(atlas_sql):
    """Verify the async streaming API contract: yields StreamData objects."""
    items = []
    async for stream_data in atlas_sql.aanswer_question_stream(
        "What is the ECI of Japan in 2019?"
    ):
        items.append(stream_data)

    assert len(items) > 0
    for item in items:
        assert isinstance(item, StreamData)
        assert isinstance(item.content, str) and len(item.content) > 0
        assert item.source in ("agent", "tool")
        assert item.message_type in ("agent_talk", "tool_output", "tool_call")


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_json_loading(base_dir):
    """Test the JSON loading functionality"""
    test_file = base_dir / "db_table_descriptions.json"

    result = AtlasTextToSQL._load_json_as_dict(test_file)
    assert isinstance(result, dict)
    assert len(result) > 0


async def test_max_results_limit(atlas_sql, logger):
    """Test if the max_results parameter is respected"""
    question = "List all products exported from United States"
    answer = await atlas_sql.aanswer_question(question)
    logger.info(f"Question: {question}\nAnswer: {answer}")
    assert isinstance(answer, str)
    assert len(answer) > 0


async def test_conversation_history_and_threads(atlas_sql, logger):
    """Test conversation history with thread IDs"""
    thread_1 = "test_thread_1"
    thread_2 = "test_thread_2"

    # First question in thread 1
    q1 = "What were the top 3 products exported from US to China in 2020?"
    a1 = await atlas_sql.aanswer_question(q1, thread_id=thread_1)
    logger.info(f"Thread 1 - Q1: {q1}\nA1: {a1}")
    assert "united states" in a1.lower() or "us" in a1.lower()

    # First question in thread 2
    q2 = "What were Germany's top exports to France in 2020?"
    a2 = await atlas_sql.aanswer_question(q2, thread_id=thread_2)
    logger.info(f"Thread 2 - Q1: {q2}\nA2: {a2}")
    assert "germany" in a2.lower()

    # Follow-up question in thread 1 - should maintain US-China context
    q3 = "How did these numbers change in 2021?"
    a3 = await atlas_sql.aanswer_question(q3, thread_id=thread_1)
    logger.info(f"Thread 1 - Q2: {q3}\nA3: {a3}")
    assert any(word in a3.lower() for word in ["united states", "china", "us"])


async def test_thread_isolation(atlas_sql, logger):
    """Verify that threads are truly isolated — follow-up in thread A doesn't leak context from thread B."""
    thread_a = "isolation_thread_a"
    thread_b = "isolation_thread_b"

    # Thread A: US context
    await atlas_sql.aanswer_question(
        "What were the top 3 exports of the United States in 2020?",
        thread_id=thread_a,
    )

    # Thread B: India context
    await atlas_sql.aanswer_question(
        "What were the top 3 exports of India in 2020?",
        thread_id=thread_b,
    )

    # Follow up in thread A — should reference US, NOT India
    follow_up = await atlas_sql.aanswer_question(
        "How did those change in 2021?",
        thread_id=thread_a,
    )
    logger.info(f"Thread A follow-up: {follow_up}")
    assert any(
        word in follow_up.lower() for word in ["united states", "us", "u.s."]
    )
