"""Tests for trade toggle overrides — schema, direction, and mode.

Covers:
- ChatRequest Pydantic validation (rejection of invalid values, backward compat)
- extract_products_node applying schema/mode overrides to LLM output
- create_query_generation_chain injecting constraint text into the SQL prompt
- generate_sql_node reading overrides from state and forwarding them
- agent_node building dynamic system prompts with override sections
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from langchain_core.messages import AIMessage, HumanMessage

from src.api import ChatRequest
from src.generate_query import (
    create_query_generation_chain,
    create_sql_agent,
    extract_products_node,
    generate_sql_node,
)
from src.product_and_schema_lookup import (
    ProductDetails,
    SchemasAndProductsFound,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_call_message(
    question: str = "What did Brazil export in 2021?",
    tool_call_id: str = "call_abc123",
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": tool_call_id,
                "name": "query_tool",
                "args": {"question": question},
            }
        ],
    )


def _base_state(**overrides) -> dict:
    state: dict = {
        "messages": [_make_tool_call_message()],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        "pipeline_question": "",
        "pipeline_products": None,
        "pipeline_codes": "",
        "pipeline_table_info": "",
        "pipeline_sql": "",
        "pipeline_result": "",
        "pipeline_result_columns": [],
        "pipeline_result_rows": [],
        "pipeline_execution_time_ms": 0,
        "override_schema": None,
        "override_direction": None,
        "override_mode": None,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# 1. ChatRequest validation — reject invalid values, accept valid ones
# ---------------------------------------------------------------------------


class TestChatRequestOverrideValidation:
    """Pydantic validation for override fields on ChatRequest."""

    def test_no_overrides_accepted(self):
        """Backward compat: request without overrides is valid."""
        req = ChatRequest(question="What did Brazil export?")
        assert req.override_schema is None
        assert req.override_direction is None
        assert req.override_mode is None

    def test_invalid_schema_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="q", override_schema="hs2024")

    def test_invalid_direction_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="q", override_direction="re-exports")

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="q", override_mode="digital")

    def test_all_overrides_together(self):
        """All three overrides can be set simultaneously."""
        req = ChatRequest(
            question="q",
            override_schema="hs12",
            override_direction="imports",
            override_mode="goods",
        )
        assert req.override_schema == "hs12"
        assert req.override_direction == "imports"
        assert req.override_mode == "goods"


# ---------------------------------------------------------------------------
# 2. extract_products_node — schema/mode overrides transform LLM output
# ---------------------------------------------------------------------------


class TestExtractProductsNodeOverrides:
    """extract_products_node should apply schema and mode overrides to the
    LLM's product extraction output before passing it downstream."""

    @staticmethod
    def _mock_extraction(canned_result):
        """Return a context manager that patches ProductAndSchemaLookup
        to return `canned_result` from extraction."""
        mock_lookup = MagicMock()
        mock_lookup.return_value.aextract_schemas_and_product_mentions_direct = (
            AsyncMock(return_value=canned_result)
        )
        return patch("src.generate_query.ProductAndSchemaLookup", mock_lookup)

    async def test_schema_override_replaces_llm_schemas_and_rebinds_products(self):
        """override_schema should replace classification_schemas AND update
        each product's classification_schema — so downstream code lookups
        use the right schema even though the LLM detected a different one."""
        canned = SchemasAndProductsFound(
            classification_schemas=["hs92"],
            products=[
                ProductDetails(
                    name="cotton", classification_schema="hs92", codes=["5201"]
                ),
                ProductDetails(
                    name="wheat", classification_schema="hs92", codes=["1001"]
                ),
            ],
            requires_product_lookup=True,
        )

        with self._mock_extraction(canned):
            state = _base_state(
                pipeline_question="US cotton and wheat exports?", override_schema="hs12"
            )
            result = await extract_products_node(
                state, llm=MagicMock(), engine=MagicMock()
            )

        products = result["pipeline_products"]
        assert products.classification_schemas == ["hs12"]
        assert all(p.classification_schema == "hs12" for p in products.products)
        # Product names/codes preserved (LLM still extracted them correctly)
        assert {p.name for p in products.products} == {"cotton", "wheat"}

    async def test_mode_goods_filters_out_services_schemas(self):
        """When mode=goods, services schemas should be removed from the
        LLM result while goods schemas are preserved."""
        canned = SchemasAndProductsFound(
            classification_schemas=["hs92", "services_bilateral"],
            products=[],
            requires_product_lookup=False,
        )

        with self._mock_extraction(canned):
            state = _base_state(
                pipeline_question="Trade between US and China?", override_mode="goods"
            )
            result = await extract_products_node(
                state, llm=MagicMock(), engine=MagicMock()
            )

        schemas = result["pipeline_products"].classification_schemas
        assert schemas == ["hs92"]
        assert "services_bilateral" not in schemas

    async def test_mode_goods_defaults_to_hs92_when_only_services_detected(self):
        """Edge case: LLM only detected services schemas but user wants goods.
        Should fall back to hs92 rather than leaving schemas empty."""
        canned = SchemasAndProductsFound(
            classification_schemas=["services_unilateral", "services_bilateral"],
            products=[],
            requires_product_lookup=False,
        )

        with self._mock_extraction(canned):
            state = _base_state(pipeline_question="Trade data?", override_mode="goods")
            result = await extract_products_node(
                state, llm=MagicMock(), engine=MagicMock()
            )

        assert result["pipeline_products"].classification_schemas == ["hs92"]

    async def test_mode_services_keeps_only_services_schemas(self):
        """When mode=services, goods schemas (hs92/hs12/sitc) should be
        removed and only services_ schemas preserved."""
        canned = SchemasAndProductsFound(
            classification_schemas=["hs92", "services_bilateral"],
            products=[],
            requires_product_lookup=False,
        )

        with self._mock_extraction(canned):
            state = _base_state(
                pipeline_question="Services trade?", override_mode="services"
            )
            result = await extract_products_node(
                state, llm=MagicMock(), engine=MagicMock()
            )

        schemas = result["pipeline_products"].classification_schemas
        assert schemas == ["services_bilateral"]
        assert "hs92" not in schemas

    async def test_mode_services_defaults_when_no_services_detected(self):
        """Edge case: LLM detected only goods schemas but user wants services.
        Should fall back to services_unilateral."""
        canned = SchemasAndProductsFound(
            classification_schemas=["hs92"],
            products=[],
            requires_product_lookup=False,
        )

        with self._mock_extraction(canned):
            state = _base_state(
                pipeline_question="Services trade?", override_mode="services"
            )
            result = await extract_products_node(
                state, llm=MagicMock(), engine=MagicMock()
            )

        assert result["pipeline_products"].classification_schemas == [
            "services_unilateral"
        ]

    async def test_schema_override_takes_precedence_over_mode(self):
        """When both schema and mode are set, schema wins — because a
        specific schema like 'sitc' is more precise than mode='services'."""
        canned = SchemasAndProductsFound(
            classification_schemas=["hs92"],
            products=[],
            requires_product_lookup=False,
        )

        with self._mock_extraction(canned):
            state = _base_state(
                pipeline_question="q",
                override_schema="sitc",
                override_mode="services",
            )
            result = await extract_products_node(
                state, llm=MagicMock(), engine=MagicMock()
            )

        assert result["pipeline_products"].classification_schemas == ["sitc"]

    async def test_no_overrides_preserves_llm_output(self):
        """Without overrides, the LLM's detection result passes through unchanged."""
        canned = SchemasAndProductsFound(
            classification_schemas=["hs92", "services_bilateral"],
            products=[
                ProductDetails(
                    name="coffee", classification_schema="hs92", codes=["0901"]
                ),
            ],
            requires_product_lookup=True,
        )

        with self._mock_extraction(canned):
            state = _base_state(pipeline_question="Coffee trade?")
            result = await extract_products_node(
                state, llm=MagicMock(), engine=MagicMock()
            )

        products = result["pipeline_products"]
        assert products.classification_schemas == ["hs92", "services_bilateral"]
        assert products.products[0].classification_schema == "hs92"
        assert products.products[0].name == "coffee"


