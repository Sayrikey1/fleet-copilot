"""Chat-model factory and the minimal interface the graph depends on.

The graph only needs an object that can ``bind_tools(schemas)`` and ``invoke(messages)``
returning a LangChain ``AIMessage``. Real runs use ``ChatAnthropic``; the deterministic
test suite injects a scripted fake with the same two methods — so the graph is exercised
end to end with no network and no API key.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import BaseMessage


@runtime_checkable
class ChatModel(Protocol):
    def bind_tools(self, tools: list[Any], **kwargs: Any) -> Any: ...
    def invoke(self, messages: list[BaseMessage], **kwargs: Any) -> Any: ...


def build_model(settings: Any) -> Any:
    """Construct a ChatAnthropic bound to the configured model (temperature 0 by default).

    Returns an object satisfying :class:`ChatModel` (``bind_tools`` + ``invoke``). Imported
    lazily so the package works without ``langchain_anthropic`` installed when only the
    deterministic (fake-model) paths are used.
    """
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
    # Some Claude models (reasoning models) only accept their default temperature,
    # so only pass it when explicitly configured.
    if settings.temperature is not None:
        kwargs["temperature"] = settings.temperature
    return ChatAnthropic(**kwargs)
