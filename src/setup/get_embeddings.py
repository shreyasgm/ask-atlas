import os
from pathlib import Path
import sys
from dotenv import load_dotenv
from typing import List, Dict, Any
import psycopg2
from psycopg2.extensions import connection, cursor
from openai import OpenAI
import logging
from dataclasses import dataclass

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

    BATCH_SIZE: int = 100
    VECTOR_DIMENSION: int = 1536
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    DB_SCHEMA: str = "classification_embeddings"
    LOG_LEVEL: str = "INFO"


@dataclass
class ProductClassification:
    """Represents a product classification system and its associated table names."""

    name: str
    source_table: str
    target_table: str


class DatabaseManager:
    """Manages database connections and operations."""

    def __init__(self, config: Config) -> None:
        """Initialize database connection using environment variables.

        Args:
            config: Application configuration object

        Raises:
            ValueError: If required environment variables are not set
        """
        self.config = config
        db_url = os.getenv("ATLAS_DB_URL")
        if not db_url:
            raise ValueError("ATLAS_DB_URL environment variable not set")
        self.conn: connection = psycopg2.connect(db_url)
        self.cur: cursor = self.conn.cursor()

    def __enter__(self) -> "DatabaseManager":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            self.cur.close()
            self.conn.close()
        except Exception as e:
            logger.error(f"Error closing database connections: {e}")

    def setup_vector_extension(self) -> None:
        """Enable the vector extension if not already enabled."""
        logger.info("Setting up vector extension...")
        self.cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self.conn.commit()

    def create_schema(self) -> None:
        """Create the classification_embeddings schema if it doesn't exist."""
        logger.info(f"Creating {self.config.DB_SCHEMA} schema...")
        self.cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.config.DB_SCHEMA}")
        self.conn.commit()

    def create_embeddings_table(
        self, classification: ProductClassification, drop_existing: bool = True
    ) -> None:
        """Create a table for storing product embeddings for a specific classification.

        Args:
            classification: ProductClassification object containing table information
            drop_existing: If True, drops the existing table before creating a new one
        """
        logger.info(f"Creating embeddings table for {classification.name}...")

        if drop_existing:
            logger.info(
                f"Dropping existing table {self.config.DB_SCHEMA}.{classification.target_table} if exists..."
            )
            self.cur.execute(
                f"DROP TABLE IF EXISTS {self.config.DB_SCHEMA}.{classification.target_table}"
            )
            self.conn.commit()

        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {self.config.DB_SCHEMA}.{classification.target_table} (
            product_id INTEGER PRIMARY KEY,
            product_name TEXT NOT NULL,
            product_code TEXT NOT NULL,
            product_level INTEGER NOT NULL,
            embedding vector({self.config.VECTOR_DIMENSION})
        );
        
        -- Create GIN index for text search on product_name
        CREATE INDEX IF NOT EXISTS {classification.target_table}_name_idx 
        ON {self.config.DB_SCHEMA}.{classification.target_table} 
        USING GIN (to_tsvector('english', product_name));
        
        -- Create HNSW index for vector similarity search
        CREATE INDEX IF NOT EXISTS {classification.target_table}_embedding_idx 
        ON {self.config.DB_SCHEMA}.{classification.target_table} 
        USING hnsw (embedding vector_cosine_ops);
        """

        self.cur.execute(create_table_sql)
        self.conn.commit()

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

    def insert_products_batch(
        self,
        classification: ProductClassification,
        products: List[Dict[str, Any]],
        embeddings: List[List[float]],
    ) -> None:
        """Insert a batch of products with their embeddings into the database.

        Args:
            classification: ProductClassification object containing table information
            products: List of product dictionaries
            embeddings: List of embedding vectors
        """
        insert_sql = f"""
        INSERT INTO {self.config.DB_SCHEMA}.{classification.target_table}
        (product_id, product_name, product_code, product_level, embedding)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (product_id) DO UPDATE
        SET product_name = EXCLUDED.product_name,
            product_code = EXCLUDED.product_code,
            product_level = EXCLUDED.product_level,
            embedding = EXCLUDED.embedding
        """

        values = [
            (
                p["product_id"],
                p["product_name"],
                p["product_code"],
                p["product_level"],
                embedding,
            )
            for p, embedding in zip(products, embeddings)
        ]

        self.cur.executemany(insert_sql, values)
        self.conn.commit()


class EmbeddingGenerator:
    """Handles the generation of embeddings using OpenAI's API."""

    def __init__(self, config: Config) -> None:
        """Initialize OpenAI client using environment variables.

        Args:
            config: Application configuration object

        Raises:
            ValueError: If required environment variables are not set
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")
        self.client = OpenAI(api_key=api_key)
        self.config = config

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts using OpenAI's API.

        Args:
            texts: List of texts to generate embeddings for

        Returns:
            List of embedding vectors

        Raises:
            Exception: If embedding generation fails
        """
        try:
            response = self.client.embeddings.create(
                input=texts, model=self.config.EMBEDDING_MODEL
            )
            return [v.embedding for v in response.data]
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}")
            raise


def main() -> None:
    """Main function to orchestrate the creation of embeddings tables."""
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

    # Initialize services
    embedding_generator = EmbeddingGenerator(config)

    with DatabaseManager(config) as db:
        # Setup database
        db.setup_vector_extension()
        db.create_schema()

        # Process each classification
        for classification in classifications:
            logger.info(f"Processing {classification.name} classification...")

            # Create table
            db.create_embeddings_table(classification)

            # Get products
            products = db.get_products(classification)

            if not products:
                logger.warning(f"No products found for {classification.name}")
                continue

            # Generate embeddings in batches
            for i in range(0, len(products), config.BATCH_SIZE):
                batch = products[i : i + config.BATCH_SIZE]

                # Generate embeddings for product names
                product_names = [p["product_name"] for p in batch]
                embeddings = embedding_generator.generate_embeddings(product_names)

                # Insert products with embeddings
                db.insert_products_batch(classification, batch, embeddings)

                logger.info(
                    f"Processed batch of {len(batch)} products for {classification.name}"
                )


if __name__ == "__main__":
    try:
        main()
        logger.info("Successfully completed embedding generation process")
    except Exception as e:
        logger.error(f"Error in main process: {e}")
        raise
