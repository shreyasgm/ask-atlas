"""Structural tests for src/prompts.py.

These tests guard against real failure modes:
- Format placeholder mismatches between constants and builder functions
- Accidental removal of tool name references that break agent routing
- XML tags that break provider-agnostic compatibility
- ChatPromptTemplate brace escaping corruption
- Circular imports from the leaf-dependency module
- Builder conditional block logic (inclusion, exclusion, ordering)
- Content drift from the canonical prompts (key phrases that must survive)
"""

import re
import string

import pytest

from src import prompts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_format_fields(text: str) -> set[str]:
    """Extract .format() field names from a string (ignores positional)."""
    return {
        fname for _, fname, _, _ in string.Formatter().parse(text) if fname is not None
    }


def _has_unresolved_format_fields(text: str) -> bool:
    """Return True if text contains unresolved {name} format placeholders."""
    # Match {word} but not {{ or }}
    return bool(re.search(r"(?<!\{)\{[a-zA-Z_]\w*\}(?!\})", text))


# ---------------------------------------------------------------------------
# All prompt constant names — used by parametrized guard-rail tests
# ---------------------------------------------------------------------------

_PROMPT_CONSTANTS = [
    "AGENT_SYSTEM_PROMPT",
    "DUAL_TOOL_EXTENSION",
    "DOCS_TOOL_EXTENSION",
    "SQL_GENERATION_PROMPT",
    "SQL_CODES_BLOCK",
    "SQL_DIRECTION_BLOCK",
    "SQL_MODE_BLOCK",
    "SQL_CONTEXT_BLOCK",
    "PRODUCT_EXTRACTION_PROMPT",
    "PRODUCT_CODE_SELECTION_PROMPT",
    "GRAPHQL_CLASSIFICATION_PROMPT",
    "GRAPHQL_ENTITY_EXTRACTION_PROMPT",
    "ID_RESOLUTION_SELECTION_PROMPT",
    "DOCUMENT_SELECTION_PROMPT",
    "DOCUMENTATION_SYNTHESIS_PROMPT",
]


# ---------------------------------------------------------------------------
# Format placeholder contract tests
#
# These verify that each prompt's placeholders exactly match what
# its builder function passes. A mismatch means either:
#   (a) someone added {foo} to the prompt but the builder doesn't pass it → KeyError
#   (b) the builder passes a kwarg the prompt doesn't use → silent waste / wrong prompt
# ---------------------------------------------------------------------------


class TestFormatPlaceholderContracts:
    """Each prompt with a builder must have placeholders matching the builder's kwargs."""

    def test_agent_system_prompt_matches_builder(self):
        """Builder passes max_uses + top_k_per_query; prompt must use exactly those."""
        assert _get_format_fields(prompts.AGENT_SYSTEM_PROMPT) == {
            "max_uses",
            "top_k_per_query",
        }

    def test_dual_tool_extension_matches_caller(self):
        """agent_node.py calls .format(max_uses=..., budget_status=..., sql_max_year=..., graphql_max_year=...)."""
        assert _get_format_fields(prompts.DUAL_TOOL_EXTENSION) == {
            "max_uses",
            "budget_status",
            "sql_max_year",
            "graphql_max_year",
        }

    def test_docs_tool_extension_matches_caller(self):
        """agent_node.py calls .format(max_uses=...)."""
        assert _get_format_fields(prompts.DOCS_TOOL_EXTENSION) == {"max_uses"}

    def test_sql_generation_prompt_matches_builder(self):
        """build_sql_generation_prefix passes top_k + table_info + sql_max_year to the base prompt."""
        assert _get_format_fields(prompts.SQL_GENERATION_PROMPT) == {
            "top_k",
            "table_info",
            "sql_max_year",
        }

    def test_sql_conditional_blocks_match_builder(self):
        """Each SQL conditional block uses exactly the placeholder its builder passes."""
        assert _get_format_fields(prompts.SQL_CODES_BLOCK) == {"codes"}
        assert _get_format_fields(prompts.SQL_DIRECTION_BLOCK) == {"direction"}
        assert _get_format_fields(prompts.SQL_MODE_BLOCK) == {"mode"}
        assert _get_format_fields(prompts.SQL_CONTEXT_BLOCK) == {"context"}

    def test_classification_prompt_matches_builder(self):
        """build_classification_prompt passes question + context_block."""
        assert _get_format_fields(prompts.GRAPHQL_CLASSIFICATION_PROMPT) == {
            "question",
            "context_block",
        }

    def test_extraction_prompt_matches_builder(self):
        """build_extraction_prompt passes question, query_type, context_block, services_catalog_block."""
        assert _get_format_fields(prompts.GRAPHQL_ENTITY_EXTRACTION_PROMPT) == {
            "question",
            "query_type",
            "context_block",
            "services_catalog_block",
        }

    def test_id_resolution_prompt_matches_builder(self):
        """build_id_resolution_prompt passes question, options, num_candidates."""
        assert _get_format_fields(prompts.ID_RESOLUTION_SELECTION_PROMPT) == {
            "question",
            "options",
            "num_candidates",
        }

    def test_document_selection_prompt_matches_caller(self):
        """docs_pipeline calls .format(question=..., context_block=..., manifest=..., max_docs=...)."""
        assert _get_format_fields(prompts.DOCUMENT_SELECTION_PROMPT) == {
            "question",
            "context_block",
            "manifest",
            "max_docs",
        }

    def test_documentation_synthesis_prompt_matches_caller(self):
        """docs_pipeline calls .format(question=..., context_block=..., docs_content=...)."""
        assert _get_format_fields(prompts.DOCUMENTATION_SYNTHESIS_PROMPT) == {
            "question",
            "context_block",
            "docs_content",
        }