# ---------------------------------------------------------------------------
# 3. create_query_generation_chain — constraint text in SQL prompt
# ---------------------------------------------------------------------------


class TestQueryGenerationChainConstraints:
    """create_query_generation_chain should inject constraint paragraphs
    into the prompt when direction_constraint or mode_constraint is set."""

    @staticmethod
    def _render_prompt(**kwargs) -> str:
        """Build the chain and render the prompt text (without invoking LLM).

        Uses a mock LLM since we only care about the prompt, not the output.
        The chain is: prompt | llm | StrOutputParser | _strip.
        We extract the first element (the prompt) and format it.
        """
        mock_llm = MagicMock()
        chain = create_query_generation_chain(
            llm=mock_llm,
            example_queries=[],
            **kwargs,
        )
        # chain.first is the FewShotPromptTemplate
        return chain.first.format(question="What did Brazil export?")

    def test_no_constraints_no_override_text(self):
        """Without constraints, the prompt should not contain override paragraphs."""
        prompt = self._render_prompt()
        assert "User override" not in prompt
        assert (
            "trade direction"
            not in prompt.lower().split("user override")[-1:].__repr__()
        )

    def test_direction_constraint_appears_in_prompt(self):
        """direction_constraint='imports' should inject an imports-specific
        paragraph that instructs the LLM to use import data."""
        prompt = self._render_prompt(direction_constraint="imports")
        assert "User override" in prompt
        assert "imports" in prompt
        assert "trade direction" in prompt.lower()

    def test_mode_constraint_appears_in_prompt(self):
        """mode_constraint='services' should inject a paragraph instructing
        the LLM to use only services tables."""
        prompt = self._render_prompt(mode_constraint="services")
        assert "User override" in prompt
        assert "services" in prompt
        assert "trade mode" in prompt.lower()

    def test_both_constraints_appear_together(self):
        """Both direction and mode constraints can appear simultaneously."""
        prompt = self._render_prompt(
            direction_constraint="exports",
            mode_constraint="goods",
        )
        assert "exports" in prompt
        assert "goods" in prompt
        # Both override sections present
        assert prompt.count("User override") == 2

    def test_direction_constraint_mentions_correct_direction(self):
        """The injected text should reference the specific direction, not
        a generic placeholder."""
        for direction in ("exports", "imports"):
            prompt = self._render_prompt(direction_constraint=direction)
            # The constraint paragraph should mention the direction multiple times
            # (once in the header, at least once in the instruction)
            assert prompt.count(direction) >= 2


