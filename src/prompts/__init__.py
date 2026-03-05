"""Central prompt registry for the Ask-Atlas agent.

All LLM prompts live in this package.  Pipeline modules (``sql_pipeline``,
``graphql_pipeline``, ``docs_pipeline``, ``agent_node``) import the
constants and builder functions defined here rather than defining prompts
inline.

Design rules
~~~~~~~~~~~~
* **Zero imports from other ``src/`` modules** — this package is a leaf
  dependency so it can never create circular-import problems.
* All constants are plain ``str`` with ``.format()`` placeholders (no
  f-strings).  Literal braces inside prompt text are escaped as ``{{``
  and ``}}``.
* Each constant has a preceding ``# --- `` comment block that documents
  purpose, pipeline, and placeholders.
* Builder functions handle conditional sections or multi-part assembly.
* Private ``_BLOCK`` constants are shared building blocks used to
  assemble the public agent system prompts (DRY).

Architecture (post-rewrite)
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Two standalone agent system prompts replace the old additive composition:

* ``SQL_ONLY_SYSTEM_PROMPT``  — for SQL-only mode (query_tool + docs_tool)
* ``DUAL_TOOL_SYSTEM_PROMPT`` — for dual-tool mode (all 3 tools)
* ``GRAPHQL_ONLY_OVERRIDE``   — short prefix prepended to the dual-tool
  prompt in GraphQL-only mode

Both are assembled from shared ``_BLOCK`` constants for DRY code, but the
assembled prompt strings are fully independent.
"""

# -- Data-year constants --
from ._blocks import GRAPHQL_DATA_MAX_YEAR, SQL_DATA_MAX_YEAR

# -- Agent system prompts + builders --
from .prompt_agent import (
    DUAL_TOOL_SYSTEM_PROMPT,
    GRAPHQL_ONLY_OVERRIDE,
    SQL_ONLY_SYSTEM_PROMPT,
    build_dual_tool_system_prompt,
    build_sql_only_system_prompt,
)

# -- Documentation prompts --
from .prompt_docs import DOCUMENT_SELECTION_PROMPT, DOCUMENTATION_SYNTHESIS_PROMPT

# -- GraphQL prompts + builders --
from .prompt_graphql import (
    GRAPHQL_CLASSIFICATION_PROMPT,
    GRAPHQL_ENTITY_EXTRACTION_PROMPT,
    ID_RESOLUTION_SELECTION_PROMPT,
    build_classification_prompt,
    build_extraction_prompt,
    build_id_resolution_prompt,
    build_query_plan_prompt,
)

# -- SQL / product prompts + builder --
from .prompt_sql import (
    PRODUCT_CODE_SELECTION_PROMPT,
    PRODUCT_EXTRACTION_PROMPT,
    SQL_CODES_BLOCK,
    SQL_CONTEXT_BLOCK,
    SQL_DIRECTION_BLOCK,
    SQL_GENERATION_PROMPT,
    SQL_GROUP_TABLES_BLOCK,
    SQL_MODE_BLOCK,
    SQL_RETRY_BLOCK,
    build_sql_generation_prefix,
)

__all__ = [
    # Data-year constants
    "GRAPHQL_DATA_MAX_YEAR",
    "SQL_DATA_MAX_YEAR",
    # Agent prompts
    "DUAL_TOOL_SYSTEM_PROMPT",
    "GRAPHQL_ONLY_OVERRIDE",
    "SQL_ONLY_SYSTEM_PROMPT",
    "build_dual_tool_system_prompt",
    "build_sql_only_system_prompt",
    # Documentation prompts
    "DOCUMENT_SELECTION_PROMPT",
    "DOCUMENTATION_SYNTHESIS_PROMPT",
    # GraphQL prompts
    "GRAPHQL_CLASSIFICATION_PROMPT",
    "GRAPHQL_ENTITY_EXTRACTION_PROMPT",
    "ID_RESOLUTION_SELECTION_PROMPT",
    "build_classification_prompt",
    "build_extraction_prompt",
    "build_id_resolution_prompt",
    "build_query_plan_prompt",
    # SQL prompts
    "PRODUCT_CODE_SELECTION_PROMPT",
    "PRODUCT_EXTRACTION_PROMPT",
    "SQL_CODES_BLOCK",
    "SQL_CONTEXT_BLOCK",
    "SQL_DIRECTION_BLOCK",
    "SQL_GENERATION_PROMPT",
    "SQL_GROUP_TABLES_BLOCK",
    "SQL_MODE_BLOCK",
    "SQL_RETRY_BLOCK",
    "build_sql_generation_prefix",
]