# ---------------------------------------------------------------------------
# Builder function behavior tests
#
# These test the real conditional logic in builders — which blocks get
# included/excluded, ordering, and that formatting resolves all placeholders.
# ---------------------------------------------------------------------------


class TestBuildAgentSystemPrompt:
    def test_formats_without_unresolved_placeholders(self):
        """After formatting, no {name} placeholders should remain."""
        result = prompts.build_agent_system_prompt(max_uses=3, top_k_per_query=15)
        assert not _has_unresolved_format_fields(result)

    def test_canonical_phrases_survive(self):
        """Key phrases from the original prompt must survive the move.

        These are the phrases that test_agent_node.py asserts against,
        so if they disappear the integration tests would also break.
        """
        result = prompts.build_agent_system_prompt(max_uses=3, top_k_per_query=15)
        assert "You are Ask-Atlas" in result
        assert "international trade data" in result
        assert "SQL" in result

    def test_max_uses_value_injected_in_rules_section(self):
        """max_uses=7 should appear in the 'Important Rules' section, not just anywhere."""
        result = prompts.build_agent_system_prompt(max_uses=7, top_k_per_query=20)
        # The prompt says "up to {max_uses} times" — verify the 7 is in that context
        assert "up to 7 times" in result

    def test_top_k_value_injected_in_rules_section(self):
        """top_k_per_query=42 should appear in the 'Important Rules' section."""
        result = prompts.build_agent_system_prompt(max_uses=3, top_k_per_query=42)
        assert "at most 42 rows" in result


class TestBuildSqlGenerationPrefix:
    def test_minimal_has_base_prompt_only(self):
        """With no codes/constraints/context, only the base SQL prompt is present."""
        result = prompts.build_sql_generation_prefix(
            codes=None,
            top_k=10,
            table_info="CREATE TABLE test (id int);",
            direction_constraint=None,
            mode_constraint=None,
            context="",
        )
        assert "CREATE TABLE test" in result
        assert "Product codes for reference" not in result
        assert "User override" not in result
        assert "Additional technical context" not in result

    def test_codes_block_included_when_codes_present(self):
        result = prompts.build_sql_generation_prefix(
            codes="- coffee (HS92): 0901",
            top_k=10,
            table_info="DDL",
            direction_constraint=None,
            mode_constraint=None,
            context="",
        )
        assert "Product codes for reference" in result
        assert "0901" in result

    def test_codes_block_excluded_when_codes_empty_string(self):
        """Empty string codes should NOT trigger the codes block (truthy check)."""
        result = prompts.build_sql_generation_prefix(
            codes="",
            top_k=10,
            table_info="DDL",
            direction_constraint=None,
            mode_constraint=None,
            context="",
        )
        assert "Product codes for reference" not in result

    def test_direction_block_included_with_exports(self):
        result = prompts.build_sql_generation_prefix(
            codes=None,
            top_k=10,
            table_info="DDL",
            direction_constraint="exports",
            mode_constraint=None,
            context="",
        )
        assert "**exports**" in result
        assert "User override" in result

    def test_mode_block_included_with_services(self):
        result = prompts.build_sql_generation_prefix(
            codes=None,
            top_k=10,
            table_info="DDL",
            direction_constraint=None,
            mode_constraint="services",
            context="",
        )
        assert "**services**" in result

    def test_context_block_included_when_nonempty(self):
        result = prompts.build_sql_generation_prefix(
            codes=None,
            top_k=10,
            table_info="DDL",
            direction_constraint=None,
            mode_constraint=None,
            context="PCI is stored in export_pci column",
        )
        assert "PCI is stored in export_pci column" in result
        assert "Additional technical context" in result

    def test_all_blocks_present_in_correct_order(self):
        """When all options are provided, blocks appear in order: codes, direction, mode, context."""
        result = prompts.build_sql_generation_prefix(
            codes="- coffee: 0901",
            top_k=10,
            table_info="DDL",
            direction_constraint="exports",
            mode_constraint="goods",
            context="some context",
        )
        codes_pos = result.index("Product codes for reference")
        direction_pos = result.index("trade direction")
        mode_pos = result.index("trade mode")
        context_pos = result.index("Additional technical context")
        assert codes_pos < direction_pos < mode_pos < context_pos

    def test_no_unresolved_placeholders_after_full_build(self):
        result = prompts.build_sql_generation_prefix(
            codes="- coffee: 0901",
            top_k=10,
            table_info="DDL",
            direction_constraint="exports",
            mode_constraint="goods",
            context="some context",
        )
        assert not _has_unresolved_format_fields(result)


