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

## 2. The Outer Agent Loop: `create_react_agent` vs. Custom StateGraph

### What We Chose

A hand-built `StateGraph` with an `agent` node and custom conditional routing (`route_after_agent`).

### What the Docs Recommend: Three Tiers

LangGraph documentation presents three levels of abstraction for building ReAct agents:

**Tier 1: `create_react_agent` (prebuilt, highest abstraction)**

```python
from langgraph.prebuilt import create_react_agent
agent = create_react_agent(model, tools=[query_tool], prompt="You are Ask-Atlas...")
```

This is the recommended starting point in the docs. It builds an internal graph with `agent` + `tools` nodes, `tools_condition` routing, and accepts parameters for:
- `prompt`: string or `SystemMessage` for the system prompt
- `state_schema`: custom `TypedDict`/`BaseModel` extending `MessagesState` for extra state fields
- `response_format`: Pydantic model for structured final output
- `pre_model_hook` / `post_model_hook`: functions called before/after the LLM for message trimming, scoring, etc.

As of 2025, it supports version `"v2"` (default) with custom state schemas.

**Tier 2: `ToolNode` + `tools_condition` (prebuilt components, manual graph)**

```python
from langgraph.prebuilt import ToolNode, tools_condition

builder = StateGraph(State)
builder.add_node("agent", call_model)
builder.add_node("tools", ToolNode([query_tool]))
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", tools_condition)
builder.add_edge("tools", "agent")
```

`tools_condition` checks the last message for `tool_calls`; if present, routes to `"tools"`, otherwise to `END`. `ToolNode` executes the tool function and returns a `ToolMessage`.

**Tier 3: Fully custom (what we chose)**

Everything hand-written: the agent node, the routing function, and the tool execution logic.

### Why We Chose Tier 3 Over Tier 1

| Concern | `create_react_agent` | Our custom graph |
|---------|---------------------|-----------------|
| **Tool execution model** | `ToolNode` calls the tool function directly. One function call per tool. | Our "tool" is an 8-node pipeline (extract products → lookup codes → generate SQL → execute → format). `ToolNode` cannot orchestrate this. |
| **Max-query guard** | Not supported. Would need `post_model_hook` to check a counter, but that can't prevent tool routing. | `route_after_agent` implements a 3-way branch (END / pipeline / max_queries_exceeded) natively. |
| **Pipeline state** | Custom state via `state_schema` is possible, but all pipeline intermediate fields (`pipeline_sql`, `pipeline_codes`, etc.) would need to be in the state schema with no way to scope them to the pipeline nodes. | Same limitation (we use flat `pipeline_*` fields), but we have full control over which nodes read/write which fields. |
| **Streaming granularity** | Streams as a single `tools` node; no visibility into sub-steps. | Each pipeline node is independently visible in `stream_mode="updates"`, enabling fine-grained UI feedback. |

### Alternative: Could We Use `create_react_agent` + Override?

Yes, with `state_schema` and `pre_model_hook`:

```python
class AtlasState(MessagesState):
    queries_executed: int
    pipeline_sql: str
    # ... other fields

def pre_hook(state):
    if state["queries_executed"] >= MAX:
        # Could inject a warning message
        ...
    return state["messages"]

agent = create_react_agent(
    model, tools=[real_query_tool],
    state_schema=AtlasState,
    pre_model_hook=pre_hook,
    prompt=SYSTEM_PROMPT,
)
```

But this collapses the 8-node pipeline into a single tool function, losing streaming granularity and per-node testability. It would work for simpler agents where the tool is a single function call.

### Alternative: Subgraph for the Pipeline

Instead of flat nodes in one graph, the pipeline could be a separate compiled subgraph:

```python
# Pipeline subgraph
pipeline_builder = StateGraph(PipelineState)
pipeline_builder.add_node("extract_products", ...)
# ... all 8 nodes
pipeline_graph = pipeline_builder.compile()

# Parent graph
def run_pipeline(state: AtlasAgentState):
    result = pipeline_graph.invoke({"question": state["pipeline_question"], ...})
    return {"messages": [ToolMessage(content=result["formatted_result"], ...)]}
```

**Pros**: Clean encapsulation; pipeline has its own private state; parent graph stays simple.
**Cons**: State mapping boilerplate (must transform parent ↔ child state at boundaries); loses direct access to `messages` and `queries_executed` in pipeline nodes; streaming subgraph output requires `subgraphs=True` parameter.

The LangGraph docs show two approaches to subgraph state: (1) **shared keys** (automatic mapping when parent and child share key names) and (2) **manual transformation** (wrapper function converts between schemas). Our pipeline shares `messages` and `queries_executed` with the outer loop, making shared-key subgraphs tempting but leaky (the child would see all parent state). Manual transformation is cleaner but verbose.

**Verdict**: Flat nodes are simpler for a single-purpose agent. Subgraphs would make sense if the pipeline were reused across multiple agents or if pipeline state needed strict isolation.

### Alternative: `Command` Object for Routing + State Updates

LangGraph's `Command` object combines state updates with routing in a single return:

```python
from langgraph.types import Command

def route_after_agent(state) -> Command[Literal["pipeline", "max_exceeded", END]]:
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        if state.get("queries_executed", 0) >= max_uses:
            return Command(goto="max_queries_exceeded")
        return Command(goto="extract_tool_question")
    return Command(goto=END)
```

This is stylistically cleaner than `add_conditional_edges` + return-string but functionally equivalent. Important caveat from docs: `Command` does NOT prevent static edges from firing -- if you have both a static edge and a `Command` with `goto`, both destinations are visited.

We use the traditional conditional-edge approach because it's more established and avoids the `Command` edge-interaction gotcha.

### Alternative Frameworks Entirely

| Framework | How it would handle this | Trade-off vs. LangGraph |
|-----------|------------------------|------------------------|
| **Pydantic AI** | `Agent` class with `@agent.tool` decorator. DI via `RunContext[MyDeps]`. The tool function would be the full pipeline. | Excellent type safety and DI, but no multi-step tool execution, no graph visualization, no checkpointing. Better for single-agent typed pipelines. |
| **OpenAI Agents SDK** | `Agent(tools=[query_tool], handoffs=[...], input_guardrails=[...])`. Built-in tracing. | Great for OpenAI-centric teams. No fine-grained workflow control, no checkpointing/resume. |
| **CrewAI** | Role-based: `Agent(role="SQL Expert")` + `Task(agent=...)` + `Crew(...)`. | Easy to prototype. Much less control over transitions, retries, and state. Teams often migrate *from* CrewAI *to* LangGraph for production. |
| **AutoGen v0.4** | Actor model with async message-passing. Agents communicate via `publish_message()`. | Better for genuinely distributed multi-agent systems. Overkill for our single-agent + pipeline architecture. |
| **smolagents** | `CodeAgent` that writes and executes Python code. ~1000 lines total. | Radically simple. Agent could write `pandas` + SQL code directly. No state management, checkpointing, or streaming. |

---

## 3. State Design: TypedDict, Reducers, and Alternatives

### What We Chose

`AtlasAgentState` as a `TypedDict` with `add_messages` reducer on `messages` and flat `pipeline_*` fields:

```python
class AtlasAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    queries_executed: int
    last_error: str
    retry_count: int
    pipeline_question: str
    pipeline_products: Optional[SchemasAndProductsFound]
    pipeline_codes: str
    pipeline_table_info: str
    pipeline_sql: str
    pipeline_result: str
```

### What the Docs Offer: Five State Patterns

