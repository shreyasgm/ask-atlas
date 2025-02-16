#!/usr/bin/env python3
"""Shared utility functions for evaluation scripts."""

import json
import logging
import datetime
from pathlib import Path
from typing import Dict
from dotenv import load_dotenv

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[1]

# Load environment variables if needed
load_dotenv(BASE_DIR / ".env")

# Common constants
EVALUATION_BASE_DIR = BASE_DIR / "evaluation"
DB_DESCRIPTIONS_FILE = BASE_DIR / "db_table_descriptions.json"
DB_STRUCTURE_FILE = BASE_DIR / "db_table_structure.json"

# Configure logging
LOGS_DIR = EVALUATION_BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)  # Create logs directory if it doesn't exist

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOGS_DIR / "evaluation.log"),
        logging.StreamHandler()  # Keep console output as well
    ]
)

def load_json_file(filepath: Path) -> dict:
    """Loads and returns JSON data from the given file path."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading JSON file {filepath}: {str(e)}")
        raise

def save_json_file(filepath: Path, data: dict) -> None:
    """Saves the given data as JSON to the specified file path."""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logging.info(f"Saved JSON file: {filepath}")
    except Exception as e:
        logging.error(f"Error saving JSON file {filepath}: {str(e)}")
        raise

def get_timestamp() -> str:
    """Returns current timestamp as an ISO-formatted string."""
    return datetime.datetime.now(datetime.UTC).isoformat() + "Z"

def generate_question_id(seq_number: int) -> str:
    """Generates a unique question ID using a sequential number."""
    return f"Q{seq_number:03d}"

def setup_directories(question_id: str) -> Dict[str, Path]:
    """Creates evaluation directory structure for a question."""
    dirs = {}
    try:
        # Base directories
        dirs["question_dir"] = EVALUATION_BASE_DIR / "questions" / question_id
        dirs["queries_dir"] = dirs["question_dir"] / "queries"
        dirs["results_dir"] = EVALUATION_BASE_DIR / "results" / question_id
        dirs["ground_truth_dir"] = dirs["results_dir"] / "ground_truth"
        dirs["agent_runs_dir"] = dirs["results_dir"] / "agent_runs"
        dirs["evaluation_dir"] = EVALUATION_BASE_DIR / "evaluations" / question_id

        # Create directories
        for directory in dirs.values():
            directory.mkdir(parents=True, exist_ok=True)
            
    except Exception as e:
        logging.error(f"Error setting up directories for {question_id}: {str(e)}")
        raise
    return dirs 