class TestBuildClassificationPrompt:
    def test_no_context_omits_context_section(self):
        result = prompts.build_classification_prompt("What did Kenya export?")
        assert "Kenya" in result
        assert "Context from conversation" not in result

    def test_with_context_includes_context_section(self):
        result = prompts.build_classification_prompt(
            "What did Kenya export?", "User is interested in complexity"
        )
        assert "User is interested in complexity" in result
        assert "Context from conversation" in result

    def test_no_unresolved_placeholders(self):
        result = prompts.build_classification_prompt("test question", "test context")
        assert not _has_unresolved_format_fields(result)

    def test_empty_question_still_formats(self):
        """Edge case: empty question should not raise."""
        result = prompts.build_classification_prompt("")
        assert "**Question:**" in result


class TestBuildExtractionPrompt:
    def test_minimal_includes_query_type(self):
        result = prompts.build_extraction_prompt("Brazil coffee", "treemap_products")
        assert "treemap_products" in result
        assert "Brazil" in result

    def test_with_context(self):
        result = prompts.build_extraction_prompt(
            "Brazil coffee",
            "treemap_products",
            context="User wants HS92 data",
        )
        assert "User wants HS92 data" in result

    def test_with_services_catalog(self):
        result = prompts.build_extraction_prompt(
            "Kenya tourism",
            "treemap_products",
            services_catalog="Travel & tourism\nTransport\nICT",
        )
        assert "Travel & tourism" in result
        assert "Available service categories" in result

    def test_without_services_catalog_omits_section(self):
        result = prompts.build_extraction_prompt(
            "Kenya exports",
            "treemap_products",
        )
        assert "Available service categories" not in result

    def test_no_unresolved_placeholders(self):
        result = prompts.build_extraction_prompt(
            "q", "t", context="c", services_catalog="s"
        )
        assert not _has_unresolved_format_fields(result)


class TestBuildIdResolutionPrompt:
    def test_includes_all_parts(self):
        result = prompts.build_id_resolution_prompt(
            question="Turkey exports",
            options="1. Turkey (TUR)\n2. Turkey meat (0207)",
            num_candidates=2,
        )
        assert "Turkey exports" in result
        assert "Turkey (TUR)" in result
        assert "1-2" in result

    def test_no_unresolved_placeholders(self):
        result = prompts.build_id_resolution_prompt("q", "1. opt", 1)
        assert not _has_unresolved_format_fields(result)


# ---------------------------------------------------------------------------
# Guard rail: no XML tags (provider-agnostic design rule)
# ---------------------------------------------------------------------------

_XML_TAG_PATTERN = re.compile(r"<(?!.*@|.*http)[a-zA-Z][a-zA-Z0-9_]*(?:\s[^>]*)?>")


class TestNoXmlTags:
    @pytest.mark.parametrize("name", _PROMPT_CONSTANTS)
    def test_prompt_has_no_xml_tags(self, name):
        val = getattr(prompts, name)
        matches = _XML_TAG_PATTERN.findall(val)
        assert not matches, f"{name} contains XML-like tags: {matches}"


# ---------------------------------------------------------------------------
# Guard rail: tool names referenced in prompts match real tool schema names
#
# If someone renames a tool (e.g. atlas_graphql -> graphql_tool) but
# forgets to update the prompt, the agent will reference a nonexistent tool.
# ---------------------------------------------------------------------------


