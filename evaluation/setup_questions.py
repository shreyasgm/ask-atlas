#!/usr/bin/env python3
"""
Initial setup script that:
1. Sets up directory structure for each question
2. Creates question.json files
3. Generates initial placeholder SQL queries using GPT-4
"""

import asyncio
import openai
import backoff
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel
import json

from utils import (
    BASE_DIR,
    load_json_file,
    save_json_file,
    setup_directories,
    logging,
)
from src.config import get_settings

# Load settings
settings = get_settings()


class SQLQuery(BaseModel):
    query: str


class SQLResponse(BaseModel):
    plan: Optional[str] = None
    queries: List[SQLQuery]


@backoff.on_exception(backoff.expo, openai.RateLimitError)
async def call_openai_api(
    user_question: str, db_descriptions_text: str, db_structure_text: str
) -> dict:
    """Calls OpenAI API to generate initial SQL queries."""
    client = openai.AsyncOpenAI()
    try:
        # Read base system prompt
        SYSTEM_PROMPT = Path(BASE_DIR / "evaluation/system_prompt.md").read_text()

        # Add database schema information
        SYSTEM_PROMPT = (
            SYSTEM_PROMPT
            + f"\n\nDatabase Table Descriptions:\n{db_descriptions_text}\n\n"
            + f"Database Schema Structure:\n{db_structure_text}"
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Generate SQL queries for: {user_question}"},
        ]

        completion = await client.beta.chat.completions.parse(
            model=settings.query_model,
            messages=messages,
            response_format=SQLResponse,
        )

        response = completion.choices[0].message.parsed
        return {
            "plan": response.plan,
            "queries": [query.model_dump() for query in response.queries],
        }

    except Exception as e:
        logging.error(f"Error during OpenAI API call: {str(e)}")
        raise


async def setup_question(
    question: dict, categories: dict, db_descriptions_text: str, db_structure_text: str
) -> None:
    """Sets up initial structure and files for a single question."""
    try:
        question_id = str(
            question["id"]
        )  # Use existing ID from JSON, convert to string
        logging.info(f"Setting up question {question_id}: {question['text']}")

        # Create directory structure
        dirs = setup_directories(question_id)

        # Save question.json with updated structure
        question_json = {
            "question_id": question_id,
            "user_question": question["text"],
            "category": categories[question["category_id"]]["name"],
            "difficulty": question["difficulty"],
        }
        save_json_file(dirs["question_dir"] / "question.json", question_json)

        # Check if SQL files already exist
        existing_sql_files = list(dirs["queries_dir"].glob("*.sql"))
        if existing_sql_files:
            logging.info(
                f"SQL queries already exist for question {question_id}, skipping generation"
            )
            return

        # Generate initial SQL queries only if no existing files
        llm_result = await call_openai_api(
            question["text"],
            db_descriptions_text,
            db_structure_text,
        )

        # Save plan if provided
        if llm_result.get("plan"):
            plan_filepath = dirs["queries_dir"] / "plan.txt"
            plan_filepath.write_text(llm_result["plan"], encoding="utf-8")

        # Save initial queries
        for idx, query_obj in enumerate(llm_result.get("queries", []), start=1):
            query_filepath = dirs["queries_dir"] / f"{idx:02d}.sql"
            query_filepath.write_text(query_obj["query"], encoding="utf-8")

        logging.info(f"Completed setup for question {question_id}")

    except Exception as e:
        logging.error(f"Error setting up question {question_id}: {str(e)}")
        raise


async def main():
    logging.info("Starting evaluation setup...")

    # Load database schema files
    db_descriptions = load_json_file(BASE_DIR / "db_table_descriptions.json")
    db_structure = load_json_file(BASE_DIR / "db_table_structure.json")
    db_descriptions_text = json.dumps(db_descriptions, indent=2)
    db_structure_text = json.dumps(db_structure, indent=2)

    # Load questions and categories from eval_questions.json
    eval_data = load_json_file(BASE_DIR / "evaluation/eval_questions.json")

    # Create a dictionary of categories for easy lookup
    categories = {cat["id"]: cat for cat in eval_data["categories"]}

    # Process each question
    tasks = [
        setup_question(question, categories, db_descriptions_text, db_structure_text)
        for question in eval_data["questions"]
    ]
    await asyncio.gather(*tasks)

    logging.info("Evaluation setup complete.")


if __name__ == "__main__":
    asyncio.run(main())
