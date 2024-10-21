import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv
from pathlib import Path
import sys
import os
import json  # Import the json module

# Add the root directory to the Python path
BASE_DIR = Path(__file__).parents[1]
print(BASE_DIR)
sys.path.append(BASE_DIR)

load_dotenv(dotenv_path=BASE_DIR / ".env")


def get_db_schema(db_url):
    # Connect to the PostgreSQL database
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # Get all schemas
    cur.execute("""
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT LIKE 'pg_%'
        AND schema_name != 'information_schema'
    """)
    schemas = cur.fetchall()

    # Get all tables across all schemas
    cur.execute("""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
    """)
    tables = cur.fetchall()

    # Create a dictionary to group tables by schema
    schema_tables = {}
    for schema_name in schemas:
        schema_tables[schema_name[0]] = []

    for table in tables:
        schema_name = table[0]
        table_name = table[1]
        schema_tables[schema_name].append(table_name)

    cur.close()
    conn.close()

    return schema_tables


def save_to_json(data, file_path):
    # Save the data to a JSON file
    with open(file_path, "w") as json_file:
        json.dump(data, json_file, indent=4)
    print(f"Schema saved to {file_path}")


# Example usage
print("Fetching schema...")
schema_tables = get_db_schema(os.getenv("ATLAS_DB_URL"))
print("Schema fetched successfully.")

# Print the schema names and their tables with dividers
for schema, tables in schema_tables.items():
    print(f"\nSchema: {schema}")
    print("-" * 40)
    for table in tables:
        print(f"  Table: {table}")
    print("=" * 40)  # Divider between schemas

# Save schema and tables to JSON
json_file_path = BASE_DIR / "postgres_db_schema.json"
save_to_json(schema_tables, json_file_path)
