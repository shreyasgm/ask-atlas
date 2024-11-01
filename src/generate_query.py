from typing import List, Dict, Union
import json
from pathlib import Path
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import PromptTemplate, FewShotPromptTemplate
from langchain_core.output_parsers import StrOutputParser


def load_example_queries(
    queries_json: Union[str, Path], directory: Union[str, Path]
) -> List[Dict[str, str]]:
    """
    Loads example SQL queries from files in the specified directory and maps them to their questions.
    Returns a list of dictionaries, each containing a 'question' and its corresponding 'query'.

    Args:
        queries_json: Path to the queries.json file
        directory: Path to the directory containing the example SQL queries
    """
    # Load the queries.json file
    with open(Path(queries_json), "r") as f:
        query_metadata = json.load(f)

    # Create list of question-query pairs
    example_queries = []
    for entry in query_metadata:
        query_path = Path(directory) / entry["file"]
        with open(query_path, "r") as f:
            example_queries.append({"question": entry["question"], "query": f.read()})

    return example_queries


def _strip(text: str) -> str:
    return text.strip().replace("```sql", "").replace("```", "")


def create_query_generation_chain(
    llm: BaseLanguageModel,
    example_queries: List[Dict[str, str]],
):
    """
    Creates a chain that generates SQL queries based on the user's question.

    Args:
        llm: The language model to use for query generation
        example_queries: List of example SQL queries for reference

    Returns:
        A chain that generates SQL queries
    """
    prefix = """You are a SQL expert that writes queries for a postgres database containing trade data.
Given an input question, create a syntactically correct SQL query to answer the user's question. Unless otherwise specified, do not return more than {top_k} rows. Note that you should never use the location_level or partner_level columns in your query. Just ignore those columns.

Only use the tables and columns provided. Here is the relevant table information:
{table_info}

Below are some examples of user questions and their corresponding SQL queries.
    """

    example_prompt = PromptTemplate.from_template(
        "User question: {question}\nSQL query: {query}"
    )
    prompt = FewShotPromptTemplate(
        examples=example_queries,
        example_prompt=example_prompt,
        prefix=prefix,
        suffix="User question: {question}\nSQL query: ",
        input_variables=["question", "top_k", "table_info"],
    )

    # example_prompt = ChatPromptTemplate.from_messages(
    #     [("human", "{question}"), ("ai", "{query}")]
    # )
    # few_shot_prompt = FewShotChatMessagePromptTemplate(
    #     input_variables=["question", "top_k", "table_info"],
    #     examples=example_queries,
    #     example_prompt=example_prompt,
    # )
    # final_prompt = ChatPromptTemplate.from_messages(
    #     [
    #         ("system", prefix),
    #         few_shot_prompt,
    #         ("human", "{input}"),
    #     ]
    # )

    return prompt | llm | StrOutputParser() | _strip
