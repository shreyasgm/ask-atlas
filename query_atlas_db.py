import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from llama_index.core.indices.struct_store.sql_query import SQLTableRetrieverQueryEngine
from llama_index.core import SQLDatabase, VectorStoreIndex, PromptTemplate, Settings
from llama_index.core.objects import SQLTableNodeMapping, SQLTableSchema, ObjectIndex
from llama_index.core.chat_engine import CondenseQuestionChatEngine
from llama_index.llms.openai import OpenAI
import openai
import json
from pathlib import Path
from sqlalchemy import inspect

# Define BASE_DIR (assuming it's the directory where the script is located)
BASE_DIR = Path(__file__).resolve().parent

# Initialize the llm
Settings.llm = OpenAI(
    model="gpt-4o",
    temperature=0.2,
)


@st.cache_resource(ttl=3600)
def init_db():
    # Create the connection using the secrets from Streamlit's secrets.toml
    db_connection_string = st.secrets["ATLAS_DB_URL"]
    engine = create_engine(
        db_connection_string, execution_options={"postgresql_readonly": True}
    )

    # Create a session factory
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    return engine, SessionLocal


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
    Welcome to Ask-Atlas, a chatbot that provides insights from the Atlas of Economic Complexity using trade data sourced from UN COMTRADE and cleaned and processed by the Growth Lab at Harvard University.

    Created by: [Shreyas Gadgin Matha](https://growthlab.hks.harvard.edu/people/shreyas-matha)
    
    Learn more about the Growth Lab on our [website](https://growthlab.hks.harvard.edu/).
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
    testing_tables_to_use = [
        # "product_hs92",
        # "location_country",
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
    ]

    if test:
        chosen_schema = "hs92"
        # # Call the debug function to check
        # debug_table_names(engine, chosen_schema=chosen_schema)

        # Create the SQLDatabase object with schema information
        sql_database = SQLDatabase(
            engine,
            schema=chosen_schema,
            include_tables=testing_tables_to_use,
        )

        # Define all table schemas you want to use for the LlamaIndex
        table_schema_objs = [
            SQLTableSchema(
                table_name=table["table_name"],
                context_str=table["context_str"],
            )
            for schema, tables in db_schema_data.items()
            for table in tables
            if table["table_name"] in testing_tables_to_use and schema == chosen_schema
        ]
    else:
        # Create the SQLDatabase object for the full schema
        sql_database = SQLDatabase(engine)

        # Define all table schemas you want to use for the LlamaIndex
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

    # Initialize the query engine
    query_engine = SQLTableRetrieverQueryEngine(
        sql_database=sql_database,
        table_retriever=obj_index.as_retriever(similarity_top_k=4),
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
            "content": "Hello! Ask me anything about trade data from the Atlas of Economic Complexity.",
        }
    ]

# Sidebar info
with st.sidebar:
    st.header("About Ask-Atlas")
    st.write("""
    **Ask-Atlas** allows users to query trade data (currently only in HS92) from the Atlas of Economic Complexity database. 
    Powered by OpenAI's GPT-4 and LlamaIndex, it can handle natural language queries and retrieve real-time insights from the underlying data.
    """)


# Initialize the chat engine
if "chat_engine" not in st.session_state.keys():
    st.session_state.chat_engine = chat_engine = init_chat_engine(query_engine)

# Get user input for questions
if prompt := st.chat_input("Ask a question about trade data or country profiles"):
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
