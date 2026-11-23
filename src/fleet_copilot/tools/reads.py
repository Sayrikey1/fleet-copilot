"""Read (grounding) tools. Each is tenant-scoped via ``ctx`` and returns evidence.

Parameters never include ``company_id`` — the tenant is injected from the session.
Every returned :class:`EvidenceRow` carries a snapshot id + timestamp so the agent
can cite the source of each claim.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..context import SessionContext
from ..db import HISTORY_METRICS
from ..grounding import EvidenceRow, ReadResult
from ..registry import read_tool


def _evidence(rows: list[dict[str, Any]], source: str, keep: tuple[str, ...]) -> list[EvidenceRow]:
    out: list[EvidenceRow] = []
    for r in rows:
        out.append(
            EvidenceRow(
                device_id=r["device_id"],
                snapshot_id=r.get("snapshot_id"),
                collected_at=r.get("collected_at"),
                source_tool=source,
                detail={k: r[k] for k in keep if k in r},
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Fleet overview
# --------------------------------------------------------------------------- #
class EmptyParams(BaseModel):
    pass


@read_tool(
    name="get_fleet_summary",
    description="Overview of the fleet for the current company: device count and the latest-known "
    "disk, memory, OS and battery state per device.",
    input_model=EmptyParams,
)
def get_fleet_summary(ctx: SessionContext, params: EmptyParams) -> ReadResult:
    summary = ctx.db.fleet_summary(ctx.company_id)
    ev = _evidence(
        summary["devices"],
        "get_fleet_summary",
        ("disk_free_pct", "ram_used_pct", "os_version", "battery_cycle_count", "battery_condition"),
    )
    return ReadResult(
        summary=f"{summary['device_count']} devices in {ctx.company_id}.",
        evidence=ev,
        data=summary,
    )


# --------------------------------------------------------------------------- #
# Disk
# --------------------------------------------------------------------------- #
class DiskParams(BaseModel):
    max_free_pct: float | None = Field(
        default=None, description="Free-space %% at or below which a device counts as low. "
        "Defaults to the configured threshold.")
    mode: Literal["mean", "latest", "sustained"] = Field(
        default="mean",
        description="'mean' (avg over the window, default), 'latest' (most recent snapshot), "
        "or 'sustained' (every snapshot below threshold).",
    )


@read_tool(
    name="find_devices_by_disk",
    description="Find devices low on disk space (root volume). Use for 'low on disk / running out "
    "of storage' questions.",
    input_model=DiskParams,
)
def find_devices_by_disk(ctx: SessionContext, params: DiskParams) -> ReadResult:
    thr = params.max_free_pct if params.max_free_pct is not None else ctx.settings.disk_low_free_pct
    rows = ctx.db.devices_by_disk(ctx.company_id, thr, params.mode)
    ev = _evidence(rows, "find_devices_by_disk", ("value", "min_free_pct", "disk_available_bytes"))
    return ReadResult(
        summary=f"{len(rows)} device(s) in {ctx.company_id} at/below {thr:.0f}% free disk ({params.mode}).",
        evidence=ev,
        data={"threshold_pct": thr, "mode": params.mode, "devices": rows},
    )


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
class MemoryParams(BaseModel):
    min_used_pct: float | None = Field(
        default=None, description="Mean RAM-used %% at or above which a device is 'constrained'. "
        "Defaults to the configured threshold.")


@read_tool(
    name="find_devices_by_memory",
    description="Find devices constrained by RAM (high mean memory utilisation over the window).",
    input_model=MemoryParams,
)
def find_devices_by_memory(ctx: SessionContext, params: MemoryParams) -> ReadResult:
    thr = params.min_used_pct if params.min_used_pct is not None else ctx.settings.ram_high_used_pct
    rows = ctx.db.devices_by_memory(ctx.company_id, thr)
    ev = _evidence(rows, "find_devices_by_memory", ("value", "max_used_pct"))
    return ReadResult(
        summary=f"{len(rows)} device(s) in {ctx.company_id} with mean RAM use >= {thr:.0f}%.",
        evidence=ev,
        data={"threshold_pct": thr, "devices": rows},
    )


# --------------------------------------------------------------------------- #
# Battery
# --------------------------------------------------------------------------- #
class BatteryParams(BaseModel):
    min_cycle_count: int | None = Field(
        default=None, description="Peak charge-cycle count at or above which a battery is near "
        "end-of-life. Defaults to the configured threshold.")
    include_service_conditions: bool = Field(
        default=True, description="Also include devices whose battery condition flags service "
        "(e.g. 'Service Recommended').")


@read_tool(
    name="find_devices_by_battery",
    description="Find devices whose batteries are approaching end-of-life (high cycle count or a "
    "service-recommended condition). Devices with no battery (e.g. desktops) are excluded.",
    input_model=BatteryParams,
)
def find_devices_by_battery(ctx: SessionContext, params: BatteryParams) -> ReadResult:
    cycles = params.min_cycle_count if params.min_cycle_count is not None else ctx.settings.battery_eol_cycles
    conds = ctx.settings.battery_service_conditions if params.include_service_conditions else ()
    rows = ctx.db.devices_by_battery(ctx.company_id, cycles, conds)
    ev = _evidence(rows, "find_devices_by_battery", ("max_cycle_count", "battery_condition"))
    return ReadResult(
        summary=f"{len(rows)} device(s) in {ctx.company_id} with battery near end-of-life "
        f"(>= {cycles} cycles or service-flagged).",
        evidence=ev,
        data={"min_cycle_count": cycles, "devices": rows},
    )


# --------------------------------------------------------------------------- #
# OS version
# --------------------------------------------------------------------------- #
class OsParams(BaseModel):
    product_name: str = Field(
        default="macOS", description="OS family, e.g. 'macOS' or 'Windows'. Required so the query "
        "never cross-compares families.")
    older_than_major: int = Field(description="Major version floor; devices below it are returned.")


@read_tool(
    name="find_devices_by_os",
    description="Find devices of a given OS family running a major version older than a floor "
    "(e.g. macOS older than 15). Does not cross-compare OS families.",
    input_model=OsParams,
)
def find_devices_by_os(ctx: SessionContext, params: OsParams) -> ReadResult:
    rows = ctx.db.devices_by_os(ctx.company_id, params.product_name, params.older_than_major)
    ev = _evidence(rows, "find_devices_by_os", ("product_name", "os_version", "value"))
    return ReadResult(
        summary=f"{len(rows)} {params.product_name} device(s) in {ctx.company_id} older than major "
        f"version {params.older_than_major}.",
        evidence=ev,
        data={
            "product_name": params.product_name,
            "older_than_major": params.older_than_major,
            "devices": rows,
        },
    )


# --------------------------------------------------------------------------- #
# Compliance
# --------------------------------------------------------------------------- #
class ComplianceParams(BaseModel):
    check_id: str | None = Field(
        default=None, description="e.g. 'disk_encryption', 'screen_lock', 'os_up_to_date'."
    )
    severity: str | None = Field(default=None, description="'high' | 'medium' | 'low'.")
    status: str | None = Field(default=None, description="'pass' | 'fail'.")


@read_tool(
    name="get_compliance",
    description="Query compliance checks for the fleet (latest snapshot per device). Filter by "
    "check_id, severity and/or status. Use for 'failing X check' / 'high-severity' questions.",
    input_model=ComplianceParams,
)
def get_compliance(ctx: SessionContext, params: ComplianceParams) -> ReadResult:
    rows = ctx.db.compliance(
        ctx.company_id, check_id=params.check_id, severity=params.severity, status=params.status
    )
    ev = _evidence(rows, "get_compliance", ("check_id", "status", "severity"))
    filt = ", ".join(
        f"{k}={v}" for k, v in
        [("check_id", params.check_id), ("severity", params.severity), ("status", params.status)]
        if v
    ) or "all checks"
    return ReadResult(
        summary=f"{len(rows)} device(s) in {ctx.company_id} match compliance filter ({filt}).",
        evidence=ev,
        data={"filter": filt, "devices": rows},
    )


class DriftParams(BaseModel):
    check_id: str = Field(default="screen_lock", description="Compliance check to track over time.")
    direction: Literal["pass_to_fail", "fail_to_pass"] = Field(
        default="pass_to_fail", description="'pass_to_fail' finds regressions (drift)."
    )


@read_tool(
    name="detect_compliance_drift",
    description="Detect devices whose compliance status for a check changed over the 30-day window "
    "(e.g. screen_lock that went from pass to fail). Returns the timestamp of the first flip.",
    input_model=DriftParams,
)
def detect_compliance_drift(ctx: SessionContext, params: DriftParams) -> ReadResult:
    rows = ctx.db.compliance_drift(ctx.company_id, params.check_id, params.direction)
    ev = _evidence(rows, "detect_compliance_drift", ("check_id", "first_flip_at", "from_status", "to_status"))
    return ReadResult(
        summary=f"{len(rows)} device(s) in {ctx.company_id} drifted {params.direction} on "
        f"'{params.check_id}'.",
        evidence=ev,
        data={"check_id": params.check_id, "direction": params.direction, "devices": rows},
    )


# --------------------------------------------------------------------------- #
# Device time-series + software
# --------------------------------------------------------------------------- #
class HistoryParams(BaseModel):
    device_id: str = Field(description="The device to trace.")
    metric: Literal["disk_free_pct", "ram_used_pct", "battery_cycle_count", "battery_pct"] = Field(
        description="Which metric to return over time."
    )


@read_tool(
    name="get_device_history",
    description="Return a single device's time-series for one metric over the window, for trend "
    "analysis.",
    input_model=HistoryParams,
)
def get_device_history(ctx: SessionContext, params: HistoryParams) -> ReadResult:
    if params.metric not in HISTORY_METRICS:
        return ReadResult(summary=f"Unknown metric {params.metric!r}.", evidence=[], data={})
    if not ctx.db.device_in_tenant(ctx.company_id, params.device_id):
        return ReadResult(
            summary=f"Device {params.device_id} is not in {ctx.company_id}.", evidence=[], data={}
        )
    rows = ctx.db.device_history(ctx.company_id, params.device_id, params.metric)
    ev = [
        EvidenceRow(
            device_id=params.device_id,
            snapshot_id=r["snapshot_id"],
            collected_at=r["collected_at"],
            source_tool="get_device_history",
            detail={params.metric: r["value"]},
        )
        for r in rows
    ]
    series = [{"collected_at": r["collected_at"], "value": r["value"]} for r in rows]
    return ReadResult(
        summary=f"{len(rows)} readings of {params.metric} for {params.device_id}.",
        evidence=ev[:1] + ev[-1:],  # cite first + last reading for trend claims
        data={"device_id": params.device_id, "metric": params.metric, "series": series},
    )


class SoftwareParams(BaseModel):
    name: str | None = Field(default=None, description="Substring match on software name.")
    device_id: str | None = Field(default=None, description="Restrict to one device.")


@read_tool(
    name="list_software",
    description="List installed software across the fleet, optionally filtered by name or device.",
    input_model=SoftwareParams,
)
def list_software(ctx: SessionContext, params: SoftwareParams) -> ReadResult:
    rows = ctx.db.software(ctx.company_id, name=params.name, device_id=params.device_id)
    ev = _evidence(rows, "list_software", ("name", "version", "publisher"))
    return ReadResult(
        summary=f"{len(rows)} software record(s) in {ctx.company_id}.",
        evidence=ev,
        data={"records": rows},
    )
