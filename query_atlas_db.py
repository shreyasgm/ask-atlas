import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from llama_index.core.indices.struct_store.sql_query import SQLTableRetrieverQueryEngine
from llama_index.core import (
    VectorStoreIndex,
    PromptTemplate,
    Settings,
    set_global_handler,
)
from llama_index.core.prompts.prompt_type import PromptType
from llama_index.core.objects import SQLTableNodeMapping, SQLTableSchema, ObjectIndex
from llama_index.core.chat_engine import CondenseQuestionChatEngine
from llama_index.llms.openai import OpenAI
import openai
import json
from pathlib import Path
from sqlalchemy import inspect
import logging
import sys
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
import sqlalchemy.exc
from sqlalchemy import text


# Set up logging
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logging.getLogger().addHandler(logging.StreamHandler(stream=sys.stdout))

# Set up global callback handler
set_global_handler("simple")


# Define BASE_DIR (assuming it's the directory where the script is located)
BASE_DIR = Path(__file__).resolve().parent

# Initialize the llm
Settings.llm = OpenAI(
    model="gpt-4o",
    temperature=0.2,
)


@st.cache_resource(ttl=3600, show_spinner=False)
def init_db():
    try:
        with st.spinner("Connecting to Atlas Database..."):
            # Create the connection using the secrets from Streamlit's secrets.toml
            db_connection_string = st.secrets["ATLAS_DB_URL"]
            engine = create_engine(
                db_connection_string, 
                execution_options={"postgresql_readonly": True},
                connect_args={"connect_timeout": 10}
            )

            # Test the connection
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))

            # Create a session factory
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

            return engine, SessionLocal

    except sqlalchemy.exc.SQLAlchemyError:
        st.error("Unable to connect to the Atlas Database. Please try again later.")
        logging.error("Failed to connect to Atlas Database", exc_info=True)
        st.stop()


def debug_table_names(engine, chosen_schema="hs92"):
    inspector = inspect(engine)

    # Print all schemas
    schemas = inspector.get_schema_names()
    print(f"Schemas available: {schemas}")

    # Loop through all schemas and print tables and views for each
    for schema in schemas:
        print(f"\nSchema: {schema}")

        # Get and print table names in the current schema
        table_names = inspector.get_table_names(schema=schema)
        if table_names:
            print(f"  Tables in '{schema}': {table_names}")
        else:
            print(f"  No tables found in schema '{schema}'")

        # Get and print view names in the current schema
        view_names = inspector.get_view_names(schema=schema)
        if view_names:
            print(f"  Views in '{schema}': {view_names}")
        else:
            print(f"  No views found in schema '{schema}'")

    # Specifically print tables and views for the chosen schema (e.g., 'hs92')
    if chosen_schema in schemas:
        print(f"\nTables and views in the chosen schema '{chosen_schema}':")

        # Get and print table names in the chosen schema
        table_names = inspector.get_table_names(schema=chosen_schema)
        print(f"  Tables in '{chosen_schema}': {table_names}")

        # Get and print view names in the chosen schema
        view_names = inspector.get_view_names(schema=chosen_schema)
        print(f"  Views in '{chosen_schema}': {view_names}")
    else:
        print(f"\nSchema '{chosen_schema}' not found.")


# Set Streamlit page configuration
st.set_page_config(
    page_title="Ask-Atlas",
    page_icon="🌍",
    layout="centered",
    initial_sidebar_state="auto",
)

# Title of the app
st.title("Ask-Atlas 🌍: Your Trade Data Assistant")

# Display some information
st.info(
    """
    Welcome to Ask-Atlas, a chatbot that provides insights from the [Atlas of Economic Complexity](https://atlas.cid.harvard.edu/) using trade data sourced from UN COMTRADE and cleaned and processed by the [Growth Lab at Harvard University](https://growthlab.hks.harvard.edu/).

    Created by: [Shreyas Gadgin Matha](https://growthlab.hks.harvard.edu/people/shreyas-matha)
    """
)

# Load OpenAI API Key from Streamlit secrets
openai.api_key = st.secrets["OPENAI_API_KEY"]

# Initialize the database connection
engine, SessionLocal = init_db()

# Load the JSON schema from BASE_DIR / "postgres_db_schema.json"
with open(BASE_DIR / "postgres_db_schema.json", "r") as f:
    db_schema_data = json.load(f)


