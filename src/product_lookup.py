from typing import List, Dict, Optional, Any, Union
from pydantic import BaseModel, Field, field_validator
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers.openai_tools import PydanticToolsParser
from langchain_core.runnables import Runnable
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

SCHEMA_TO_CLASSIFICATION_MAP = {
    "hs92": "HS 1992",
    "hs12": "HS 2012",
    "sitc": "SITC",
    "services_unilateral": "UN COMTRADE Services",
    "services_bilateral": "UN COMTRADE Services",
}

CLASSIFICATION_TO_SCHEMA_MAP = {v: k for k, v in SCHEMA_TO_CLASSIFICATION_MAP.items()}


SCHEMA_TO_PRODUCTS_TABLE_MAP = {
    "hs92": "classification.product_hs92",
    "hs12": "classification.product_hs12",
    "sitc": "classification.product_sitc",
    "services_unilateral": "classification.product_services_unilateral",
    "services_bilateral": "classification.product_services_bilateral",
}


class ProductNameWithCodes(BaseModel):
    """A single mapping between a product name and its product codes."""

    product_name: str = Field(description="The name of the product")
    product_classification_str: str = Field(
        description="The classification system name this product belongs to (e.g. 'HS 1992', 'SITC')"
    )
    codes: list[str] = Field(
        description="The product codes associated with the product"
    )

    @field_validator("product_classification_str")
    def validate_classification(cls, v):
        if v not in CLASSIFICATION_TO_SCHEMA_MAP:
            raise ValueError(f"Invalid classification: {v}")
        return v


class ProductMention(BaseModel):
    """Product mentions and candidate codes found in the user's question."""

    has_mentions: bool = Field(
        description="Whether the question contains any product names"
    )
    product_names_with_codes: list[ProductNameWithCodes] = Field(
        description="List of product names and their potential product codes",
    )


class ProductDetails(BaseModel):
    """Details about a product - its name, schema, and codes."""

    product_name: str = Field(description="The name of the product")
    classification_schema: str = Field(
        description="The classification schema this product belongs to"
    )
    codes: list[str] = Field(
        description="The product codes associated with the product"
    )


class ProductCodeMapping(BaseModel):
    """Mapping between product names and their corresponding product codes."""

    mappings: List[ProductNameWithCodes] = Field(
        description="List of product name to product code mappings"
    )


class ProductSearchResult(BaseModel):
    """Container for product search results from different sources."""

    product_name: str = Field(
        description="Original product name mentioned in the user's question"
    )
    classification_schema: str = Field(
        description="The classification schema this product belongs to"
    )
    llm_suggestions: List[Dict[str, Any]] = Field(
        description="Product codes and names suggested by LLM"
    )
    db_suggestions: List[Dict[str, Any]] = Field(
        description="Product codes and names found through direct text search"
    )


