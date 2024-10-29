from pathlib import Path
from dotenv import load_dotenv
from langchain.globals import set_verbose, set_debug

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[3]
print(f"BASE_DIR: {BASE_DIR}")
load_dotenv(BASE_DIR / ".env")

# set_verbose(True)
# set_debug(True)
