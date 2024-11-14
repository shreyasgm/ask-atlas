# Ask-Atlas

Ask-Atlas is a chatbot that allows users to ask questions about the Atlas database and receive answers. It is a RAG chatbot that uses Langchain, Chainlit, and OpenAI's LLMs (GPT-4o, GPT-4o-mini).


```mermaid
flowchart TD
    A[User Question] --> B{Check Input Type}
    B -->|Non-trade/Harmful| C[Refuse to Answer]
    B -->|Valid Trade Input| D1[Condense question]
    
    D1 --> D[Get Product Classification]

    D1 --> E{Check for Product Names}
    
    E -->|Products Found| F[Product Lookup Vector Search]
    E -->|No Products| H[Generate SQL Query]
    
    D --> F
    
    F -->|Product Codes| H
    D --> D2[Get Schema]
    D2 -->|Schema| H
    
    H --> I[Execute SQL Query]
    I --> J[Generate Response]
    
    style C fill:#ffcccc
    style F fill:#e6f3ff
```