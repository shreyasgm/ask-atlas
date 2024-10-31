"""
Langchain's SQLDatabase does not support multiple schemas, and this is an open issue: https://github.com/langchain-ai/langchain/issues/3036
This subclass serves as a temporary workaround to allow for querying multiple schemas.
"""

from typing import Any, List, Optional
from sqlalchemy import MetaData, create_engine, inspect, Table, select
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateTable
from sqlalchemy.exc import ProgrammingError
from langchain_community.utilities import SQLDatabase
import sqlalchemy


def _format_index(index: sqlalchemy.engine.interfaces.ReflectedIndex) -> str:
    """Format index information with schema awareness."""
    return (
        f'Name: {index["name"]}, Unique: {index["unique"]}, '
        f'Columns: {str(index["column_names"])}'
    )


class SQLDatabaseWithSchemas(SQLDatabase):
    """SQLDatabase subclass that supports multiple schemas."""

    def __init__(
        self,
        engine: Engine,
        schemas: Optional[List[str]] = None,
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
        else:
            self._schemas = existing_schemas

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
                    "table_info must be a dictionary with schema-qualified table names as keys and the desired table info as values"
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

        # Add id to tables metadata
        for t in self._metadata.sorted_tables:
            t.id = f"{t.schema}.{t.name}"

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

    def get_usable_table_names(self) -> List[str]:
        """Get names of tables available across multiple schemas."""
        if self._include_tables:
            return sorted(self._include_tables)
        return sorted(self._all_tables - self._ignore_tables)

    def _get_table_indexes(self, table: Table) -> str:
        """Get table indexes with schema support."""
        indexes = self._inspector.get_indexes(table.name, schema=table.schema)
        indexes_formatted = "\n".join(map(_format_index, indexes))
        return f"Table Indexes for {table.schema}.{table.name}:\n{indexes_formatted}"

    def _get_sample_rows(self, table: Table) -> str:
        """Get sample rows from a table with schema support."""
        # build the select command
        command = select(table).limit(self._sample_rows_in_table_info)

        # save the columns in string format
        columns_str = "\t".join([col.name for col in table.columns])

        try:
            # get the sample rows
            with self._engine.connect() as connection:
                sample_rows_result = connection.execute(command)  # type: ignore
                # shorten values in the sample rows
                sample_rows = list(
                    map(
                        lambda ls: [
                            str(i)[: self._max_string_length]
                            if i is not None
                            else "NULL"
                            for i in ls
                        ],
                        sample_rows_result,
                    )
                )

            # save the sample rows in string format
            sample_rows_str = "\n".join(["\t".join(row) for row in sample_rows])

        # in some dialects when there are no rows in the table a
        # 'ProgrammingError' is returned
        except ProgrammingError:
            sample_rows_str = ""

        return (
            f"{self._sample_rows_in_table_info} rows from {table.schema}.{table.name} table:\n"
            f"{columns_str}\n"
            f"{sample_rows_str}"
        )

    def get_table_info(
        self,
        table_names: Optional[List[str]] = None,
        include_comments: bool = False,
        include_foreign_keys: bool = False,
        include_indexes: bool = False,
        include_sample_rows: bool = False,
    ) -> str:
        """Get information about specified tables across multiple schemas.

        Follows best practices as specified in: Rajkumar et al, 2022
        (https://arxiv.org/abs/2204.00498)

        Args:
            table_names: Optional list of schema-qualified table names (e.g., ['schema.table']).
                        If None, returns info for all usable tables.
            include_comments: If True, includes table and column comments when available.
            include_foreign_keys: If True, includes foreign key relationships when present.
            include_indexes: If True, includes index information when present.
            include_sample_rows: If True, includes sample rows when available.

        Returns:
            str: Formatted string containing table information including requested optional
                sections that have content.

        Raises:
            ValueError: If specified tables are not found or schemas are not recognized.
        """
        # Get list of all available tables
        all_table_names = self.get_usable_table_names()
        if table_names is not None:
            missing_tables = set(table_names).difference(all_table_names)
            if missing_tables:
                existing_tables = "\n".join(
                    f"Schema '{schema}': {', '.join(sorted(tables))}"
                    for schema, tables in self._all_tables_per_schema.items()
                )
                raise ValueError(
                    f"Table(s) {missing_tables} not found in database.\n"
                    f"Existing tables:\n{existing_tables}"
                )
            all_table_names = table_names

        # Get metadata tables
        meta_tables = [
            tbl
            for tbl in self._metadata.sorted_tables
            if f"{tbl.schema}.{tbl.name}" in set(all_table_names)
            and not (self.dialect == "sqlite" and tbl.name.startswith("sqlite_"))
        ]

        tables = []
        for table in meta_tables:
            schema_qualified_name = f"{table.schema}.{table.name}"

            # Check if we have custom info for this table
            if (
                self._custom_table_info
                and schema_qualified_name in self._custom_table_info
            ):
                tables.append(self._custom_table_info[schema_qualified_name])
                continue

            # Start with the CREATE TABLE statement
            create_table = str(CreateTable(table).compile(self._engine))
            table_info = [create_table.rstrip()]

            extra_sections = []

            # Add comments if requested and available
            if include_comments:
                comments_section = []

                # Try to get table comment
                try:
                    table_comment = self._inspector.get_table_comment(
                        table.name, schema=table.schema
                    )["text"]
                    if table_comment:
                        comments_section.append(f"Table Comment: {table_comment}")
                except NotImplementedError:
                    pass

                # Get column comments
                column_comments = []
                for column in self._inspector.get_columns(
                    table.name, schema=table.schema
                ):
                    if column.get("comment"):
                        column_comments.append(
                            f"Column {column['name']}: {column.get('comment')}"
                        )
                if column_comments:
                    comments_section.append("Column Comments:")
                    comments_section.extend(
                        f"  {comment}" for comment in column_comments
                    )

                if comments_section:
                    extra_sections.append("\n".join(comments_section))

            # Add foreign keys if requested and present
            if include_foreign_keys:
                foreign_keys = []
                for fk in self._inspector.get_foreign_keys(
                    table.name, schema=table.schema
                ):
                    foreign_keys.append(
                        f"Foreign Key {fk['constrained_columns']} -> "
                        f"{fk['referred_table']}.{fk['referred_columns']}"
                    )
                if foreign_keys:
                    fk_section = ["Foreign Keys:"]
                    fk_section.extend(f"  {fk}" for fk in foreign_keys)
                    extra_sections.append("\n".join(fk_section))

            # Add indexes if requested and present
            if include_indexes:
                indexes = self._get_table_indexes(table)
                if indexes.strip():  # Check if there's actual content
                    extra_sections.append(f"Indexes:\n{indexes}")

            # Add sample rows if requested
            if include_sample_rows:
                sample_rows = self._get_sample_rows(table)
                if sample_rows.strip():  # Check if there's actual content
                    extra_sections.append(f"Sample Rows:\n{sample_rows}")

            # Add extra sections if any exist
            if extra_sections:
                table_info.append("\n/*")
                table_info.extend(extra_sections)
                table_info.append("*/")

            # Join all sections with appropriate spacing
            tables.append("\n".join(table_info))

        # Join all tables with double line breaks
        return "\n\n".join(tables)
