"""Failure taxonomy for eval system error categorization.

Provides a standardized set of failure categories for classifying
why an agent answer failed or partially failed judge evaluation.
"""

from __future__ import annotations

from enum import StrEnum


class FailureCategory(StrEnum):
    """Primary failure categories for agent answer evaluation."""

    FABRICATED_DATA = "fabricated_data"
    WRONG_ENTITY_OR_METRIC = "wrong_entity_or_metric"
    NUMERIC_INACCURACY = "numeric_inaccuracy"
    MISSING_REQUIRED_DATA = "missing_required_data"
    UNSUPPORTED_EMBELLISHMENT = "unsupported_embellishment"
    SCOPE_REFUSAL_FAILURE = "scope_refusal_failure"
    METHODOLOGY_ERROR = "methodology_error"


FAILURE_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    FailureCategory.FABRICATED_DATA: (
        "Agent fabricates numbers, products, rankings, or claims "
        "data/visualizations exist when they don't"
    ),
    FailureCategory.WRONG_ENTITY_OR_METRIC: (
        "Agent identifies wrong country, product, strategic approach, or metric"
    ),
    FailureCategory.NUMERIC_INACCURACY: (
        "Core numbers are off by >±5% from ground truth"
    ),
    FailureCategory.MISSING_REQUIRED_DATA: (
        "Agent fails to provide data that exists in ground truth, "
        "or uses wrong time window"
    ),
    FailureCategory.UNSUPPORTED_EMBELLISHMENT: (
        "Core answer correct but agent adds unverifiable extra claims"
    ),
    FailureCategory.SCOPE_REFUSAL_FAILURE: (
        "Agent fails to properly refuse out-of-scope questions"
    ),
    FailureCategory.METHODOLOGY_ERROR: (
        "Agent uses wrong calculation method, wrong time window, "
        "or flawed reasoning chain"
    ),
}