class ProductLookupTool:
    """Tool for looking up product codes based on product names in natural language."""

    def __init__(
        self,
        llm: BaseLanguageModel,
        connection_string: str,
        engine_args: Dict[str, Any] = None,
    ):
        """
        Initialize the product lookup tool.

        Args:
            llm: Language model for processing text
            connection_string: PostgreSQL connection string
            engine_args: Additional arguments for the SQLAlchemy engine
        """
        self.llm = llm
        self.connection_string = connection_string
        self.engine_args = engine_args or {}

        # Initialize SQLAlchemy engine for direct text searches
        self.engine = create_engine(connection_string, **engine_args)

    def _extract_product_mentions(
        self, product_classification: str = "HS 1992"
    ) -> Runnable:
        """
        Extract product names mentioned in the question and suggest potential product codes.

        Args:
            product_classification: String indicating the product classification schema(s) to include in the prompt

        Returns:
            Langchain Runnable that takes a question and returns a ProductMention object
        """
        system = """
        Analyze the user's question about trade data and identify any product names that are mentioned 
        but don't have associated product codes explicitly specified. Ignore any mentions that already have associated product codes specified.
        For product codes, only use the codes from the following product classification system(s) as appropriate: {product_classification}.
        If any product names are mentioned without associated product codes specified, return has_mentions as True and product_names_with_codes as a dict of product names with their associated product codes.
        If not, return has_mentions as False and product_names_with_codes as an empty dict.
        Note that both has_mentions and product_names_with_codes are required fields in the response.
        Also suggest potential product codes for each product based on your knowledge of the product classification system.
        Be specific with the codes - suggest the product code at the level most specific to the product mentioned.
        
        Examples:
        An example of a question that already had product codes specified, so further lookups are not needed.
        Question: "What were US exports of cars and vehicles (HS 87) in 2020?"
        Response: {{
            "has_mentions": False,
            "product_names_with_codes": {{}}
        }}

        An example of a question that had product names without product codes, so further lookups are needed.
        Question: "How much cotton and wheat did Brazil export in 2021?"
        Response: {{
            "has_mentions": True,
            "product_names_with_codes": {{
                "cotton": ["5201", "5202", "5203"],
                "wheat": ["1001"]
            }}
        }}

        An example of a question that had no product names without product codes, so further lookups are not needed.
        Question: "What were the top 5 products exported from United States to China?"
        Response: {{
            "has_mentions": False,
            "product_names_with_codes": {{}}
        }}
        """

        prompt = ChatPromptTemplate.from_messages(
            [("system", system), ("human", "{question}")]
        )
        # Format prompt with product classification
        prompt = prompt.partial(product_classification=product_classification)

        # Send to LLM
        llm = self.llm.bind_tools([ProductMention], tool_choice=True)

        chain = (
            prompt
            | llm
            | PydanticToolsParser(tools=[ProductMention], first_tool_only=True)
        )

        return chain

    def _verify_product_codes(
        self, codes: List[str], classification_schema: str
    ) -> List[Dict[str, Any]]:
        """
        Query the database to verify product codes and get their official names.

        Args:
            codes: List of potential product codes to verify
            classification_schema: The classification schema to use for verification

        Returns:
            List of verified products with their codes and official names
        """
        if not codes:
            return []

        if classification_schema not in SCHEMA_TO_PRODUCTS_TABLE_MAP:
            raise ValueError(f"Invalid classification schema: {classification_schema}")

        products_table = SCHEMA_TO_PRODUCTS_TABLE_MAP[classification_schema]

        query = text(f"""
            SELECT DISTINCT 
                code as product_code,
                name_short_en as product_name,
                product_id,
                product_level
            FROM {products_table}
            WHERE code = ANY(:codes)
        """)

        try:
            with self.engine.connect() as conn:
                results = conn.execute(query, {"codes": codes}).fetchall()
                return [
                    {
                        "product_code": str(r[0]),
                        "product_name": str(r[1]),
                        "product_id": str(r[2]),
                        "product_level": str(r[3]),
                    }
                    for r in results
                ]
        except SQLAlchemyError as e:
            print(f"Database error during code verification: {e}")
            return []

    def _direct_text_search(
        self, product_to_search: str, classification_schema: str
    ) -> List[Dict[str, Any]]:
        """
        Perform direct text search using PostgreSQL's full-text search capabilities.
        Uses tsvector/tsquery for primary search with trigram similarity as fallback.

        Args:
            product_to_search: Product name to search for
            classification_schema: The classification schema to use for search

        Returns:
            List of dictionaries containing product information
        """
        if classification_schema not in SCHEMA_TO_PRODUCTS_TABLE_MAP:
            raise ValueError(f"Invalid classification schema: {classification_schema}")

        products_table = SCHEMA_TO_PRODUCTS_TABLE_MAP[classification_schema]

        # Using English text search configuration
        # First try full text search with ranking
        ts_query = text(f"""
            SELECT DISTINCT 
                name_short_en as product_name,
                code as product_code,
                product_id,
                product_level,
                ts_rank_cd(to_tsvector('english', name_short_en),
                        plainto_tsquery('english', :product_to_search)) as rank
            FROM {products_table}
            WHERE to_tsvector('english', name_short_en) @@ 
                plainto_tsquery('english', :product_to_search)
            ORDER BY rank DESC
            LIMIT 5
        """)

        # Fallback to trigram similarity for non-matching terms or misspellings
        fuzzy_query = text(f"""
            SELECT DISTINCT 
                name_short_en as product_name,
                code as product_code,
                product_id,
                product_level,
                similarity(LOWER(name_short_en), LOWER(:product_to_search)) as sim
            FROM {products_table}
            WHERE similarity(LOWER(name_short_en), LOWER(:product_to_search)) > 0.3
            ORDER BY sim DESC
            LIMIT 5
        """)

        try:
            with self.engine.connect() as conn:
                # Try full-text search first
                ts_results = conn.execute(
                    ts_query, {"product_to_search": product_to_search}
                ).fetchall()

                if ts_results:
                    return [
                        {
                            "product_name": str(r[0]),
                            "product_code": str(r[1]),
                            "product_id": str(r[2]),
                            "product_level": str(r[3]),
                        }
                        for r in ts_results
                    ]

                # Fall back to trigram similarity if no full-text matches
                fuzzy_results = conn.execute(
                    fuzzy_query, {"product_to_search": product_to_search}
                ).fetchall()

                return [
                    {
                        "product_name": str(r[0]),
                        "product_code": str(r[1]),
                        "product_id": str(r[2]),
                        "product_level": str(r[3]),
                    }
                    for r in fuzzy_results
                ]

        except SQLAlchemyError as e:
            print(f"Database error during text search: {e}")
            return []

    def _select_final_codes(
        self,
        product_search_results: List[ProductSearchResult],
    ) -> Union[Runnable, ProductCodeMapping]:
        """
        Select the most appropriate product codes from both LLM and DB suggestions.

        Args:
            product_search_results: Combined search results from LLM and DB

        Returns:
            Langchain Runnable which when invoked returns a ProductCodeMapping object with final selected codes
        """
        # If product_search_results is empty, return an empty list
        if not product_search_results:
            return ProductCodeMapping(mappings=[])

        system = """
        Select the most appropriate product code for each product name based on the context of the user's 
        question and the candidate codes.

        Choose the most accurate match based on the specific context. Include only the products that have clear matches. If a product name is too ambiguous or has no good matches among the candidates, exclude it from the final mapping.
        """

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system),
                (
                    "human",
                    """
            Question: {question}
            
            Search results for each product:
            {product_search_results}
            
            Return the final mapping of product names to product codes.
            """,
                ),
            ]
        )

        # Format search results for prompt
        results_str = "\n".join(
            f"Product to search for: {result.product_name}"
            f"Product classification system to use: {SCHEMA_TO_CLASSIFICATION_MAP[result.classification_schema]}"
            f"Candidate matches:\n"
            + "\n".join(
                f"- {s['product_code']}: {s['product_name']}"
                for s in (result.llm_suggestions + result.db_suggestions)
            )
            for result in product_search_results
        )

        # Partially format prompt template using search results
        prompt = prompt.partial(product_search_results=results_str)

        llm = self.llm.bind_tools([ProductCodeMapping], tool_choice=True)
        chain = (
            prompt
            | llm
            | PydanticToolsParser(tools=[ProductCodeMapping], first_tool_only=True)
        )

        return chain

    def get_candidate_codes(
        self, product_mention: ProductMention
    ) -> List[ProductSearchResult]:
        """
        Get candidate codes for each product name.

        Args:
            product_mention: ProductMention object containing product names and codes

        Returns:
            List of ProductSearchResult objects with candidate codes from LLM and DB
        """
        if not product_mention.has_mentions:
            return []

        # Process each product separately
        search_results: List[ProductSearchResult] = []

        for product in product_mention.product_names_with_codes:
            # Get classification schema name from product classification string
            classification_schema = CLASSIFICATION_TO_SCHEMA_MAP[
                product.product_classification_str
            ]

            # Verify LLM-suggested codes for this product
            verified_llm_suggestions = self._verify_product_codes(
                product.codes, classification_schema
            )

            # Get database suggestions through text search
            db_suggestions = self._direct_text_search(
                product.product_name, classification_schema
            )

            search_results.append(
                ProductSearchResult(
                    product_name=product.product_name,
                    classification_schema=classification_schema,
                    llm_suggestions=verified_llm_suggestions,
                    db_suggestions=db_suggestions,
                )
            )

        return search_results

    def lookup_product_codes(
        self, classification_schemas: List[str] = ["hs92"]
    ) -> Runnable:
        """
        Process a user's question to identify and look up any product codes needed.

        Args:
            classification_schema: The classification schema to use for lookups

        Returns:
            Langchain Runnable which when invoked returns a ProductCodeMapping object with final selected codes

        Usage: lookup_product_codes(classification_schema).invoke({"question": question})
        """
        for schema in classification_schemas:
            if schema not in SCHEMA_TO_CLASSIFICATION_MAP:
                raise ValueError(f"Invalid classification schema: {schema}")

        # Remove classification schema from schema_selected if it's in the list
        schema_selected = [s for s in classification_schemas if s != "classification"]
        # Format schemas as a string
        product_classification_str = ", ".join(
            [SCHEMA_TO_CLASSIFICATION_MAP[s] for s in schema_selected]
        )

        # Extract product mentions and LLM's suggested codes
        mentions_chain = self._extract_product_mentions(
            product_classification=product_classification_str
        )

        final_chain = (
            # Extract product mentions
            mentions_chain
            # Get candidate codes for each product
            | self.get_candidate_codes
            # Select final codes using both LLM and DB suggestions
            | self._select_final_codes
        )

        return final_chain


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

    result = "\nProduct name to product code mappings:\n"
    for mapping in mappings.mappings:
        result += f"- {mapping.product_name}: Code {mapping.codes} (Classification: {SCHEMA_TO_CLASSIFICATION_MAP[mapping.classification_schema]})\n"
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
    mappings = lookup_tool.lookup_product_codes(classification_schema="HS 1992").invoke(
        {"question": question}
    )

    if mappings:
        prompt_addition = format_product_codes_for_prompt(mappings)
        print(f"Adding to SQL generation prompt:{prompt_addition}")
    else:
        print("No product codes needed to be looked up.")
