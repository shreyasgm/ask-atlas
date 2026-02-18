from pathlib import Path
import psycopg
import logging
from dataclasses import dataclass
from typing import List

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parents[2]

from src.config import get_settings

# Load settings (replaces load_dotenv)
settings = get_settings()


@dataclass
class ProductClassification:
    """Represents a product classification system and its associated table names."""

    name: str
    source_table: str


def setup_extensions(conn: psycopg.Connection) -> None:
    """
    Set up required PostgreSQL extensions for text search capabilities.

    Args:
        conn: PostgreSQL connection object
    """
    with conn.cursor() as cur:
        try:
            # Create extensions if they don't exist
            cur.execute("""
                CREATE EXTENSION IF NOT EXISTS pg_trgm;
                CREATE EXTENSION IF NOT EXISTS btree_gin;
            """)
            conn.commit()
            logger.info("Successfully created required extensions")
        except Exception as e:
            logger.error(f"Error creating extensions: {e}")
            raise


def create_search_indices(
    conn: psycopg.Connection, classifications: List[ProductClassification]
) -> None:
    """
    Create text search and trigram indices for each classification table.

    Args:
        conn: PostgreSQL connection object
        classifications: List of ProductClassification objects
    """
    with conn.cursor() as cur:
        for classification in classifications:
            table_name = f"classification.{classification.source_table}"

            # Generate index names
            fts_index_name = f"idx_{classification.source_table}_name_fts"
            trgm_index_name = f"idx_{classification.source_table}_name_trgm"

            try:
                # Create GIN index for full-text search on name_short_en
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS {fts_index_name}
                    ON {table_name} USING gin(to_tsvector('english', name_short_en));
                """)

                # Create GIN index for trigram similarity on name_short_en
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS {trgm_index_name}
                    ON {table_name} USING gin(name_short_en gin_trgm_ops);
                """)

                conn.commit()
                logger.info(f"Successfully created indices for {classification.name}")

            except Exception as e:
                logger.error(f"Error creating indices for {classification.name}: {e}")
                conn.rollback()


def main() -> None:
    """Main function to set up PostgreSQL search capabilities."""
    # Define product classifications
    classifications = [
        ProductClassification("HS 1992", "product_hs92"),
        ProductClassification("HS 2012", "product_hs12"),
        ProductClassification("SITC", "product_sitc"),
        ProductClassification("Services Unilateral", "product_services_unilateral"),
        ProductClassification("Services Bilateral", "product_services_bilateral"),
    ]

    # Get database URL from settings
    db_url = settings.atlas_db_url

    try:
        # Connect to database
        with psycopg.connect(db_url) as conn:
            # Setup extensions
            setup_extensions(conn)

            # Create indices for each classification
            create_search_indices(conn, classifications)

        logger.info("Successfully completed search setup process")

    except Exception as e:
        logger.error(f"Error in main process: {e}")
        raise


if __name__ == "__main__":
    main()
