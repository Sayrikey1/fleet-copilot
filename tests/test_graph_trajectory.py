"""Tier-2 eval: the graph calls the right tools and grounds its answer.

Drives the real LangGraph graph with a scripted model, so we assert the *trajectory*
(which tool ran) and grounding — not fuzzy prose.
"""

from __future__ import annotations

from fixtures.fake_llm import ScriptedModel, ai_text, ai_tool_call
from fleet_copilot import audit
from fleet_copilot.runner import run_turn


def _audited_tools(ctx, decision: str | None = None) -> list[str]:
    return [
        r["tool"]
        for r in ctx.db.audit_entries(ctx.company_id)
        if decision is None or r["decision"] == decision
    ]


def test_disk_question_calls_disk_tool_and_grounds(make_ctx) -> None:
    ctx = make_ctx("acme-001")
    model = ScriptedModel([
        ai_text("Plan: call find_devices_by_disk, then answer."),
        ai_tool_call("find_devices_by_disk", {}),
        ai_text("Two Acme devices are low on disk: [8NM23J95R5I6 @ t] and [MT7PJB7N5LRE @ t]."),
    ])
    state = run_turn(ctx, model, "Which devices are low on disk?")

    assert "find_devices_by_disk" in _audited_tools(ctx, audit.READ)
    assert state["evidence"], "expected grounding evidence to be gathered"
    assert "low on disk" in state["answer"]
    # the scripted model was given the tool schemas
    assert model.bound_tools and any(t["name"] == "find_devices_by_disk" for t in model.bound_tools)


def test_compliance_question_calls_compliance_tool(make_ctx) -> None:
    ctx = make_ctx("acme-001")
    model = ScriptedModel([
        ai_text("Plan: check high-severity compliance."),
        ai_tool_call("get_compliance", {"severity": "high", "status": "fail"}),
        ai_text("No devices are failing high-severity checks."),
    ])
    state = run_turn(ctx, model, "Any high-severity compliance failures?")
    assert "get_compliance" in _audited_tools(ctx, audit.READ)
    # correct grounded conclusion: nothing matched, so no device ids are cited
    assert state["evidence"] == []
    assert "No devices" in state["answer"]


def test_pure_read_turn_has_no_pending_action(make_ctx) -> None:
    ctx = make_ctx("globex-002")
    model = ScriptedModel([
        ai_text("Plan."),
        ai_tool_call("find_devices_by_memory", {}),
        ai_text("One device is RAM constrained."),
    ])
    state = run_turn(ctx, model, "Which devices are constrained by RAM?")
    assert not state.get("pending_approval")
    assert state.get("action_results", []) == []
