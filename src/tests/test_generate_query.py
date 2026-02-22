import pytest
from pathlib import Path
import json
import tempfile
from unittest.mock import Mock
from src.generate_query import (
    _classification_tables_for_schemas,
    _query_tool_schema,
    get_tables_in_schemas,
    load_example_queries,
    load_table_descriptions,
    get_table_info_for_schemas,
    QueryToolInput,
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


class TestGetTablesInSchemas:
    """Tests for get_tables_in_schemas — maps (table_descriptions, schemas) → schema-qualified table dicts."""

    TABLE_DESCRIPTIONS = {
        "hs92": [
            {"table_name": "country_year", "context_str": "Year-level HS92 data"},
            {"table_name": "country_product_year_4", "context_str": "4-digit product trade"},
        ],
        "sitc": [
            {"table_name": "country_year", "context_str": "Year-level SITC data"},
        ],
        "classification": [
            {"table_name": "location_country", "context_str": "Countries"},
        ],
    }

    def test_single_schema_qualifies_table_names(self):
        """Tables should be returned with schema prefix (e.g. hs92.country_year)."""
        result = get_tables_in_schemas(self.TABLE_DESCRIPTIONS, ["hs92"])
        names = [t["table_name"] for t in result]
        assert names == ["hs92.country_year", "hs92.country_product_year_4"]

    def test_single_schema_preserves_context_str(self):
        """context_str must pass through unchanged."""
        result = get_tables_in_schemas(self.TABLE_DESCRIPTIONS, ["hs92"])
        assert result[0]["context_str"] == "Year-level HS92 data"

    def test_multiple_schemas_combined(self):
        """Passing two schemas should return tables from both."""
        result = get_tables_in_schemas(self.TABLE_DESCRIPTIONS, ["hs92", "sitc"])
        names = {t["table_name"] for t in result}
        assert "hs92.country_year" in names
        assert "hs92.country_product_year_4" in names
        assert "sitc.country_year" in names

    def test_missing_schema_returns_nothing(self):
        """A schema key absent from table_descriptions should produce no tables."""
        result = get_tables_in_schemas(self.TABLE_DESCRIPTIONS, ["nonexistent"])
        assert result == []

    def test_empty_schemas_returns_empty(self):
        result = get_tables_in_schemas(self.TABLE_DESCRIPTIONS, [])
        assert result == []

    def test_classification_schema_treated_like_any_other(self):
        """'classification' is a valid key — its tables should be schema-qualified too."""
        result = get_tables_in_schemas(self.TABLE_DESCRIPTIONS, ["classification"])
        assert result == [{"table_name": "classification.location_country", "context_str": "Countries"}]


class TestQueryToolSchema:
    """Tests for the _query_tool_schema tool definition used by the LLM agent."""

    def test_tool_name_is_query_tool(self):
        """The agent graph routes on tool name — renaming breaks the pipeline."""
        assert _query_tool_schema.name == "query_tool"

    def test_args_schema_has_question_field(self):
        """The pipeline extracts tool_calls[0]['args']['question']; schema must match."""
        fields = QueryToolInput.model_fields
        assert "question" in fields


class TestClassificationTablesForSchemas:
    """Tests for the _classification_tables_for_schemas helper."""

    TABLE_DESCRIPTIONS = {
        "hs92": [
            {"table_name": "country_year", "context_str": "Year-level data"},
        ],
        "classification": [
            {"table_name": "location_country", "context_str": "Country-level data."},
            {"table_name": "product_hs92", "context_str": "HS92 products."},
            {"table_name": "product_hs12", "context_str": "HS12 products."},
            {"table_name": "product_sitc", "context_str": "SITC products."},
        ],
    }

    def test_hs92_returns_location_and_product_table(self):
        result = _classification_tables_for_schemas(["hs92"], self.TABLE_DESCRIPTIONS)
        names = {t["table_name"] for t in result}
        assert "classification.location_country" in names
        assert "classification.product_hs92" in names

    def test_always_includes_location_country(self):
        result = _classification_tables_for_schemas(["hs92"], self.TABLE_DESCRIPTIONS)
        names = {t["table_name"] for t in result}
        assert "classification.location_country" in names

    def test_no_duplicate_location_country_for_multiple_schemas(self):
        result = _classification_tables_for_schemas(["hs92", "hs12"], self.TABLE_DESCRIPTIONS)
        location_count = sum(1 for t in result if t["table_name"] == "classification.location_country")
        assert location_count == 1

    def test_multiple_schemas_get_respective_product_tables(self):
        result = _classification_tables_for_schemas(["hs92", "sitc"], self.TABLE_DESCRIPTIONS)
        names = {t["table_name"] for t in result}
        assert "classification.product_hs92" in names
        assert "classification.product_sitc" in names

    def test_empty_schemas_still_includes_location_country(self):
        result = _classification_tables_for_schemas([], self.TABLE_DESCRIPTIONS)
        names = {t["table_name"] for t in result}
        assert "classification.location_country" in names
        assert len(result) == 1

    def test_unknown_schema_only_includes_location(self):
        result = _classification_tables_for_schemas(["nonexistent"], self.TABLE_DESCRIPTIONS)
        names = {t["table_name"] for t in result}
        assert "classification.location_country" in names
        assert len(result) == 1


class TestGetTableInfoForSchemas:
    def test_get_table_info_for_schemas(self, mock_db, project_paths):
        """Test if table information can be retrieved for given schemas"""
        table_descriptions = load_table_descriptions(
            project_paths["table_descriptions_json"]
        )
        # Set the return value of get_table_info to a mock string
        mock_db.get_table_info.return_value = "Mock table info with columns: id, name, value"

        # Test with sample schemas
        sample_schemas = ["hs92"]
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
        assert "hs92." in table_info

    def test_includes_classification_tables_for_hs92(self, mock_db, project_paths):
        """Passing only hs92 should auto-include classification.location_country and classification.product_hs92."""
        table_descriptions = load_table_descriptions(
            project_paths["table_descriptions_json"]
        )
        mock_db.get_table_info.return_value = "CREATE TABLE placeholder (\n  id integer\n);\n"

        table_info = get_table_info_for_schemas(
            db=mock_db,
            table_descriptions=table_descriptions,
            classification_schemas=["hs92"],
        )

        assert "classification.location_country" in table_info
        assert "classification.product_hs92" in table_info