# ---------------------------------------------------------------------------
# 4. generate_sql_node — reads overrides from state, passes to chain
# ---------------------------------------------------------------------------


class TestGenerateSqlNodeOverrides:
    """generate_sql_node should read override_direction and override_mode
    from state and pass them as direction_constraint/mode_constraint."""

    async def test_direction_override_forwarded_to_chain(self):
        mock_llm = MagicMock()

        with patch("src.generate_query.create_query_generation_chain") as mock_create:
            mock_chain = MagicMock()
            mock_chain.ainvoke = AsyncMock(return_value="SELECT 1")
            mock_create.return_value = mock_chain

            state = _base_state(
                pipeline_question="Brazil exports?",
                pipeline_codes="",
                pipeline_table_info="table info",
                override_direction="imports",
            )
            await generate_sql_node(
                state, llm=mock_llm, example_queries=[], max_results=15
            )

        _, kwargs = mock_create.call_args
        assert kwargs["direction_constraint"] == "imports"

    async def test_mode_override_forwarded_to_chain(self):
        mock_llm = MagicMock()

        with patch("src.generate_query.create_query_generation_chain") as mock_create:
            mock_chain = MagicMock()
            mock_chain.ainvoke = AsyncMock(return_value="SELECT 1")
            mock_create.return_value = mock_chain

            state = _base_state(
                pipeline_question="Trade data?",
                pipeline_codes="",
                pipeline_table_info="table info",
                override_mode="services",
            )
            await generate_sql_node(
                state, llm=mock_llm, example_queries=[], max_results=15
            )

        _, kwargs = mock_create.call_args
        assert kwargs["mode_constraint"] == "services"

    async def test_no_overrides_passes_none_constraints(self):
        mock_llm = MagicMock()

        with patch("src.generate_query.create_query_generation_chain") as mock_create:
            mock_chain = MagicMock()
            mock_chain.ainvoke = AsyncMock(return_value="SELECT 1")
            mock_create.return_value = mock_chain

            state = _base_state(
                pipeline_question="q", pipeline_codes="", pipeline_table_info=""
            )
            await generate_sql_node(
                state, llm=mock_llm, example_queries=[], max_results=15
            )

        _, kwargs = mock_create.call_args
        assert kwargs["direction_constraint"] is None
        assert kwargs["mode_constraint"] is None


