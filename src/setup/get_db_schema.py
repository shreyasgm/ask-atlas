import psycopg2
from pathlib import Path
import json

BASE_DIR = Path(__file__).parents[2]

from src.config import get_settings  # noqa: E402

# Load settings (replaces load_dotenv)
settings = get_settings()


def simplify_type(udt_name, char_length, numeric_precision, numeric_scale):
    # Basic types that don't need additional info
    simple_types = {
        "int2",
        "int4",
        "int8",
        "float4",
        "float8",
        "bool",
        "json",
        "jsonb",
        "text",
        "date",
        "timestamptz",
        "timestamp",
    }

    if udt_name in simple_types:
        return udt_name

    # Only show length for varchar/char types
    if udt_name in ("varchar", "char", "bpchar"):
        return f"{udt_name}({char_length})" if char_length else udt_name

    return udt_name


def get_db_schema(db_url):
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

    schema_tables = {}
    for schema_name in schemas:
        schema = schema_name[0]
        schema_tables[schema] = {}

        # Modified query to get enum types and basic column info
        cur.execute(
            """
            WITH enum_types AS (
                SELECT
                    t.typname,
                    ARRAY_AGG(e.enumlabel ORDER BY e.enumsortorder) as enum_values
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                GROUP BY t.typname
            )
            SELECT
                t.table_name,
                c.column_name,
                c.udt_name,
                c.character_maximum_length,
                c.numeric_precision,
                c.numeric_scale,
                (SELECT enum_values FROM enum_types WHERE typname = c.udt_name) as enum_values
            FROM information_schema.tables t
            JOIN information_schema.columns c
                ON t.table_name = c.table_name
                AND t.table_schema = c.table_schema
            WHERE t.table_schema = %s
            AND t.table_type = 'BASE TABLE'
            ORDER BY t.table_name, c.ordinal_position
            """,
            (schema,),
        )

        for (
            table_name,
            col_name,
            udt_name,
            char_length,
            numeric_precision,
            numeric_scale,
            enum_values,
        ) in cur.fetchall():
            if table_name not in schema_tables[schema]:
                schema_tables[schema][table_name] = {}

            # Handle enum types
            if enum_values is not None:
                data_type = f"ENUM({', '.join(enum_values)})"
            else:
                data_type = simplify_type(
                    udt_name, char_length, numeric_precision, numeric_scale
                )

            schema_tables[schema][table_name][col_name] = data_type

    cur.close()
    conn.close()
    return schema_tables


print("Fetching schema...")
schema_tables = get_db_schema(settings.atlas_db_url)

# Save to JSON
json_file_path = BASE_DIR / "db_table_structure.json"
with open(json_file_path, "w") as json_file:
    json.dump(schema_tables, json_file, indent=2)
print(f"Schema saved to {json_file_path}")
