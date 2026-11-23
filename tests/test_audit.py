"""Audit eval: every tool interaction is recorded, append-only and tenant-scoped."""

from __future__ import annotations

from fixtures.fake_llm import ScriptedModel, ai_text, ai_tool_call
from fleet_copilot import audit
from fleet_copilot.runner import run_turn


def test_reads_are_audited(make_ctx) -> None:
    ctx = make_ctx("acme-001")
    model = ScriptedModel([
        ai_text("Plan."),
        ai_tool_call("find_devices_by_disk", {}),
        ai_text("Answer."),
    ])
    run_turn(ctx, model, "low disk?")
    entries = ctx.db.audit_entries("acme-001")
    assert any(e["decision"] == audit.READ and e["tool"] == "find_devices_by_disk" for e in entries)
    # every row is stamped with the correct tenant
    assert all(e["company_id"] == "acme-001" for e in entries)


def test_audit_is_append_only_and_grows(make_ctx) -> None:
    ctx = make_ctx("acme-001")
    before = len(ctx.db.audit_entries("acme-001"))
    model = ScriptedModel([ai_text("Plan."), ai_tool_call("get_fleet_summary", {}), ai_text("ok")])
    run_turn(ctx, model, "summary?")
    after = len(ctx.db.audit_entries("acme-001"))
    assert after > before


def test_audit_is_tenant_scoped(make_ctx) -> None:
    acme = make_ctx("acme-001")
    model = ScriptedModel([ai_text("Plan."), ai_tool_call("get_fleet_summary", {}), ai_text("ok")])
    run_turn(acme, model, "summary?")
    # a different tenant's audit view does not see Acme's entries
    initech = make_ctx("initech-003")
    assert initech.db.audit_entries("initech-003") == []


def test_full_action_lifecycle_is_audited(make_ctx) -> None:
    ctx = make_ctx("acme-001")
    model = ScriptedModel([
        ai_text("Plan."),
        ai_tool_call("flag_device_for_replacement",
                     {"device_id": "8NM23J95R5I6", "reason": "battery EOL"}),
        ai_text("Flagged."),
    ])
    run_turn(ctx, model, "flag it", approver=lambda req: True)
    decisions = [e["decision"] for e in ctx.db.audit_entries("acme-001")]
    # proposed -> approved -> executed, in order
    assert decisions.count(audit.PROPOSED) == 1
    assert decisions.index(audit.PROPOSED) < decisions.index(audit.APPROVED) < decisions.index(audit.EXECUTED)