# ---------------------------------------------------------------------------
# 5. agent_node — dynamic system prompt includes override section
# ---------------------------------------------------------------------------


class TestAgentNodeDynamicPrompt:
    """The agent_node closure inside create_sql_agent should build a
    SystemMessage that includes an 'Active User Overrides' section when
    overrides are present in state, and omit it when they're absent."""

    @staticmethod
    def _build_agent_and_capture_prompt(state: dict) -> str:
        """Build the graph, invoke agent_node with the given state, and
        capture the SystemMessage content sent to the LLM.

        Returns the system prompt text.
        """
        captured_messages = []

        mock_llm = MagicMock()
        mock_bound = MagicMock()

        async def _capture_invoke(messages):
            captured_messages.extend(messages)
            return AIMessage(content="I'll help with that.")

        mock_bound.ainvoke = _capture_invoke
        mock_llm.bind_tools.return_value = mock_bound

        mock_db = MagicMock()
        mock_engine = MagicMock()

        graph = create_sql_agent(
            llm=mock_llm,
            db=mock_db,
            engine=mock_engine,
            table_descriptions={},
            example_queries=[],
        )

        # Extract just the agent_node function from the compiled graph
        # We need to call it directly with state to capture the prompt
        # The node is registered under "agent" in the graph
        import asyncio

        agent_fn = graph.nodes["agent"]
        asyncio.get_event_loop().run_until_complete(agent_fn.ainvoke(state))

        # Find the SystemMessage in captured_messages
        system_msgs = [
            m
            for m in captured_messages
            if hasattr(m, "content") and "Ask-Atlas" in m.content
        ]
        assert system_msgs, "No system message captured"
        return system_msgs[0].content

    def test_overrides_present_adds_override_section(self):
        """When all three overrides are set, the system prompt should
        contain an 'Active User Overrides' section with all three."""
        state = _base_state(
            messages=[HumanMessage(content="What did Brazil export?")],
            override_schema="hs12",
            override_direction="exports",
            override_mode="goods",
        )
        prompt = self._build_agent_and_capture_prompt(state)

        assert "Active User Overrides" in prompt
        assert "hs12" in prompt
        assert "exports" in prompt
        assert "goods" in prompt
        assert "contradicts an override" in prompt

    def test_no_overrides_omits_override_section(self):
        """When no overrides are set, the system prompt should NOT contain
        an override section."""
        state = _base_state(
            messages=[HumanMessage(content="What did Brazil export?")],
        )
        prompt = self._build_agent_and_capture_prompt(state)

        assert "Active User Overrides" not in prompt
        assert "contradicts an override" not in prompt

    def test_single_override_only_shows_that_override(self):
        """Only the set overrides should appear — not the unset ones."""
        state = _base_state(
            messages=[HumanMessage(content="What did Brazil export?")],
            override_direction="imports",
        )
        prompt = self._build_agent_and_capture_prompt(state)

        assert "Active User Overrides" in prompt
        assert "imports" in prompt
        # Schema and mode not mentioned in override section
        assert "Classification schema:" not in prompt
        assert "Trade mode:" not in prompt
