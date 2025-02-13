#!/usr/bin/env python3
"""
evaluation_dataset_generator.py

This script generates an evaluation dataset for a text-to-SQL system.
For each common question, it:
  - Sets up a directory structure.
  - Saves a question.json file.
  - Loads database schema information.
  - Builds a prompt (using static cached parts and a dynamic user question)
    to call Anthropic's Claude 3.5 Sonnet LLM for SQL generation.
  - Parses the LLM response and writes the SQL query/queries.
  - Executes the SQL queries against a Postgres database and saves the ground truth results.
  - Simulates an agent run and then compares the results with the ground truth.
  - Stores evaluation metrics.

Before running, ensure that:
  - The database schema files (db_table_descriptions.json and db_table_structure.json) exist.
  - PostgreSQL connection parameters are provided via environment variable ATLAS_DB_URL
  - The OpenAI API key is set appropriately in the environment variable OPENAI_API_KEY
"""

import os
import json
import datetime
import logging
import traceback
from pathlib import Path
import openai
import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
from typing import List, Optional
from pydantic import BaseModel

# Load environment variables
# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[1]
print(f"BASE_DIR: {BASE_DIR}")

# Load environment variables
load_dotenv(BASE_DIR / ".env")

# -----------------------------------------------------------------------------
# Global configuration
# -----------------------------------------------------------------------------

EVALUATION_BASE_DIR = BASE_DIR / "evaluation"
DB_DESCRIPTIONS_FILE = BASE_DIR / "db_table_descriptions.json"
DB_STRUCTURE_FILE = BASE_DIR / "db_table_structure.json"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# -----------------------------------------------------------------------------
# Utility Functions
# -----------------------------------------------------------------------------


def load_json_file(filepath):
    """Loads and returns JSON data from the given file path."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        logging.error(f"Error loading JSON file {filepath}: {str(e)}")
        raise


def save_json_file(filepath, data):
    """Saves the given data as JSON to the specified file path."""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logging.info(f"Saved JSON file: {filepath}")
    except Exception as e:
        logging.error(f"Error saving JSON file {filepath}: {str(e)}")
        raise


def get_timestamp():
    """Returns current timestamp as an ISO-formatted string."""
    return datetime.datetime.now(datetime.UTC).isoformat() + "Z"


def generate_question_id(seq_number):
    """
    Generates a unique question ID using the current date and a sequential number.
    Format: Q{YYYYMMDD}_{sequential_number:03d}
    """
    today = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    return f"Q{today}_{seq_number:03d}"


def setup_directories(question_id):
    """
    Given a question_id, create the following directory structure:
      evaluation/
        questions/{question_id}/ (with a queries/ subfolder)
        results/{question_id}/ground_truth/
                    /agent_runs/{timestamp}/
        evaluations/{question_id}/
    Returns a dictionary with paths to the created directories.
    """
    dirs = {}
    try:
        # Base directories
        dirs["question_dir"] = os.path.join(
            EVALUATION_BASE_DIR, "questions", question_id
        )
        dirs["queries_dir"] = os.path.join(dirs["question_dir"], "queries")
        dirs["results_dir"] = os.path.join(EVALUATION_BASE_DIR, "results", question_id)
        dirs["ground_truth_dir"] = os.path.join(dirs["results_dir"], "ground_truth")
        dirs["agent_runs_dir"] = os.path.join(dirs["results_dir"], "agent_runs")
        dirs["evaluation_dir"] = os.path.join(
            EVALUATION_BASE_DIR, "evaluations", question_id
        )

        for key, directory in dirs.items():
            os.makedirs(directory, exist_ok=True)
            logging.info(f"Directory created (or exists): {directory}")
    except Exception as e:
        logging.error(f"Error setting up directories for {question_id}: {str(e)}")
        raise
    return dirs



# -----------------------------------------------------------------------------
# Anthropic API / Prompt Caching Functions
# -----------------------------------------------------------------------------


class SQLQuery(BaseModel):
    filename: str
    query: str


class SQLResponse(BaseModel):
    plan: Optional[str] = None
    queries: List[SQLQuery]





def call_openai_api(
    user_question, db_descriptions_text, db_structure_text
):
    """
    Calls OpenAI API with structured output format using Pydantic.
    Returns a dictionary with the following structure:
    {
      "plan": "<plan text, if any>",
      "queries": [
         {"filename": "query.sql", "query": "<SQL query text>"},
         ... (if multiple queries are provided for a complex question)
      ]
    }
    """
    client = openai.OpenAI()
    try:
        # Read base system prompt
        SYSTEM_PROMPT = Path(BASE_DIR / "evaluation/system_prompt.md").read_text()
        
        # Add database schema information
        SYSTEM_PROMPT = SYSTEM_PROMPT + """

