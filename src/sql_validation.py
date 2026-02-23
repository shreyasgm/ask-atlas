"""SQL validation utilities for pre-execution checks.

Pure functions — no LLM calls, no DB access, no async needed.
Uses sqlglot for syntax parsing and table extraction.
"""

import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

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


def validate_sql(sql: str, valid_tables: set[str]) -> ValidationResult:
    """Validate a SQL string before execution.

    Checks performed:
        1. Empty / whitespace-only SQL — reject.
        2. Syntax parse via sqlglot — catch ``ParseError``.
        3. Table existence — extracted tables checked against *valid_tables*.
        4. ``SELECT *`` detection — warn but allow.
        5. Leading LIKE wildcard (``LIKE '%...'``) — warn but allow.

    Args:
        sql: The SQL query string to validate.
        valid_tables: Set of schema-qualified table names known to exist.

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

    # 3. Table existence
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

    # 4. SELECT * warning
    for select_node in parsed.find_all(exp.Select):
        for sel_expr in select_node.expressions:
            if isinstance(sel_expr, exp.Star):
                warnings.append(
                    "Query uses SELECT * — consider selecting specific columns."
                )
                break

    # 5. Leading LIKE wildcard warning
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
