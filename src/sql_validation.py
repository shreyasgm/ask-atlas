"""SQL validation utilities for pre-execution checks.

Pure functions — no LLM calls, no DB access, no async needed.
Uses sqlglot for syntax parsing and write-operation detection.
"""

import logging
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


def validate_sql(sql: str) -> ValidationResult:
    """Validate a SQL string before execution.

    Checks performed:
        1. Empty / whitespace-only SQL — reject.
        2. Syntax parse via sqlglot — catch ``ParseError``.
        3. Write-operation blocking — reject DML/DDL statements.
        4. ``SELECT *`` detection — warn but allow.
        5. Leading LIKE wildcard (``LIKE '%...'``) — warn but allow.

    Table existence, schema mismatch, and column existence checks are
    intentionally omitted — the database catches these with clearer error
    messages, and pre-checking them requires fragile DDL parsing that
    produces false positives.

    Args:
        sql: The SQL query string to validate.

    Returns:
        A ``ValidationResult`` with errors/warnings populated.
    """
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
                    f"Write operations are not allowed. Detected: {node_type.__name__}."
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
                    f"Write operations are not allowed. Detected: {prefix} statement."
                ],
                sql=sql,
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

    # 6. LIKE/ILIKE on name_short_en warning (double-counting risk)
    for like_node in parsed.find_all(exp.Like, exp.ILike):
        col_expr = like_node.this
        col_name = None
        if isinstance(col_expr, exp.Column):
            col_name = col_expr.name
        if col_name and col_name.lower() == "name_short_en":
            warnings.append(
                "LIKE/ILIKE on column 'name_short_en' risks double-counting "
                "across product hierarchy levels. Use product_code with exact "
                "match instead."
            )
            break  # one warning is enough

    if warnings:
        for w in warnings:
            logger.warning("SQL validation warning: %s", w)

    return ValidationResult(
        is_valid=True,
        errors=[],
        warnings=warnings,
        sql=sql,
    )
