from pathlib import Path
from dotenv import load_dotenv
from langchain.globals import set_verbose, set_debug
import pytest

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[2]
print(f"BASE_DIR: {BASE_DIR}")
load_dotenv(BASE_DIR / ".env")

@pytest.fixture
def base_dir():
    return BASE_DIR

# set_verbose(True)
# set_debug(True)
