"""SQL validation utilities for pre-execution checks.

Pure functions — no LLM calls, no DB access, no async needed.
Uses sqlglot for syntax parsing, table extraction, and column validation.
"""

import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import MappingSchema, exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.qualify import qualify

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of SQL validation.

    Attributes:
        is_valid: Whether the SQL passed all critical checks.
        errors: Critical issues that prevent execution.
        warnings: Informational issues — logged but not blocking.
        sql: The (potentially cleaned) SQL string.
    """

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sql: str = ""


def extract_table_names_from_ddl(table_info: str) -> set[str]:
    """Extract schema-qualified table names from a DDL string.

    Parses CREATE TABLE statements to find names like ``hs92.country_year``.
    Uses regex — simpler and more reliable than parsing DDL with sqlglot.

    Args:
        table_info: The DDL/description string (``pipeline_table_info``).

    Returns:
        Set of schema-qualified table names found in the DDL.
    """
    pattern = r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+\.\w+)"
    return {m.group(1) for m in re.finditer(pattern, table_info, re.IGNORECASE)}


def build_schema_from_ddl(table_info: str) -> dict:
    """Parse DDL into a nested dict for ``sqlglot.MappingSchema``.

    Returns ``{schema: {table: {column: type}}}`` suitable for passing
    to ``MappingSchema()``.

    Args:
        table_info: The DDL/description string (``pipeline_table_info``).

    Returns:
        Nested dict mapping schema → table → column → type string.
    """
    schema_dict: dict[str, dict[str, dict[str, str]]] = {}

    # Split on CREATE TABLE to process each table block
    table_pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\.(\w+)\s*\(",
        re.IGNORECASE,
    )
    # Find each CREATE TABLE and extract columns from its body
    for match in table_pattern.finditer(table_info):
        schema_name = match.group(1)
        table_name = match.group(2)

        # Find the matching closing ) using paren-depth counting.
        # Naive find(")") breaks on types like CHAR(3), VARCHAR(255).
        start = match.end()
        depth = 1
        end = start
        while end < len(table_info) and depth > 0:
            ch = table_info[end]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            end += 1
        if depth != 0:
            continue

        block = table_info[start : end - 1]  # exclude the closing )

        # Extract column definitions: indented "column_name TYPE..."
        _CONSTRAINT_KEYWORDS = frozenset(
            {"primary", "constraint", "foreign", "unique", "check", "index"}
        )
        columns: dict[str, str] = {}
        for col_match in re.finditer(r"^\s+(\w+)\s+(\w+)", block, re.MULTILINE):
            col_name = col_match.group(1).lower()
            col_type = col_match.group(2).upper()
            if col_name not in _CONSTRAINT_KEYWORDS:
                columns[col_name] = col_type

        if columns:
            schema_dict.setdefault(schema_name, {})[table_name] = columns

    return schema_dict


def _validate_columns_with_qualify(
    parsed: exp.Expression, schema_dict: dict
) -> list[str]:
    """Use sqlglot's qualify() for scope-aware column validation.

    Builds a ``MappingSchema`` and runs ``qualify()`` with
    ``validate_qualify_columns=True``.  This correctly resolves CTE aliases,
    aggregation aliases, subquery-derived columns, and JOIN scopes —
    eliminating the false positives of flat column checking.

    Fails open: any unexpected error logs a warning and returns no errors,
    so column validation never blocks a query due to internal bugs.

    Args:
        parsed: The parsed sqlglot AST.
        schema_dict: Nested ``{schema: {table: {col: type}}}`` dict.

    Returns:
        List of error strings (empty if all columns resolve).
    """
    try:
        mapping_schema = MappingSchema(schema_dict, dialect="postgres")
        qualify(
            parsed,
            schema=mapping_schema,
            validate_qualify_columns=True,
        )
        return []
    except OptimizeError as exc:
        err_str = str(exc)
        # sqlglot uses varying formats: "Column 'x' could not be resolved"
        # or "Unknown column: x" — try both patterns.
        col_match = re.search(r"Column '(\w+)'", err_str) or re.search(
            r"Unknown column:\s*(\w+)", err_str
        )
        col_name = col_match.group(1) if col_match else err_str.split("\n")[0]
        return [
            f"Unknown column '{col_name}'. "
            f"Check spelling or verify the column exists in the table DDL."
        ]
    except Exception:
        logger.warning(
            "Column validation via qualify() failed unexpectedly; skipping check",
            exc_info=True,
        )
        return []


def validate_sql(
    sql: str,
    valid_tables: set[str],
    expected_schemas: set[str] | None = None,
    column_schema: dict | None = None,
) -> ValidationResult:
    """Validate a SQL string before execution.

    Checks performed:
        1. Empty / whitespace-only SQL — reject.
        2. Syntax parse via sqlglot — catch ``ParseError``.
        3. Write-operation blocking — reject DML/DDL statements.
        4. Table existence — extracted tables checked against *valid_tables*.
        5. Schema-mismatch detection — SQL schema prefixes vs *expected_schemas*.
        6. Column existence — scope-aware check via sqlglot ``qualify()``.
        7. ``SELECT *`` detection — warn but allow.
        8. Leading LIKE wildcard (``LIKE '%...'``) — warn but allow.

    Args:
        sql: The SQL query string to validate.
        valid_tables: Set of schema-qualified table names known to exist.
        expected_schemas: If provided, data-schema prefixes in the SQL must be
            a subset of this set.  ``classification`` is always allowed.
        column_schema: If provided, a nested ``{schema: {table: {col: type}}}``
            dict.  Column references are validated via ``sqlglot.qualify()``.

    Returns:
        A ``ValidationResult`` with errors/warnings populated.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Empty check
    if not sql or not sql.strip():
        return ValidationResult(
            is_valid=False,
            errors=["SQL is empty or whitespace-only."],
            sql=sql or "",
        )

    # 2. Syntax parse
    try:
        parsed = sqlglot.parse_one(sql, dialect="postgres")
    except sqlglot.errors.ParseError as exc:
        return ValidationResult(
            is_valid=False,
            errors=[f"SQL syntax error: {exc}"],
            sql=sql,
        )

    # 3. Write-operation blocking (defense-in-depth for read-only DB)
    _BLOCKED_NODE_TYPES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter)
    for node_type in _BLOCKED_NODE_TYPES:
        if parsed.find(node_type):
            return ValidationResult(
                is_valid=False,
                errors=[
                    f"Write operations are not allowed. "
                    f"Detected: {node_type.__name__}."
                ],
                sql=sql,
            )

    stripped_upper = sql.strip().upper()
    _BLOCKED_PREFIXES = ("TRUNCATE", "CREATE", "GRANT", "REVOKE")
    for prefix in _BLOCKED_PREFIXES:
        if stripped_upper.startswith(prefix):
            return ValidationResult(
                is_valid=False,
                errors=[
                    f"Write operations are not allowed. "
                    f"Detected: {prefix} statement."
                ],
                sql=sql,
            )

    # 4. Table existence
    query_tables: set[str] = set()
    for table_node in parsed.find_all(exp.Table):
        db = table_node.db  # schema in sqlglot terms
        name = table_node.name
        if db:
            query_tables.add(f"{db}.{name}")

    unknown = query_tables - valid_tables
    if unknown:
        errors.append(
            f"Unknown table(s): {', '.join(sorted(unknown))}. "
            f"Valid tables: {', '.join(sorted(valid_tables))}"
        )

    # 5. Schema-mismatch detection
    if expected_schemas is not None:
        sql_schemas = {t.split(".", 1)[0] for t in query_tables if "." in t} - {
            "classification"
        }
        unexpected = sql_schemas - expected_schemas
        if unexpected:
            errors.append(
                f"Schema mismatch: SQL references {', '.join(sorted(unexpected))} "
                f"but expected schemas are {', '.join(sorted(expected_schemas))}."
            )

    # 6. Column existence check (scope-aware via qualify)
    if column_schema is not None:
        # Re-parse to avoid mutating the AST used for other checks
        col_parsed = sqlglot.parse_one(sql, dialect="postgres")
        col_errors = _validate_columns_with_qualify(col_parsed, column_schema)
        errors.extend(col_errors)

    # 7. SELECT * warning
    for select_node in parsed.find_all(exp.Select):
        for sel_expr in select_node.expressions:
            if isinstance(sel_expr, exp.Star):
                warnings.append(
                    "Query uses SELECT * — consider selecting specific columns."
                )
                break

    # 8. Leading LIKE wildcard warning
    for like_node in parsed.find_all(exp.Like):
        pattern_expr = like_node.expression
        if isinstance(pattern_expr, exp.Literal) and pattern_expr.is_string:
            value = pattern_expr.this
            if value.startswith("%"):
                warnings.append(
                    f"LIKE pattern '{value}' has a leading wildcard — "
                    "this prevents index usage and may be slow."
                )

    is_valid = len(errors) == 0
    if warnings:
        for w in warnings:
            logger.warning("SQL validation warning: %s", w)

    return ValidationResult(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        sql=sql,
    )
