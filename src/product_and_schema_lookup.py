from typing import List, Dict, Any, Optional, Union

from langchain_core.language_models import BaseLanguageModel
from langchain_core.output_parsers.openai_tools import PydanticToolsParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

import logging
import warnings

logger = logging.getLogger(__name__)

SCHEMA_TO_PRODUCTS_TABLE_MAP = {
    "hs92": "classification.product_hs92",
    "hs12": "classification.product_hs12",
    "sitc": "classification.product_sitc",
    "services_unilateral": "classification.product_services_unilateral",
    "services_bilateral": "classification.product_services_bilateral",
}


class CountryDetails(BaseModel):
    """A country mentioned in the user query."""

    name: str = Field(
        description="The common name of the country (e.g. 'India', 'United States')"
    )
    iso3_code: str = Field(
        description="The ISO 3166-1 alpha-3 code (e.g. 'IND', 'USA')"
    )


class ProductDetails(BaseModel):
    """A single product code with its basic metadata."""

    name: str = Field(description="The name of the product mentioned in the user query")
    classification_schema: str = Field(
        description="The database schema this product code belongs to, based on the product classification system"
    )
    codes: list[str] = Field(
        description="The product codes associated with the product"
    )


class ProductCodesMapping(BaseModel):
    """Mapping between product names and their corresponding product codes."""

    mappings: List[ProductDetails] = Field(
        description="List of product name to product code mappings"
    )


class ProductSearchResult(BaseModel):
    """Results from searching for a product."""

    name: str = Field(
        description="The name of the mentioned in the user query, used to search for the product"
    )
    classification_schema: str = Field(
        description="Database schema used for the search"
    )
    llm_suggestions: List[Dict[str, Any]] = Field(
        description="Products suggested by the LLM"
    )
    db_suggestions: List[Dict[str, Any]] = Field(
        description="Products found in database"
    )


class SchemasAndProductsFound(BaseModel):
    """Schemas, products, and countries found in a trade-related question."""

    classification_schemas: List[str] = Field(
        description="List of relevant schema names from the db to use, based on the product classification systems implied in the user's question"
    )
    products: List[ProductDetails] = Field(
        description="List of identified products and their codes"
    )
    requires_product_lookup: bool = Field(
        description="Whether the query mentions products without associated codes (those need to be looked up in the db)"
    )
    countries: List[CountryDetails] = Field(
        default_factory=list,
        description="List of countries mentioned in the user's question, with their ISO 3166-1 alpha-3 codes",
    )


