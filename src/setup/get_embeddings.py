import os
from pathlib import Path
import sys
from dotenv import load_dotenv
from typing import List, Dict, Any
import logging
from dataclasses import dataclass
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_core.documents import Document

BASE_DIR = Path(__file__).parents[2]
sys.path.append(BASE_DIR)
load_dotenv(dotenv_path=BASE_DIR / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Log base directory
logger.info(f"BASE_DIR: {BASE_DIR}")


@dataclass
class Config:
    """Application configuration settings."""

    BATCH_SIZE: int = 500
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    LOG_LEVEL: str = "INFO"


@dataclass
class ProductClassification:
    """Represents a product classification system and its associated table names."""

    name: str
    source_table: str
    collection_name: str  # Changed from target_table to collection_name for LangChain


class DatabaseReader:
    """Handles reading source data from the database."""

    def __init__(self, db_url: str) -> None:
        """Initialize database connection for reading source data.

        Args:
            db_url: Database connection URL
        """
        import psycopg

        self.conn = psycopg.connect(db_url)
        self.cur = self.conn.cursor()

    def __enter__(self) -> "DatabaseReader":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            self.cur.close()
            self.conn.close()
        except Exception as e:
            logger.error(f"Error closing database connections: {e}")

    def get_products(
        self, classification: ProductClassification
    ) -> List[Dict[str, Any]]:
        """Retrieve products from the source classification table.

        Args:
            classification: ProductClassification object containing table information

        Returns:
            List of dictionaries containing product information
        """
        logger.info(f"Fetching products from {classification.source_table}...")

        self.cur.execute(f"""
            SELECT product_id, name_short_en as product_name, code as product_code, product_level
            FROM classification.{classification.source_table}
            WHERE name_short_en IS NOT NULL
            ORDER BY product_id
        """)

        columns = [desc[0] for desc in self.cur.description]
        return [dict(zip(columns, row)) for row in self.cur.fetchall()]


class EmbeddingManager:
    """Manages the creation and storage of embeddings using LangChain."""

    def __init__(self, config: Config) -> None:
        """Initialize LangChain components.

        Args:
            config: Application configuration object

        Raises:
            ValueError: If required environment variables are not set
        """
        self.config = config
        db_url = os.getenv("ATLAS_DB_URL")
        api_key = os.getenv("OPENAI_API_KEY")

        if not db_url or not api_key:
            raise ValueError("Required environment variables not set")

        self.embeddings = OpenAIEmbeddings(
            model=config.EMBEDDING_MODEL, openai_api_key=api_key
        )

        self.db_url = db_url

    def setup_vectorstore(self, collection_name: str) -> PGVector:
        """Create or get a PGVector instance for a collection.

        Args:
            collection_name: Name of the collection to store embeddings

        Returns:
            PGVector instance
        """
        return PGVector(
            embeddings=self.embeddings,
            collection_name=collection_name,
            connection=self.db_url,
            use_jsonb=True,
        )

    def process_products(
        self, products: List[Dict[str, Any]], collection_name: str
    ) -> None:
        """Process products and store their embeddings using LangChain.

        Args:
            products: List of product dictionaries
            collection_name: Name of the collection to store embeddings
        """
        vectorstore = self.setup_vectorstore(collection_name)

        # Process in batches
        total_products = len(products)
        for i in range(0, total_products, self.config.BATCH_SIZE):
            batch = products[i : i + self.config.BATCH_SIZE]

            # Convert products to Documents
            docs = [
                Document(
                    page_content=p["product_name"],
                    metadata={
                        "id": p["product_id"],
                        "product_code": p["product_code"],
                        "product_level": p["product_level"],
                    },
                )
                for p in batch
            ]

            # Add documents with their IDs
            vectorstore.add_documents(
                docs, ids=[str(doc.metadata["id"]) for doc in docs]
            )

            products_processed = min(i + self.config.BATCH_SIZE, total_products)
            logger.info(f"Processed {products_processed} of {total_products} products")


def main() -> None:
    """Main function to orchestrate the creation of embeddings."""
    # Load configuration
    config = Config()

    # Configure logging
    logging.getLogger().setLevel(config.LOG_LEVEL)

    # Define product classifications
    classifications = [
        ProductClassification("HS 1992", "product_hs92", "hs92_embeddings"),
        ProductClassification("HS 2012", "product_hs12", "hs12_embeddings"),
        ProductClassification("SITC", "product_sitc", "sitc_embeddings"),
        ProductClassification(
            "Services Unilateral",
            "product_services_unilateral",
            "services_unilateral_embeddings",
        ),
        ProductClassification(
            "Services Bilateral",
            "product_services_bilateral",
            "services_bilateral_embeddings",
        ),
    ]

    embedding_manager = EmbeddingManager(config)
    db_url = os.getenv("ATLAS_DB_URL")
    if not db_url:
        raise ValueError("ATLAS_DB_URL environment variable not set")

    with DatabaseReader(db_url) as db:
        # Process each classification
        for classification in classifications:
            logger.info(f"Processing {classification.name} classification...")

            # Get products
            products = db.get_products(classification)

            if not products:
                logger.warning(f"No products found for {classification.name}")
                continue

            # Process products and create embeddings
            embedding_manager.process_products(products, classification.collection_name)


if __name__ == "__main__":
    try:
        main()
        logger.info("Successfully completed embedding generation process")
    except Exception as e:
        logger.error(f"Error in main process: {e}")
        raise
