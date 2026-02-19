# LangGraph Migration: Technical Design Document

## 1. Overview

### What Changed

The Ask-Atlas agent was migrated from an opaque `create_agent()` call (from `langchain.agents`) combined with implicit Runnable composition to an **explicit LangGraph `StateGraph`** with well-defined nodes and edges.

### Why

- **Visibility**: `create_agent()` hides routing logic, tool dispatch, and state management. A `StateGraph` makes every transition explicit and inspectable.
- **Testability**: Individual pipeline steps are now standalone functions testable with mocked dependencies -- no LLM or database required.
- **Control**: Conditional routing (e.g., max-query guards) is expressed as named edges rather than buried in agent internals.
- **Streaming**: Pipeline node names are exposed via `PIPELINE_NODES`, enabling the streaming layer to distinguish internal pipeline output from agent-to-user text.

### Migration Stages

| Stage | Scope | Key Files |
|-------|-------|-----------|
| 1 | Replace outer agent with StateGraph | `generate_query.py`, `state.py` |
| 2 | Refactor inner pipeline to graph nodes | `generate_query.py`, `state.py`, `product_and_schema_lookup.py`, `text_to_sql.py` |
| 3 | Full unit test coverage | `test_pipeline_nodes.py`, `test_graph_wiring.py`, `test_agent_trajectory.py`, `test_state.py` |
| 4 | Dependency cleanup | `pyproject.toml` |

---

## 2. Stage 1: Replace Outer Agent with StateGraph

### What Was Removed

```python
# Before (langchain.agents)
from langchain.agents import create_agent
agent = create_agent(llm, tools=[query_tool], ...)
```

`create_agent` from `langchain.agents` was the sole import from the main `langchain` package. It assembled an opaque ReAct loop internally.

### What Replaced It

An explicit `StateGraph` with two core nodes and conditional routing:

```python
from langgraph.graph import END, START, StateGraph

builder = StateGraph(AtlasAgentState)
builder.add_node("agent", agent_node)
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", route_after_agent)
```

The `agent_node` function binds the tool schema to the LLM and invokes it:

```python
def agent_node(state: AtlasAgentState) -> dict:
    model_with_tools = llm.bind_tools([_query_tool_schema])
    response = model_with_tools.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}
```

### Design Choices

**`SystemMessage` prepended at invocation, not as constructor arg.** The system prompt is prepended to the message list in `agent_node` rather than passed as a constructor argument. This keeps the system prompt visible and mutable without rebuilding the model.

**`bind_tools()` with a schema-only tool.** The LLM sees the tool definition (name, description, args schema) via `bind_tools([_query_tool_schema])`, but execution routes through graph nodes rather than through the tool's function body. The tool function raises `NotImplementedError` to catch accidental direct invocation. See Stage 2 for details.

### Comparison with LangGraph Docs

