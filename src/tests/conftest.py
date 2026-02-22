import logging
import os
from pathlib import Path

from dotenv import load_dotenv
import pytest

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require integration with external services (like LLM)",
    )
    config.addinivalue_line(
        "markers",
        "db: marks tests that require a live database connection",
    )
    config.addinivalue_line(
        "markers",
        "eval: marks eval-based integration tests (real LLM + production DB + LLM-as-judge)",
    )

    # Set up logging configuration for stdout only
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler()  # This prints to stdout
        ],
    )

@pytest.fixture(scope="session")
def base_dir():
    return BASE_DIR

@pytest.fixture
def logger():
    """Fixture to provide a logger instance to tests."""
    return logging.getLogger("test_logger")

@pytest.fixture
def db_available():
    """Skip test if database is not available."""
    from src.config import get_settings
    settings = get_settings()
    if not settings.atlas_db_url:
        pytest.skip("ATLAS_DB_URL not configured in settings")
    return settings.atlas_db_url


@pytest.fixture
def checkpoint_db_url():
    """Provide the app-db URL for persistence integration tests."""
    return os.environ.get(
        "CHECKPOINT_DB_URL",
        "postgresql://ask_atlas_app:testpass@localhost:5434/ask_atlas_app",
    )
