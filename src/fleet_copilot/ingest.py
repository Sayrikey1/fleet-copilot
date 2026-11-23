"""Load the NDJSON telemetry into a flat, indexed, tenant-stamped SQLite db (idempotent)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .data import queries as q


def _os_major(version: str | None) -> int | None:
    if not version:
        return None
    head = version.split(".", 1)[0].strip()
    return int(head) if head.isdigit() else None


def _root_volume(volumes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not volumes:
        return None
    for v in volumes:
        if v.get("mount_point") == "/":
            return v
    return volumes[0]


def _flatten(record: dict[str, Any]) -> dict[str, Any]:
    os_ = record.get("os") or {}
    ident = record.get("device_identity") or {}
    mem = record.get("memory") or {}
    battery = record.get("battery")  # may be absent
    volumes = record.get("disk_volumes") or []
    root = _root_volume(volumes)

    total_mem = mem.get("total_memory_bytes") or mem.get("ram_bytes")
    used_mem = mem.get("used_memory_bytes")
    ram_used_pct = (100.0 * used_mem / total_mem) if (total_mem and used_mem is not None) else None

    disk_size = root.get("size_bytes") if root else None
    disk_avail = root.get("available_bytes") if root else None
    disk_free_pct = (100.0 * disk_avail / disk_size) if (disk_size and disk_avail is not None) else None

    os_version = os_.get("product_version")

    return {
        "device_id": record["device_id"],
        "company_id": record["company_id"],
        "employee_id": record.get("employee_id"),
        "collected_at": record["collected_at"],
        "agent_version": record.get("agent_version"),
        "platform": os_.get("platform"),
        "product_name": os_.get("product_name"),
        "os_version": os_version,
        "os_major": _os_major(os_version),
        "model_name": ident.get("model_name"),
        "processor": ident.get("processor"),
        "total_memory_bytes": total_mem,
        "used_memory_bytes": used_mem,
        "ram_used_pct": ram_used_pct,
        "disk_size_bytes": disk_size,
        "disk_available_bytes": disk_avail,
        "disk_free_pct": disk_free_pct,
        "battery_present": None if battery is None else int(bool(battery.get("battery_present", True))),
        "battery_pct": battery.get("percentage") if battery else None,
        "battery_cycle_count": battery.get("cycle_count") if battery else None,
        "battery_condition": battery.get("condition") if battery else None,
        "battery_full_charge": battery.get("full_charge_capacity") if battery else None,
        "has_network": 1 if record.get("network") else 0,
        "raw_json": json.dumps(record, separators=(",", ":")),
    }


def ingest(dataset_path: Path, db_path: Path) -> dict[str, int]:
    """(Re)build the db from the NDJSON file; returns row counts."""
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(q.SCHEMA)
        snap_rows = 0
        comp_rows = 0
        sw_rows = 0

        with open(dataset_path, encoding="utf-8") as fh:
            for snapshot_id, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                flat = _flatten(record)
                flat["snapshot_id"] = snapshot_id
                cols = ", ".join(flat.keys())
                placeholders = ", ".join(f":{k}" for k in flat)
                conn.execute(f"INSERT INTO snapshots ({cols}) VALUES ({placeholders})", flat)
                snap_rows += 1

                stamp = (snapshot_id, record["device_id"], record["company_id"], record["collected_at"])
                for c in record.get("compliance_results") or []:
                    conn.execute(q.INSERT_COMPLIANCE, (*stamp, c["check_id"], c["status"], c["severity"]))
                    comp_rows += 1
                for s in record.get("installed_software") or []:
                    conn.execute(
                        q.INSERT_SOFTWARE,
                        (*stamp, s.get("name"), s.get("version"), s.get("publisher")),
                    )
                    sw_rows += 1
        conn.commit()
        return {"snapshots": snap_rows, "compliance_results": comp_rows, "installed_software": sw_rows}
    finally:
        conn.close()
