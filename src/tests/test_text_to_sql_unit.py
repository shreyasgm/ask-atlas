"""Unit tests for AtlasTextToSQL helpers that need no DB or LLM."""

import pytest

from src.text_to_sql import AtlasTextToSQL


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_json_loading(base_dir):
    """Test the JSON loading functionality."""
    test_file = base_dir / "src" / "schema" / "db_table_descriptions.json"

    result = AtlasTextToSQL._load_json_as_dict(test_file)
    assert isinstance(result, dict)
    assert len(result) > 0