**1. `TypedDict` (what we use)**

Default, most common. No runtime validation. Fields without `Annotated` use **overwrite** semantics (last writer wins).

**2. `MessagesState` (prebuilt convenience)**

```python
from langgraph.graph import MessagesState

class AtlasState(MessagesState):  # Already has messages: Annotated[list, add_messages]
    queries_executed: int
    pipeline_sql: str
    # ...
```

We could inherit from `MessagesState` instead of declaring `messages` ourselves. The result is identical -- `MessagesState` just saves one line.

**3. Pydantic `BaseModel` (runtime validation)**

```python
from pydantic import BaseModel

class AtlasAgentState(BaseModel):
    messages: list[BaseMessage]
    queries_executed: int = 0
    pipeline_sql: str = ""
    # ...
```

Pydantic state adds **runtime input validation** and **type coercion** (e.g., `"42"` → `42`). The docs note that validation is on *inputs only* -- `graph.invoke()` output is still a dict.

**Trade-off**: Slightly more overhead. Would catch type errors at runtime rather than silently accepting wrong types. Worth considering if we add external API inputs to the graph.

**4. Input/Output schemas (API boundary control)**

```python
class InputState(TypedDict):
    question: str

class OutputState(TypedDict):
    answer: str

class InternalState(InputState, OutputState):
    pipeline_sql: str  # hidden from external API
    pipeline_codes: str
    # ...

builder = StateGraph(InternalState, input_schema=InputState, output_schema=OutputState)
```

This hides all `pipeline_*` intermediate fields from the graph's external interface. Callers only see `{question: str}` in and `{answer: str}` out.

**Trade-off**: More schema classes to maintain. Very clean API boundary. We don't currently need this because the Streamlit UI directly accesses the full state, but it would be valuable if Ask-Atlas exposed a REST API.

**5. Private state between specific nodes**

```python
class PipelineOutput(TypedDict):
    private_sql: str  # Only visible to the next node

def generate_sql_node(state: AtlasAgentState) -> PipelineOutput:
    return {"private_sql": "SELECT ..."}
```

Nodes can write to keys not in the main state schema, making them "private" to the nodes that read them. The docs describe this for ephemeral inter-node data.

**Trade-off**: Our `pipeline_*` fields are functionally private (only pipeline nodes use them), but they're in the shared TypedDict. True private state would prevent the agent node from accidentally reading stale pipeline data.

### Reducer Alternatives

Our state uses `add_messages` on `messages` and default overwrite on everything else. The docs describe several reducer patterns:

| Reducer | Effect | When to use |
|---------|--------|-------------|
| None (default) | Last writer overwrites | Simple scalar state (our `pipeline_sql`, `last_error`, etc.) |
| `add_messages` | Appends messages, handles deduplication | Chat message history (our `messages` field) |
| `operator.add` | Concatenates lists/sums numbers | Accumulating results from parallel branches |
| Custom function | Any merge logic | Complex state (e.g., merge dicts, pick max) |

If we ever parallelize pipeline steps (e.g., run product extraction and table info lookup concurrently), we'd need reducers on the target fields to merge results from parallel branches. Without reducers, parallel nodes writing to the same field will silently overwrite each other.

### Alternative: Runtime Context for Dependencies

LangGraph now offers `Runtime` with `context_schema` as a typed alternative to `functools.partial` for dependency injection:

```python
from langgraph.runtime import Runtime
from dataclasses import dataclass

@dataclass
class AtlasContext:
    llm: BaseLanguageModel
    engine: Engine
    db: SQLDatabaseWithSchemas
    table_descriptions: Dict
    example_queries: list
    max_results: int

def extract_products_node(state: AtlasAgentState, runtime: Runtime[AtlasContext]) -> dict:
    lookup = ProductAndSchemaLookup(llm=runtime.context.llm, connection=runtime.context.engine)
    products = lookup.extract_schemas_and_product_mentions_direct(state["pipeline_question"])
    return {"pipeline_products": products}

builder = StateGraph(AtlasAgentState, context_schema=AtlasContext)
# ... at invocation:
graph.invoke(input, context=AtlasContext(llm=llm, engine=engine, ...))
```

**Pros**: Typed dependencies, no `partial()` clutter, dependencies don't pollute state, swappable at invocation time.
**Cons**: Newer API (may not be stable across LangGraph versions). Docs note the functional API (which uses similar patterns) was deprecated in Oct 2025.

Our `functools.partial` approach is equivalent but less typed. The `Runtime` approach is cleaner for production and would be a good future migration.

---

## 4. Tool Execution: Schema-Only Tool vs. Alternatives

### What We Chose

A "schema-only" tool that the LLM sees but never executes:

```python
@tool("query_tool", args_schema=QueryToolInput)
def _query_tool_schema(question: str) -> str:
    """A tool that generates and executes SQL queries on the trade database."""
    raise NotImplementedError("Schema-only tool; execution routes through graph nodes.")
```

The LLM calls this tool via `bind_tools()`, but `route_after_agent` intercepts the tool call and routes to the pipeline nodes instead. The tool function body is dead code (a safety net).

### What the Docs Recommend

**Standard pattern: `ToolNode` + real tool function**

```python
from langgraph.prebuilt import ToolNode, tools_condition

@tool
def query_tool(question: str) -> str:
    # Actually executes -- runs the full pipeline in one function
    return run_pipeline(question)

builder.add_node("tools", ToolNode([query_tool]))
builder.add_conditional_edges("agent", tools_condition)
```

`tools_condition` checks the last message for tool_calls; if present, routes to `"tools"` (which calls `ToolNode`). `ToolNode` executes the tool function and returns a `ToolMessage`.

**Why we didn't use this**: `ToolNode` calls one function per tool. Our pipeline is 8 steps. Collapsing them into one function loses per-step streaming, testability, and error granularity.

**Alternative: `InjectedState` for state-aware tools**

LangGraph provides `InjectedState` to give tools access to graph state:

```python
from langgraph.prebuilt import InjectedState
from typing import Annotated

@tool
def query_tool(question: str, state: Annotated[dict, InjectedState]) -> str:
    """A trade data query tool that can access graph state."""
    # state["pipeline_codes"] is accessible here
    return run_pipeline(question, state)
```

`InjectedState` is hidden from the LLM's tool schema but injected at runtime. This would let a `ToolNode`-executed tool read/write state -- but it still collapses the pipeline into one function.

**Known issue (2025)**: The LangGraph CLI incorrectly validates `InjectedState` parameters as required tool arguments, causing deployment failures.

**Alternative: Multi-tool approach**

Instead of one `query_tool`, expose each pipeline step as a separate tool:

```python
@tool
def extract_products(question: str) -> str: ...
@tool
def lookup_codes(products: str) -> str: ...
@tool
def generate_sql(question: str, codes: str, table_info: str) -> str: ...
@tool
def execute_sql(sql: str) -> str: ...
```

The LLM would call them in sequence, with the agent loop coordinating between steps.

**Pros**: Maximum LLM flexibility (it could skip steps, reorder, retry individual steps).
**Cons**: The LLM must learn the correct calling order. More tokens per turn. Higher latency. Risk of the LLM calling tools in the wrong order or skipping schema lookup.

