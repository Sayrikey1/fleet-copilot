"""A scripted, offline chat model — the deterministic stand-in for ChatAnthropic.

Mirrors the approach in SpinStrip-AI's ``testing.py``: replay pre-scripted responses in
order and record every call, so the whole graph (plan → agent → tools → approval → verify)
runs with no network and no API key. The graph only needs ``bind_tools`` + ``invoke``.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage


class ScriptedModel:
    """Returns ``responses`` one per ``invoke`` call (clamping at the last one)."""

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
    """A plain assistant answer (no tool calls) — ends the agent loop."""
    return AIMessage(content=text)


def ai_tool_call(name: str, args: dict[str, Any], *, call_id: str = "call-1", content: str = "") -> AIMessage:
    """An assistant turn that calls one tool."""
    return AIMessage(content=content, tool_calls=[{"name": name, "args": args, "id": call_id}])
