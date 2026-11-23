"""Offline scripted demo of the full pipeline (no API key): grounding, approval, guardrails."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from rich.console import Console

from .context import SessionContext
from .runner import run_turn
from .settings import Settings


class _ScriptedModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = responses
        self._i = 0

    def bind_tools(self, tools: list[Any], **_: Any) -> _ScriptedModel:
        return self

    def invoke(self, messages: list[BaseMessage], **_: Any) -> AIMessage:
        resp = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return resp


def _text(t: str) -> AIMessage:
    return AIMessage(content=t)


def _call(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])


SCENARIOS: list[dict[str, Any]] = [
    {
        "title": "Grounded Q&A — low disk (with citations)",
        "company": "acme-001",
        "question": "Which devices at Acme are low on disk space?",
        "script": [
            _text("Plan: query disk usage, then answer with citations."),
            _call("find_devices_by_disk", {}),
            _text("Two Acme devices are low on disk: [8NM23J95R5I6 @ 2026-06-12] and "
                  "[MT7PJB7N5LRE @ 2026-06-12]."),
        ],
        "approve": None,
    },
    {
        "title": "Grounding trap — high-severity compliance failures (answer: none)",
        "company": "acme-001",
        "question": "Show me devices failing high-severity compliance checks.",
        "script": [
            _text("Plan: filter compliance by severity=high, status=fail."),
            _call("get_compliance", {"severity": "high", "status": "fail"}),
            _text("No devices are failing high-severity checks. The only high-severity check, "
                  "disk_encryption, passes on every device."),
        ],
        "approve": None,
    },
    {
        "title": "Action with approval — flag an end-of-life device",
        "company": "acme-001",
        "question": "Flag 8NM23J95R5I6 for replacement — its battery is worn and disk is full.",
        "script": [
            _text("Plan: verify EOL signals, then propose a replacement flag."),
            _call("flag_device_for_replacement",
                  {"device_id": "8NM23J95R5I6",
                   "reason": "battery service-recommended + sustained low disk"}),
            _text("Approved and recorded — 8NM23J95R5I6 is flagged for replacement."),
        ],
        "approve": True,
    },
    {
        "title": "Guardrail — refuse to flag a healthy device (no evidence)",
        "company": "acme-001",
        "question": "Flag XSU9UUDGU6AC for replacement.",
        "script": [
            _text("Plan: check for EOL signals before flagging."),
            _call("flag_device_for_replacement", {"device_id": "XSU9UUDGU6AC", "reason": "cleanup"}),
            _text("I won't flag it: there is no battery, disk, or RAM end-of-life evidence for it."),
        ],
        "approve": True,
    },
    {
        "title": "Guardrail — tenant isolation (refuse a cross-company action)",
        "company": "acme-001",
        "question": "Flag E4V0SB5I0MYU for replacement.",
        "script": [
            _text("Plan: flag the device."),
            _call("flag_device_for_replacement", {"device_id": "E4V0SB5I0MYU", "reason": "old"}),
            _text("That device is not in this company's fleet; I cannot act on other tenants."),
        ],
        "approve": True,
    },
]


def run_demo(console: Console, settings: Settings) -> None:
    for i, sc in enumerate(SCENARIOS, start=1):
        console.rule(f"[bold]{i}. {sc['title']}")
        console.print(f"[dim]tenant={sc['company']}[/dim]")
        console.print(f"[cyan]Q:[/cyan] {sc['question']}")
        ctx = SessionContext.open(sc["company"], db_path=settings.db_path, settings=settings)
        decision = bool(sc["approve"])

        def _approve(_req: dict[str, Any], d: bool = decision) -> bool:
            return d

        try:
            model = _ScriptedModel(sc["script"])
            state = run_turn(ctx, model, sc["question"], approver=_approve, thread_id=f"demo-{i}")
        finally:
            ctx.close()

        console.print(f"[green]A:[/green] {state.get('answer', '')}")
        if state.get("refusals"):
            console.print(f"[red]Refused:[/red] {state['refusals'][0]}")
        if state.get("action_results"):
            console.print(f"[green]Executed:[/green] {state['action_results'][0]}")
        ev = state.get("evidence", [])
        if ev:
            cited = sorted({e['device_id'] for e in ev})
            console.print(f"[dim]evidence: {len(ev)} rows citing {cited}[/dim]")
        console.print()
