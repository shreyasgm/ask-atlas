import pytest
from pathlib import Path
import json
import tempfile
from unittest.mock import Mock
from src.generate_query import (
    load_example_queries,
    create_query_generation_chain,
    create_query_tool,
    create_sql_agent,
)
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from langchain_openai import ChatOpenAI


@pytest.fixture
def temp_query_files():
    """Create temporary query files and metadata for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create temporary query files
        query_dir = Path(temp_dir) / "queries"
        query_dir.mkdir()

        # Create example SQL query files
        queries = {
            "query1.sql": "SELECT * FROM trades WHERE year = 2020 LIMIT 5;",
            "query2.sql": "SELECT product, SUM(value) FROM exports GROUP BY product;",
        }

        for filename, content in queries.items():
            with open(query_dir / filename, "w") as f:
                f.write(content)

        # Create queries.json metadata file
        metadata = [
            {"question": "Show me the top 5 trades from 2020", "file": "query1.sql"},
            {
                "question": "What are the total exports by product?",
                "file": "query2.sql",
            },
        ]

        metadata_file = Path(temp_dir) / "queries.json"
        with open(metadata_file, "w") as f:
            json.dump(metadata, f)

        yield {
            "temp_dir": temp_dir,
            "query_dir": query_dir,
            "metadata_file": metadata_file,
            "expected_queries": queries,
            "metadata": metadata,
        }


@pytest.fixture
def llm():
    """Create a GPT-4 language model for testing."""
    return ChatOpenAI(model="gpt-4o-mini", temperature=0)


@pytest.fixture
def project_paths(base_dir):
    """Fixture providing paths to actual project files."""
    return {
        "queries_json": base_dir / "src/example_queries/queries.json",
        "example_queries_dir": base_dir / "src/example_queries",
    }


@pytest.fixture
def mock_db():
    """Create a mock database for testing."""
    mock = Mock(spec=SQLDatabaseWithSchemas)
    
    # Create a string response that simulates database output
    mock_response = str([{"result": "mock data", "value": 100}])
    
    # Set return_value instead of side_effect to ensure we get a string
    mock.run.return_value = mock_response
    mock.run_no_throw.return_value = mock_response
    
    return mock


class TestLoadExampleQueries:
    def test_successful_load(self, temp_query_files):
        """Test successful loading of example queries."""
        result = load_example_queries(
            temp_query_files["metadata_file"], temp_query_files["query_dir"]
        )

        assert len(result) == 2
        assert result[0]["question"] == "Show me the top 5 trades from 2020"
        assert (
            result[0]["query"].strip()
            == temp_query_files["expected_queries"]["query1.sql"]
        )
        assert result[1]["question"] == "What are the total exports by product?"
        assert (
            result[1]["query"].strip()
            == temp_query_files["expected_queries"]["query2.sql"]
        )

    def test_load_with_empty_directory(self, temp_query_files):
        """Test loading when query directory is empty."""
        # Remove all files from query directory
        for file in temp_query_files["query_dir"].iterdir():
            file.unlink()

        with pytest.raises(FileNotFoundError):
            load_example_queries(
                temp_query_files["metadata_file"], temp_query_files["query_dir"]
            )

    def test_load_with_missing_metadata_file(self, temp_query_files):
        """Test loading when metadata file is missing."""
        temp_query_files["metadata_file"].unlink()

        with pytest.raises(FileNotFoundError):
            load_example_queries(
                temp_query_files["metadata_file"], temp_query_files["query_dir"]
            )

    def test_load_with_invalid_json(self, temp_query_files):
        """Test loading when metadata file contains invalid JSON."""
        with open(temp_query_files["metadata_file"], "w") as f:
            f.write("invalid json content")

        with pytest.raises(json.JSONDecodeError):
            load_example_queries(
                temp_query_files["metadata_file"], temp_query_files["query_dir"]
            )

    def test_load_with_missing_query_file(self, temp_query_files):
        """Test loading when a referenced query file is missing."""
        # Add entry for non-existent file to metadata
        metadata = temp_query_files["metadata"].copy()
        metadata.append({"question": "Missing query", "file": "missing.sql"})

        with open(temp_query_files["metadata_file"], "w") as f:
            json.dump(metadata, f)

        with pytest.raises(FileNotFoundError):
            load_example_queries(
                temp_query_files["metadata_file"], temp_query_files["query_dir"]
            )


class TestProjectFiles:
    """Integration tests for actual project files."""

    def test_queries_json_exists(self, project_paths):
        """Test that queries.json exists in the expected location."""
        assert project_paths[
            "queries_json"
        ].exists(), f"queries.json not found at {project_paths['queries_json']}"

    def test_example_queries_dir_exists(self, project_paths):
        """Test that example_queries directory exists."""
        assert project_paths[
            "example_queries_dir"
        ].exists(), f"example_queries directory not found at {project_paths['example_queries_dir']}"

    def test_queries_json_is_valid(self, project_paths):
        """Test that queries.json contains valid JSON and expected structure."""
        with open(project_paths["queries_json"], "r") as f:
            data = json.load(f)

        assert isinstance(data, list), "queries.json should contain a list"
        for entry in data:
            assert "question" in entry, "Each entry should have a 'question' field"
            assert "file" in entry, "Each entry should have a 'file' field"

    def test_all_referenced_sql_files_exist(self, project_paths):
        """Test that all SQL files referenced in queries.json exist."""
        with open(project_paths["queries_json"], "r") as f:
            data = json.load(f)

        for entry in data:
            sql_file = project_paths["example_queries_dir"] / entry["file"]
            assert sql_file.exists(), f"Referenced SQL file not found: {sql_file}"

    def test_load_actual_example_queries(self, project_paths):
        """Test that example queries can be successfully loaded from actual project files."""
        result = load_example_queries(
            project_paths["queries_json"], project_paths["example_queries_dir"]
        )

        assert len(result) > 0, "Should load at least one example query"
        for entry in result:
            assert "question" in entry
            assert "query" in entry
            assert len(entry["query"]) > 0, "SQL query should not be empty"
            # Verify basic SQL structure - first non-comment line should be SELECT
            query_lines = [line.strip() for line in entry["query"].split("\n")]
            non_comment_lines = [
                line for line in query_lines if line and not line.startswith("--")
            ]
            assert (
                non_comment_lines[0].upper().startswith("SELECT")
            ), f"First non-comment line should start with SELECT: {entry['query']}"


class TestCreateQueryGenerationChain:
    @pytest.mark.integration  # Mark as integration test since it uses real LLM
    def test_chain_creation(self, project_paths):
        """Test successful creation of query generation chain with actual LLM."""
        # Load actual examples
        example_queries = load_example_queries(
            project_paths["queries_json"], project_paths["example_queries_dir"]
        )

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        chain = create_query_generation_chain(
            llm=llm,
            example_queries=example_queries,
            codes=None,
            top_k=5,
            table_info="Use the example queries to infer the table structure.",
        )
        assert chain is not None

        # Test chain invocation with real LLM
        result = chain.invoke(
            {
                "question": "What are the top US exports by value?"
            }
        )

        assert isinstance(result, str)
        assert "SELECT" in result.upper()
        assert "LIMIT 5" in result.upper()

    def test_chain_with_empty_examples(self, project_paths):
        """Test chain creation with empty example queries."""
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        chain = create_query_generation_chain(llm, [])
        assert chain is not None

        result = chain.invoke(
            {
                "question": "What are the top exports?",
                "top_k": 5,
                "table_info": "exports(date, country, product, value)",
            }
        )

        assert isinstance(result, str)
        assert "SELECT" in result.upper()


class TestQueryTool:
    def test_core_functionality(self, llm, mock_db, project_paths, logger):
        """Test basic query generation and execution functionality of QueryTool."""
        example_queries = load_example_queries(
            project_paths["queries_json"], project_paths["example_queries_dir"]
        )

        # Mock the database response
        mock_db.run.return_value = [{"result": "mock data"}]
        
        # Create the tool
        tool = create_query_tool(
            llm=llm,
            db=mock_db,
            example_queries=example_queries,
            table_info="trades(date, country, value)",
            top_k_per_query=5,
        )

        # Modify the test to expect a string result instead of a list
        result = tool.invoke({"question": "Show me the top trades"})
        logger.info(f"Result: {result}")
        assert isinstance(result, str)  # Changed from list to str
        assert "mock data" in result

    def test_max_queries(self, llm, mock_db, project_paths):
        """Test that QueryTool enforces maximum query limit."""
        example_queries = load_example_queries(
            project_paths["queries_json"], project_paths["example_queries_dir"]
        )

        tool = create_query_tool(
            llm=llm,
            db=mock_db,
            example_queries=example_queries,
            table_info="trades(date, country, value)",
            max_uses=2,
        )

        # Execute queries up to and beyond limit
        tool.invoke({"question": "Query 1"})
        tool.invoke({"question": "Query 2"})
        result = tool.invoke({"question": "Query 3"})

        assert result == "Error: Maximum number of queries exceeded."


class TestCreateSQLAgent:
    def test_agent_creation_and_execution(self, llm, mock_db, project_paths, logger):
        """Test creation and basic execution of SQL agent."""
        example_queries = load_example_queries(
            project_paths["queries_json"], project_paths["example_queries_dir"]
        )
        # Create agent
        agent = create_sql_agent(
            llm=llm,
            db=mock_db,
            example_queries=example_queries,
            table_info="trades(date, country, value)",
            top_k_per_query=5,
            max_uses=3,
        )

        # Initialize with proper message structure
        result = agent.invoke({
            "messages": [{"content": "What is the total trade value?", "role": "user"}]
        })
        logger.info(f"Result: {result}")
        assert result is not None

    @pytest.mark.integration
    def test_integration_with_real_llm(self, mock_db, project_paths):
        """Test agent with real LLM (marked as integration test)."""
        example_queries = load_example_queries(
            project_paths["queries_json"], project_paths["example_queries_dir"]
        )

        # Setup mock database response
        mock_db.run.return_value = [
            {"country": "US", "value": 1000000},
            {"country": "UK", "value": 800000},
        ]

        # Create agent with real LLM
        llm = ChatOpenAI(model="gpt-4", temperature=0)
        agent = create_sql_agent(
            llm=llm,
            db=mock_db,
            example_queries=example_queries,
            table_info="trades(date, country, value)",
            top_k_per_query=5,
        )

        # Initialize with proper message structure
        result = agent.invoke({
            "messages": [{"content": "What are our top trading partners by value?", "role": "user"}]
        })

        assert result is not None
        assert mock_db.run.call_count <= 3  # Should not exceed max_uses
