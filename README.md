# Ask-Atlas

Ask-Atlas is a chatbot that allows users to ask questions about the Atlas database and receive answers. It is a RAG chatbot that uses Langchain, Streamlit, and LLM's from either OpenAI, Anthropic, or Google (whichever one seems to be performing best at the time).


```mermaid
flowchart TD
    A[User Question] --> B{Check Input Type}
    B -->|Non-trade/Harmful| C[Refuse to Answer]
    B -->|Valid Trade Input| D1[Condense question]
    
    D1 --> D[Get Product Classification]

    D1 --> E{Check for Product Names}
    
    E -->|Products Found| F[Product Lookup]
    E -->|No Products| H[Agentic Query Planning]
    
    D --> F
    
    F -->|Product Codes| H
    D --> D2[Get Schema]
    D2 -->|Schema| H
    
    H --> H1[Generate SQL Query]
    H1 --> H2[Execute Query]
    H2 --> H3{Answer Complete?}
    H3 -->|No| H
    H3 -->|Yes| J[Generate Final Response]
    
    
    style C fill:#ffcccc
    style F fill:#e6f3ff
    style H fill:#e6e6ff
    style J fill:#e6ffe6
```