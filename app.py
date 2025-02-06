import streamlit as st
from pathlib import Path
import logging
import sys
from src.text_to_sql import AtlasTextToSQL
import uuid

# Set up logging
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logging.getLogger().addHandler(logging.StreamHandler(stream=sys.stdout))

# Define BASE_DIR (assuming it's the directory where the script is located)
BASE_DIR = Path(__file__).resolve().parent

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
    Welcome to Ask-Atlas, an AI agent that provides insights from the [Atlas of Economic Complexity](https://atlas.cid.harvard.edu/) using trade data sourced from UN COMTRADE and cleaned and processed by the [Growth Lab at Harvard University](https://growthlab.hks.harvard.edu/).

    Created by: [Shreyas Gadgin Matha](https://growthlab.hks.harvard.edu/people/shreyas-matha)
    """
)

# Add disclaimers
st.warning(
    """
    **Notes:**
    - This tool is currently in alpha stage and under active development. Please report any bugs or issues to Shreyas through Slack.
    - This tool is open source ([Github repo](https://github.com/shreyasgm/ask-atlas)) and the code is licensed under [CC-BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
    - You can currently only access this tool while connected to the Harvard network (or using a VPN into the Harvard network).
    - As with any LLM-powered tool, responses may contain inaccuracies or hallucinations. Please verify all results independently
    """
)

# Add example questions section
with st.expander("📝 Example Questions You Can Ask"):
    st.markdown("""
        Try asking questions like:
        - What were India's top 5 exports in 2020?
        - How has the trade relationship between China and the USA evolved from 2010 to 2020?
        - Which countries are the largest exporters of semiconductors?
        - What is the trade balance between Brazil and Argentina?
        - Show me Germany's main trading partners in the automotive sector
    """)


# Initialize the AtlasTextToSQL instance
@st.cache_resource(ttl=3600, show_spinner=False)
def init_atlas_sql():
    try:
        with st.spinner("Connecting to Atlas Database..."):
            return AtlasTextToSQL(
                db_uri=st.secrets["ATLAS_DB_URL"],
                table_descriptions_json=BASE_DIR / "db_table_descriptions.json",
                table_structure_json=BASE_DIR / "db_table_structure.json",
                queries_json=BASE_DIR / "src/example_queries/queries.json",
                example_queries_dir=BASE_DIR / "src/example_queries",
                max_results=15,
            )
    except ConnectionError:
        st.error(
            "⚠️ Unable to connect to the database. Please check your VPN connection to the Harvard network."
        )
        st.stop()
    except Exception as e:
        st.error("Unable to connect to the Atlas Database")
        logging.error(f"Failed to connect to Atlas Database: {e}", exc_info=True)
        st.stop()


# Initialize the AtlasTextToSQL instance
if "atlas_sql" not in st.session_state:
    st.session_state.atlas_sql = init_atlas_sql()

# Initialize the thread ID
if "thread_id" not in st.session_state:
    st.session_state["thread_id"] = str(uuid.uuid4())

# Initialize message history for the chat
if "messages" not in st.session_state:
    st.session_state["messages"] = [
        {
            "role": "assistant",
            "content": "Hello! Ask me a question about trade data from the Atlas of Economic Complexity.",
        }
    ]

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
    with st.chat_message("assistant"):
        try:
            response_gen, agent_messages = st.session_state.atlas_sql.answer_question(
                prompt, stream_response=True, thread_id=st.session_state["thread_id"]
            )
            full_response = st.write_stream(response_gen)
            final_message = st.session_state.atlas_sql.process_agent_messages(
                agent_messages
            )

        except ConnectionError:
            error_message = "⚠️ Lost connection to the database. Please check your VPN connection to the Harvard network and try again."
            st.error(error_message)
            logging.error("Database connection error", exc_info=True)
            full_response = error_message

        except ValueError as e:
            error_message = f"⚠️ Invalid query: {str(e)}"
            st.warning(error_message)
            logging.warning(f"Invalid query: {e}")
            full_response = error_message

        except Exception as e:
            error_message = "Sorry, an unexpected error occurred while processing your request. Please report this query to Shreyas through Slack."
            st.error(error_message)
            logging.error(f"Error in answer_question: {e}", exc_info=True)
            full_response = error_message

        # Add the assistant's response to the message history
        st.session_state["messages"].append(
            {"role": "assistant", "content": full_response}
        )


# Add a clear chat button below the chat
def reset_chat():
    # Delete all the items in Session state
    for key in st.session_state.keys():
        del st.session_state[key]


st.button("Clear Chat", on_click=reset_chat)