class TestToolNameReferences:
    def test_dual_tool_extension_references_exact_tool_names(self):
        """DUAL_TOOL_EXTENSION must reference both actual tool schema names."""
        assert "atlas_graphql" in prompts.DUAL_TOOL_EXTENSION
        assert "query_tool" in prompts.DUAL_TOOL_EXTENSION

    def test_docs_tool_extension_references_all_tools(self):
        """DOCS_TOOL_EXTENSION tells the agent to pass context to data tools by name."""
        assert "docs_tool" in prompts.DOCS_TOOL_EXTENSION
        assert "query_tool" in prompts.DOCS_TOOL_EXTENSION
        assert "atlas_graphql" in prompts.DOCS_TOOL_EXTENSION


# ---------------------------------------------------------------------------
# Guard rail: ChatPromptTemplate double-brace escaping
#
# PRODUCT_EXTRACTION_PROMPT is used inside ChatPromptTemplate, which treats
# {var} as template variables. The JSON examples use {{ and }} to produce
# literal braces. If someone "cleans up" the double braces to single braces,
# ChatPromptTemplate will crash with a KeyError on the JSON keys.
# ---------------------------------------------------------------------------


class TestProductExtractionEscaping:
    def test_double_braces_present(self):
        """The prompt must contain {{ and }} for ChatPromptTemplate escaping."""
        assert "{{" in prompts.PRODUCT_EXTRACTION_PROMPT
        assert "}}" in prompts.PRODUCT_EXTRACTION_PROMPT

    def test_no_unescaped_format_fields_after_brace_resolution(self):
        """After resolving {{ -> { and }} -> }, no bare .format() fields should exist.

        This catches: someone adds a {new_field} without escaping it as {{new_field}}.
        """
        collapsed = prompts.PRODUCT_EXTRACTION_PROMPT.replace("{{", "").replace(
            "}}", ""
        )
        fields = _get_format_fields(collapsed)
        assert (
            fields == set()
        ), f"Found unescaped format fields in PRODUCT_EXTRACTION_PROMPT: {fields}"


# ---------------------------------------------------------------------------
# Guard rail: leaf dependency (zero src/ imports)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Content assertion tests — prompt additions from eval diagnostics
# ---------------------------------------------------------------------------


class TestPromptContentAdditions:
    """Verify that key prompt additions from eval diagnostic fixes are present."""

    def test_dual_tool_extension_has_pre_computed_fields_guidance(self):
        """DUAL_TOOL_EXTENSION must instruct the agent to trust pre-computed metrics."""
        assert "Pre-Computed Fields" in prompts.DUAL_TOOL_EXTENSION
        assert "diversificationGrade" in prompts.DUAL_TOOL_EXTENSION
        assert "exportValueConstGrowthCagr" in prompts.DUAL_TOOL_EXTENSION

    def test_dual_tool_extension_has_data_coverage_section(self):
        """DUAL_TOOL_EXTENSION must include the Data Coverage routing guidance."""
        assert "Data Coverage" in prompts.DUAL_TOOL_EXTENSION

    def test_agent_system_prompt_has_anti_fabrication_rule(self):
        """AGENT_SYSTEM_PROMPT must contain the anti-fabrication rule."""
        assert "fabricate" in prompts.AGENT_SYSTEM_PROMPT
        assert "tool response" in prompts.AGENT_SYSTEM_PROMPT

    def test_classification_prompt_has_services_example(self):
        """build_classification_prompt output must include services routing example."""
        result = prompts.build_classification_prompt("test question")
        assert "services exports" in result

    def test_classification_prompt_has_services_routing(self):
        """The GRAPHQL_CLASSIFICATION_PROMPT must route services to treemap_products."""
        assert "tourism" in prompts.GRAPHQL_CLASSIFICATION_PROMPT
        assert "treemap_products" in prompts.GRAPHQL_CLASSIFICATION_PROMPT


class TestLeafDependency:
    def test_no_src_imports(self):
        """src/prompts.py must not import from any other src/ module.

        This is a design invariant: prompts.py is a leaf so it can never
        cause circular imports regardless of how other modules are restructured.
        """
        import inspect

        source = inspect.getsource(prompts)
        src_imports = re.findall(r"(?:^|\n)\s*(?:from|import)\s+src\.", source)
        assert not src_imports, (
            f"src/prompts.py must be a leaf dependency with zero src/ imports, "
            f"found: {src_imports}"
        )
