from typing import Any, List, Optional

from sqlalchemy import MetaData, create_engine, inspect
from sqlalchemy.engine import Engine

from llama_index.core import SQLDatabase


class SQLDatabaseWithSchemas(SQLDatabase):
    """SQLDatabase subclass that supports multiple schemas."""

    def __init__(
        self,
        engine: Engine,
        schemas: List[str],
        metadata: Optional[MetaData] = None,
        ignore_tables: Optional[List[str]] = None,
        include_tables: Optional[List[str]] = None,
        sample_rows_in_table_info: int = 3,
        indexes_in_table_info: bool = False,
        custom_table_info: Optional[dict] = None,
        view_support: bool = False,
        max_string_length: int = 300,
    ):
        """Create engine with support for multiple schemas."""
        self._engine = engine
        self._schemas = schemas
        self._inspector = inspect(self._engine)

        # Check if all specified schemas exist in the database
        existing_schemas = set(self._inspector.get_schema_names())
        if schemas:
            missing_schemas = set(self._schemas) - existing_schemas
            if missing_schemas:
                raise ValueError(
                    f"The following schemas were not found in the database: {missing_schemas}\n"
                    f"Existing schemas: {', '.join(sorted(existing_schemas))}"
                )

        if include_tables and ignore_tables:
            raise ValueError("Cannot specify both include_tables and ignore_tables")

        # Inspect tables and views across multiple schemas
        self._all_tables_per_schema = {}
        for schema in self._schemas:
            self._all_tables_per_schema[schema] = set(
                self._inspector.get_table_names(schema=schema)
                + (
                    self._inspector.get_view_names(schema=schema)
                    if view_support
                    else []
                )
            )
        self._all_tables = set(
            f"{schema}.{table}"
            for schema, tables in self._all_tables_per_schema.items()
            for table in tables
        )

        self._include_tables = set(include_tables) if include_tables else set()
        if self._include_tables:
            missing_tables = self._include_tables - self._all_tables
            if missing_tables:
                existing_tables = "\n".join(
                    f"Schema '{schema}': {', '.join(sorted(tables))}"
                    for schema, tables in self._all_tables_per_schema.items()
                )
                print(self._schemas)
                raise ValueError(
                    f"include_tables {missing_tables} not found in database.\n"
                    f"Existing tables:\n{existing_tables}"
                )

        self._ignore_tables = set(ignore_tables) if ignore_tables else set()
        if self._ignore_tables:
            missing_tables = self._ignore_tables - self._all_tables
            if missing_tables:
                raise ValueError(
                    f"ignore_tables {missing_tables} not found in database"
                )

        usable_tables = self.get_usable_table_names()
        self._usable_tables = set(usable_tables) if usable_tables else self._all_tables

        if not isinstance(sample_rows_in_table_info, int):
            raise TypeError("sample_rows_in_table_info must be an integer")

        self._sample_rows_in_table_info = sample_rows_in_table_info
        self._indexes_in_table_info = indexes_in_table_info
        self._custom_table_info = custom_table_info

        if self._custom_table_info:
            if not isinstance(self._custom_table_info, dict):
                raise TypeError(
                    "table_info must be a dictionary with table names as keys and the desired table info as values"
                )
            intersection = set(self._custom_table_info).intersection(self._all_tables)
            self._custom_table_info = {
                table: info
                for table, info in self._custom_table_info.items()
                if table in intersection
            }

        self._max_string_length = max_string_length
        self._metadata = metadata or MetaData()

        # Reflect metadata across all schemas
        for schema in self._schemas:
            self._metadata.reflect(
                views=view_support,
                bind=self._engine,
                only=[
                    v.split(".")[-1]
                    for v in self._usable_tables
                    if v.startswith(schema)
                ],
                schema=schema,
            )

    def get_usable_table_names(self) -> List[str]:
        """Get names of tables available across multiple schemas."""
        if self._include_tables:
            return sorted(self._include_tables)
        return sorted(self._all_tables - self._ignore_tables)

    @classmethod
    def from_uri(
        cls,
        database_uri: str,
        schemas: Optional[List[str]] = None,
        engine_args: Optional[dict] = None,
        **kwargs: Any,
    ) -> "SQLDatabaseWithSchemas":
        """Construct a SQLAlchemy engine from URI with support for multiple schemas."""
        engine = create_engine(database_uri, **(engine_args or {}))
        return cls(engine, schemas=schemas, **kwargs)
