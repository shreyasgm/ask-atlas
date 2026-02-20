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
    """Verify the async streaming API contract: yields StreamData objects.

    The stream includes both legacy event types (agent_talk, tool_output,
    tool_call) and new pipeline visibility events (node_start, pipeline_state).
    """
    items = []
    async for stream_data in atlas_sql.aanswer_question_stream(
        "What is the ECI of Japan in 2019?"
    ):
        items.append(stream_data)

    assert len(items) > 0

    valid_sources = {"agent", "tool", "pipeline"}
    valid_types = {"agent_talk", "tool_output", "tool_call", "node_start", "pipeline_state"}

    for item in items:
        assert isinstance(item, StreamData)
        assert isinstance(item.content, str)
        assert item.source in valid_sources
        assert item.message_type in valid_types
        # Legacy events should have content (except tool_call which may be empty)
        if item.message_type in ("agent_talk", "tool_output"):
            assert len(item.content) > 0
        # Pipeline events carry data in payload, not content
        if item.message_type in ("node_start", "pipeline_state"):
            assert item.payload is not None
            assert isinstance(item.payload, dict)


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_json_loading(base_dir):
    """Test the JSON loading functionality"""
    test_file = base_dir / "db_table_descriptions.json"

    result = AtlasTextToSQL._load_json_as_dict(test_file)
    assert isinstance(result, dict)
    assert len(result) > 0


