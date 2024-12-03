import streamlit as st
from pathlib import Path
import logging
import sys
from src.text_to_sql import AtlasTextToSQL

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
    Welcome to Ask-Atlas, a chatbot that provides insights from the [Atlas of Economic Complexity](https://atlas.cid.harvard.edu/) using trade data sourced from UN COMTRADE and cleaned and processed by the [Growth Lab at Harvard University](https://growthlab.hks.harvard.edu/).

    Created by: [Shreyas Gadgin Matha](https://growthlab.hks.harvard.edu/people/shreyas-matha)
    """
)

# Add disclaimers
st.warning(
    """
    **Important Disclaimers:**
    - This tool is currently in alpha stage and under active development. Please report any bugs or issues to Shreyas through Slack.
    - This tool is open source ([Github repo](https://github.com/shreyasgm/ask-atlas)) and licensed under [CC-BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). 
    - As with any AI-powered tool, responses may contain inaccuracies or hallucinations. Please verify all results independently
    """
)

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
    except Exception as e:
        st.error("Unable to connect to the Atlas Database")
        logging.error(f"Failed to connect to Atlas Database: {e}", exc_info=True)
        st.stop()


# Initialize the AtlasTextToSQL instance
if "atlas_sql" not in st.session_state:
    st.session_state.atlas_sql = init_atlas_sql()

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
    # Append user message to the modified session state for agent chat history
    st.session_state["agent_chat_history"].append({"role": "user", "content": prompt})

# Display the chat history in the UI
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

# If last message is not from assistant, generate a new response
if st.session_state.messages[-1]["role"] != "assistant":
    with st.chat_message("assistant"):
        try:
            response_gen, messages = st.session_state.atlas_sql.answer_question(
                prompt, stream_response=True, use_agent=True
            )
            full_response = st.write_stream(response_gen)
            final_message = st.session_state.atlas_sql.process_agent_messages(messages)
            
        except Exception as e:
            error_message = f"An error occurred while processing your request: {str(e)}"
            st.error(error_message)
            logging.error(f"Error in answer_question: {e}", exc_info=True)
            full_response = error_message

        # Add the assistant's response to the message history
        st.session_state["messages"].append(
            {"role": "assistant", "content": full_response}
        )

        # Add the final message to the agent chat history
        st.session_state["agent_chat_history"].append(
            {"role": "assistant", "content": final_message}
        )
