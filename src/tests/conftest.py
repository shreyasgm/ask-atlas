from pathlib import Path
from dotenv import load_dotenv
from langchain.globals import set_verbose, set_debug
import pytest
import logging

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[2]
print(f"BASE_DIR: {BASE_DIR}")
load_dotenv(BASE_DIR / ".env")

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require integration with external services (like LLM)",
    )
    
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require integration with external services (like LLM)",
    )

    # Set up logging configuration for stdout only
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler()  # This prints to stdout
        ],
    )

@pytest.fixture
def base_dir():
    return BASE_DIR

@pytest.fixture
def logger():
    """Fixture to provide a logger instance to tests."""
    return logging.getLogger("test_logger")


# set_verbose(True)
# set_debug(True)
