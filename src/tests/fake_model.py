"""Fake chat model for testing agent trajectories without a real LLM."""

from typing import Any, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeToolCallingModel(BaseChatModel):
    """A minimal BaseChatModel that returns scripted AIMessage responses in order.

    Designed to work with ``langchain.agents.create_agent()`` which calls
    ``model.bind_tools()`` internally.  Since responses are pre-scripted,
    ``bind_tools`` is a no-op that returns ``self``.
    """

    responses: List[AIMessage]
    index: int = 0

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = self.responses[self.index % len(self.responses)]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeToolCallingModel":
        """No-op — the model ignores tool schemas since responses are scripted."""
        return self
