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
        table_descriptions_json=base_dir
        / "src"
        / "schema"
        / "db_table_descriptions.json",
        table_structure_json=base_dir / "src" / "schema" / "db_table_structure.json",
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


@pytest.mark.integration
async def test_answer_question_basic(atlas_sql, logger):
    """Test if the system can answer a basic trade-related question"""
    question1 = (
        "What were the top 5 products exported from United States to China in 2020?"
    )
    result = await atlas_sql.aanswer_question(question1)
    answer = result.answer
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
    result2 = await atlas_sql.aanswer_question(question2)
    answer2 = result2.answer
    logger.info(f"Question: {question2}\nAnswer: {answer2}")
    assert isinstance(answer2, str)
    assert len(answer2) > 0


@pytest.mark.integration
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
    valid_types = {
        "agent_talk",
        "tool_output",
        "tool_call",
        "node_start",
        "pipeline_state",
    }

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
