"""
Langchain's SQLDatabase does not support multiple schemas, and this is an open issue: https://github.com/langchain-ai/langchain/issues/3036
This subclass serves as a temporary workaround to allow for querying multiple schemas.

Also provides AsyncSQLDatabaseWithSchemas — an async-native equivalent built on
AsyncEngine + conn.run_sync() that eliminates asyncio.to_thread() calls.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence, Union

import sqlalchemy
import warnings
from sqlalchemy import MetaData, create_engine, inspect, Table, select, text
from sqlalchemy.engine import Engine, Result
from sqlalchemy.exc import ProgrammingError, SAWarning
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.expression import Executable
from langchain_community.utilities import SQLDatabase


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
        self._schemas = schemas or []
        self._schema = None  # Add this for compatibility with parent class
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
            self._schemas = list(existing_schemas)

        if include_tables and ignore_tables:
            raise ValueError("Cannot specify both include_tables and ignore_tables")

        # Initialize rest of the attributes
        self._view_support = view_support
        self._initialize_tables(ignore_tables, include_tables)
        self._initialize_metadata(metadata, view_support)
        self._initialize_other_settings(
            sample_rows_in_table_info,
            indexes_in_table_info,
            custom_table_info,
            max_string_length,
        )

    def _initialize_tables(
        self, ignore_tables: Optional[List[str]], include_tables: Optional[List[str]]
    ) -> None:
        """Initialize table-related attributes."""
        self._all_tables_per_schema = {}
        for schema in self._schemas:
            self._all_tables_per_schema[schema] = set(
                self._inspector.get_table_names(schema=schema)
                + (
                    self._inspector.get_view_names(schema=schema)
                    if self._view_support
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

        self._usable_tables = (
            self._include_tables
            if self._include_tables
            else self._all_tables - self._ignore_tables
        )

    def _initialize_metadata(
        self, metadata: Optional[MetaData], view_support: bool
    ) -> None:
        """Initialize metadata for all schemas."""
        self._metadata = metadata or MetaData()
        for schema in self._schemas:
            # Suppress only the specific warning about vector type
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=SAWarning,
                    message="Did not recognize type 'vector' of column 'embedding'",
                )
                self._metadata.reflect(
                    views=view_support,
                    bind=self._engine,
                    only=[
                        v.split(".")[-1]
                        for v in self._usable_tables
                        if v.startswith(f"{schema}.")
                    ],
                    schema=schema,
                )

    def _initialize_other_settings(
        self,
        sample_rows_in_table_info: int,
        indexes_in_table_info: bool,
        custom_table_info: Optional[dict],
        max_string_length: int,
    ) -> None:
        """Initialize other settings."""
        if not isinstance(sample_rows_in_table_info, int):
            raise TypeError("sample_rows_in_table_info must be an integer")

        self._sample_rows_in_table_info = sample_rows_in_table_info
        self._indexes_in_table_info = indexes_in_table_info
        self._max_string_length = max_string_length

        self._custom_table_info = None
        if custom_table_info:
            if not isinstance(custom_table_info, dict):
                raise TypeError(
                    "table_info must be a dictionary with schema-qualified table names as keys"
                )
            intersection = set(custom_table_info).intersection(self._all_tables)
            self._custom_table_info = {
                table: info
                for table, info in custom_table_info.items()
                if table in intersection
            }

    def _execute(
        self,
        command: Union[str, Executable],
        fetch: Literal["all", "one", "cursor"] = "all",
        *,
        parameters: Optional[Dict[str, Any]] = None,
        execution_options: Optional[Dict[str, Any]] = None,
    ) -> Union[Sequence[Dict[str, Any]], Result]:
        """Execute SQL command with schema support."""
        parameters = parameters or {}
        execution_options = execution_options or {}

        with self._engine.begin() as connection:
            if isinstance(command, str):
                command = text(command)

            cursor = connection.execute(
                command,
                parameters,
                execution_options=execution_options,
            )

            if cursor.returns_rows:
                if fetch == "all":
                    # Get both keys and rows from the cursor
                    keys = cursor.keys()
                    rows = cursor.fetchall()
                    # Create dictionaries by zipping keys with each row's values
                    result = [dict(zip(keys, row)) for row in rows]
                elif fetch == "one":
                    keys = cursor.keys()
                    first_row = cursor.fetchone()
                    result = [] if first_row is None else [dict(zip(keys, first_row))]
                elif fetch == "cursor":
                    return cursor
                else:
                    raise ValueError(
                        "Fetch parameter must be either 'one', 'all', or 'cursor'"
                    )
                return result
        return []

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
                            (
                                str(i)[: self._max_string_length]
                                if i is not None
                                else "NULL"
                            )
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

    def get_context(self) -> Dict[str, Any]:
        """Return db context with schema-aware table information."""
        table_names = list(self.get_usable_table_names())
        table_info = self.get_table_info_no_throw()
        schemas_info = ", ".join(self._schemas)
        return {
            "table_info": table_info,
            "table_names": ", ".join(table_names),
            "schemas": schemas_info,
        }


# ---------------------------------------------------------------------------
# AsyncSQLDatabaseWithSchemas — async-native equivalent
# ---------------------------------------------------------------------------


class AsyncSQLDatabaseWithSchemas:
    """Async-native multi-schema database class built on AsyncEngine.

    Uses ``conn.run_sync()`` (greenlet-based, no thread pool slot) instead of
    ``asyncio.to_thread()`` for metadata reflection and inspector operations.

    Usage::

        db = await AsyncSQLDatabaseWithSchemas.create(async_engine, schemas=["hs92", "classification"])
        ddl = await db.aget_table_info(table_names=["hs92.country_year"])
        rows = await db._aexecute("SELECT 1")
    """

    def __init__(self) -> None:
        """Private — use the async factory ``create()`` instead."""
        # All attributes are set by create(); this prevents accidental sync init.
        raise TypeError(
            "Use `await AsyncSQLDatabaseWithSchemas.create(engine, ...)` instead."
        )

    @classmethod
    async def create(
        cls,
        async_engine: AsyncEngine,
        schemas: Optional[List[str]] = None,
        ignore_tables: Optional[List[str]] = None,
        include_tables: Optional[List[str]] = None,
        sample_rows_in_table_info: int = 3,
        indexes_in_table_info: bool = False,
        custom_table_info: Optional[dict] = None,
        view_support: bool = False,
        max_string_length: int = 300,
    ) -> "AsyncSQLDatabaseWithSchemas":
        """Async factory: reflects metadata via ``conn.run_sync()``.

        Args:
            async_engine: SQLAlchemy AsyncEngine instance.
            schemas: List of schema names to reflect. If None, reflects all.
            ignore_tables: Schema-qualified tables to exclude.
            include_tables: Schema-qualified tables to include (exclusive with ignore).
            sample_rows_in_table_info: Number of sample rows in table info.
            indexes_in_table_info: Whether to include index info.
            custom_table_info: Dict of custom table info strings.
            view_support: Whether to include views.
            max_string_length: Max length for sample row values.

        Raises:
            ValueError: If schemas are missing or both include/ignore tables specified.
        """
        instance = cls.__new__(cls)
        instance._async_engine = async_engine
        instance._view_support = view_support

        # Validate include/ignore tables conflict (before any I/O)
        if include_tables and ignore_tables:
            raise ValueError("Cannot specify both include_tables and ignore_tables")

        # Use run_sync to get schema/table info via the inspector
        async with async_engine.connect() as conn:

            def _reflect_schemas(sync_conn):
                """Runs inside run_sync — has access to a sync connection."""
                insp = inspect(sync_conn)
                return set(insp.get_schema_names())

            existing_schemas = await conn.run_sync(_reflect_schemas)

        # Validate schemas
        if schemas:
            instance._schemas = list(schemas)
            missing = set(schemas) - existing_schemas
            if missing:
                raise ValueError(
                    f"The following schemas were not found in the database: {missing}\n"
                    f"Existing schemas: {', '.join(sorted(existing_schemas))}"
                )
        else:
            instance._schemas = list(existing_schemas)

        # Discover tables per schema via run_sync
        async with async_engine.connect() as conn:

            def _discover_tables(sync_conn):
                insp = inspect(sync_conn)
                all_tables_per_schema: Dict[str, set] = {}
                for schema in instance._schemas:
                    tables = set(insp.get_table_names(schema=schema))
                    if view_support:
                        tables |= set(insp.get_view_names(schema=schema))
                    all_tables_per_schema[schema] = tables
                return all_tables_per_schema

            instance._all_tables_per_schema = await conn.run_sync(_discover_tables)

        instance._all_tables = set(
            f"{schema}.{table}"
            for schema, tables in instance._all_tables_per_schema.items()
            for table in tables
        )

        # Apply include/ignore filters
        instance._include_tables = set(include_tables) if include_tables else set()
        if instance._include_tables:
            missing_tables = instance._include_tables - instance._all_tables
            if missing_tables:
                raise ValueError(
                    f"include_tables {missing_tables} not found in database."
                )

        instance._ignore_tables = set(ignore_tables) if ignore_tables else set()
        if instance._ignore_tables:
            missing_tables = instance._ignore_tables - instance._all_tables
            if missing_tables:
                raise ValueError(
                    f"ignore_tables {missing_tables} not found in database"
                )

        instance._usable_tables = (
            instance._include_tables
            if instance._include_tables
            else instance._all_tables - instance._ignore_tables
        )

        # Reflect metadata via run_sync
        instance._metadata = MetaData()
        async with async_engine.connect() as conn:

            def _reflect_metadata(sync_conn):
                for schema in instance._schemas:
                    with warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore",
                            category=SAWarning,
                            message="Did not recognize type 'vector' of column 'embedding'",
                        )
                        instance._metadata.reflect(
                            views=view_support,
                            bind=sync_conn,
                            only=[
                                v.split(".")[-1]
                                for v in instance._usable_tables
                                if v.startswith(f"{schema}.")
                            ],
                            schema=schema,
                        )

            await conn.run_sync(_reflect_metadata)

        # Store settings
        instance._sample_rows_in_table_info = sample_rows_in_table_info
        instance._indexes_in_table_info = indexes_in_table_info
        instance._max_string_length = max_string_length
        instance._custom_table_info = None
        if custom_table_info:
            intersection = set(custom_table_info).intersection(instance._all_tables)
            instance._custom_table_info = {
                t: info for t, info in custom_table_info.items() if t in intersection
            }

        return instance

    # -- Properties -----------------------------------------------------------

    @property
    def dialect(self) -> str:
        """Return the dialect name (e.g. 'postgresql')."""
        return self._async_engine.dialect.name

    # -- Sync, in-memory methods ----------------------------------------------

    def get_usable_table_names(self) -> List[str]:
        """Get sorted list of schema-qualified usable table names."""
        if self._include_tables:
            return sorted(self._include_tables)
        return sorted(self._all_tables - self._ignore_tables)

    # -- Async methods --------------------------------------------------------

    async def aget_table_info(
        self,
        table_names: Optional[List[str]] = None,
        include_comments: bool = False,
        include_foreign_keys: bool = False,
        include_indexes: bool = False,
        include_sample_rows: bool = False,
    ) -> str:
        """Async equivalent of ``get_table_info``.

        Returns CREATE TABLE DDL for requested tables. Optionally includes
        comments, foreign keys, indexes, and sample rows.
        """
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

        # Filter metadata tables
        meta_tables = [
            tbl
            for tbl in self._metadata.sorted_tables
            if f"{tbl.schema}.{tbl.name}" in set(all_table_names)
            and not (self.dialect == "sqlite" and tbl.name.startswith("sqlite_"))
        ]

        # For comments, foreign keys, indexes — we need the inspector via run_sync
        need_inspector = include_comments or include_foreign_keys or include_indexes
        inspector_data: Dict[str, dict] = {}
        if need_inspector:
            async with self._async_engine.connect() as conn:

                def _get_inspector_data(sync_conn):
                    insp = inspect(sync_conn)
                    data: Dict[str, dict] = {}
                    for table in meta_tables:
                        key = f"{table.schema}.{table.name}"
                        entry: dict = {}
                        if include_comments:
                            try:
                                entry["table_comment"] = insp.get_table_comment(
                                    table.name, schema=table.schema
                                ).get("text")
                            except NotImplementedError:
                                entry["table_comment"] = None
                            entry["columns"] = insp.get_columns(
                                table.name, schema=table.schema
                            )
                        if include_foreign_keys:
                            entry["foreign_keys"] = insp.get_foreign_keys(
                                table.name, schema=table.schema
                            )
                        if include_indexes:
                            entry["indexes"] = insp.get_indexes(
                                table.name, schema=table.schema
                            )
                        data[key] = entry
                    return data

                inspector_data = await conn.run_sync(_get_inspector_data)

        tables = []
        for table in meta_tables:
            schema_qualified_name = f"{table.schema}.{table.name}"

            # Custom info override
            if (
                self._custom_table_info
                and schema_qualified_name in self._custom_table_info
            ):
                tables.append(self._custom_table_info[schema_qualified_name])
                continue

            # DDL via dialect (no I/O)
            create_table = str(
                CreateTable(table).compile(dialect=self._async_engine.dialect)
            )
            table_info = [create_table.rstrip()]

            extra_sections = []

            # Comments
            if include_comments:
                entry = inspector_data.get(schema_qualified_name, {})
                comments_section = []
                table_comment = entry.get("table_comment")
                if table_comment:
                    comments_section.append(f"Table Comment: {table_comment}")
                column_comments = []
                for column in entry.get("columns", []):
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

            # Foreign keys
            if include_foreign_keys:
                entry = inspector_data.get(schema_qualified_name, {})
                foreign_keys = []
                for fk in entry.get("foreign_keys", []):
                    foreign_keys.append(
                        f"Foreign Key {fk['constrained_columns']} -> "
                        f"{fk['referred_table']}.{fk['referred_columns']}"
                    )
                if foreign_keys:
                    fk_section = ["Foreign Keys:"]
                    fk_section.extend(f"  {fk}" for fk in foreign_keys)
                    extra_sections.append("\n".join(fk_section))

            # Indexes
            if include_indexes:
                entry = inspector_data.get(schema_qualified_name, {})
                indexes = entry.get("indexes", [])
                indexes_formatted = "\n".join(map(_format_index, indexes))
                index_str = f"Table Indexes for {table.schema}.{table.name}:\n{indexes_formatted}"
                if index_str.strip():
                    extra_sections.append(f"Indexes:\n{index_str}")

            # Sample rows
            if include_sample_rows:
                sample_rows = await self._aget_sample_rows(table)
                if sample_rows.strip():
                    extra_sections.append(f"Sample Rows:\n{sample_rows}")

            # Assemble
            if extra_sections:
                table_info.append("\n/*")
                table_info.extend(extra_sections)
                table_info.append("*/")

            tables.append("\n".join(table_info))

        return "\n\n".join(tables)

    async def _aget_sample_rows(self, table: Table) -> str:
        """Get sample rows from a table via async connection."""
        command = select(table).limit(self._sample_rows_in_table_info)
        columns_str = "\t".join([col.name for col in table.columns])

        try:
            async with self._async_engine.connect() as connection:
                result = await connection.execute(command)
                sample_rows = list(
                    map(
                        lambda ls: [
                            (
                                str(i)[: self._max_string_length]
                                if i is not None
                                else "NULL"
                            )
                            for i in ls
                        ],
                        result,
                    )
                )

            sample_rows_str = "\n".join(["\t".join(row) for row in sample_rows])
        except ProgrammingError:
            sample_rows_str = ""

        return (
            f"{self._sample_rows_in_table_info} rows from {table.schema}.{table.name} table:\n"
            f"{columns_str}\n"
            f"{sample_rows_str}"
        )

    async def _aexecute(
        self,
        command: Union[str, Executable],
        fetch: Literal["all", "one"] = "all",
        *,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> list[Dict[str, Any]]:
        """Execute SQL command asynchronously.

        Args:
            command: SQL string or executable.
            fetch: "all" returns all rows, "one" returns first row only.
            parameters: Optional query parameters.

        Returns:
            List of dicts (column_name -> value).
        """
        parameters = parameters or {}

        async with self._async_engine.begin() as connection:
            if isinstance(command, str):
                command = text(command)

            cursor = await connection.execute(command, parameters)

            if cursor.returns_rows:
                keys = list(cursor.keys())
                if fetch == "all":
                    rows = cursor.fetchall()
                    return [dict(zip(keys, row)) for row in rows]
                elif fetch == "one":
                    first_row = cursor.fetchone()
                    return [] if first_row is None else [dict(zip(keys, first_row))]
                else:
                    raise ValueError("Fetch parameter must be 'one' or 'all'")
        return []
