"""The four state-changing action tools. Each is held for human approval, and each
validator enforces tenant membership + sufficient evidence before an action can be
proposed. Executors mint a stable id (a real deployment would call the backing systems)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from ..context import SessionContext
from ..grounding import ActionCheck, EvidenceRow
from ..registry import action_tool


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:10].upper()
    return f"{prefix}-{digest}"


def _ev(rows: list[dict[str, Any]], source: str, keep: tuple[str, ...]) -> list[EvidenceRow]:
    return [
        EvidenceRow(
            device_id=r["device_id"],
            snapshot_id=r.get("snapshot_id"),
            collected_at=r.get("collected_at"),
            source_tool=source,
            detail={k: r[k] for k in keep if k in r},
        )
        for r in rows
    ]


# create_upgrade_order
class UpgradeOrderParams(BaseModel):
    device_id: str = Field(description="Device to upgrade.")
    component: str = Field(description="One of 'disk', 'memory', 'battery'.")
    spec: str = Field(description="Requested spec, e.g. '1TB SSD' or '32GB RAM'.")


def _validate_upgrade(ctx: SessionContext, p: UpgradeOrderParams) -> ActionCheck:
    if not ctx.db.snapshots.device_in_tenant(ctx.company_id, p.device_id):
        return ActionCheck(allowed=False, reasons=[f"Device {p.device_id} is not in {ctx.company_id}."])
    component = p.component.lower().strip()
    if component in {"disk", "storage", "ssd"}:
        rows = [r for r in ctx.db.snapshots.by_disk(ctx.company_id, ctx.settings.disk_low_free_pct, "mean")
                if r["device_id"] == p.device_id]
        ev = _ev(rows, "find_devices_by_disk", ("value", "min_free_pct"))
    elif component in {"memory", "ram"}:
        rows = [r for r in ctx.db.snapshots.by_memory(ctx.company_id, ctx.settings.ram_high_used_pct)
                if r["device_id"] == p.device_id]
        ev = _ev(rows, "find_devices_by_memory", ("value", "max_used_pct"))
    elif component in {"battery"}:
        rows = [r for r in ctx.db.snapshots.by_battery(ctx.company_id, ctx.settings.battery_eol_cycles,
                ctx.settings.battery_service_conditions) if r["device_id"] == p.device_id]
        ev = _ev(rows, "find_devices_by_battery", ("max_cycle_count", "battery_condition"))
    else:
        return ActionCheck(allowed=False, reasons=[f"Unknown component {p.component!r}."])
    if not ev:
        return ActionCheck(
            allowed=False,
            reasons=[f"No telemetry shows {p.device_id} is constrained on {component}; "
                     "refusing to order an upgrade without evidence."],
        )
    return ActionCheck(
        allowed=True,
        preview=f"Order {p.component} upgrade ({p.spec}) for {p.device_id} in {ctx.company_id}.",
        supporting_evidence=ev,
    )


@action_tool(
    name="create_upgrade_order",
    description="Create a hardware upgrade order for a device (disk/memory/battery). Requires "
    "evidence the device is constrained on that component.",
    input_model=UpgradeOrderParams,
    validator=_validate_upgrade,
    risk="normal",
)
def create_upgrade_order(ctx: SessionContext, p: UpgradeOrderParams) -> dict[str, Any]:
    order_id = _stable_id("UPG", {"d": p.device_id, "c": p.component, "s": p.spec, "co": ctx.company_id})
    return {"order_id": order_id, "device_id": p.device_id, "component": p.component, "spec": p.spec,
            "status": "created"}


# open_remediation_ticket
class RemediationTicketParams(BaseModel):
    device_id: str = Field(description="Device with the failing check.")
    check_id: str = Field(description="Compliance check, e.g. 'os_up_to_date' or 'screen_lock'.")
    note: str = Field(default="", description="Optional note for the ticket.")


def _validate_remediation(ctx: SessionContext, p: RemediationTicketParams) -> ActionCheck:
    if not ctx.db.snapshots.device_in_tenant(ctx.company_id, p.device_id):
        return ActionCheck(allowed=False, reasons=[f"Device {p.device_id} is not in {ctx.company_id}."])
    rows = [r for r in ctx.db.compliance.query(ctx.company_id, check_id=p.check_id, status="fail")
            if r["device_id"] == p.device_id]
    if not rows:
        return ActionCheck(
            allowed=False,
            reasons=[f"{p.device_id} is not currently failing '{p.check_id}'; nothing to remediate."],
        )
    return ActionCheck(
        allowed=True,
        preview=f"Open remediation ticket for {p.device_id} on '{p.check_id}' in {ctx.company_id}.",
        supporting_evidence=_ev(rows, "get_compliance", ("check_id", "status", "severity")),
    )


@action_tool(
    name="open_remediation_ticket",
    description="Open a remediation ticket for a device failing a compliance check. Requires the "
    "device to actually be failing that check.",
    input_model=RemediationTicketParams,
    validator=_validate_remediation,
    risk="normal",
)
def open_remediation_ticket(ctx: SessionContext, p: RemediationTicketParams) -> dict[str, Any]:
    ticket_id = _stable_id("TIC", {"d": p.device_id, "c": p.check_id, "co": ctx.company_id})
    return {"ticket_id": ticket_id, "device_id": p.device_id, "check_id": p.check_id, "status": "open"}


# flag_device_for_replacement
class ReplacementParams(BaseModel):
    device_id: str = Field(description="Device to flag for replacement.")
    reason: str = Field(description="Why the device should be replaced.")


def _validate_replacement(ctx: SessionContext, p: ReplacementParams) -> ActionCheck:
    if not ctx.db.snapshots.device_in_tenant(ctx.company_id, p.device_id):
        return ActionCheck(allowed=False, reasons=[f"Device {p.device_id} is not in {ctx.company_id}."])
    evidence: list[EvidenceRow] = []
    batt = [r for r in ctx.db.snapshots.by_battery(ctx.company_id, ctx.settings.battery_eol_cycles,
            ctx.settings.battery_service_conditions) if r["device_id"] == p.device_id]
    evidence += _ev(batt, "find_devices_by_battery", ("max_cycle_count", "battery_condition"))
    disk = [r for r in ctx.db.snapshots.by_disk(ctx.company_id, ctx.settings.disk_low_free_pct, "mean")
            if r["device_id"] == p.device_id]
    evidence += _ev(disk, "find_devices_by_disk", ("value", "min_free_pct"))
    ram = [r for r in ctx.db.snapshots.by_memory(ctx.company_id, ctx.settings.ram_high_used_pct)
           if r["device_id"] == p.device_id]
    evidence += _ev(ram, "find_devices_by_memory", ("value", "max_used_pct"))
    if not evidence:
        return ActionCheck(
            allowed=False,
            reasons=[f"No end-of-life signal (battery, disk or RAM) for {p.device_id}; "
                     "refusing to flag a healthy device for replacement."],
        )
    return ActionCheck(
        allowed=True,
        preview=f"Flag {p.device_id} for replacement in {ctx.company_id} ({len(evidence)} signal(s)).",
        supporting_evidence=evidence,
    )


@action_tool(
    name="flag_device_for_replacement",
    description="Flag a device for hardware replacement. Requires at least one end-of-life signal "
    "(battery near EOL, sustained low disk, or chronic RAM pressure).",
    input_model=ReplacementParams,
    validator=_validate_replacement,
    risk="danger",
)
def flag_device_for_replacement(ctx: SessionContext, p: ReplacementParams) -> dict[str, Any]:
    flag_id = _stable_id("RPL", {"d": p.device_id, "co": ctx.company_id})
    return {"flag_id": flag_id, "device_id": p.device_id, "reason": p.reason, "status": "flagged"}


# notify_employee
class NotifyParams(BaseModel):
    employee_id: str = Field(description="Employee to notify (must belong to this company).")
    message: str = Field(description="The message to send.")


def _validate_notify(ctx: SessionContext, p: NotifyParams) -> ActionCheck:
    if not ctx.db.snapshots.employee_in_tenant(ctx.company_id, p.employee_id):
        return ActionCheck(
            allowed=False, reasons=[f"Employee {p.employee_id} is not in {ctx.company_id}."]
        )
    if not p.message.strip():
        return ActionCheck(allowed=False, reasons=["Refusing to send an empty message."])
    return ActionCheck(
        allowed=True,
        preview=f"Notify {p.employee_id} in {ctx.company_id}: {p.message!r}",
    )


@action_tool(
    name="notify_employee",
    description="Send a notification to an employee in this company (e.g. to prompt a reboot or an "
    "OS update). Requires the employee to belong to the tenant.",
    input_model=NotifyParams,
    validator=_validate_notify,
    risk="normal",
)
def notify_employee(ctx: SessionContext, p: NotifyParams) -> dict[str, Any]:
    msg_id = _stable_id("MSG", {"e": p.employee_id, "m": p.message, "co": ctx.company_id})
    return {"message_id": msg_id, "employee_id": p.employee_id, "status": "sent"}