Database Table Descriptions:
The following text describes each table in the database and its purpose:

""" + db_descriptions_text + """

Database Schema Structure:
The following contains the detailed schema for each table, including columns, data types, and constraints:

""" + db_structure_text
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": f"Generate SQL queries for: {user_question}"
            }
        ]

        completion = client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=messages,
            response_format=SQLResponse,
        )
        
        response = completion.choices[0].message.parsed
        logging.info("OpenAI API call successful.")
        return {
            "plan": response.plan,
            "queries": [query.model_dump() for query in response.queries]
        }

    except Exception as e:
        logging.error("Error during OpenAI API call: " + str(e))
        traceback.print_exc()
        raise


# -----------------------------------------------------------------------------
# SQL Execution Functions
# -----------------------------------------------------------------------------


def execute_sql_query(query, query_file_name):
    """
    Executes a single SQL query against a Postgres database.

    Returns a tuple: (result_rows, execution_log)
      - result_rows: list of dictionaries representing rows
      - execution_log: dictionary with details of the execution (start_time, end_time, status, etc.)
    """
    start_time = datetime.datetime.utcnow()
    execution_log = {
        "query_file": query_file_name,
        "start_time": start_time.isoformat() + "Z",
        "end_time": None,
        "status": None,
        "rows_returned": 0,
        "error_log": [],
    }
    results = []

    try:
        with psycopg2.connect(os.getenv('ATLAS_DB_URL')) as conn:
            with conn.cursor(cursor_factory=DictCursor) as cursor:
                logging.info(f"Executing query from {query_file_name}")
                cursor.execute(query)
                try:
                    # Try to fetch results if any
                    results = [dict(row) for row in cursor.fetchall()]
                except psycopg2.ProgrammingError:
                    # No results to fetch (e.g. if the query was an update)
                    results = []
                execution_log["status"] = "success"
                execution_log["rows_returned"] = len(results)
    except Exception as e:
        execution_log["status"] = "failure"
        error_message = str(e)
        execution_log["error_log"].append(error_message)
        logging.error(
            f"Error executing SQL query from {query_file_name}: {error_message}"
        )
    finally:
        end_time = datetime.datetime.utcnow()
        execution_log["end_time"] = end_time.isoformat() + "Z"

    return results, execution_log


def execute_sql_queries(queries, dirs):
    """
    Given a list of queries (each with filename and query text) and directory information,
    execute each query against the database.

    Saves:
      - Combined results into {ground_truth_dir}/results.json
      - Execution logs into {ground_truth_dir}/execution_log.json

    Returns a tuple (combined_results, execution_logs)
    """
    combined_results = []
    execution_logs = []

    for query_obj in queries:
        filename = query_obj.get("filename", "query.sql")
        query_text = query_obj.get("query", "")
        query_filepath = os.path.join(dirs["queries_dir"], filename)
        try:
            # Save the SQL query to file
            with open(query_filepath, "w", encoding="utf-8") as f:
                f.write(query_text)
            logging.info(f"Saved query file: {query_filepath}")
        except Exception as e:
            logging.error(f"Error saving query file {query_filepath}: {str(e)}")
            continue

        # Execute the query
        results, exec_log = execute_sql_query(query_text, filename)
        combined_results.extend(results)
        execution_logs.append(exec_log)

    # Save combined results and logs in the ground truth directory
    results_json_path = os.path.join(dirs["ground_truth_dir"], "results.json")
    execution_log_path = os.path.join(dirs["ground_truth_dir"], "execution_log.json")

    ground_truth = {
        "question_id": dirs["question_dir"].split(os.sep)[-1],
        "execution_timestamp": get_timestamp(),
        "results": {"data": combined_results},
        "execution_stats": {
            "duration_ms": sum(
                (
                    datetime.datetime.fromisoformat(log["end_time"][:-1])
                    - datetime.datetime.fromisoformat(log["start_time"][:-1])
                ).total_seconds()
                * 1000
                for log in execution_logs
                if log["status"] == "success"
            ),
            "queries_executed": len(queries),
        },
    }

    try:
        save_json_file(results_json_path, ground_truth)
        save_json_file(execution_log_path, {"steps": execution_logs, "error_log": []})
    except Exception as e:
        logging.error("Error saving ground truth files: " + str(e))

    return ground_truth, execution_logs


# -----------------------------------------------------------------------------
# Agent Run Simulation and Evaluation Functions
# -----------------------------------------------------------------------------


def simulate_agent_run(ground_truth):
    """
    Simulates an agent run.
    For demonstration purposes, this function copies the ground truth query and results
    into a simulated agent run folder. In a real system, this would be replaced by the actual agent.

    Returns a dictionary with keys: "query", "results", "execution_log"
    """
    # For now, we simply duplicate the ground truth.
    simulated_run = {
        "query": "/* Simulated agent query, assumed same as ground truth */",
        "results": ground_truth.get("results", {}),
        "execution_log": {},  # In a real run, this would capture timing, etc.
    }
    return simulated_run


def evaluate_agent_run(ground_truth, agent_run):
    """
    Compares the ground truth and agent run results and produces evaluation metrics.
    This is a simulated evaluation that returns dummy metrics.

    Returns a dictionary with evaluation metrics.
    """
    evaluation = {
        "question_id": ground_truth.get("question_id", ""),
        "agent_run_timestamp": get_timestamp(),
        "evaluation_timestamp": get_timestamp(),
        "metrics": {
            "query_correctness": {
                "score": 0.95,
                "notes": [
                    "Correctly identifies required tables.",
                    "Minor differences in ORDER BY clause.",
                ],
            },
            "result_correctness": {"exact_match": True, "row_match_percentage": 100.0},
        },
        "llm_evaluation": {
            "score": 0.92,
            "reasoning": "The agent's query correctly implements the core logic.",
            "suggestions": ["Consider adding an index hint for improved performance."],
        },
    }
    return evaluation


# -----------------------------------------------------------------------------
# Main Orchestration Function
# -----------------------------------------------------------------------------


def main():
    logging.info("Starting evaluation dataset generation...")

    # Load database schema files
    try:
        db_descriptions = load_json_file(DB_DESCRIPTIONS_FILE)
        db_structure = load_json_file(DB_STRUCTURE_FILE)
        # Convert to a pretty-printed string for the prompt.
        db_descriptions_text = json.dumps(db_descriptions, indent=2)
        db_structure_text = json.dumps(db_structure, indent=2)
    except Exception as e:
        logging.error("Failed to load database schema files. Exiting.")
        return

    # Define a list of common questions for evaluation
    # Each question is a dictionary with required fields.
    questions = [
        {
            "user_question": "What were the top 10 goods and services exported from Bolivia to Morocco between 2010-2022?",
            "category": "trade",
        },
        {
            "user_question": "What is the total export value of goods for Bolivia in 2015?",
            "category": "trade",
        },
        # Add more questions as needed.
    ]

    # Process each question sequentially.
    for idx, question in enumerate(questions, start=1):
        try:
            question_id = generate_question_id(idx)
            logging.info(
                f"Processing question {question_id}: {question['user_question']}"
            )
            dirs = setup_directories(question_id)

            # Save the question.json file
            question_json = {
                "question_id": question_id,
                "user_question": question["user_question"],
                "category": question["category"],
            }
            question_json_path = os.path.join(dirs["question_dir"], "question.json")
            save_json_file(question_json_path, question_json)

            # Call the LLM to generate SQL (or plan and queries)
            llm_result = call_openai_api(
                question["user_question"],
                db_descriptions_text,
                db_structure_text,
            )

            # Save the plan (if any) as a separate file (optional)
            if llm_result.get("plan"):
                plan_filepath = os.path.join(dirs["queries_dir"], "plan.txt")
                with open(plan_filepath, "w", encoding="utf-8") as f:
                    f.write(llm_result["plan"])
                logging.info(f"Saved plan file: {plan_filepath}")

            # Save and execute queries
            ground_truth, exec_logs = execute_sql_queries(
                llm_result.get("queries", []), dirs
            )

            # Simulate an agent run (in a real scenario, run the system agent here)
            agent_run = simulate_agent_run(ground_truth)
            # Create a subfolder under agent_runs with a timestamp
            agent_timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            agent_run_dir = os.path.join(dirs["agent_runs_dir"], agent_timestamp)
            os.makedirs(agent_run_dir, exist_ok=True)
            # Save simulated agent files
            agent_query_path = os.path.join(agent_run_dir, "query.sql")
            with open(agent_query_path, "w", encoding="utf-8") as f:
                f.write(agent_run["query"])
            save_json_file(
                os.path.join(agent_run_dir, "results.json"), agent_run["results"]
            )
            save_json_file(
                os.path.join(agent_run_dir, "execution_log.json"),
                agent_run["execution_log"],
            )

            # Evaluate the agent run against the ground truth
            evaluation = evaluate_agent_run(ground_truth, agent_run)
            eval_filepath = os.path.join(
                dirs["evaluation_dir"], f"{agent_timestamp}.json"
            )
            save_json_file(eval_filepath, evaluation)

            logging.info(f"Finished processing question {question_id}")
        except Exception as e:
            logging.error(f"Error processing question {idx}: {str(e)}")
            traceback.print_exc()

    logging.info("Evaluation dataset generation complete.")


if __name__ == "__main__":
    main()
