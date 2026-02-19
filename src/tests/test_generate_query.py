import pytest
from pathlib import Path
import json
import tempfile
from unittest.mock import Mock
from src.generate_query import (
    load_example_queries,
    load_table_descriptions,
    get_table_info_for_schemas,
)
from src.sql_multiple_schemas import SQLDatabaseWithSchemas


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
def project_paths(base_dir):
    """Fixture providing paths to actual project files."""
    return {
        "queries_json": base_dir / "src/example_queries/queries.json",
        "example_queries_dir": base_dir / "src/example_queries",
        "table_descriptions_json": base_dir / "db_table_descriptions.json",
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


class TestLoadFiles:
    def test_successful_example_queries_load(self, temp_query_files):
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

    def test_successful_table_descriptions_load(self, tmp_path):
        """Test successful loading of table descriptions."""
        # Create a temporary table descriptions file
        table_desc = {
            "schema1": [
                {
                    "table_name": "table1",
                    "context_str": "Description of table1"
                }
            ]
        }
        desc_file = tmp_path / "table_descriptions.json"
        with open(desc_file, "w") as f:
            json.dump(table_desc, f)

        result = load_table_descriptions(desc_file)
        
        assert result == table_desc
        assert "schema1" in result
        assert len(result["schema1"]) == 1
        assert result["schema1"][0]["table_name"] == "table1"
        assert result["schema1"][0]["context_str"] == "Description of table1"


class TestGetTableInfoForSchemas:
    def test_get_table_info_for_schemas(self, mock_db, project_paths):
        """Test if table information can be retrieved for given schemas"""
        table_descriptions = load_table_descriptions(
            project_paths["table_descriptions_json"]
        )
        # Set the return value of get_table_info to a mock string
        mock_db.get_table_info.return_value = "Mock table info with columns: id, name, value"

        # Test with sample schemas
        sample_schemas = ["hs92", "classification"]
        table_info = get_table_info_for_schemas(
            db=mock_db,
            table_descriptions=table_descriptions,
            classification_schemas=sample_schemas
        )
        
        assert isinstance(table_info, str)
        assert len(table_info) > 0
        # Check if it properly filters out tables with 'group' in the name
        assert "group" not in table_info.lower()
        # Check if schema-qualified table names are present
        assert "hs92.product" in table_info
        assert "classification.product_services_bilateral" in table_info



