"""Tier-1 eval: read tools return the exact hand-verified device sets per tenant (no LLM)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleet_copilot import tools  # noqa: F401  (registers tools)
from fleet_copilot.registry import REGISTRY

GOLDEN = json.loads((Path(__file__).parent / "golden" / "golden_qa.json").read_text())


def _run(ctx, call: dict):
    spec = REGISTRY.all_specs()[call["tool"]]
    params = spec.input_model(**call["args"])
    return spec.handler(ctx, params)


@pytest.mark.parametrize("g", GOLDEN, ids=[g["id"] for g in GOLDEN])
def test_golden_answer(make_ctx, g: dict) -> None:
    ctx = make_ctx(g["company"])
    result = _run(ctx, g["call"])
    got = sorted({e.device_id for e in result.evidence})
    if "expect_devices" in g:
        assert got == sorted(g["expect_devices"]), f"{g['id']}: {got}"
    if "expect_count" in g:
        assert len(got) == g["expect_count"], f"{g['id']}: count {len(got)}"


def test_high_severity_failures_are_zero_everywhere(make_ctx) -> None:
    # the trap: the only high-severity check (disk_encryption) never fails
    for company in ("acme-001", "globex-002", "initech-003"):
        ctx = make_ctx(company)
        result = _run(ctx, {"tool": "get_compliance", "args": {"severity": "high", "status": "fail"}})
        assert result.evidence == []


def test_macos_floor_is_os_family_scoped(make_ctx) -> None:
    # Windows must never match an "older than macOS 15" query
    ctx = make_ctx("acme-001")
    result = _run(ctx, {"tool": "find_devices_by_os",
                        "args": {"product_name": "macOS", "older_than_major": 15}})
    for e in result.evidence:
        assert e.detail.get("product_name") == "macOS"


def test_battery_excludes_devices_without_battery(make_ctx) -> None:
    # battery-less devices must never appear in a battery query
    ctx = make_ctx("acme-001")
    result = _run(ctx, {"tool": "find_devices_by_battery", "args": {}})
    for e in result.evidence:
        assert e.detail.get("max_cycle_count") is not None