This is the approach the LangChain docs use in their ["Build a custom SQL agent"](https://docs.langchain.com/oss/python/langgraph/sql-agent) tutorial. It works well for exploratory SQL agents where the LLM needs flexibility, but our pipeline has a fixed order (product extraction must happen before code lookup, which must happen before SQL generation), so encoding that order in graph edges is more reliable than relying on LLM behavior.

---

## 5. Pipeline Architecture: Flat Nodes vs. Alternatives

### What We Chose

Eight nodes forming a linear chain within the main `StateGraph`:

```
agent → extract_tool_question → extract_products → lookup_codes → get_table_info
    → generate_sql → execute_sql → format_results → agent
```

### Alternative: Subgraph with Private State

```python
class PipelineState(TypedDict):
    question: str
    products: Optional[SchemasAndProductsFound]
    codes: str
    table_info: str
    sql: str
    result: str
    error: str

pipeline_builder = StateGraph(PipelineState)
# ... add all 8 nodes with edges ...
pipeline_graph = pipeline_builder.compile()

# In main graph:
def run_pipeline(state: AtlasAgentState) -> dict:
    last_msg = state["messages"][-1]
    question = last_msg.tool_calls[0]["args"]["question"]
    result = pipeline_graph.invoke({"question": question})
    content = result.get("error") or result["result"]
    return {
        "messages": [ToolMessage(content=content, tool_call_id=last_msg.tool_calls[0]["id"])],
        "queries_executed": state.get("queries_executed", 0) + 1,
    }
```

**Pros**:
- Pipeline state is truly private (no `pipeline_*` prefix pollution in main state)
- Pipeline is independently compilable, testable, and reusable
- Cleaner separation of concerns
- Could be deployed as a separate service if needed

**Cons**:
- Streaming requires `subgraphs=True` to see intermediate pipeline output
- State mapping boilerplate at the boundary
- Two graphs to reason about instead of one
- The LangGraph docs note that when parent and child have different schemas, you must invoke the subgraph manually and transform state explicitly

### Alternative: Parallel Pipeline Steps

Some of our pipeline steps are independent and could run concurrently:

```
extract_tool_question
    ├── extract_products ──→ lookup_codes
    │                              ↓
    └── get_table_info  ──────────→ generate_sql → execute_sql → format_results
```

`extract_products` and `get_table_info` both need only `pipeline_question`. They could fan out, and `generate_sql` would fan in after both complete.

LangGraph supports this natively:
```python
builder.add_edge("extract_tool_question", "extract_products")
builder.add_edge("extract_tool_question", "get_table_info")
builder.add_edge("extract_products", "lookup_codes")
builder.add_edge("lookup_codes", "generate_sql")
builder.add_edge("get_table_info", "generate_sql")  # Fan-in: waits for both
```

**Catch**: `get_table_info` actually reads `pipeline_products` (to get the schema list). So it depends on `extract_products`. The true dependency graph is:

```
extract_tool_question → extract_products → lookup_codes ─┐
                                         └─ get_table_info → generate_sql → ...
```

Only `lookup_codes` and `get_table_info` could theoretically be parallelized (both read from `pipeline_products`). The latency savings would be small since `get_table_info` is a local operation while `lookup_codes` makes LLM + DB calls.

### Alternative: DRGC Pipeline (Industry Standard for Text-to-SQL)

Modern text-to-SQL systems in 2025 follow a **Decomposition-Retrieval-Generation-Correction** pipeline:

1. **Schema Pruning Agent** -- Identifies relevant tables/columns (our `extract_products` + `get_table_info`)
2. **Planning Agent** -- Breaks complex questions into sub-queries (we don't have this -- the agent loop handles it implicitly)
3. **Generation Agent** -- Writes SQL (our `generate_sql`)
4. **Validation/Correction Agent** -- Executes, checks results, retries on error (our `execute_sql` + the agent's ability to re-call the tool)

Our pipeline maps closely to DRGC. A `validate_sql` node was added between `generate_sql` and `execute_sql` (see issue #17) to catch syntax errors and unknown tables before hitting the database. The previously unused `create_query_validation_chain` was removed as dead code (issue #11) and replaced by this structural validation approach using `sqlglot`.

The other gap is **explicit error recovery routing**: if `execute_sql` fails, our pipeline reports the error via `format_results` and the agent decides whether to retry. An alternative is a conditional edge from `execute_sql` directly back to `generate_sql` (with `last_error` in state as context), which would be faster than going through the full agent loop.

---

## 6. Dependency Injection: `partial()` vs. Alternatives

### What We Chose

`functools.partial` to bind dependencies into node functions:

```python
builder.add_node(
    "extract_products",
    partial(extract_products_node, llm=llm, engine=engine),
)
```

### Alternative: `Runtime` with `context_schema` (LangGraph's recommended approach)

```python
from langgraph.runtime import Runtime
from dataclasses import dataclass

@dataclass
class AtlasContext:
    llm: BaseLanguageModel
    engine: Engine
    db: SQLDatabaseWithSchemas
    table_descriptions: Dict

def extract_products_node(state: AtlasAgentState, runtime: Runtime[AtlasContext]) -> dict:
    lookup = ProductAndSchemaLookup(llm=runtime.context.llm, connection=runtime.context.engine)
    ...

builder = StateGraph(AtlasAgentState, context_schema=AtlasContext)
graph.invoke(input, context=AtlasContext(llm=llm, engine=engine, db=db, ...))
```

**Pros**: Typed, swappable at invocation time, doesn't pollute state, official LangGraph pattern.
**Cons**: Newer API. Requires context at every `invoke()` call. The docs note that `context` is *not* persisted as state (good for secrets/connections, but they must be re-supplied on resume).

### Alternative: `RunnableConfig` for thread-level configuration

```python
def extract_products_node(state: AtlasAgentState, config: RunnableConfig) -> dict:
    llm = config["configurable"]["llm"]
    engine = config["configurable"]["engine"]
    ...
```

Lower-level, less typed. Good for accessing `thread_id` but awkward for complex dependencies.

### Alternative: Closures (what `create_sql_agent` partially does)

```python
def create_sql_agent(llm, engine, ...):
    def agent_node(state):
        model_with_tools = llm.bind_tools(...)  # llm captured by closure
        ...
    builder.add_node("agent", agent_node)
```

Our `agent_node` is already a closure. The pipeline nodes use `partial()` instead for testability -- closures can't easily have their captured variables swapped in tests.

### Comparison

| Approach | Typing | Testability | Official support |
|----------|--------|-------------|-----------------|
| `functools.partial` (ours) | Weak (kwargs) | Good (pass deps directly) | Not officially recommended |
| `Runtime` + `context_schema` | Strong (dataclass) | Good (pass context) | Recommended as of 2025 |
| `RunnableConfig` | None | Moderate | Supported but low-level |
| Closures | None | Poor (can't swap captures) | Common in examples |

---

## 7. Streaming: Current Approach vs. Alternatives

### What We Chose

Combined `stream_mode=["messages", "updates"]` with manual buffering in `stream_agent_response()`. Pipeline nodes are identified via `PIPELINE_NODES` frozenset.

### What the Docs Offer: Six Stream Modes

| Mode | Returns | Our use |
|------|---------|---------|
| `"values"` | Full state after each node | Used in non-streaming `answer_question()` |
| `"updates"` | State deltas per node | Used to detect pipeline ToolMessages and agent tool calls |
| `"messages"` | `(message_chunk, metadata)` per LLM token | Used for token-by-token streaming to UI |
| `"custom"` | Arbitrary data via `get_stream_writer()` | **Not used** -- could emit progress events |
| `"debug"` | Verbose node names + full state | Used in `stream_agent_response_debug()` |
| Combined list | `(mode, chunk)` tuples | Our primary streaming approach |

### Alternative: `stream_mode="custom"` for Progress Events

Instead of inferring pipeline progress from node names in "updates" mode, nodes could explicitly emit progress:

```python
from langgraph.config import get_stream_writer

def generate_sql_node(state, *, llm, ...):
    writer = get_stream_writer()
    writer({"step": "generate_sql", "status": "started"})
    sql = chain.invoke(...)
    writer({"step": "generate_sql", "status": "completed", "sql_preview": sql[:100]})
    return {"pipeline_sql": sql}
```

**Pros**: Explicit, structured progress events; doesn't depend on node naming conventions.
**Cons**: More code in each node; custom stream data must be consumed in the UI layer; requires `"custom"` in the stream mode list.

### Alternative: Streaming LLM Output from Pipeline Nodes

Currently, pipeline LLM calls (e.g., `generate_sql_node` calls `chain.invoke()`) are non-streaming. We could use `chain.stream()` inside nodes, but the outer graph's `stream_mode="messages"` already captures LLM tokens from *any* LangChain model invocation within any node, even when the node itself uses `.invoke()`. The docs confirm this:

> "Works even when the LLM is called with `.invoke` (not just `.stream`)"

This means SQL generation tokens are already streamable to the UI if we filter on `metadata["langgraph_node"] == "generate_sql"`. We just don't surface them because they'd show raw SQL to the user before it's complete.

---

## 8. Testing: Our Patterns vs. Doc-Recommended Patterns

### What We Chose

Three test layers:
1. **Node-level unit tests** (`test_pipeline_nodes.py`): Each node as a pure function with mocked dependencies
2. **Graph routing tests** (`test_graph_wiring.py`): `FakeToolCallingModel` + `pipeline_stub`
3. **Trajectory tests** (`test_agent_trajectory.py`): Full message sequence validation

### What the Docs Recommend

The LangGraph testing documentation describes three strategies:

**1. Basic agent execution test** -- Compile graph with `MemorySaver`, invoke, assert on final state.

We do this in `test_graph_wiring.py` and `test_agent_trajectory.py`.

**2. Individual node execution test** -- Access nodes via `compiled_graph.nodes["node_name"].invoke(state)`.

```python
result = compiled_graph.nodes["node1"].invoke({"my_key": "initial_value"})
```

We don't use this. Instead, we call node functions directly (bypassing the graph entirely). This is equivalent but avoids graph compilation overhead. The docs' approach has the advantage of testing the node as it would run in the graph (with any wrapping the compiler adds).

**3. Partial execution test** -- Use `update_state(..., as_node="node_name")` to resume from mid-graph.

```python
compiled_graph.update_state(
    config={"configurable": {"thread_id": "1"}},
    values={"my_key": "initial_value"},
    as_node="node1",  # Pretend node1 just finished
)
result = compiled_graph.invoke(None, config=config, interrupt_after="node3")
```

We don't use this but it could test specific pipeline segments without running the full graph.

### Our Testing Innovations vs. Alternatives

**`FakeToolCallingModel`**: Our deterministic model mock is a common pattern in the LangGraph community. The key design: `bind_tools()` is a no-op that returns `self`, since responses are pre-scripted. This is simpler than mocking the LLM provider's API.

**`pipeline_stub`**: Our test graph replaces the 8-node pipeline with a single stub node. This is a custom pattern not in the LangGraph docs. It isolates routing logic from pipeline logic, which is critical for testing conditional edges in isolation.

**Alternative: Property-based testing with Hypothesis**

Instead of scripted `FakeToolCallingModel` responses, generate random sequences of tool-call / no-tool-call messages and verify invariants (e.g., `queries_executed` never exceeds `max_uses`, every `ToolMessage` has a matching `tool_call_id`).

**Alternative: Snapshot testing**

Capture the full message trajectory as a JSON snapshot and compare against a known-good baseline. Useful for regression testing but brittle against message content changes.

---

## 9. Dependency Cleanup

### Removed

- **`langchain`** (main package) -- `create_agent` was the only import. After migrating to `StateGraph`, this dependency is no longer needed.

### Kept

| Package | Reason | Could we remove it? |
|---------|--------|-------------------|
| `langchain-core` | `BaseLanguageModel`, `SystemMessage`, `ToolMessage`, `PromptTemplate`, `StrOutputParser`, `Runnable`, `@tool` | No -- tightly integrated with LangGraph's type system |
| `langchain-openai` | `ChatOpenAI` model provider | Could replace with `init_chat_model()` from `langchain` for model-agnostic setup |
| `langchain-community` | `sql_multiple_schemas.py` inherits `SQLDatabase` | Could rewrite the schema-aware DB wrapper from scratch, but significant effort |
| `langchain-postgres` | PostgreSQL checkpointer | Required for production persistence |
| `langgraph` | Core framework | Required |
| `langgraph-checkpoint-postgres` | Production checkpointer | Required |

---

## 10. Future Design Directions

Based on the research, several patterns could improve the current architecture:

### Near-Term (Low Effort, High Value)

1. ~~**Wire in SQL validation node**~~: **Done** (issue #17). A `validate_sql` node using `sqlglot` was added between `generate_sql` and `execute_sql`. The dead `create_query_validation_chain` was removed (issue #11).

2. **Migrate from `partial()` to `Runtime` + `context_schema`**: Gives typed dependency injection and cleaner invocation. Wait for the API to stabilize post-LangGraph v1.0.

3. **Add `RetryPolicy` to LLM-calling nodes**: LangGraph's built-in retry with exponential backoff is cleaner than our manual `execute_with_retry`:
   ```python
   builder.add_node("generate_sql", generate_sql_node,
                     retry_policy=RetryPolicy(max_attempts=3, retry_on=rate_limit_check))
   ```

### Medium-Term (Moderate Effort)

4. **Input/Output schemas**: Define `InputState(question: str)` and `OutputState(answer: str)` to hide `pipeline_*` fields from the external API.

5. **`stream_mode="custom"` for structured progress**: Emit explicit progress events from pipeline nodes instead of inferring progress from node names.

6. **SQL error recovery edge**: Add a conditional edge from `execute_sql` back to `generate_sql` (with error context) for faster retry without going through the full agent loop.

### Longer-Term (Architectural)

7. **Subgraph extraction**: If Ask-Atlas grows to support multiple query types (e.g., graph queries, vector search), extract the SQL pipeline into a subgraph and add a routing layer.

8. **Human-in-the-loop**: Use `interrupt()` before `execute_sql` to let users review generated SQL before execution. Requires checkpointer (already in place).

9. **Guardrails pattern** (from OpenAI Agents SDK): Input validation (is this a trade question?) and output validation (does the answer make sense?) as first-class guardrail nodes rather than embedded in the system prompt.

---

## 11. Files Modified Summary

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

---

## Appendix: Sources

### LangGraph Official Documentation (via context7)
- [Graph API overview](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [State management and reducers](https://docs.langchain.com/oss/python/langgraph/use-graph-api)
- [Streaming modes](https://docs.langchain.com/oss/python/langgraph/streaming)
- [Testing patterns](https://docs.langchain.com/oss/python/langgraph/test)
- [Runtime configuration and context_schema](https://docs.langchain.com/oss/python/langgraph/use-graph-api)
- [Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-graph-api)
- [Interrupt and human-in-the-loop](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Error handling and RetryPolicy](https://docs.langchain.com/oss/python/langgraph/use-graph-api)

### External Sources (2025+)
- [Anthropic: Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)
- [LangChain: Build a custom SQL agent](https://docs.langchain.com/oss/python/langgraph/sql-agent)
- [Towards AI: Architecting State-of-the-Art Text-to-SQL Agents](https://pub.towardsai.net/architecting-state-of-the-art-text-to-sql-agents-for-enterprise-complexity-629c5c5197b8)
- [ZenML: Pydantic AI vs LangGraph](https://www.zenml.io/blog/pydantic-ai-vs-langgraph)
- [Langfuse: Comparing Open-Source AI Agent Frameworks](https://langfuse.com/blog/2025-03-19-ai-agent-comparison)
- [OpenAI: New Tools for Building Agents](https://openai.com/index/new-tools-for-building-agents/)
- [LangWatch: Best AI Agent Frameworks 2025](https://langwatch.ai/blog/best-ai-agent-frameworks-in-2025-comparing-langgraph-dspy-crewai-agno-and-more)
- [Microsoft Research: AutoGen v0.4](https://www.microsoft.com/en-us/research/articles/autogen-v0-4-reimagining-the-foundation-of-agentic-ai-for-scale-extensibility-and-robustness/)

---

## 12. Agent Paradigms: ReAct vs. CodeAct vs. ReWOO — and Why It Matters for Ask-Atlas

The agent framework landscape in 2025–2026 has crystallized around several distinct paradigms for how an LLM decides *what to do next*. Our current architecture uses a hybrid: a ReAct outer loop with a deterministic inner pipeline. Understanding the alternatives is essential for evaluating whether we've chosen well.

### The Three Main Paradigms

**ReAct (Reasoning and Acting)** — what our outer agent loop does:

The LLM follows a Thought → Action → Observation cycle. It reasons about the question, decides to call a tool (or respond directly), observes the tool result, and decides again. Each cycle is one full LLM call with the entire conversation history.

- **Strength**: Exceptional adaptability. The agent can change strategy after observing errors, ask different questions, retry with modified parameters.
- **Weakness**: High token usage and latency. Every reasoning step is a full LLM round-trip. Sequential by nature — the LLM must finish thinking before acting.
- **Token cost**: Grows with conversation length since the full message history is re-sent every turn.

**CodeAct (Code Actions)** — the paradigm from [Wang et al., ICML 2024](https://arxiv.org/html/2402.01030v4):

Instead of calling named tools, the LLM generates executable Python code. The code runs in a sandbox, and the output becomes the next observation. The LLM can use loops, conditionals, variables, and compose multiple operations in a single action.

- **Strength**: Up to 20% higher success rate vs. ReAct in benchmarks across 17 LLMs. Code naturally supports control flow (if/else, for loops), intermediate variable storage, and multi-step composition in a single action.
- **Weakness**: Security risk — running arbitrary LLM-generated code requires a locked-down sandbox (Docker, E2B, Pyodide). Higher iteration costs when code fails repeatedly. The LLM must be good at coding, not just reasoning.
- **Implementation**: HuggingFace's [smolagents](https://huggingface.co/docs/smolagents/en/index) is the leading open-source CodeAct framework. ~1000 lines, model-agnostic, with built-in sandbox options.

**ReWOO (Reasoning Without Observation)** — plan-then-execute:

A three-phase approach: (1) a Planner generates a complete multi-step strategy upfront, (2) Workers execute all planned tasks (potentially in parallel), (3) a Solver synthesizes the results.

- **Strength**: 5–10x token savings vs. ReAct. Parallel execution of planned steps. Efficient for predictable workflows.
- **Weakness**: Fragile. No mechanism to self-correct mid-execution. If the initial plan is wrong, the entire execution is wasted.

### Could Ask-Atlas Use CodeAct?

A CodeAct version of Ask-Atlas would look like this:

```python
# The LLM would generate code like this:
schemas = extract_schemas("What did Brazil export in 2022?")  # LLM-generated call
codes = lookup_product_codes(schemas.products, engine)
table_info = get_table_info(schemas.classification_schemas)
sql = generate_sql(question, codes, table_info)
result = execute_query(sql, engine)
print(format_results(result))
```

The LLM writes Python that calls our pipeline functions directly, with full control over ordering, error handling, and data flow. This is essentially what smolagents' [`CodeAgent` does for text-to-SQL](https://huggingface.co/learn/cookbook/agent_text_to_sql).

**Arguments for CodeAct in Ask-Atlas**:

1. **Self-correction is native**: If a SQL query returns an error, the LLM can catch the exception and rewrite the query in the same code block — no round-trip through the full agent loop.
2. **Flexible composition**: The LLM could skip product lookup for questions that don't mention products, or run multiple queries and join results in pandas — all in one code generation step.
3. **Variable reuse**: Intermediate results are Python variables, naturally composable. No need for `pipeline_*` state fields in a shared TypedDict.
4. **Benchmark superiority**: CodeAct outperforms tool-calling ReAct in general benchmarks.

**Arguments against CodeAct for Ask-Atlas**:

1. **Our pipeline order is not negotiable**. Product extraction *must* happen before code lookup, which *must* happen before SQL generation. In a CodeAct setup, the LLM *could* decide to skip product lookup or generate SQL without table info. Our current graph edges enforce this ordering mechanically — the LLM cannot bypass it. With CodeAct, correctness depends on the LLM generating the right code every time.

2. **Security surface**. CodeAct requires executing arbitrary LLM-generated Python against our production database. Even with a sandbox, the attack surface is fundamentally larger than a schema-only tool where the LLM can only emit a `question` string. Our current pipeline controls exactly what SQL reaches the database; CodeAct would need to defend against arbitrary code paths.

3. **Streaming granularity lost**. Our 8-node pipeline emits per-node updates via `stream_mode="updates"`, letting the UI show "Extracting products...", "Generating SQL...", etc. In CodeAct, the entire pipeline is one code execution block — the UI sees "Running code..." until it's done.

4. **Testability regression**. Each pipeline node is individually testable with mocked dependencies. CodeAct collapses the pipeline into generated code that varies between runs — you'd need to test the *code generation* behavior rather than the *pipeline logic*.

5. **Benchmarks mislead for structured tasks**. CodeAct's 20% improvement comes from *general-purpose* benchmarks (API-Bank) where the flexibility of code composition matters. For our fixed-order pipeline, that flexibility is a liability, not an asset.

**Verdict**: CodeAct is the wrong paradigm for our *inner pipeline* but could be interesting as an *alternative execution mode* for power users. Imagine a "data analyst mode" where the agent writes and executes Python/pandas code directly for complex analytical questions that don't fit our standard pipeline (e.g., "Compare the export growth rates of the top 10 products between Brazil and Mexico over the last decade and plot the trends"). This would be a separate tool alongside `query_tool`, not a replacement.

### Could Ask-Atlas Use ReWOO?

ReWOO's plan-then-execute model maps poorly to Ask-Atlas because:

1. Our pipeline steps have strict sequential dependencies — product extraction must complete before code lookup can start. ReWOO's parallel execution only helps when steps are independent.
2. Our agent often needs multiple rounds (query → observe results → refine query), which requires the adaptive reasoning that ReWOO explicitly eliminates.
3. The token savings are irrelevant — our bottleneck is LLM latency and database query time, not token consumption.

ReWOO would be relevant if Ask-Atlas needed to answer questions requiring *multiple independent queries* simultaneously (e.g., "Compare Brazil's exports to China vs. India" → two parallel pipeline runs). But even then, LangGraph's fan-out/fan-in handles this natively without adopting a different agent paradigm.

### The Hybrid Is Right — But the Labels Matter

Our architecture is actually not pure ReAct. It's:

- **Outer loop**: ReAct (LLM decides whether to query or respond)
- **Inner pipeline**: Deterministic workflow (fixed node sequence enforced by graph edges)

This is closer to what Anthropic's ["Building Effective Agents"](https://www.anthropic.com/research/building-effective-agents) guide calls a **workflow with an agentic outer layer** — the agent delegates to a structured process rather than improvising each step. The guide explicitly recommends this pattern: "Workflows are appropriate when the task can be decomposed into fixed subtasks... agents are better when flexibility is needed at the task level."

We use agent reasoning where it matters (deciding *what* to query, interpreting *results*) and deterministic control where reliability matters (the SQL generation pipeline). This is the right split.

---

## 13. The Anthropic Agent SDK: Why Not Use It?

The [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview) (released May 2025 as "Claude Code SDK", renamed September 2025) provides the same infrastructure powering Claude Code as a programmable library. It includes built-in tools (file read/write/edit, bash execution, web search), automatic context management, session persistence, and MCP extensibility.

### What the Agent SDK Does Well

The SDK excels at **general-purpose autonomous agents** that operate like a human at a computer:

- **SRE agents** that read logs, analyze metrics, execute diagnostic scripts, and propose fixes
- **Code migration agents** that read source files, understand patterns, and rewrite code
- **Research agents** that search the web, read documents, and synthesize findings

The core loop is: **gather context → take action → verify work → repeat**. The SDK manages the agent loop, context window, tool execution, and retries automatically. You provide tools (via MCP servers or custom definitions) and the agent decides how to use them.

### Why the Agent SDK Is Wrong for Ask-Atlas

**1. No workflow graph control.** The Agent SDK is a ReAct loop — Claude decides what to do next at every step. There is no mechanism to define deterministic node sequences, conditional edges, or fan-out/fan-in parallelism. Our 8-node pipeline *must* execute in a specific order; the Agent SDK would require Claude to learn and reliably follow that order via prompt instructions alone.

**2. Claude-only.** The Agent SDK is tightly coupled to Anthropic's Claude models. Ask-Atlas uses OpenAI models for SQL generation (`settings.query_model`) and metadata extraction (`settings.metadata_model`). Our pipeline needs model-agnostic orchestration — different steps may benefit from different models (e.g., a smaller model for product extraction, a larger model for complex SQL generation, potentially a specialized fine-tuned model for code lookup).

**3. No typed state management.** LangGraph's `TypedDict` state with reducers gives us compile-time visibility into what each node reads and writes. The Agent SDK's state is the conversation context — an opaque message stream managed internally by the SDK. There's no equivalent to `pipeline_codes`, `pipeline_sql`, or `queries_executed` as first-class typed fields.

**4. Pipeline as a single tool.** To use the Agent SDK, we'd need to collapse our pipeline into a single MCP tool. The SDK [shows this pattern for database queries](https://platform.claude.com/docs/en/agent-sdk/mcp) — connect a Postgres MCP server and let Claude write SQL directly:

```python
async for message in query(
    prompt="How many users signed up last week?",
    options=ClaudeAgentOptions(
        mcp_servers={"postgres": {"command": "npx", "args": [...]}},
        allowed_tools=["mcp__postgres__query"]
    )
):
    print(message.result)
```

This is a CodeAct-like approach where Claude writes SQL directly. It works for simple queries but completely bypasses our product code lookup, schema-aware table selection, and few-shot prompt engineering — the components that make Ask-Atlas work on the Harvard Growth Lab's trade data with its complex multi-schema classification system.

**5. No per-step streaming.** Our Streamlit UI shows pipeline progress: "Extracting products...", "Looking up codes...", "Generating SQL...", "Executing query...". The Agent SDK streams at the message level (Claude's text output and tool calls), but a collapsed pipeline tool would be a single opaque step.

**6. No conditional routing.** Our `route_after_agent` implements a 3-way branch: END (respond to user), pipeline (run query), or max_queries_exceeded (rate limit). The Agent SDK provides no equivalent — you'd need to embed rate limiting in tool execution logic, losing the clean graph-level visibility.

### Where the Agent SDK *Would* Make Sense

The Agent SDK would be appropriate for Ask-Atlas if:

- **We wanted a "give it a database and let it figure it out" approach** — Claude explores the schema, writes queries, iterates on errors. This is viable for simpler databases but unreliable for our multi-schema trade data with implicit classification hierarchies.
- **We were building a Claude-only product** — if we committed to Claude as the sole LLM provider and didn't need the model flexibility.
- **The pipeline were truly simple** — if text-to-SQL were just "generate SQL, execute, return results" without product code lookup, schema selection, or few-shot prompting.
- **We wanted rapid prototyping** — the Agent SDK gets you to a working demo in minutes. For production with our specific requirements, LangGraph's explicitness pays for itself.

### The Deeper Issue: Agent SDKs vs. Orchestration Frameworks

The [LangChain blog](https://blog.langchain.com/how-to-think-about-agent-frameworks/) frames this well: the hard thing about building reliable agents is making sure the agent has the right context at the right time. Agent SDKs (Anthropic's, OpenAI's) *obfuscate* context management — they handle it for you, which is great when it works but opaque when it doesn't. Orchestration frameworks like LangGraph *expose* it — more work upfront, but full visibility and control.

For Ask-Atlas, where the pipeline has been carefully engineered to provide exactly the right context at each step (product codes for SQL generation, schema-specific table DDL, curated few-shot examples), we need that control. An Agent SDK's automatic context management would likely inject too much or too little context at each step.

---

## 14. Optimal Workflow Design: Parallelism, Sequencing, and Latency

### Current Latency Profile

Our pipeline's critical path per tool invocation:

```
Step                     | Nature        | Estimated Latency
─────────────────────────┼───────────────┼──────────────────
agent_node (LLM)         | I/O (LLM)    | 1–10s
extract_tool_question    | CPU           | ~0ms
extract_products_node    | I/O (LLM)    | 0.5–3s
lookup_codes_node        | I/O (DB+LLM) | 2–5s
get_table_info_node      | CPU (memory)  | ~10ms
generate_sql_node        | I/O (LLM)    | 2–15s
execute_sql_node         | I/O (DB)      | 0.1–10s
format_results_node      | CPU           | ~0ms
agent_node (LLM, final)  | I/O (LLM)    | 1–10s
─────────────────────────┴───────────────┴──────────────────
Total wall clock (happy path): ~7–50s
```

The three LLM calls dominate: `extract_products` (metadata model, ~1–3s), `lookup_codes`'s final selection (metadata model, ~1–2s), and `generate_sql` (primary model, ~2–15s). The database calls (`lookup_codes`'s candidate search + `execute_sql`) add 0.1–15s depending on query complexity.

### Data Dependency Graph

```
                    extract_tool_question
                            │
                    extract_products_node
                       ╱           ╲
              lookup_codes    get_table_info    ← can run in parallel
                       ╲           ╱
                    generate_sql_node
                            │
                     execute_sql_node
                            │
                    format_results_node
```

True dependencies:
- `extract_products` needs only `pipeline_question` → no upstream pipeline dependency
- `lookup_codes` needs `pipeline_products` and `pipeline_question`
- `get_table_info` needs `pipeline_products.classification_schemas`
- `generate_sql` needs `pipeline_codes` AND `pipeline_table_info` → must wait for both
- `execute_sql` needs `pipeline_sql`
- `format_results` needs `pipeline_result` and `last_error`

### Optimization 1: Parallelize lookup_codes and get_table_info

**Savings: Minimal (~10ms).** `get_table_info` is already fast (in-memory metadata lookup). Parallelizing it with `lookup_codes` saves only the time `get_table_info` spends waiting in the sequential queue. Worth doing for correctness of the dependency graph, but not a meaningful latency win.

```python
# LangGraph fan-out from extract_products:
builder.add_edge("extract_products", "lookup_codes")
builder.add_edge("extract_products", "get_table_info")
# Fan-in at generate_sql:
builder.add_edge("lookup_codes", "generate_sql")
builder.add_edge("get_table_info", "generate_sql")
```

### Optimization 2: Pre-compute Table Info for All Schemas

**Savings: Decouples `get_table_info` from `extract_products` entirely.**

Currently, `get_table_info_node` reads `pipeline_products.classification_schemas` to know *which* schemas to fetch DDL for. But the DDL is already in memory (reflected at startup). If we pre-computed DDL strings for all schemas at startup and stored them in a dict, `get_table_info` would become a simple dict lookup from the question — or we could pass *all* schema DDL to `generate_sql` and let the LLM ignore irrelevant tables.

**Trade-off**: Passing all schemas increases the SQL generation prompt size. With 4–6 schemas × 5–10 tables each, the DDL could grow to 3,000–5,000 tokens. With prompt caching (supported by both OpenAI and Anthropic as of 2025), this constant prefix is cached across calls and adds negligible latency. Without caching, the extra tokens increase `generate_sql` latency by 0.5–2s.

**Implementation**:
```python
# At startup:
all_table_info = {schema: get_table_info_for_schemas(db, table_desc, [schema])
                  for schema in ALL_SCHEMAS}

# At pipeline time: skip get_table_info_node entirely, OR:
def get_table_info_node(state, *, precomputed_info):
    schemas = state["pipeline_products"].classification_schemas
    return {"pipeline_table_info": "\n".join(precomputed_info[s] for s in schemas)}
```

This is a low-effort, low-risk optimization.

### Optimization 3: Parallelize Per-Product Database Lookups

**Savings: Significant when multiple products are mentioned (2–4x for 2–4 products).**

Inside `lookup_codes_node`, the current code calls `get_candidate_codes(products)`, which iterates over products sequentially. Each product triggers 2–4 database queries (exact code match + text search + trigram fallback). With 3 products, that's 6–12 sequential DB round-trips at 5–50ms each = 30–600ms total.

These are independent — product A's code lookup doesn't depend on product B's results. With `asyncio.gather()` or LangGraph's `Send` API for dynamic fan-out:

```python
# Current: sequential
for product in products:
    candidates = get_candidate_codes(product, engine)

# Optimized: parallel
import asyncio
candidates = await asyncio.gather(
    *[get_candidate_codes_async(product, engine) for product in products]
)
```

This requires making the DB lookup functions async (or using `concurrent.futures.ThreadPoolExecutor` for sync code). The final LLM call in `lookup_codes` (selecting among all candidates) must still wait for all lookups to complete.

### Optimization 4: Prompt Caching for generate_sql

**Savings: 1–5s reduction on the largest bottleneck.**

The `generate_sql` prompt consists of:
1. System instructions + SQL dialect rules (~500 tokens, static)
2. Table DDL for selected schemas (~1,000–3,000 tokens, varies by schema but repeats across queries in the same schema)
3. Few-shot example queries (~2,000–5,000 tokens, static)
4. The actual question + product codes (~100–300 tokens, varies)

Parts 1–3 are highly cacheable. OpenAI's [prompt caching](https://platform.openai.com/docs/guides/prompt-caching) (automatic for prompts >1,024 tokens) and Anthropic's [explicit cache control](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) both reduce time-to-first-token for repeated prefixes by 50–80%. For our use case, where users often ask multiple questions about the same schema, this is a significant win.

**Implementation**: Ensure the prompt template puts static content first (system prompt → few-shot examples → table DDL → question). This maximizes cache hit rate since the prefix stays constant across queries.

### Optimization 5: SQL Error Recovery Shortcut

**Savings: Eliminates one full agent LLM round-trip (~2–10s) on retry.**

Currently, if `execute_sql` fails:
1. `format_results` packages the error as a `ToolMessage`
2. The agent receives the error, reasons about it (~2–10s LLM call)
3. The agent calls `query_tool` again with a modified question
4. The entire pipeline runs again (~5–30s)

An alternative: add a conditional edge from `execute_sql` directly back to `generate_sql`, with the error in state:

```python
def route_after_execute(state):
    if state["last_error"] and state.get("retry_count", 0) < MAX_RETRIES:
        return "generate_sql"  # Retry SQL generation with error context
    return "format_results"  # Proceed normally (success or max retries)

builder.add_conditional_edges("execute_sql", route_after_execute,
                               ["generate_sql", "format_results"])
```

The `generate_sql` node would check `last_error` and include it in the prompt: "The previous query failed with: {error}. Rewrite the query to avoid this error."

**Trade-off**: Faster retry (~2–15s for just SQL regeneration vs. ~7–40s for a full agent loop), but the agent loses the ability to fundamentally rethink the approach (e.g., querying a different schema, asking a clarifying question). Best used for syntax errors and timeout errors, not semantic failures.

### Optimization 6: Speculative Schema Pre-fetch

**Savings: Up to 1–3s by overlapping extract_products with early DB work.**

Some questions contain explicit schema hints: "HS92 exports from Brazil" clearly refers to the hs92 schema. A lightweight regex/keyword matcher could identify likely schemas *before* the LLM extraction completes, and speculatively start code lookup for mentioned products:

```python
def speculative_prefetch(state):
    question = state["pipeline_question"]
    likely_schemas = regex_detect_schemas(question)  # ~0ms
    likely_products = regex_detect_products(question)  # ~0ms
    if likely_schemas and likely_products:
        # Start DB lookups speculatively
        return {"speculative_candidates": fetch_candidates(likely_products, likely_schemas)}
    return {}
```

This would run in parallel with `extract_products_node`. If the LLM's extraction matches the speculative results, `lookup_codes` can skip its DB calls entirely. If they differ, the speculative results are discarded.

**Trade-off**: Complexity for marginal gain. Only helps when the question has explicit schema/product references, which is common for experienced users but rare for natural language questions like "What did Brazil export last year?"

### The Optimal Pipeline (Proposed)

Combining the most impactful optimizations:

```
extract_tool_question
        │
        ├── extract_products_node          (LLM, 0.5–3s)
        │       │
        │       ├── lookup_codes_node      (DB parallel per product + LLM, 1–3s)
        │       └── get_table_info_node    (memory, ~0ms)
        │               │
        │               └──┬── [both fan in]
        │                  │
        │           generate_sql_node      (LLM w/ prompt cache, 1–10s)
        │                  │
        │            execute_sql_node      (DB, 0.1–10s)
        │               ╱       ╲
        │     [error + retry<3]  [success or max retries]
        │         ↓                    ↓
        │   generate_sql_node    format_results_node
        │                              │
        └──────────────────────────────→ agent
```

**Estimated savings**:
- Prompt caching on `generate_sql`: −1–5s (most impactful)
- Parallel per-product DB lookups: −0.1–0.5s (modest)
- SQL error recovery shortcut: −5–15s per retry (significant when errors occur)
- Pre-computed table info: −10ms (negligible, but simplifies the graph)

**Estimated optimized wall clock**: ~5–25s (down from ~7–50s), with the largest gains from prompt caching and the error recovery shortcut.

### Why Not Parallelize More Aggressively?

The fundamental constraint is that our pipeline is a *chain of transformations* where each step produces the input for the next. The only true independence is between `lookup_codes` and `get_table_info` (both consume `pipeline_products`, produce separate outputs for `generate_sql`).

Proposals to parallelize further break down:
- **Run `extract_products` in parallel with something**: Nothing else can start until we know what products and schemas the question refers to. This is the pipeline's serial bottleneck.
- **Run `generate_sql` before `lookup_codes` completes**: SQL generation needs the product codes to generate correct WHERE clauses. Without codes, the SQL would be wrong.
- **Run `execute_sql` speculatively with partial SQL**: Executing incomplete SQL wastes database resources and could return misleading results.

The honest conclusion: **most of our latency is irreducible** because it's spent on sequential LLM calls that each need the previous step's output. The biggest wins come not from parallelism but from **making each LLM call faster** (prompt caching, smaller prompts, faster models) and **reducing the number of LLM round-trips** (error recovery shortcut, pre-computed table info eliminating a dependency).

---

## 15. Framework Verdict: LangGraph Remains the Right Choice

### Decision Matrix

| Criterion | LangGraph | Anthropic Agent SDK | smolagents (CodeAct) | Raw Anthropic/OpenAI SDK |
|-----------|-----------|--------------------|--------------------|------------------------|
| **Deterministic pipeline control** | Native (graph edges) | None (ReAct loop only) | None (code generation) | Manual implementation |
| **Per-step streaming** | Native (`stream_mode="updates"`) | Message-level only | None (single code block) | Manual implementation |
| **Model agnostic** | Yes (any LangChain-compatible) | Claude only | Yes (via LiteLLM) | Provider-specific |
| **Typed state management** | `TypedDict` + reducers | Opaque context window | Python variables | Manual implementation |
| **Parallelism (fan-out/fan-in)** | Native (supersteps) | Via subagents only | Code-level (`asyncio`) | Manual implementation |
| **Checkpointing/resume** | Built-in (`MemorySaver`, Postgres) | Built-in (sessions) | None | Manual implementation |
| **Conditional routing** | `add_conditional_edges` | LLM decides | Code-level (`if/else`) | Manual implementation |
| **Error recovery** | Conditional edges + `RetryPolicy` | LLM self-corrects | Code-level (`try/except`) | Manual implementation |
| **Testability** | Per-node unit tests | End-to-end only | Code output testing | Per-function unit tests |
| **Community/maturity** | High (largest ecosystem) | Growing (released 2025) | Moderate (HuggingFace) | Highest (raw API) |

### Why LangGraph Wins for Ask-Atlas

1. **The pipeline is a workflow, not an agent task.** Our 8-node SQL generation pipeline has a fixed order, deterministic transitions, and no need for LLM-driven routing. LangGraph's graph edges enforce this mechanically. Neither the Agent SDK (pure ReAct) nor CodeAct (LLM-generated control flow) offer this guarantee.

2. **Model diversity is a feature.** We use different models for different steps (metadata model for product extraction, primary model for SQL generation). LangGraph is model-agnostic; the Agent SDK locks us to Claude.

3. **Streaming granularity matters for UX.** Our Streamlit UI shows real-time progress through the pipeline. LangGraph's per-node streaming enables this. The Agent SDK and CodeAct would collapse the pipeline into opaque steps.

4. **The outer loop IS a ReAct agent — and LangGraph implements it.** LangGraph's conditional edges give us a ReAct outer loop (agent decides to query or respond) without sacrificing pipeline control. We get the best of both worlds in one framework.

5. **Parallelism has a clear upgrade path.** LangGraph's fan-out/fan-in is the natural way to parallelize `lookup_codes` and `get_table_info`, and its `Send` API handles dynamic per-product fan-out. No other framework makes this as clean.

### When to Reconsider

- **If we move to Claude as the sole LLM provider** and want rapid iteration over production reliability, the Agent SDK becomes viable. But we'd still need to handle the pipeline-as-single-tool limitation.
- **If we add a "data analyst mode"** for power users, a CodeAct approach (smolagents or direct code execution) would complement the existing pipeline rather than replace it.
- **If LangGraph's complexity becomes a maintenance burden** and the pipeline stabilizes to the point where we rarely modify it, a raw SDK implementation with manual orchestration could be simpler.

---

## Appendix: Additional Sources (Section 12–15)

### Agent Paradigms
- [Capabl: Agentic AI Design Patterns — ReAct, ReWOO, CodeAct, and Beyond](https://capabl.in/blog/agentic-ai-design-patterns-react-rewoo-codeact-and-beyond)
- [Wang et al.: Executable Code Actions Elicit Better LLM Agents (ICML 2024)](https://arxiv.org/html/2402.01030v4)
- [Apple ML Research: CodeAct](https://machinelearning.apple.com/research/codeact)
- [HuggingFace: smolagents](https://huggingface.co/docs/smolagents/en/index)
- [HuggingFace: Agent for text-to-SQL with automatic error correction](https://huggingface.co/learn/cookbook/agent_text_to_sql)
- [HuggingFace: CodeAgents + Structure](https://huggingface.co/blog/structured-codeagent)
- [Medium: CodeAgent — The Evolution Beyond Tool-Calling](https://medium.com/@barunsaha/codeagent-the-evolution-beyond-tool-calling-7792781e19f4)

### Anthropic Agent SDK
- [Claude Agent SDK Documentation](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Anthropic: Building Agents with the Claude Agent SDK](https://claude.com/blog/building-agents-with-the-claude-agent-sdk)
- [Medium: The Definitive Guide to the Claude Agent SDK (Feb 2026)](https://datapoetica.medium.com/the-definitive-guide-to-the-claude-agent-sdk-building-the-next-generation-of-ai-69fda0a0530f)
- [SkyWork: Claude Agent SDK Best Practices (2025)](https://skywork.ai/blog/claude-agent-sdk-best-practices-ai-agents-2025/)

### Framework Comparisons
- [LangChain: How to Think About Agent Frameworks](https://blog.langchain.com/how-to-think-about-agent-frameworks/)
- [Langfuse: Comparing Open-Source AI Agent Frameworks](https://langfuse.com/blog/2025-03-19-ai-agent-comparison)
- [Enhancial: AI Framework Comparison 2025 — OpenAI vs Claude vs LangGraph](https://enhancial.substack.com/p/choosing-the-right-ai-framework-a)
- [Softcery: 14 AI Agent Frameworks Compared](https://softcery.com/lab/top-14-ai-agent-frameworks-of-2025-a-founders-guide-to-building-smarter-systems)
- [AI Multiple: Top 5 Open-Source Agentic AI Frameworks in 2026](https://aimultiple.com/agentic-frameworks)
- [Chatbase: LLM Agent Frameworks 2026 Guide](https://www.chatbase.co/blog/llm-agent-framework-guide)

### LangGraph Parallelism
- [AI Practitioner: Scaling LangGraph Agents — Parallelization, Subgraphs, and Map-Reduce Trade-Offs](https://aipractitioner.substack.com/p/scaling-langgraph-agents-parallelization)
- [LangChain Changelog: Deferred Nodes in LangGraph](https://changelog.langchain.com/announcements/deferred-nodes-in-langgraph)
- [GitHub Discussion: Branches for parallel node execution](https://github.com/langchain-ai/langgraph/discussions/2931)
