"""Chat-model factory + the minimal interface the graph needs (bind_tools + invoke).
Real runs use ChatAnthropic; tests inject a scripted fake with the same two methods."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import BaseMessage


@runtime_checkable
class ChatModel(Protocol):
    def bind_tools(self, tools: list[Any], **kwargs: Any) -> Any: ...
    def invoke(self, messages: list[BaseMessage], **kwargs: Any) -> Any: ...


def build_model(settings: Any) -> Any:
    """ChatAnthropic for the configured model (langchain_anthropic imported lazily)."""
    if not settings.has_anthropic_key:
        raise RuntimeError(
            "No ANTHROPIC_API_KEY configured. Set it in .env to run the live agent, "
            "or use the deterministic `demo` command / test suite (no key needed)."
        )
    from langchain_anthropic import ChatAnthropic

    kwargs: dict[str, Any] = {
        "model_name": settings.model,
        "timeout": 60,
        "stop": None,
        "api_key": settings.anthropic_api_key,
    }
    # Reasoning models reject non-default temperature; only pass it when set.
    if settings.temperature is not None:
        kwargs["temperature"] = settings.temperature
    return ChatAnthropic(**kwargs)
