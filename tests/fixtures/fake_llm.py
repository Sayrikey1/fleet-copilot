"""A scripted offline chat model (bind_tools + invoke) — the deterministic stand-in."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage


class ScriptedModel:
    """Returns one scripted response per invoke (clamping at the last)."""

    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = list(responses)
        self.calls: list[list[BaseMessage]] = []
        self.bound_tools: list[Any] | None = None
        self._i = 0

    def bind_tools(self, tools: list[Any], **_: Any) -> ScriptedModel:
        self.bound_tools = tools
        return self

    def invoke(self, messages: list[BaseMessage], **_: Any) -> AIMessage:
        self.calls.append(list(messages))
        resp = self.responses[min(self._i, len(self.responses) - 1)]
        self._i += 1
        return resp


def ai_text(text: str) -> AIMessage:
    return AIMessage(content=text)


def ai_tool_call(name: str, args: dict[str, Any], *, call_id: str = "call-1", content: str = "") -> AIMessage:
    return AIMessage(content=content, tool_calls=[{"name": name, "args": args, "id": call_id}])
