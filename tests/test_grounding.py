"""Grounding eval: hallucinated device ids are caught, and the graph self-corrects."""

from __future__ import annotations

from fixtures.fake_llm import ScriptedModel, ai_text, ai_tool_call
from fleet_copilot.grounding import EvidenceRow, unsupported_device_ids
from fleet_copilot.runner import run_turn


def test_unsupported_ids_detected() -> None:
    evidence = [EvidenceRow(device_id="8NM23J95R5I6", source_tool="t")]
    answer = "Low devices: 8NM23J95R5I6 and ZZZZZZZZZZZZ need attention."
    assert unsupported_device_ids(answer, evidence) == ["ZZZZZZZZZZZZ"]


def test_supported_ids_pass() -> None:
    evidence = [EvidenceRow(device_id="8NM23J95R5I6", source_tool="t")]
    assert unsupported_device_ids("Only 8NM23J95R5I6 is low.", evidence) == []


def test_graph_corrects_a_hallucinated_id(make_ctx) -> None:
    ctx = make_ctx("acme-001")
    model = ScriptedModel([
        ai_text("Plan: query disk."),
        ai_tool_call("find_devices_by_disk", {}),
        ai_text("Low: [8NM23J95R5I6 @ t] and [AAAAAAAAAAAA @ t]."),  # cites a non-evidence id
        ai_text("Low: [8NM23J95R5I6 @ t] and [MT7PJB7N5LRE @ t]."),  # corrected
    ])
    state = run_turn(ctx, model, "Which devices are low on disk?")
    assert "AAAAAAAAAAAA" not in state["answer"]
    assert state.get("verified") is True
    assert len(model.calls) == 4  # plan, tool, bad, corrected
