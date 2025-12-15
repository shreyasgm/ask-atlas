# Ask-Atlas Technical Overview

> A comprehensive technical guide to the Ask-Atlas AI agent for international trade data analysis.  
> Last updated: December 2024

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Detailed Workflow](#detailed-workflow)
4. [Component Deep Dive](#component-deep-dive)
5. [Database Schema](#database-schema)
6. [Feature Set Analysis](#feature-set-analysis)
7. [Known Simplifications & Nerfs](#known-simplifications--nerfs)
8. [Development & Testing](#development--testing)
9. [Future Enhancements](#future-enhancements)
10. [Quick Start for Developers](#quick-start-for-developers)

---

## Executive Summary

Ask-Atlas is an AI-powered text-to-SQL agent that answers natural language questions about international trade data. It uses:

- **LangChain + LangGraph** for agent orchestration
- **OpenAI GPT-4o** as the underlying LLM
- **PostgreSQL** database with the Atlas of Economic Complexity trade data
- **Streamlit** for the web UI frontend

The agent follows a multi-step workflow that includes:
1. Input validation (safety and relevance checks)
2. Schema and product classification detection
3. Product code lookup (LLM suggestions + database verification)
4. Dynamic SQL generation with few-shot examples
5. Query execution and response formatting

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                STREAMLIT UI                                  │
│                              (app.py)                                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AtlasTextToSQL                                     │
│                          (src/text_to_sql.py)                                │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  • Initializes database connection                                   │    │
│  │  • Loads example queries & table descriptions                        │    │
│  │  • Creates the ReAct agent with query_tool                           │    │
│  │  • Manages conversation threads                                       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ReAct Agent                                        │
│                     (LangGraph create_react_agent)                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  • System prompt with trade domain expertise                         │    │
│  │  • Technical metrics documentation (RCA, ECI, PCI, COG, etc.)        │    │
│  │  • Can use query_tool up to N times per question                     │    │
│  │  • Maintains conversation history via MemorySaver                     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            query_tool                                        │
│                      (src/generate_query.py)                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Full chain pipeline:                                                │    │
│  │  1. Extract schemas and product mentions                             │    │
│  │  2. Get candidate product codes (LLM + DB search)                    │    │
│  │  3. Select final codes                                               │    │
│  │  4. Generate SQL query with few-shot examples                        │    │
│  │  5. Execute query against database                                   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SQLDatabaseWithSchemas                                │
│                    (src/sql_multiple_schemas.py)                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Extended SQLDatabase class that:                                    │    │
│  │  • Supports multiple PostgreSQL schemas                              │    │
│  │  • Schema-qualified table names (e.g., hs92.country_product_year_4)  │    │
│  │  • Read-only execution mode                                          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Workflow

### Step 1: User Input Reception

```
User Question → Streamlit UI → AtlasTextToSQL.answer_question()
```

The Streamlit app (`app.py`) maintains:
- Session state for database connection
- Thread ID for conversation history
- Message history for chat UI

### Step 2: Agent Processing

The ReAct agent receives the question and follows this logic:

```python
# Simplified pseudo-code of agent behavior
1. Initial Checks:
   - Safety check: Is the question harmful or inappropriate?
   - Relevance check: Is this about international trade data?
   
2. For Simple Questions:
   - Send directly to query_tool
   - Return answer
   
3. For Complex Questions:
   - Break down into sub-questions
   - Execute query_tool for each sub-question
   - Synthesize final answer
```

**Agent System Prompt Key Points:**
- Can use query_tool up to `max_uses` times (default: 10)
- Each query returns at most `top_k_per_query` rows (default: 15)
- Must be precise and efficient with queries
- Converts large dollar amounts to readable formats (millions, billions)
- Responds in markdown format

### Step 3: Query Tool Pipeline

The `query_tool` executes a LangChain LCEL pipeline:

```
┌────────────────────┐
│   User Question    │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐     ┌─────────────────────────────────────────────┐
│ RunnableParallel   │────▶│ 1. mentions_chain: Extract schemas + products │
│                    │     │ 2. Pass through question                      │
└─────────┬──────────┘     └─────────────────────────────────────────────┘
          │
          ▼
┌────────────────────┐     ┌─────────────────────────────────────────────┐
│ Product Code       │────▶│ 1. get_candidate_codes: LLM suggestions       │
│ Resolution         │     │    + Database text search (FTS + trigram)     │
│                    │     │ 2. select_final_codes: LLM picks best codes   │
└─────────┬──────────┘     └─────────────────────────────────────────────┘
          │
          ▼
┌────────────────────┐     ┌─────────────────────────────────────────────┐
│ Table Info         │────▶│ Get table info for selected schemas           │
│ Retrieval          │     │ (filters out tables with "group" in name)     │
└─────────┬──────────┘     └─────────────────────────────────────────────┘
          │
          ▼
┌────────────────────┐     ┌─────────────────────────────────────────────┐
│ SQL Generation     │────▶│ Few-shot prompt with example queries          │
│                    │     │ Uses GPT-4o to generate SQL                   │
└─────────┬──────────┘     └─────────────────────────────────────────────┘
          │
          ▼
┌────────────────────┐     ┌─────────────────────────────────────────────┐
│ Query Execution    │────▶│ Execute via QuerySQLDatabaseTool              │
│                    │     │ Return results or "no results" message        │
└────────────────────┘     └─────────────────────────────────────────────┘
```

### Step 4: Product and Schema Lookup

The `ProductAndSchemaLookup` class (`src/product_and_schema_lookup.py`) handles:

**Available Schemas:**
| Schema | Description |
|--------|-------------|
| `hs92` | Trade data for goods in HS 1992 classification |
| `hs12` | Trade data for goods in HS 2012 classification |
| `sitc` | Trade data for goods in SITC classification |
| `services_unilateral` | Services trade for single country |
| `services_bilateral` | Services trade between two countries |

**Product Code Resolution:**
1. **LLM Suggestion**: GPT-4o suggests likely product codes based on product name
2. **Database Verification**: Verify codes exist via SQL query
3. **Full-Text Search**: PostgreSQL `tsvector/tsquery` search for product names
4. **Fuzzy Search Fallback**: Trigram similarity (`pg_trgm`) for misspellings
5. **Final Selection**: LLM picks best codes from candidates

---

## Component Deep Dive

### AtlasTextToSQL Class (`src/text_to_sql.py`)

```python
class AtlasTextToSQL:
    """Main orchestrator class"""
    
    def __init__(self, db_uri, table_descriptions_json, 
                 table_structure_json, queries_json, 
                 example_queries_dir, max_results=15):
        # Initialize DB connection with read-only mode
        self.engine = create_engine(db_uri, 
            execution_options={"postgresql_readonly": True})
        
        # Initialize multi-schema database wrapper
        self.db = SQLDatabaseWithSchemas(engine=self.engine)
        
        # Load configuration files
        self.table_descriptions = self._load_json_as_dict(table_descriptions_json)
        self.table_structure = self._load_json_as_dict(table_structure_json)
        self.example_queries = load_example_queries(queries_json, example_queries_dir)
        
        # Initialize LLMs (using GPT-4o for both)
        self.metadata_llm = ChatOpenAI(model="gpt-4o", temperature=0)
        self.query_llm = ChatOpenAI(model="gpt-4o", temperature=0)
        
        # Create the agent
        self.agent = create_sql_agent(...)
    
    def answer_question(self, question, stream_response=True, thread_id=None):
        """Process a question and return streamed or full response"""
        # Uses thread_id for conversation history
        config = {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}
        
        if stream_response:
            # Returns generator + messages list for UI streaming
            return stream_agent_response(config), messages
        else:
            # Returns complete response string
            return final_message
```

### SQL Agent Creation (`src/generate_query.py`)

```python
def create_sql_agent(llm, db, engine, table_descriptions, 
                     example_queries, top_k_per_query=15, max_uses=3):
    """Creates a ReAct agent with query capabilities"""
    
    # Create the query tool
    query_tool = create_query_tool(...)
    
    # Agent system prompt includes:
    # - Trade domain expertise
    # - Technical metrics definitions (RCA, ECI, PCI, COG, etc.)
    # - Query limits and guidelines
    # - Response formatting rules
    
    # Create agent with memory
    memory = MemorySaver()
    agent = create_react_agent(
        model=llm,
        tools=[query_tool],
        checkpointer=memory,
        state_modifier=SystemMessage(content=AGENT_PREFIX)
    )
    
    return agent
```

### Query Tool Chain (`src/generate_query.py`)

```python
def create_query_tool(llm, db, engine, table_descriptions, 
                      example_queries, max_results=15, max_uses=3):
    """Creates the comprehensive query tool"""
    
    uses_counter = {"current": 0}  # Track usage
    
    # Product lookup components
    product_lookup = ProductAndSchemaLookup(llm=llm, connection=engine)
    mentions_chain = product_lookup.extract_schemas_and_product_mentions()
    
    # Product code resolution chain
    codes_chain = (
        RunnableLambda(product_lookup.get_candidate_codes)
        | RunnableLambda(product_lookup.select_final_codes)
        | RunnableLambda(format_product_codes_for_prompt)
    )
    
    # Table info chain
    table_info_chain = RunnableLambda(get_table_info_for_schemas)
    
    # SQL generation chain
    query_chain = create_query_generation_chain(llm, example_queries)
    
    # Query execution
    execute_query = QuerySQLDatabaseTool(db=db)
    
    # Full pipeline
    full_chain = (
        RunnableParallel({
            "products_found": mentions_chain,
            "question": itemgetter("question") | RunnablePassthrough()
        })
        | {
            "codes": itemgetter("products_found") | codes_chain,
            "table_info": itemgetter("products_found") | table_info_chain,
            "top_k": lambda x: max_results,
            "question": itemgetter("question")
        }
        | {"query": query_chain, "question": itemgetter("question")}
        | execute_query_chain
    )
    
    @tool("query_tool", args_schema=QueryToolInput)
    def query_tool(question: str) -> str:
        uses_counter["current"] += 1
        if uses_counter["current"] > max_uses:
            return "Error: Maximum number of queries exceeded."
        return full_chain.invoke({"question": question})
    
    return query_tool
```

### Multi-Schema Database Support (`src/sql_multiple_schemas.py`)

LangChain's `SQLDatabase` doesn't natively support multiple PostgreSQL schemas. This custom class extends it:

```python
class SQLDatabaseWithSchemas(SQLDatabase):
    """SQLDatabase subclass supporting multiple schemas"""
    
    def __init__(self, engine, schemas=None, ...):
        # Discovers all schemas if not specified
        if schemas:
            self._schemas = schemas
        else:
            self._schemas = list(inspector.get_schema_names())
        
        # Creates schema-qualified table names (e.g., "hs92.country_year")
        self._all_tables = set(
            f"{schema}.{table}"
            for schema, tables in self._all_tables_per_schema.items()
            for table in tables
        )
    
    def get_table_info(self, table_names=None, ...):
        """Get CREATE TABLE statements for schema-qualified tables"""
        # Supports additional options:
        # - include_comments
        # - include_foreign_keys
        # - include_indexes
        # - include_sample_rows
```

---

## Database Schema

### Schema Organization

The Atlas database is organized into multiple schemas:

```
├── public/           # Metadata (data_flags, year deflators)
├── classification/   # Reference data (countries, products, groups)
├── hs92/             # HS 1992 goods trade data
├── hs12/             # HS 2012 goods trade data
├── sitc/             # SITC goods trade data
├── services_unilateral/  # Services (single country)
└── services_bilateral/   # Services (country pairs)
```

### Common Table Patterns

Each trade schema contains similarly structured tables:

| Table Pattern | Description | Key Columns |
|--------------|-------------|-------------|
| `country_year` | Country-level yearly aggregates | country_id, year, export_value, import_value, eci, coi, diversity |
| `country_product_year_N` | Country-product-year at N-digit level | country_id, product_id, year, export_value, export_rca, distance, cog |
| `country_country_year` | Bilateral country relationships | country_id, partner_id, year, export_value |
| `country_country_product_year_N` | Bilateral trade by product | country_id, partner_id, product_id, year, export_value |
| `product_year_N` | Global product-level data | product_id, year, export_value, pci |
| `product_product_N` | Product proximity metrics | product_id, target_id, strength |

### Classification Tables

| Table | Description |
|-------|-------------|
| `classification.location_country` | Country codes and names (iso3_code, iso2_code, name_en) |
| `classification.location_group` | Country groups (regions, income levels) |
| `classification.product_hs92` | HS92 product codes and names |
| `classification.product_hs12` | HS12 product codes and names |
| `classification.product_sitc` | SITC product codes and names |
| `classification.product_services_*` | Services product codes |

### Key Technical Metrics in Database

| Metric | Description | Defined At |
|--------|-------------|------------|
| `export_rca` | Revealed Comparative Advantage | country-product-year |
| `eci` | Economic Complexity Index | country-year |
| `pci` | Product Complexity Index | product-year |
| `coi` | Complexity Outlook Index | country-year |
| `cog` | Complexity Outlook Gain | country-product-year |
| `distance` | Distance to product | country-product-year |
| `diversity` | Number of competitive products | country-year |

---

## Feature Set Analysis

### Currently Implemented Features ✅

1. **Natural Language to SQL Conversion**
   - Handles simple and complex trade questions
   - Supports multiple product classification systems (HS92, HS12, SITC, Services)

2. **Intelligent Product Code Resolution**
   - LLM-suggested product codes
   - Database verification via full-text search
   - Fuzzy matching fallback for typos/misspellings

3. **Dynamic Schema Selection**
   - Automatically selects appropriate schemas based on question context
   - Handles goods vs services distinction
   - Supports unilateral vs bilateral trade queries

4. **Conversation History**
   - Maintains context via LangGraph MemorySaver
   - Thread-based conversation tracking
   - Follow-up question support

5. **Streaming Responses**
   - Real-time response streaming in Streamlit UI
   - Progress indicators during processing

6. **Safety and Relevance Filtering**
   - Checks for harmful/inappropriate questions
   - Validates trade-related context

### Intended but Simplified Features 🔧

Based on code analysis, these features appear to have been simplified or "nerfed":

1. **Query Validation Chain**
   - `create_query_validation_chain()` exists but is **not used** in the main pipeline
   - Originally intended to validate SQL syntax and logic before execution
   - Code stub exists but returns unused output

2. **Group-level Data**
   - Tables with "group" in the name are **explicitly filtered out**
   ```python
   # In generate_query.py:
   table_descriptions = [
       table for table in table_descriptions
       if "group" not in table["table_name"].lower()
   ]
   ```
   - Original data supports country groups (regions, income levels, etc.)
   - Likely nerfed to reduce complexity/ambiguity

3. **Multiple Query Planning**
   - Agent can technically break down complex questions into sub-queries
   - Limited to `max_uses` (default 10, but initialized with 10 in code)
   - No explicit multi-step planning chain visible

4. **Product Space Visualization**
   - Agent system prompt says "product space is out of scope"
   - Database contains product space coordinates (`product_space_x`, `product_space_y`)
   - Edge data exists in `product_hs92_ps_edges` table

### Planned but Not Implemented Features 📋

From README and code comments:

1. **Evaluation Framework** (`evaluation/`)
   - Directory structure exists
   - `create_evals.py` can generate ground truth SQL
   - **Not integrated** with main application
   - LLM-as-a-judge evaluation not implemented

2. **FastAPI Backend**
   - Mentioned in README as planned enhancement
   - Would enable Slack integration, mobile apps, Atlas website integration
   - **Not implemented**

3. **Advanced Query Optimization**
   - Mentioned in README
   - No specific optimization logic beyond few-shot examples

---

## Known Simplifications & Nerfs

### 1. Query Validation Disabled

**Location:** `src/generate_query.py` lines 147-163

```python
def create_query_validation_chain(llm: BaseLanguageModel) -> Runnable:
    """Creates a chain that validates an SQL query."""
    # ... exists but is never called in the main pipeline
```

**Impact:** SQL queries are executed directly without a secondary validation step.

### 2. Group Tables Filtered Out

**Location:** `src/generate_query.py` lines 329-333

```python
# Temporarily, remove any tables that have the word "group" in the table name
table_descriptions = [
    table for table in table_descriptions
    if "group" not in table["table_name"].lower()
]
```

**Impact:** Cannot query trade data for country groups (regions, income levels, trade blocs).

### 3. Limited Number of Schemas

**Location:** `src/product_and_schema_lookup.py`

The system only recognizes 5 schemas:
- hs92, hs12, sitc, services_unilateral, services_bilateral

The `public` and `classification` schemas are used internally but not exposed for direct querying.

### 4. Hardcoded Model

**Location:** `src/text_to_sql.py`

```python
self.metadata_llm = ChatOpenAI(model="gpt-4o", temperature=0)
self.query_llm = ChatOpenAI(model="gpt-4o", temperature=0)
```

No configuration for different models or providers.

### 5. No Caching

The application doesn't cache:
- LLM responses
- Database query results
- Product code lookups

Each query runs fresh every time.

---

## Development & Testing

### Project Structure

```
ask-atlas/
├── app.py                          # Streamlit UI entry point
├── src/
│   ├── __init__.py
│   ├── text_to_sql.py              # Main orchestrator class
│   ├── generate_query.py           # Agent and tool creation
│   ├── product_and_schema_lookup.py # Product code resolution
│   ├── sql_multiple_schemas.py     # Multi-schema DB support
│   ├── example_queries/            # Few-shot examples
│   │   ├── queries.json            # Question-file mappings
│   │   └── *.sql                   # Example SQL queries
│   ├── setup/
│   │   ├── get_db_schema.py        # Generate db_table_structure.json
│   │   ├── create_search_indexes.py # Create FTS indexes
│   │   └── get_embeddings.py       # (Unused) embedding generation
│   └── tests/
│       ├── conftest.py             # Test fixtures
│       ├── test_generate_query.py  # Agent/tool tests
│       ├── test_product_and_schema_lookup.py
│       ├── test_sql_multiple_schemas.py
│       └── test_text_to_sql.py
├── evaluation/
│   ├── create_evals.py             # Ground truth generation
│   └── system_prompt.md            # Evaluation prompt
├── db_table_descriptions.json      # Table descriptions for prompts
├── db_table_structure.json         # Detailed schema structure
├── pyproject.toml                  # Project dependencies
└── requirements.txt                # Production dependencies
```

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest src/tests/

# Run with integration tests (requires OpenAI API key)
pytest src/tests/ -m integration

# Run specific test file
pytest src/tests/test_generate_query.py -v
```

### Environment Variables

```bash
# Required
ATLAS_DB_URL="postgresql://user:pass@host:port/dbname"
OPENAI_API_KEY="sk-..."

# For Streamlit (in .streamlit/secrets.toml)
ATLAS_DB_URL="postgresql://..."
```

### Running Locally

```bash
# Start Streamlit app
streamlit run app.py

# Or run the text_to_sql module directly for testing
python -m src.text_to_sql
```

---

## Future Enhancements

Based on the current codebase and README:

### High Priority

1. **Re-enable Query Validation**
   - Integrate `create_query_validation_chain` into the pipeline
   - Add error handling for malformed SQL

2. **Re-enable Group Data**
   - Remove the filter for group tables
   - Add logic to handle group-level queries properly

3. **Implement Evaluation Framework**
   - Complete the `evaluation/` infrastructure
   - Add automated testing against ground truth
   - Implement LLM-as-a-judge for answer quality

### Medium Priority

4. **Add Caching Layer**
   - Cache product code lookups
   - Cache common query results
   - Consider Redis or in-memory caching

5. **FastAPI Backend**
   - Separate the agent logic from Streamlit
   - Enable API access for other integrations
   - Add authentication/rate limiting

6. **Model Configuration**
   - Support different LLM providers (Anthropic, local models)
   - Environment variable for model selection
   - Temperature/parameter configuration

### Low Priority

7. **Product Space Visualization**
   - Re-enable product space queries
   - Add visualization support

8. **Advanced Analytics**
   - Time series analysis
   - Comparative trade analysis
   - Custom metric calculations

---

## Quick Start for Developers

### 1. Clone and Setup

```bash
git clone https://github.com/shreyasgm/ask-atlas.git
cd ask-atlas
pip install -e ".[dev]"
```

### 2. Configure Environment

```bash
# Create .env file
echo 'ATLAS_DB_URL="postgresql://..."' > .env
echo 'OPENAI_API_KEY="sk-..."' >> .env

# Or for Streamlit Cloud, configure .streamlit/secrets.toml
```

### 3. Run the Application

```bash
streamlit run app.py
```

### 4. Key Files to Understand

1. Start with `src/text_to_sql.py` - the main orchestrator
2. Review `src/generate_query.py` - the agent and tool definitions
3. Study `src/product_and_schema_lookup.py` - product code resolution
4. Check `src/example_queries/` - few-shot examples

### 5. Making Changes

- Add new example queries: Add SQL file + update `queries.json`
- Add new schemas: Update `SCHEMA_TO_PRODUCTS_TABLE_MAP` and table descriptions
- Modify agent behavior: Edit the `AGENT_PREFIX` in `create_sql_agent()`
- Add new tools: Create new `@tool` decorated functions in `generate_query.py`

---

## Appendix: Technical Metrics Reference

### Revealed Comparative Advantage (RCA)

```
RCA = (Country's exports of product X / Country's total exports) / 
      (World exports of product X / World total exports)
```

If RCA >= 1, the country has a comparative advantage in that product.

### Economic Complexity Index (ECI)

Measures a country's productive capabilities based on the diversity and ubiquity of products it exports. Higher ECI indicates more sophisticated productive knowledge.

### Product Complexity Index (PCI)

Measures the sophistication of a product based on how many countries can produce it and their economic complexity. Higher PCI indicates more complex products.

### Complexity Outlook Index (COI)

Measures how many complex products are "nearby" to a country's current capabilities. High COI indicates easier diversification into complex products.

### Complexity Outlook Gain (COG)

Measures the potential improvement in a country's productive capabilities if it were to start producing a specific product. High COG products are "gateway" products to more complex production.

### Distance

Measures how far a product is from a country's current productive capabilities. Lower distance indicates products that require similar capabilities to what the country already has.

---

*This document should be updated as the codebase evolves. For the latest information, refer to the source code and inline documentation.*
