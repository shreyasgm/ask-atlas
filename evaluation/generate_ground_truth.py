#!/usr/bin/env python3
"""
Script to execute the manually verified SQL queries and generate ground truth results.
This script should be run after queries have been manually reviewed and corrected.
"""

import asyncio
import asyncpg
import decimal
from pathlib import Path
from typing import Tuple, List, Dict, Any

from utils import (
    BASE_DIR,  # noqa: F401
    EVALUATION_BASE_DIR,
    save_json_file,
    get_timestamp,
    logging,
)
from src.config import get_settings

# Load settings
settings = get_settings()

async def execute_sql_query(query: str, query_file_name: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Executes a single SQL query and returns results and execution log."""
    start_time = get_timestamp()
    execution_log = {
        "query_file": query_file_name,
        "start_time": start_time,
        "end_time": None,
        "status": None,
        "rows_returned": 0,
        "error_log": [],
    }
    
    conn = None
    try:
        conn = await asyncpg.connect(settings.atlas_db_url)
        rows = await conn.fetch(query)
        
        # Convert results to JSON-serializable format
        results = []
        for row in rows:
            row_dict = dict(row)
            for key, value in row_dict.items():
                if isinstance(value, decimal.Decimal):
                    row_dict[key] = float(value)
            results.append(row_dict)
            
        execution_log["status"] = "success"
        execution_log["rows_returned"] = len(results)
        
    except Exception as e:
        execution_log["status"] = "failure"
        execution_log["error_log"].append(str(e))
        logging.error(f"Error executing query {query_file_name}: {str(e)}")
        results = []
        
    finally:
        if conn:
            await conn.close()
        execution_log["end_time"] = get_timestamp()

    return results, execution_log

async def process_question_ground_truth(question_id: str) -> None:
    """Processes ground truth generation for a single question."""
    try:
        question_dir = EVALUATION_BASE_DIR / "questions" / question_id
        queries_dir = question_dir / "queries"
        ground_truth_dir = EVALUATION_BASE_DIR / "results" / question_id / "ground_truth"

        # Get all SQL files
        sql_files = sorted(queries_dir.glob("*.sql"))
        if not sql_files:
            logging.warning(f"No SQL files found for question {question_id}")
            return

        combined_results = []
        execution_logs = []

        # Execute each query
        for sql_file in sql_files:
            query = sql_file.read_text()
            results, exec_log = await execute_sql_query(query, sql_file.name)
            combined_results.extend(results)
            execution_logs.append(exec_log)

        # Save ground truth results
        ground_truth = {
            "question_id": question_id,
            "execution_timestamp": get_timestamp(),
            "results": {"data": combined_results},
            "execution_stats": {
                "queries_executed": len(sql_files),
            }
        }

        save_json_file(ground_truth_dir / "results.json", ground_truth)
        save_json_file(ground_truth_dir / "execution_log.json", 
                      {"steps": execution_logs, "error_log": []})

        logging.info(f"Generated ground truth for question {question_id}")

    except Exception as e:
        logging.error(f"Error processing ground truth for {question_id}: {str(e)}")
        raise

async def main():
    logging.info("Starting ground truth generation...")

    # Get all question directories
    question_dirs = sorted(Path(EVALUATION_BASE_DIR / "questions").glob("Q*"))
    
    # Process each question
    tasks = [
        process_question_ground_truth(question_dir.name)
        for question_dir in question_dirs
    ]
    await asyncio.gather(*tasks)

    logging.info("Ground truth generation complete.")

if __name__ == "__main__":
    asyncio.run(main()) 