This directly matches the ["How to create a ReAct agent"](https://langchain-ai.github.io/langgraph/how-tos/create-react-agent/) pattern from LangGraph documentation, with two differences:

1. Instead of using `ToolNode` + `tools_condition` from `langgraph.prebuilt`, we use a **custom conditional edge** (`route_after_agent`) because our tool execution is a multi-step pipeline, not a simple function call.
2. The routing function implements a **max-query guard** (`queries_executed >= max_uses`), which isn't part of the standard ReAct pattern.

---

## 3. Stage 2: Refactor Inner Pipeline to Graph Nodes

### Architecture

The query pipeline was decomposed into **8 graph nodes** forming a linear chain within the StateGraph:

```
agent
  |
  v  (tool_calls detected)
extract_tool_question --> extract_products --> lookup_codes --> get_table_info
  --> generate_sql --> execute_sql --> format_results --> agent
```

A 9th node, `max_queries_exceeded`, handles the guard edge when the query limit is reached.

Each node is a pure function with the signature:

```python
def node_name(state: AtlasAgentState, *, dep1, dep2, ...) -> dict:
    # Read from state, do work, return partial state update
```

### State Design

`AtlasAgentState` is a `TypedDict` with pipeline intermediate fields:

```python
class AtlasAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    queries_executed: int
    last_error: str
    retry_count: int
    # Pipeline intermediate state
    pipeline_question: str
    pipeline_products: Optional[SchemasAndProductsFound]
    pipeline_codes: str
    pipeline_table_info: str
    pipeline_sql: str
    pipeline_result: str
```

The `messages` field uses LangGraph's `add_messages` reducer, which appends new messages rather than replacing the list. All other fields use default replacement semantics.

The `pipeline_*` fields act as a shared scratchpad for the linear pipeline nodes. Each node reads its inputs from prior `pipeline_*` fields and writes its output to its own field.

### Design Choice: Schema-Only Tool Pattern

```python
class QueryToolInput(BaseModel):
    question: str = Field(description="A question about international trade data")

@tool("query_tool", args_schema=QueryToolInput)
def _query_tool_schema(question: str) -> str:
    """A tool that generates and executes SQL queries on the trade database."""
    raise NotImplementedError("Schema-only tool; execution routes through graph nodes.")
```

The LLM sees `query_tool` as an available tool via `bind_tools()`, but calling it triggers the graph's conditional edge to route through the pipeline nodes. The tool function itself is never executed -- its `NotImplementedError` is a safety net.

This pattern is cleaner than LangGraph's `ToolNode` for multi-step pipelines because `ToolNode` expects a single function call, not an 8-node chain.

### Design Choice: `functools.partial` for Dependency Injection

Node functions need access to the LLM, database engine, and other dependencies. Rather than making them closures or classes, we use `functools.partial`:

```python
builder.add_node(
    "extract_products",
    partial(extract_products_node, llm=llm, engine=engine),
)
```

This keeps node functions testable as pure functions -- tests pass dependencies directly via keyword arguments without needing to construct the full graph.

### Design Choice: Direct SQLAlchemy Execution

`execute_sql_node` replaces `QuerySQLDatabaseTool` (from `langchain-community`) with direct SQLAlchemy:

```python
def execute_sql_node(state: AtlasAgentState, *, engine: Engine) -> dict:
    sql = state["pipeline_sql"]
    def _run_query() -> str:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            ...
    result_str = execute_with_retry(_run_query)
    return {"pipeline_result": result_str, "last_error": ""}
```

This eliminates a dependency on `QuerySQLDatabaseTool` and gives full control over error handling, retry logic, and result formatting.

### Design Choice: Dual-Interface Methods on `ProductAndSchemaLookup`

The `ProductAndSchemaLookup` class originally only exposed LCEL `Runnable` chains (e.g., `extract_schemas_and_product_mentions()` returns a `Runnable`). For pipeline nodes that need direct invocation, we added `_direct()` wrappers:

```python
def extract_schemas_and_product_mentions_direct(self, question: str) -> SchemasAndProductsFound:
    chain = self.extract_schemas_and_product_mentions()
    return chain.invoke({"question": question})

def select_final_codes_direct(self, question, product_search_results) -> ProductCodesMapping:
    chain = self.select_final_codes(product_search_results)
    return chain.invoke({"question": question})
```

The original `Runnable`-returning methods are preserved for backward compatibility with `get_product_details()` (the LCEL-composed pipeline).

### Design Choice: `route_after_agent` with Max-Query Guard

```python
def route_after_agent(state: AtlasAgentState) -> str:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        if state.get("queries_executed", 0) >= max_uses:
            return "max_queries_exceeded"
        return "extract_tool_question"
    return END
```

Three-way routing:
1. **No tool calls** -> `END` (agent gives final answer)
2. **Tool calls + under limit** -> `extract_tool_question` (start pipeline)
3. **Tool calls + at/over limit** -> `max_queries_exceeded` (return error ToolMessage)

### Design Choice: LCEL Retained Within Nodes

Individual steps like SQL generation still use `prompt | llm | parser` LCEL chains:

```python
def create_query_generation_chain(llm, codes, top_k, table_info, example_queries) -> Runnable:
    ...
    return prompt | llm | StrOutputParser() | _strip
```

LCEL is clean for single-step chains. The migration replaced inter-node composition (the outer agent loop) with StateGraph, but kept intra-node composition as LCEL.

### Streaming

`PIPELINE_NODES` is a `frozenset` exported from `generate_query.py`:

```python
PIPELINE_NODES = frozenset({
    "extract_tool_question", "extract_products", "lookup_codes",
    "get_table_info", "generate_sql", "execute_sql",
    "format_results", "max_queries_exceeded",
})
```

The streaming layer in `text_to_sql.py` uses this set to distinguish pipeline output (buffered and displayed as tool activity) from agent output (streamed directly to the user):

```python
if metadata.get("langgraph_node") in PIPELINE_NODES:
    # Buffer as tool output
else:
    # Stream as agent text
```

### Comparison with LangGraph Docs

This approach is closest to LangGraph's ["How to create a custom ReAct agent"](https://langchain-ai.github.io/langgraph/how-tos/) pattern, where:
- The agent node is a custom function (not `create_react_agent`)
- Tool execution is handled by custom nodes (not `ToolNode`)
- State carries intermediate computation across nodes

The linear pipeline (extract -> lookup -> generate -> execute -> format) could alternatively be modeled as a **subgraph**, but since it shares state with the outer agent loop (`messages`, `queries_executed`), keeping it as flat nodes in the same graph is simpler and avoids subgraph state mapping.

---

## 4. Stage 3: Full Unit Test Coverage

### Test Architecture

All tests use `FakeToolCallingModel`, a minimal `BaseChatModel` subclass that returns scripted `AIMessage` responses in order:

```python
class FakeToolCallingModel(BaseChatModel):
    responses: List[AIMessage]
    index: int = 0

    def _generate(self, messages, stop=None, **kwargs) -> ChatResult:
        response = self.responses[self.index % len(self.responses)]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    def bind_tools(self, tools, **kwargs):
        return self  # No-op: responses are pre-scripted
```

This eliminates all LLM calls from tests while still exercising the full graph routing logic.

### Test Files

#### `test_pipeline_nodes.py` -- Node-Level Unit Tests

Tests each of the 8 pipeline nodes as a **pure function**:

| Test Class | Node Under Test | Key Assertions |
|-----------|----------------|----------------|
| `TestExtractToolQuestion` | `extract_tool_question` | Extracts question from tool_call args, handles unicode, empty strings |
| `TestExtractProductsNode` | `extract_products_node` | Calls `ProductAndSchemaLookup.extract_schemas_and_product_mentions_direct()`, handles no-products case |
| `TestLookupCodesNode` | `lookup_codes_node` | Calls `get_candidate_codes` + `select_final_codes_direct`, handles None/empty products |
| `TestGetTableInfoNode` | `get_table_info_node` | Delegates to `get_table_info_for_schemas`, passes empty schemas when no products |
| `TestGenerateSqlNode` | `generate_sql_node` | Calls `create_query_generation_chain`, converts empty codes to `None` |
| `TestExecuteSqlNode` | `execute_sql_node` | Handles rows, no-rows, non-returning statements, `QueryExecutionError`, generic exceptions |
| `TestFormatResultsNode` | `format_results_node` | Creates `ToolMessage` with correct `tool_call_id`, increments `queries_executed`, handles errors |
| `TestMaxQueriesExceededNode` | `max_queries_exceeded_node` | Returns error `ToolMessage`, does not increment `queries_executed` |

Pattern: Each test constructs its own `AtlasAgentState` dict using the `_base_state()` helper and mocks external dependencies (LLM, engine, database) using `unittest.mock`.

#### `test_graph_wiring.py` -- Graph Routing Tests

Tests the full graph routing logic with `FakeToolCallingModel` and a `pipeline_stub` that replaces the 8-node pipeline:

| Test Class | Key Tests |
|-----------|-----------|
| `TestGraphRouting` | No tool_calls -> END; tool_calls -> pipeline -> agent; max_queries enforcement; error propagation |
| `TestMultiplePipelineRounds` | `queries_executed` increments per round; `max_uses=0` blocks immediately |

The `build_test_graph()` helper constructs a simplified graph with real routing logic but stub pipeline nodes, enabling tests to verify conditional edges without needing LLM or database access.

#### `test_agent_trajectory.py` -- End-to-End Trajectory Tests

Tests the full agent trajectory (message sequence shape) using a `make_agent` fixture:

| Test Class | Key Tests |
|-----------|-----------|
| `TestAgentToolCalling` | Tool invocation when model emits tool_calls; termination without tool_calls |
| `TestAgentMessageSequence` | Exact trajectory: Human -> AI(tool_call) -> Tool -> AI(answer) |
| `TestAgentPersistence` | Multi-turn memory accumulation; thread isolation |

#### `test_state.py` -- State Schema Tests

Verifies the `AtlasAgentState` TypedDict:
- Can create initial state with all required fields
- Accepts LangChain message objects
- Tracks error information
- Is a plain dict (TypedDict instances are dicts)
- Has all expected annotations including `pipeline_*` fields

### Test Tiers

| Tier | Marker | Dependencies | When to Run |
|------|--------|-------------|-------------|
| Unit | `not db and not integration` | None | Always (CI, local) |
| DB Integration | `@pytest.mark.db` | Docker test DB on port 5433 | When testing SQL execution |
| E2E Integration | `@pytest.mark.integration` | DB + LLM API key | Before release |

### Comparison with LangGraph Docs

LangGraph's testing documentation recommends:
- Using `MemorySaver` for in-memory checkpointing in tests (we do this)
- Testing graph routing with deterministic models (our `FakeToolCallingModel` pattern)
- Verifying state transitions after `invoke()` (our trajectory tests)

Our `pipeline_stub` pattern is an adaptation: instead of testing the full pipeline end-to-end, we replace it with a stub to isolate routing logic from pipeline logic.

---

## 5. Stage 4: Dependency Cleanup

### Removed

- **`langchain`** (main package) -- `create_agent` was the only import. After migrating to `StateGraph`, this dependency is no longer needed.

### Kept

| Package | Reason |
|---------|--------|
| `langchain-core` | `BaseLanguageModel`, `SystemMessage`, `ToolMessage`, `PromptTemplate`, `StrOutputParser`, `Runnable`, `tool` decorator -- tightly integrated with LangGraph |
| `langchain-openai` | `ChatOpenAI` model provider |
| `langchain-community` | `sql_multiple_schemas.py` inherits `SQLDatabase` from community; removing would require rewriting the schema-aware database wrapper |
| `langchain-postgres` | PostgreSQL checkpointer for LangGraph persistence |
| `langgraph` | Core framework for the StateGraph agent |
| `langgraph-checkpoint-postgres` | Production checkpointer |

---

## 6. Files Modified Summary

| File | Change Type | Description |
|------|------------|-------------|
| `src/generate_query.py` | **Major rewrite** | Replaced `create_agent` with `StateGraph`; added 8 pipeline node functions, `PIPELINE_NODES` frozenset, `_query_tool_schema` schema-only tool, `route_after_agent` conditional edge |
| `src/state.py` | **Extended** | Added `pipeline_*` intermediate fields to `AtlasAgentState` TypedDict |
| `src/product_and_schema_lookup.py` | **Extended** | Added `extract_schemas_and_product_mentions_direct()` and `select_final_codes_direct()` dual-interface methods |
| `src/text_to_sql.py` | **Updated** | Updated streaming to use `PIPELINE_NODES` for distinguishing pipeline vs. agent output |
| `src/tests/test_pipeline_nodes.py` | **New** | 30 unit tests for all 8 pipeline node functions |
| `src/tests/test_graph_wiring.py` | **New** | 7 tests for graph routing and state transitions |
| `src/tests/test_agent_trajectory.py` | **Rewritten** | Migrated to `pipeline_stub` pattern with `FakeToolCallingModel` |
| `src/tests/test_generate_query.py` | **Trimmed** | Removed tests that overlapped with new pipeline node tests |
| `src/tests/test_state.py` | **Updated** | Added assertions for `pipeline_*` annotation fields |
| `src/tests/fake_model.py` | **New** | `FakeToolCallingModel` for deterministic testing |
| `pyproject.toml` | **Updated** | Removed `langchain` from dependencies |
