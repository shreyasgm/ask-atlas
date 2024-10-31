import pytest
from pathlib import Path
import json
import tempfile
from unittest.mock import Mock
from src.generate_query import load_example_queries, create_query_generation_chain
from langchain_core.language_models import BaseLanguageModel
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
def mock_llm():
    """Create a mock language model for testing."""
    mock = Mock(spec=BaseLanguageModel)
    mock.invoke.return_value = "SELECT * FROM mock_table LIMIT 5;"
    return mock


@pytest.fixture
def project_paths(base_dir):
    """Fixture providing paths to actual project files."""
    return {
        "queries_json": base_dir / "src/example_queries/queries.json",
        "example_queries_dir": base_dir / "src/example_queries",
    }


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
        chain = create_query_generation_chain(llm, example_queries)
        assert chain is not None

        # Test chain invocation with real LLM
        result = chain.invoke(
            {
                "question": "What are the top US exports by value?",
                "top_k": 5,
                "table_info": "Use the example queries to infer the table structure.",
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
