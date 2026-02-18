# Ask-Atlas

Ask-Atlas is an AI agent designed to answer questions about international trade data using the Atlas of Economic Complexity database. It leverages a combination of LangChain, Streamlit, and Large Language Models (LLMs) to answer user queries.

## Features
- **Natural Language to SQL:** Converts user questions into optimized SQL queries.
- **Trade-Specific Query Processing:** Supports multiple product classifications (SITC, HS 1992, 2012, etc.) and looks up product codes separately first before generating SQL queries.
- **Agentic Query Planning:** Breaks complex queries into sub-questions and executes them sequentially.
- **Interactive Follow-ups:** Maintains conversation history, allowing users to ask follow-up questions.
- **Safe and Relevant Responses:** Automatically filters harmful or irrelevant questions.

## Workflow Overview

```mermaid
flowchart TD
    A[User Question] --> B{Check Input Type}
    B -->|Non-trade/Harmful| C[Refuse to Answer]
    B -->|Valid Trade Input| D[Agentic Query Planning]
    
    D --> E[Break Question into Sub-Questions]

    E -->|Products Found| F[Product Lookup]
    E -->|No Products| H[Generate SQL Query]
    
    F -->|Product Codes| H
    E --> E2[Get Schema]
    E2 -->|Schema| H
    
    H --> H1[Validate SQL Query]
    H1 --> H2[Execute Query]
    H2 --> H3{Answer Complete?}
    H3 -->|No| D
    H3 -->|Yes| J[Generate Final Response]
    
    
    style C fill:#ffcccc
    style F fill:#e6f3ff
    style D fill:#e6e6ff
    style J fill:#e6ffe6
```

## Usage
Ask-Atlas supports natural language queries about international trade data. Example questions:

- *"What were the top 5 products exported by the US to China in 2020?"*
- *"How did Brazil's wheat exports change between 2010 and 2020?"*
- *"What services did India export to the US in 2021?"*

Users can refine their queries or ask follow-ups, and the system will maintain context.

## Development

This project uses **test-driven development**. Write tests first, then implement.

```bash
# Run unit tests (no external dependencies)
PYTHONPATH=$(pwd) pytest -m "not db and not integration"

# Start Docker test DB (real production data subset, port 5433)
docker compose -f docker-compose.test.yml up -d --wait

# Run DB integration tests
ATLAS_DB_URL=postgresql://postgres:testpass@localhost:5433/atlas_test \
  PYTHONPATH=$(pwd) pytest -m "db" -v
```

See `CLAUDE.md` for full developer guidelines.

## Test Coverage TODOs

Prioritized gaps in the current test suite:

1. **Error scenarios in main pipeline** — LLM failures, malformed SQL recovery, empty results, rate limiting
2. **Product lookup edge cases** — services codes untested, no conflicting code tests
3. **Streaming error paths** — only happy-path streaming tested, no error-during-stream tests
4. **State transitions** — tests validate structure but not actual state changes during agent execution
5. **Broader query variety** — only 3 E2E questions tested; need year ranges, aggregations, complex filters
6. **Parametrize repetitive tests** — many tests repeat similar patterns that could use `@pytest.mark.parametrize`

## Planned Enhancements
- **Advanced Query Optimization:** Improving SQL generation efficiency.
- **Adding evals**: Expanding integration test coverage and adding LLM-as-a-judge evaluation for answer quality.
- **FastAPI Integration**: Integrating the existing project with a FastAPI backend so that the system can be deployed on other services such as Slack, an app, or even integrated into the Atlas.

## Acknowledgments
Ask-Atlas relies on the Atlas trade database curated by the Harvard Growth Lab. Thanks to the Growth Lab development team for maintaining the database and making a copy available specifically for this project.
