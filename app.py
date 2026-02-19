import json
import logging
import sys
import uuid

import httpx
import streamlit as st

# Set up logging
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logging.getLogger().addHandler(logging.StreamHandler(stream=sys.stdout))

# API base URL — configurable via Streamlit secrets or env
API_BASE_URL = st.secrets.get("API_BASE_URL", "http://localhost:8000")

# Set Streamlit page configuration
st.set_page_config(
    page_title="Ask-Atlas",
    page_icon="🌍",
    layout="centered",
    initial_sidebar_state="auto",
)

# Title and subtitle of the app
st.title("Ask-Atlas: Trade Data Assistant")

# Display some information
st.info(
    """
    Ask-Atlas is an AI agent that provides insights from the [Atlas of Economic Complexity](https://atlas.cid.harvard.edu/) using trade data sourced from UN COMTRADE and processed by the [Growth Lab at Harvard University](https://growthlab.hks.harvard.edu/).

    Created by: [Shreyas Gadgin Matha](https://growthlab.hks.harvard.edu/people/shreyas-matha)
    """
)

# Add disclaimers
st.warning(
    """
    - Alpha release. Feedback and bug reports are welcome.
    - Code is open sourced ([Github repo](https://github.com/shreyasgm/ask-atlas)) under [CC-BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
    - Responses may contain inaccuracies. Verify results independently.
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


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _stream_sse(question: str, thread_id: str):
    """POST to /chat/stream and yield (text_chunk, is_tools_block) tuples.

    Parses the SSE event stream from the FastAPI backend and translates
    events into the same (str, bool) contract that write_stream() expects.
    """
    with httpx.Client(timeout=httpx.Timeout(300.0)) as client:
        with client.stream(
            "POST",
            f"{API_BASE_URL}/chat/stream",
            json={"question": question, "thread_id": thread_id},
        ) as response:
            response.raise_for_status()
            event_type = None
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_str = line.split(":", 1)[1].strip()
                    if event_type in ("agent_talk", "tool_output", "tool_call"):
                        payload = json.loads(data_str)
                        content = payload.get("content", "")
                        is_tools = payload.get("source") == "tool"
                        if content:
                            yield content, is_tools
                    elif event_type == "thread_id":
                        # Update thread_id from server if it was auto-generated
                        payload = json.loads(data_str)
                        st.session_state["thread_id"] = payload["thread_id"]
                    event_type = None


# ---------------------------------------------------------------------------
# Writing stream with tool blocks separated into an expander
# ---------------------------------------------------------------------------


def write_stream(response_gen):
    """Stream response text with typewriter effect, handling sequential blocks of text and tool blocks.

    Args:
        response_gen: Generator yielding tuples of (text, is_in_tools_block)

    Returns:
        str: The complete response text
    """
    # Current streaming containers
    current_regular_container = None
    current_tool_container = None
    current_expander = None

    # State variables
    in_tool_block = False

    # Buffers for current block
    current_text = ""
    full_response = ""

    # Text cursor for typewriter effect
    TEXT_CURSOR = " ▏"

    def finalize_current_block():
        """Finalize the current block by removing the cursor."""
        nonlocal current_text
        if in_tool_block and current_tool_container and current_text:
            current_tool_container.markdown(current_text)
        elif not in_tool_block and current_regular_container and current_text:
            current_regular_container.markdown(current_text)

    for chunk, is_tools_block in response_gen:
        if not chunk:  # Skip empty chunks
            continue

        # Detect transition between block types
        if is_tools_block != in_tool_block:
            # Finalize the current block
            finalize_current_block()
            current_text = ""

            # Set up new block
            if is_tools_block:
                # Transition to tool block
                in_tool_block = True
                current_expander = st.expander("SQL Query", expanded=True)
                current_tool_container = current_expander.empty()
                current_regular_container = None
            else:
                # Transition to regular block
                in_tool_block = False
                current_expander = None
                current_tool_container = None
                current_regular_container = st.empty()

        # If we're starting fresh without a container, create one
        if in_tool_block and not current_tool_container:
            current_expander = st.expander("SQL Query", expanded=True)
            current_tool_container = current_expander.empty()
        elif not in_tool_block and not current_regular_container:
            current_regular_container = st.empty()

        # Update current block
        current_text += chunk
        if in_tool_block:
            current_tool_container.markdown(current_text + TEXT_CURSOR)
        else:
            current_regular_container.markdown(current_text + TEXT_CURSOR)

        full_response += chunk

    # Finalize the last block
    finalize_current_block()

    return full_response


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

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
for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        st.write(message["content"])

# If last message is not from assistant, generate a new response
if st.session_state.messages[-1]["role"] != "assistant":
    with st.chat_message("assistant"):
        try:
            response_gen = _stream_sse(prompt, st.session_state["thread_id"])
            full_response = write_stream(response_gen)

        except httpx.ConnectError:
            error_message = "⚠️ Unable to connect to the API server. Is it running?"
            st.error(error_message)
            logging.error("API connection error", exc_info=True)
            full_response = error_message

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503:
                error_message = "⚠️ The backend service is not ready yet. Please try again shortly."
            else:
                error_message = f"⚠️ API error: {e.response.status_code}"
            st.error(error_message)
            logging.error(f"HTTP error: {e}", exc_info=True)
            full_response = error_message

        except Exception as e:
            error_message = "Sorry, an unexpected error occurred while processing your request. Please report this query to Shreyas through Slack."
            st.error(error_message)
            logging.error(f"Error in chat: {e}", exc_info=True)
            full_response = error_message

        # Add the assistant's response to the message history
        st.session_state["messages"].append(
            {"role": "assistant", "content": full_response}
        )


# Add a clear chat button
def reset_chat():
    for key in list(st.session_state.keys()):
        del st.session_state[key]


# Add buttons in a compact horizontal layout
button_cols = st.columns([0.2, 0.2, 0.2, 0.4])
with button_cols[0]:
    st.button("Clear Chat :broom:", on_click=reset_chat, use_container_width=True)
with button_cols[1]:
    st.link_button(
        "Learn More :book:",
        "https://github.com/shreyasgm/ask-atlas",
        use_container_width=True,
    )
with button_cols[2]:
    st.link_button(
        "Atlas :earth_africa:",
        "https://atlas.hks.harvard.edu/",
        use_container_width=True,
    )
