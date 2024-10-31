from typing import List, Dict
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers.openai_tools import PydanticToolsParser
from pydantic import BaseModel, Field
from langchain_core.runnables import Runnable

class SchemaList(BaseModel):
    """List of schemas in SQL database."""

    schemas: List[str] = Field(description="List of schema names.")


def create_schema_selection_chain(llm: BaseLanguageModel) -> Runnable:
    """
    Creates a chain that selects relevant schemas based on the user's question.
    Always includes the 'classification' schema and selects additional schema(s) based on the question.

    Args:
        llm: The language model to use for schema selection

    Returns:
        A chain that takes a question and returns a list of schema names
    """
    system = """
    Return the name(s) of the most relevant schema(s) from the postgres database for the user's question about trade data. For most questions, return only one schema, unless the user explicitly asks about multiple types of trade data. Never return more than two schemas.

    The available schemas in the postgres database are:
    
    hs92: Trade data for goods, in HS 1992 product classification
    hs12: Trade data for goods, in HS 2012 product classification
    sitc: Trade data for goods, in SITC product classification
    services_unilateral: Trade data for services products with exporter-product-year data. Use this schema if the user asks about services data for a specific country.
    services_bilateral: Trade data for services products with exporter-importer-product-year data. Use this schema if the user asks about services trade between two specific countries.
    
    If no specific product classification is mentioned in the query, use 'hs92' by default. If no HS product code aggregation level is mentioned, use 4-digit HS codes by default. Return ONLY the schema name(s) as specified above in the list of available schemas.
    
    Here are some examples of questions and the corresponding schemas to return:

    Question: "What did the US export in 2022, both in goods and services?"
    Schemas: ["hs92", "services_unilateral"]

    Question: "What goods did Brazil export to Ecuador in 2020, in HS 2012 product classification?"
    Schemas: ["hs12"]

    Question: "Which country had the highest market share of exports in fish products in 2021?"
    Schemas: ["hs92"]

    Question: "What were the top exports of India in 2013, in HS 2012 classification?"
    Schemas: ["hs12"]
    """

    prompt = ChatPromptTemplate.from_messages(
        [("system", system), ("human", "{question}")]
    )

    llm_with_tools = llm.bind_tools([SchemaList], tool_choice=True)
    output_parser = PydanticToolsParser(tools=[SchemaList])

    chain = prompt | llm_with_tools | output_parser

    def add_classification_schema(schemas: List[SchemaList]) -> List[str]:
        """Adds the classification schema and returns a flattened list of schema names."""
        # Flatten the schemas from each SchemaList object into a single list of schema names
        schema_names = ["classification"]
        for schema_obj in schemas:
            schema_names.extend(schema_obj.schemas)

        return schema_names

    return chain | add_classification_schema


def get_tables_in_schemas(schemas: List[str], db_schema: Dict) -> List[Dict]:
    """
    Gets all tables and their descriptions for the selected schemas.

    Args:
        schemas: List of schema names
        db_schema: Database schema information

    Returns:
        List of dictionaries containing table information with schema-qualified table_name and context_str
    """
    tables = []
    for schema in schemas:
        if schema in db_schema:
            for table in db_schema[schema]:
                # Create a new dict with schema-qualified table name
                tables.append({
                    "table_name": f"{schema}.{table['table_name']}",
                    "context_str": table['context_str']
                })
    return tables