class ProductAndSchemaLookup:
    """
    Tool for analyzing trade-related questions to determine schemas and product codes.

    Args:
        llm: Language model to use for analysis
        connection: Database connection string or SQLAlchemy Engine
        engine_args: Additional arguments for SQLAlchemy Engine
    """

    def __init__(
        self,
        llm: BaseLanguageModel,
        connection: Union[str, Engine],
        engine_args: Dict[str, Any] = None,
        async_engine: Optional[AsyncEngine] = None,
    ):
        self.llm = llm
        if isinstance(connection, str):
            self.engine = create_engine(connection, **(engine_args or {}))
        else:
            if engine_args:
                warnings.warn(
                    "engine_args specified but connection is already an sqlalchemy Engine - these will be ignored"
                )
            self.engine = connection
        self.async_engine = async_engine

    def get_product_details(self) -> Runnable:
        """
        Creates a chain that takes a user query and returns the schemas mentioned and official details for the products mentioned.

        Args:
            classification_schema: The classification schema to use for lookups

        Returns:
            Langchain Runnable which when invoked returns a ProductCodeMapping object with final selected codes

        Usage: lookup_product_codes(classification_schema).invoke({"question": question})
        """
        # Extract product mentions and LLM's suggested codes
        mentions_chain = self.extract_schemas_and_product_mentions()

        final_chain = (
            # Extract product mentions
            mentions_chain
            # Get candidate codes for each product
            | self.get_candidate_codes
            # Select final codes using both LLM and DB suggestions
            | self.select_final_codes
        )

        return final_chain

    def extract_schemas_and_product_mentions(self) -> Runnable:
        """
        Creates a chain that analyzes the trade query for trade classification schemas and products.

        Returns:
            Langchain Runnable which when invoked returns a SchemasAndProductsFound object

        Usage: extract_schemas_and_product_mentions().invoke({"question": question})
        """

        system = """
        You are an assistant for a text-to-sql system that uses a database of international trade data.

        Analyze the user's question about trade data to determine which database schemas are needed and what product codes
        should be looked up.

        Available schemas in the postgres db:
        - hs92: Trade data for goods, in HS 1992 product classification
        - hs12: Trade data for goods, in HS 2012 product classification
        - sitc: Trade data for goods, in SITC product classification
        - services_unilateral: Trade data for services products with exporter-product-year data. Use this schema if the user asks about services data for a specific country.
        - services_bilateral: Trade data for services products with exporter-importer-product-year data. Use this schema if the user asks about services trade between two specific countries.

        Guidelines for schema selection:
        - For questions without a specified product classification:
            * Default to 'hs92' for goods
            * Use 'services_bilateral' for services trade between specific countries
            * Use 'services_unilateral' for services trade of a single country
        - Only include services schemas if services are explicitly mentioned, otherwise just use the goods schemas
        - Include specific product classifications if mentioned (e.g., if "HS 2012" is mentioned, include schema 'hs12')
        - Never return more than two schemas unless explicitly required

        Guidelines for product identification:
        - "products" here is how international trade data is classified. Product groups like "machinery" are considered products, and should be identified as such. Be liberal with identifying products. Products could be goods, services, or a mix of both. Here are some examples of products: "cars", "soap", "information technology", "iron", "tourism", "petroleum gas", etc. - anything classified by international trade data classification systems.
        - We are identifying products here for the purpose of looking up their product codes. Only identify products that don't already have codes specified. Ignore products that have codes specified already in the query.
        - Be specific with the codes - suggest the product code at the level most specific to the product mentioned.
        - Include multiple relevant codes if needed for broad product categories

        Guidelines for country identification:
        - Identify all countries mentioned in the user's question.
        - Provide the country's common name and its ISO 3166-1 alpha-3 code (e.g. "IND" for India, "USA" for United States, "BRA" for Brazil).
        - If no specific countries are mentioned, return an empty list.
        - Regions or continents (e.g. "Africa", "Europe") are NOT countries — do not include them.

        Examples:

        Question: "What were US exports of cars and vehicles (HS 87) in 2020?"
        Response: {{
            "classification_schemas": ["hs92"],
            "products": [],
            "requires_product_lookup": false,
            "countries": [{{"name": "United States", "iso3_code": "USA"}}]
        }}
        Reason: Since no specific product classification is mentioned, default to the schema 'hs92'. The question specifies a product code (HS 87), so no further lookup is needed for the codes. The US is mentioned.

        Question: "What were US exports of cotton and wheat in 2021?"
        Response: {{
            "classification_schemas": ["hs92"],
            "products": [
                {{
                    "name": "cotton",
                    "classification_schema": "hs92",
                    "codes": ["5201", "5202"]
                }},
                {{
                    "name": "wheat",
                    "classification_schema": "hs92",
                    "codes": ["1001"]
                }}
            ],
            "requires_product_lookup": true,
            "countries": [{{"name": "United States", "iso3_code": "USA"}}]
        }}
        Reason: The question mentions two products without codes, so the products need to be looked up in the db. The schema wasn't mentioned, so default to 'hs92'.

        Question: "What services did India export to the US in 2021?"
        Response: {{
            "classification_schemas": ["services_bilateral"],
            "products": [],
            "requires_product_lookup": false,
            "countries": [{{"name": "India", "iso3_code": "IND"}}, {{"name": "United States", "iso3_code": "USA"}}]
        }}
        Reason: The question specifically asks for services trade between two countries, so use the 'services_bilateral' schema. No products are mentioned, so no further lookup is needed for the codes.

        Question: "Show me trade in both goods and services between US and China in HS 2012."
        Response: {{
            "classification_schemas": ["hs12", "services_bilateral"],
            "products": [],
            "requires_product_lookup": false,
            "countries": [{{"name": "United States", "iso3_code": "USA"}}, {{"name": "China", "iso3_code": "CHN"}}]
        }}
        Reason: The question mentions two different product classifications, so include both 'hs12' and 'services_bilateral' schemas. No products are mentioned, so no further lookup is needed for the codes.

        Question: "Which country is the world's biggest exporter of fruits and vegetables?"
        Response: {{
            "classification_schemas": ["hs92"],
            "products": [
                {{
                    "name": "fruits",
                    "classification_schema": "hs92",
                    "codes": ["0801", "0802", "0803", "0804", "0805", "0806", "0807", "0808", "0809", "0810", "0811", "0812", "0813", "0814"]
                }},
                {{
                    "name": "vegetables",
                    "classification_schema": "hs92",
                    "codes": ["07"]
                }}
            ],
            "requires_product_lookup": true,
            "countries": []
        }}
        Reason: No specific countries are mentioned, so countries is empty.
        """

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system),
                MessagesPlaceholder(variable_name="history", optional=True),
                ("human", "{question}"),
            ]
        )

        # Create initial chain to identify product and schema mentions
        llm = self.llm.bind_tools([SchemasAndProductsFound], tool_choice="any")
        mentions_chain = (
            prompt
            | llm
            | PydanticToolsParser(tools=[SchemasAndProductsFound], first_tool_only=True)
        )
        return mentions_chain

    def extract_schemas_and_product_mentions_direct(
        self, question: str
    ) -> SchemasAndProductsFound:
        """Run product/schema extraction and return the result directly.

        Args:
            question: The user's trade-related question.

        Returns:
            SchemasAndProductsFound with identified schemas and products.
        """
        chain = self.extract_schemas_and_product_mentions()
        return chain.invoke({"question": question})

    async def aextract_schemas_and_product_mentions_direct(
        self, question: str
    ) -> SchemasAndProductsFound:
        """Async variant: run product/schema extraction and return the result directly.

        Args:
            question: The user's trade-related question.

        Returns:
            SchemasAndProductsFound with identified schemas and products.
        """
        chain = self.extract_schemas_and_product_mentions()
        return await chain.ainvoke({"question": question})

    def select_final_codes_direct(
        self,
        question: str,
        product_search_results: List[ProductSearchResult],
    ) -> ProductCodesMapping:
        """Select final product codes and return the result directly.

        Args:
            question: The user's trade-related question.
            product_search_results: Combined search results from LLM and DB.

        Returns:
            ProductCodesMapping with the final selected codes.
        """
        if not product_search_results:
            return ProductCodesMapping(mappings=[])
        chain = self.select_final_codes(product_search_results)
        return chain.invoke({"question": question})

    async def aselect_final_codes_direct(
        self,
        question: str,
        product_search_results: List[ProductSearchResult],
    ) -> ProductCodesMapping:
        """Async variant: select final product codes and return the result directly.

        Args:
            question: The user's trade-related question.
            product_search_results: Combined search results from LLM and DB.

        Returns:
            ProductCodesMapping with the final selected codes.
        """
        if not product_search_results:
            return ProductCodesMapping(mappings=[])
        chain = self.select_final_codes(product_search_results)
        return await chain.ainvoke({"question": question})

    def select_final_codes(
        self,
        product_search_results: List[ProductSearchResult],
    ) -> Union[Runnable, ProductCodesMapping]:
        """
        Select the most appropriate product codes from both LLM and DB suggestions.

        Args:
            product_search_results: Combined search results from LLM and DB

        Returns:
            Langchain Runnable which when invoked returns a ProductCodesMapping object with final selected codes
        """
        # If product_search_results is empty, return an empty list
        if not product_search_results:
            return ProductCodesMapping(mappings=[])

        system = """
        Select the most appropriate product code for each product name based on the context of the user's
        question and the candidate codes.

        Choose the most accurate match based on the specific context. Include only the products that have clear matches. If a product name is too ambiguous or has no good matches among the candidates, exclude it from the final mapping.

        If no products among the ones provided are relevant to the product mentioned in the user's question, return an empty mapping for that product.
        """

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system),
                MessagesPlaceholder(variable_name="history", optional=True),
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
            f"Product to search for: {result.name}"
            f"Product classification system to use: {result.classification_schema}"
            f"Candidate matches:\n"
            + "\n".join(
                f"- {s['product_code']}: {s['product_name']}"
                for s in (result.llm_suggestions + result.db_suggestions)
            )
            for result in product_search_results
        )

        # Partially format prompt template using search results
        prompt = prompt.partial(product_search_results=results_str)

        llm = self.llm.bind_tools([ProductCodesMapping], tool_choice="any")
        chain = (
            prompt
            | llm
            | PydanticToolsParser(tools=[ProductCodesMapping], first_tool_only=True)
        )

        return chain

    def get_candidate_codes(
        self, products_found: SchemasAndProductsFound
    ) -> List[ProductSearchResult]:
        """
        Get candidate codes for each product name.

        Args:
            products_found: SchemasAndProductsFound object containing product names and codes

        Returns:
            List of ProductSearchResult objects with candidate codes from LLM and DB
        """
        if not products_found.products:
            return []

        # Process each product separately
        search_results: List[ProductSearchResult] = []

        for product in products_found.products:
            # Get classification schema name from product classification string
            # Verify LLM-suggested codes for this product
            verified_llm_suggestions = self._get_official_product_details(
                codes=product.codes, classification_schema=product.classification_schema
            )

            # Get database suggestions through text search
            db_suggestions = self._direct_text_search(
                product.name, product.classification_schema
            )

            search_results.append(
                ProductSearchResult(
                    name=product.name,
                    classification_schema=product.classification_schema,
                    llm_suggestions=verified_llm_suggestions,
                    db_suggestions=db_suggestions,
                )
            )

        return search_results

    def _get_official_product_details(
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

    # ------------------------------------------------------------------
    # Async DB methods (use AsyncEngine for true async I/O)
    # ------------------------------------------------------------------

    async def _aget_official_product_details(
        self, codes: List[str], classification_schema: str
    ) -> List[Dict[str, Any]]:
        """Async version: query database to verify product codes and get official names.

        Delegates to the cached function in src/cache for deduplication and TTL caching.
        """
        if not codes:
            return []
        if not self.async_engine:
            raise RuntimeError("async_engine not set; pass async_engine to __init__")
        if classification_schema not in SCHEMA_TO_PRODUCTS_TABLE_MAP:
            raise ValueError(f"Invalid classification schema: {classification_schema}")

        from src.cache import cached_product_details, registry

        result = await cached_product_details(
            tuple(sorted(codes)), classification_schema, self.async_engine
        )
        # record_miss is called inside cached_product_details on actual miss;
        # if we got here without it being called, it was a cache hit
        registry.record_hit("product_details")
        return result

    async def _adirect_text_search(
        self, product_to_search: str, classification_schema: str
    ) -> List[Dict[str, Any]]:
        """Async version: full-text search with trigram fallback.

        Delegates to the cached function in src/cache for deduplication and TTL caching.
        """
        if not self.async_engine:
            raise RuntimeError("async_engine not set; pass async_engine to __init__")
        if classification_schema not in SCHEMA_TO_PRODUCTS_TABLE_MAP:
            raise ValueError(f"Invalid classification schema: {classification_schema}")

        from src.cache import cached_text_search, registry

        result = await cached_text_search(
            product_to_search, classification_schema, self.async_engine
        )
        registry.record_hit("text_search")
        return result

    async def aget_candidate_codes(
        self, products_found: SchemasAndProductsFound
    ) -> List[ProductSearchResult]:
        """Async version: get candidate codes for each product using async DB queries."""
        if not products_found.products:
            return []

        search_results: List[ProductSearchResult] = []

        for product in products_found.products:
            verified_llm_suggestions = await self._aget_official_product_details(
                codes=product.codes, classification_schema=product.classification_schema
            )
            db_suggestions = await self._adirect_text_search(
                product.name, product.classification_schema
            )
            search_results.append(
                ProductSearchResult(
                    name=product.name,
                    classification_schema=product.classification_schema,
                    llm_suggestions=verified_llm_suggestions,
                    db_suggestions=db_suggestions,
                )
            )

        return search_results


def format_product_codes_for_prompt(analysis: ProductCodesMapping) -> str:
    """Format the analysis results for inclusion in the SQL generation prompt."""
    assert isinstance(
        analysis, ProductCodesMapping
    ), "Input to format_product_codes_for_prompt must be a ProductCodesMapping object"

    if (not analysis) or (not analysis.mappings):
        return ""

    result = ""
    if analysis.mappings:
        result += "\n"
        for product in analysis.mappings:
            if product.codes:
                result += f"- {product.name} (Schema: {product.classification_schema}): {', '.join(product.codes)}\n"
            else:
                result += f"- {product.name} - There was an error looking up the product codes for this product. Ask the user to specify the product codes and the product classification system to use manually and error out. Otherwise, the SQL query will not be accurate.\n"

    return result


# Usage example
if __name__ == "__main__":
    from src.config import get_settings, create_llm

    settings = get_settings()
    llm = create_llm(settings.query_model, settings.query_model_provider, temperature=0)
    analyzer = ProductAndSchemaLookup(
        llm=llm,
        connection=settings.atlas_db_url,
        # Optionally, you can pass engine_args if needed:
        # engine_args={"echo": True}
    )

    # Example usage
    question = "How much cotton and wheat did Brazil export in 2021?"
    analysis = analyzer.get_product_details().invoke({"question": question})
    prompt_addition = format_product_codes_for_prompt(analysis)
    print(f"Adding to SQL generation prompt:\n{prompt_addition}")
