from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers.openai_tools import PydanticToolsParser
from langchain_postgres import PGVector
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings


class ProductMention(BaseModel):
    """Product mentions found in the user's question."""

    has_mentions: bool = Field(
        description="Whether the question contains any product names without codes"
    )
    product_names: List[str] = Field(
        description="List of product names mentioned without HS codes"
    )


class ProductCodeMapping(BaseModel):
    """Mapping between product names and their corresponding HS codes."""

    mappings: List[Dict[str, str]] = Field(
        description="List of mappings from product names to HS codes"
    )


class ProductLookupTool:
    """Tool for looking up HS product codes based on product names in natural language."""

    def __init__(
        self,
        llm: BaseLanguageModel,
        connection_string: str,
        collection_name: str = "product_embeddings",
        embeddings: Any = None,
    ):
        """
        Initialize the product lookup tool.

        Args:
            llm: Language model for processing text
            connection_string: PostgreSQL connection string for vector store
            collection_name: Name of the vector store collection
            embeddings: Embedding model to use (defaults to OpenAIEmbeddings)
        """
        self.llm = llm
        self.embeddings = embeddings or OpenAIEmbeddings()

        # Initialize vector store
        self.vector_store = PGVector(
            embeddings=self.embeddings,
            collection_name=collection_name,
            connection=connection_string,
            use_jsonb=True,
        )
        # Test to make sure this works
        check = self.vector_store.similarity_search("cotton", k=5)
        assert len(check) > 0, "Vector store not returning results"

        self.retriever = self.vector_store.as_retriever(
            search_type="similarity", search_kwargs={"k": 5}
        )

    def _extract_product_mentions(self, question: str) -> ProductMention:
        """
        Extract product names mentioned in the question without HS codes.

        Args:
            question: User's question about trade data

        Returns:
            ProductMention object containing found product names
        """
        system = """
        Analyze the user's question about trade data and identify any product names that are mentioned 
        without specific HS codes. Ignore any mentions that already have HS codes specified. If any
        product names are mentioned, return has_mentions as True and the list of product names. If not,
        return has_mentions as False and an empty list.

        Examples:
        Question: "What were US exports of cars and vehicles (HS 87) in 2020?"
        Response: has_mentions=False, product_names=[]

        Question: "How much cotton and wheat did Brazil export in 2021?"
        Response: has_mentions=True, product_names=["cotton", "wheat"]

        Question: "What were the top 5 products exported from United States to China?"
        Response: has_mentions=False, product_names=[]

        These product names, if any, will later be run through a product code lookup tool to get the 
        corresponding product codes.
        """

        prompt = ChatPromptTemplate.from_messages(
            [("system", system), ("human", "{question}")]
        )
        llm = self.llm.bind_tools([ProductMention], tool_choice=True)

        chain = (
            prompt
            | llm
            | PydanticToolsParser(tools=[ProductMention], first_tool_only=True)
        )

        return chain.invoke({"question": question})

    def _search_product_codes(self, product_name: str) -> List[Document]:
        """
        Search for HS codes matching a product name using vector similarity.


        Args:
            product_name: Natural language product name


        Returns:
            List of similar products with their HS codes
        """
        return self.retriever.invoke(product_name)

    def _select_final_codes(
        self,
        question: str,
        product_names: List[str],
        candidates: Dict[str, List[Document]],
    ) -> ProductCodeMapping:
        """
        Select the most appropriate HS codes from candidates for each product.

        Args:
            question: Original user question for context
            product_names: List of product names mentioned
            candidates: Dictionary mapping product names to candidate HS codes

        Returns:
            ProductCodeMapping object with final selected codes
        """
        system = """
        Select the most appropriate HS code for each product name based on the context of the user's 
        question and the candidate codes. Return a mapping from each product name to its most 
        appropriate HS code.

        Include only the products that have clear matches. If a product name is too ambiguous or 
        has no good matches among the candidates, exclude it from the final mapping.
        """

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system),
                (
                    "human",
                    """
            Question: {question}
            Product names: {product_names}
            Candidate codes for each product:
            {candidates}
            
            Return the final mapping of product names to HS codes.
            """,
                ),
            ]
        )

        # Format candidates for prompt
        candidates_str = "\n".join(
            f"{name}:\n" + "\n".join(f"- {doc.page_content}" for doc in docs)
            for name, docs in candidates.items()
        )
        llm = self.llm.bind_tools([ProductCodeMapping], tool_choice=True)
        chain = (
            prompt
            | llm
            | PydanticToolsParser(tools=[ProductCodeMapping], first_tool_only=True)
        )

        return chain.invoke(
            {
                "question": question,
                "product_names": product_names,
                "candidates": candidates_str,
            }
        )

    def lookup_product_codes(self, question: str) -> Optional[ProductCodeMapping]:
        """
        Process a user's question to identify and look up any product codes needed.

        Args:
            question: User's question about trade data

        Returns:
            ProductCodeMapping if products were found and mapped, None otherwise
        """
        # First, check if there are any product mentions needing lookup
        mentions = self._extract_product_mentions(question)

        if not mentions.has_mentions:
            return None

        # Search for candidate codes for each product
        candidates: Dict[str, List[Document]] = {}
        for product_name in mentions.product_names:
            # Search for candidate codes using similarity search
            candidates[product_name] = self._search_product_codes(product_name)

        # Select final codes
        return self._select_final_codes(question, mentions.product_names, candidates)


def format_product_codes_for_prompt(mappings: Optional[ProductCodeMapping]) -> str:
    """
    Format product code mappings for inclusion in the SQL generation prompt.

    Args:
        mappings: Product code mappings or None if no lookups were needed

    Returns:
        Formatted string to include in prompt, or empty string if no mappings
    """
    if not mappings:
        return ""

    result = "\nProduct name to HS code mappings:\n"
    for mapping in mappings.mappings:
        for name, code in mapping.items():
            result += f"- {name}: {code}\n"
    return result


# Usage example
if __name__ == "__main__":
    from langchain_openai import ChatOpenAI

    # Initialize tool
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    lookup_tool = ProductLookupTool(
        llm=llm, connection_string="postgresql://user:pass@localhost:5432/dbname"
    )

    # Example usage
    question = "How much cotton and wheat did Brazil export in 2021?"
    mappings = lookup_tool.lookup_product_codes(question)

    if mappings:
        prompt_addition = format_product_codes_for_prompt(mappings)
        print(f"Adding to SQL generation prompt:{prompt_addition}")
    else:
        print("No product codes needed to be looked up.")
