"""The explicit agent graph: plan → agent ⇄ tools → verify → respond, with an
approval_gate (interrupt) for actions. ctx + model live in closures, not in state.
See docs/ARCHITECTURE.md for the full diagram."""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from . import audit
from . import tools as _tools  # noqa: F401  (registers tools)
from .context import SessionContext
from .grounding import ProposedAction, unsupported_device_ids
from .llm import ChatModel
from .prompts import build_agent_prompt, build_planner_prompt
from .registry import REGISTRY, ActionSpec, ReadSpec


class FleetState(TypedDict, total=False):
    question: str
    plan: str
    messages: list[Any]
    evidence: list[dict[str, Any]]
    pending_approval: dict[str, Any] | None
    action_results: list[dict[str, Any]]
    refusals: list[str]
    unsupported_ids: list[str]
    verified_once: bool
    verified: bool
    answer: str
    round: int


def _last_ai(messages: list[Any]) -> AIMessage | None:
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return m
    return None


def build_graph(ctx: SessionContext, model: ChatModel) -> Any:
    """Compile a fresh graph bound to one session (tenant) and one model."""
    tool_schemas = REGISTRY.tool_schemas()

    def plan_node(state: FleetState) -> dict[str, Any]:
        question = state["question"]
        plan_text = ""
        try:
            resp = model.invoke(
                [SystemMessage(build_planner_prompt(ctx.company_id)), HumanMessage(question)]
            )
            plan_text = (resp.content if isinstance(resp.content, str) else str(resp.content)).strip()
        except Exception:  # planning is best-effort
            plan_text = ""
        messages: list[Any] = [
            SystemMessage(build_agent_prompt(ctx.company_id, plan_text)),
            HumanMessage(question),
        ]
        return {
            "plan": plan_text,
            "messages": messages,
            "evidence": [],
            "refusals": [],
            "action_results": [],
            "pending_approval": None,
            "round": 0,
        }

    def agent_node(state: FleetState) -> dict[str, Any]:
        bound = model.bind_tools(tool_schemas)
        resp = bound.invoke(state["messages"])
        return {"messages": [*state["messages"], resp], "round": state.get("round", 0) + 1}

    def tools_node(state: FleetState) -> dict[str, Any]:
        messages = list(state["messages"])
        evidence = list(state.get("evidence", []))
        refusals = list(state.get("refusals", []))
        pending: dict[str, Any] | None = None
        ai = _last_ai(messages)
        assert ai is not None
        for call in ai.tool_calls:
            name = call["name"]
            args = call.get("args", {}) or {}
            call_id = call.get("id", name)
            spec = REGISTRY.all_specs().get(name)
            if spec is None:
                messages.append(ToolMessage(content=f"Unknown tool {name!r}.", tool_call_id=call_id))
                continue
            try:
                params = spec.input_model(**args)
            except ValidationError as exc:
                messages.append(
                    ToolMessage(content=f"Invalid arguments: {exc.errors()}", tool_call_id=call_id)
                )
                continue

            if isinstance(spec, ReadSpec):
                result = spec.handler(ctx, params)
                evidence.extend(e.model_dump() for e in result.evidence)
                audit.record(ctx, tool=name, params=args, decision=audit.READ,
                             result={"summary": result.summary})
                payload = {"summary": result.summary, "data": result.data}
                messages.append(ToolMessage(content=json.dumps(payload, default=str)[:6000],
                                            tool_call_id=call_id))
            elif isinstance(spec, ActionSpec):
                if pending is not None:
                    # one action held per round; defer the rest (but still answer the call)
                    messages.append(ToolMessage(
                        content=json.dumps({"deferred": True,
                                            "reason": "another action is awaiting approval"}),
                        tool_call_id=call_id))
                    continue
                outcome = spec.propose(ctx, params)
                if isinstance(outcome, ProposedAction):
                    pending = {**outcome.model_dump(), "call_id": call_id}
                    evidence.extend(e.model_dump() for e in outcome.supporting_evidence)
                    audit.record(ctx, tool=name, params=args, decision=audit.PROPOSED,
                                 result={"preview": outcome.preview})
                    messages.append(ToolMessage(
                        content=json.dumps({
                            "status": "proposed — awaiting explicit human approval",
                            "preview": outcome.preview,
                            "requires_approval": True,
                        }), tool_call_id=call_id))
                else:  # refused by a guardrail
                    reason = "; ".join(outcome.reasons)
                    refusals.append(reason)
                    audit.record(ctx, tool=name, params=args, decision=audit.REFUSED,
                                 result={"reasons": outcome.reasons})
                    messages.append(ToolMessage(
                        content=json.dumps({"refused": True, "reasons": outcome.reasons}),
                        tool_call_id=call_id))
        return {"messages": messages, "evidence": evidence, "refusals": refusals,
                "pending_approval": pending}

    def approval_gate_node(state: FleetState) -> dict[str, Any]:
        proposal = state["pending_approval"]
        assert proposal is not None
        # interrupt() pauses here; on resume `decision` is the resume value.
        decision = interrupt({
            "type": "approval_request",
            "tool": proposal["tool"],
            "params": proposal["params"],
            "risk": proposal["risk"],
            "preview": proposal["preview"],
        })
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        messages = list(state["messages"])
        results = list(state.get("action_results", []))
        spec = REGISTRY.actions[proposal["tool"]]
        params = spec.input_model(**proposal["params"])
        if approved:
            audit.record(ctx, tool=proposal["tool"], params=proposal["params"], decision=audit.APPROVED)
            result = spec.executor(ctx, params)
            audit.record(ctx, tool=proposal["tool"], params=proposal["params"],
                         decision=audit.EXECUTED, result=result)
            results.append(result)
            messages.append(SystemMessage(
                f"The proposed action '{proposal['tool']}' was APPROVED by the human and executed. "
                f"Result: {json.dumps(result, default=str)}. Summarise this for the admin."))
        else:
            audit.record(ctx, tool=proposal["tool"], params=proposal["params"], decision=audit.DENIED)
            messages.append(SystemMessage(
                f"The proposed action '{proposal['tool']}' was DENIED by the human. "
                "Do not perform it; acknowledge and stop."))
        return {"messages": messages, "action_results": results, "pending_approval": None}

    def verify_node(state: FleetState) -> dict[str, Any]:
        ai = _last_ai(state["messages"])
        answer = (ai.content if ai and isinstance(ai.content, str) else "") or ""
        from .grounding import EvidenceRow
        ev = [EvidenceRow(**e) for e in state.get("evidence", [])]
        unsupported = unsupported_device_ids(answer, ev)
        return {"unsupported_ids": unsupported}

    def respond_node(state: FleetState) -> dict[str, Any]:
        ai = _last_ai(state["messages"])
        answer = (ai.content if ai and isinstance(ai.content, str) else "") or ""
        return {"answer": answer, "verified": not state.get("unsupported_ids")}

    def route_after_agent(state: FleetState) -> str:
        ai = _last_ai(state["messages"])
        if ai is not None and ai.tool_calls and state.get("round", 0) <= ctx.settings.max_tool_rounds:
            return "tools"
        return "verify"

    def route_after_tools(state: FleetState) -> str:
        return "approval_gate" if state.get("pending_approval") else "agent"

    def route_after_verify(state: FleetState) -> str:
        if state.get("unsupported_ids") and not state.get("verified_once"):
            return "correct"
        return "respond"

    def correct_node(state: FleetState) -> dict[str, Any]:
        bad = ", ".join(state.get("unsupported_ids", []))
        messages = [*state["messages"], SystemMessage(
            f"GROUNDING ERROR: these device ids are not supported by any evidence gathered this "
            f"turn: {bad}. Remove them or gather evidence. Re-answer using only cited devices.")]
        return {"messages": messages, "verified_once": True}

    g: StateGraph = StateGraph(FleetState)
    g.add_node("plan", plan_node)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("approval_gate", approval_gate_node)
    g.add_node("verify", verify_node)
    g.add_node("correct", correct_node)
    g.add_node("respond", respond_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")
    g.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "verify": "verify"})
    g.add_conditional_edges("tools", route_after_tools,
                            {"agent": "agent", "approval_gate": "approval_gate"})
    g.add_edge("approval_gate", "agent")
    g.add_conditional_edges("verify", route_after_verify, {"correct": "correct", "respond": "respond"})
    g.add_edge("correct", "agent")
    g.add_edge("respond", END)

    return g.compile(checkpointer=MemorySaver())