# Function to load the LlamaIndex data
@st.cache_resource
def init_query_engine(test: bool = False):
    # Optional tables to include for testing
    testing_tables_to_use = {
        "hs92": [
            "country_product_year_1",
            "country_product_year_2",
            "country_product_year_4",
            "country_country_product_year_1",
            "country_country_product_year_2",
            "country_country_product_year_4",
            "country_year",
            "product_year_1",
            "product_year_2",
            "product_year_4",
        ],
        "classification": ["location_country", "product_hs92"],
    }

    if test:
        chosen_schemas = ["hs92", "classification"]
        # # Call the debug function to check
        # debug_table_names(engine, chosen_schema=chosen_schema)

        # Create the SQLDatabase object with schema information
        sql_database = SQLDatabaseWithSchemas(
            engine,
            schemas=chosen_schemas,
            include_tables=[
                f"{schema}.{table}"
                for schema, tables in testing_tables_to_use.items()
                for table in tables
            ],
        )
        table_schema_objs = [
            SQLTableSchema(
                table_name=f"{schema}.{table['table_name']}",
                context_str=table["context_str"],
            )
            for schema, tables in db_schema_data.items()
            for table in tables
            if table["table_name"] in testing_tables_to_use.get(schema, [])
        ]
    else:
        # Create the SQLDatabase object for the full schema
        schemas = list(db_schema_data.keys())
        sql_database = SQLDatabaseWithSchemas(engine, schemas)

        table_schema_objs = [
            SQLTableSchema(
                table_name=f"{schema}.{table['table_name']}",
                context_str=table["context_str"],
            )
            for schema, tables in db_schema_data.items()
            for table in tables
        ]

    # Define the node mapping
    table_node_mapping = SQLTableNodeMapping(sql_database)

    # Create the object index for querying
    obj_index = ObjectIndex.from_objects(
        table_schema_objs, table_node_mapping, VectorStoreIndex
    )

    # Prepare the text to sql prompt
    TRADE_DATA_TEXT_TO_SQL_TMPL = (
        "Given an input question, first create a syntactically correct postgresql "
        "query to run, then look at the results of the query and return the answer. "
        "You can order the results by a relevant column if needed.\n\n "
        "Unless otherwise specified by the user, use the HS 1992 product classification system.\n\n"
        "Note that tables are schema-qualified. For example:\n."
        "- classification.location_country and classification.product_hs92 contain reference data about countries and products respectively.\n"
        "- 'hs92.country_product_*' tables contain data about country exports and imports for specific products and years, for the HS92 classification system.\n"
        "- 'hs92.country_year' contains data about countries in specific years, without product-specific information in the table itself, but derived from underlying data based on the HS92 classification system.\n"
        "- 'hs92.country_country_product_*' tables contain data about bilateral trade flows between countries, for specific products and years, for the HS92 classification system.\n"
        "Note that country ID and product ID are internal ID's and not the ISO country codes or SITC/HS product codes.\n\n"
        "Never query for all the columns from a specific table, only ask for a "
        "few relevant columns based on the user's question.\n\n"
        "Pay attention to use only the column names that you can see in the schema description. "
        "Be careful to not query for columns that do not exist. "
        "Make sure to check which column belongs to which table. "
        "Also, qualify column names with the table name when needed. \n"
        "Make sure to only provide your response based on the result of the SQL query. "
        "Do not answer based on your prior knowledge, and do not make up a generic answer.\n\n"
        "Use the following format for your output, each line following the structure below:\n\n"
        "Question: Question here\n"
        "SQLQuery: SQL Query to run\n"
        "SQLResult: Result of the SQLQuery\n"
        "Answer: Final answer here\n\n"
        "Only use tables listed below.\n"
        "{schema}\n\n"
        "Question: {query_str}\n"
        "SQLQuery: "
    )

    TRADE_DATA_TEXT_TO_SQL_PROMPT = PromptTemplate(
        TRADE_DATA_TEXT_TO_SQL_TMPL,
        prompt_type=PromptType.TEXT_TO_SQL,
    )

    # Initialize the query engine
    query_engine = SQLTableRetrieverQueryEngine(
        sql_database=sql_database,
        text_to_sql_prompt=TRADE_DATA_TEXT_TO_SQL_PROMPT,
        table_retriever=obj_index.as_retriever(similarity_top_k=6),
        streaming=True,
    )

    return query_engine


query_engine = init_query_engine(test=True)


# Define the chat engine using LlamaIndex
def init_chat_engine(query_engine):
    # Custom Prompt Template for condensing user queries
    custom_prompt = PromptTemplate(
        """
        Given a conversation (between Human and Assistant) and a follow-up message from Human, 
        rewrite the message to be a standalone question that captures all relevant context.
        
        <Chat History>
        {chat_history}

        <Follow Up Message>
        {question}

        <Standalone question>
        """
    )

    # Initialize chat engine
    chat_engine = CondenseQuestionChatEngine.from_defaults(
        query_engine=query_engine, condense_question_prompt=custom_prompt, verbose=True
    )
    return chat_engine


# Initialize message history for the chat
if "messages" not in st.session_state:
    st.session_state["messages"] = [
        {
            "role": "assistant",
            "content": "Hello! Ask me a question about trade data from the Atlas of Economic Complexity.",
        }
    ]

# Initialize the chat engine
if "chat_engine" not in st.session_state.keys():
    st.session_state.chat_engine = chat_engine = init_chat_engine(query_engine)

# Get user input for questions
if prompt := st.chat_input("Ask a question about trade data"):
    # Append user message to the session state
    st.session_state["messages"].append({"role": "user", "content": prompt})

# Display the chat history in the UI
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

# If last message is not from assistant, generate a new response
if st.session_state.messages[-1]["role"] != "assistant":
    # Process the input through the chat engine
    with st.chat_message("assistant"):
        # Streaming response from the chat engine
        response_stream = st.session_state.chat_engine.stream_chat(prompt)
        st.write_stream(response_stream.response_gen)

        # Capture the assistant's response and add it to the message history
        message = {"role": "assistant", "content": response_stream.response}
        st.session_state["messages"].append(message)
