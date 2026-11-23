"""Repositories: tenant-scoped data access. Each runs SQL from queries.py against the
connection — no SQL text lives here. Every method requires company_id, so a row can
never be read without naming a tenant."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from . import queries as q


def rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r, strict=True)) for r in cursor.fetchall()]


@dataclass
class SnapshotRepository:
    conn: sqlite3.Connection

    def companies(self) -> list[str]:
        return [r[0] for r in self.conn.execute(q.COMPANIES).fetchall()]

    def device_ids(self, company_id: str) -> list[str]:
        return [r[0] for r in self.conn.execute(q.DEVICE_IDS, (company_id,)).fetchall()]

    def device_in_tenant(self, company_id: str, device_id: str) -> bool:
        return self.conn.execute(q.DEVICE_IN_TENANT, (company_id, device_id)).fetchone() is not None

    def employee_in_tenant(self, company_id: str, employee_id: str) -> bool:
        return self.conn.execute(q.EMPLOYEE_IN_TENANT, (company_id, employee_id)).fetchone() is not None

    def fleet_summary(self, company_id: str) -> dict[str, Any]:
        devices = rows(self.conn.execute(q.FLEET_SUMMARY, {"cid": company_id}))
        return {"company_id": company_id, "device_count": len(devices), "devices": devices}

    def by_disk(self, company_id: str, max_free_pct: float, mode: str = "mean") -> list[dict[str, Any]]:
        """Root-volume free% at/below max_free_pct. mode: mean | latest | sustained(max)."""
        if mode == "latest":
            cur = self.conn.execute(q.DISK_LATEST, {"cid": company_id, "thr": max_free_pct})
            return rows(cur)
        agg = "MAX(disk_free_pct)" if mode == "sustained" else "AVG(disk_free_pct)"
        cur = self.conn.execute(q.DISK_AGG.format(agg=agg), {"cid": company_id, "thr": max_free_pct})
        return rows(cur)

    def by_memory(self, company_id: str, min_used_pct: float) -> list[dict[str, Any]]:
        """Devices whose mean RAM-used% is at/above min_used_pct."""
        return rows(self.conn.execute(q.MEMORY, {"cid": company_id, "thr": min_used_pct}))

    def by_battery(
        self, company_id: str, min_cycle_count: int | None = None, conditions: tuple[str, ...] = ()
    ) -> list[dict[str, Any]]:
        """Qualifies if peak cycle_count >= min_cycle_count OR condition in conditions.
        Battery-less devices are excluded (no non-null rows)."""
        clauses: list[str] = []
        params: dict[str, Any] = {"cid": company_id}
        if min_cycle_count is not None:
            clauses.append("MAX(battery_cycle_count) >= :mincyc")
            params["mincyc"] = min_cycle_count
        if conditions:
            placeholders = ",".join(f":c{i}" for i in range(len(conditions)))
            clauses.append(q.BATTERY_CONDITION_CLAUSE.format(placeholders=placeholders))
            for i, c in enumerate(conditions):
                params[f"c{i}"] = c
        having = " OR ".join(clauses) if clauses else "1=1"
        return rows(self.conn.execute(q.BATTERY.format(having=having), params))

    def by_os(self, company_id: str, product_name: str, older_than_major: int) -> list[dict[str, Any]]:
        """OS-family-scoped version floor (product_name required, so families never cross-compare)."""
        return rows(self.conn.execute(
            q.OS_OLDER_THAN, {"cid": company_id, "pn": product_name, "floor": older_than_major}
        ))

    def history(self, company_id: str, device_id: str, metric: str) -> list[dict[str, Any]]:
        column = q.HISTORY_METRICS.get(metric)
        if column is None:
            raise ValueError(f"unknown metric {metric!r}; choose from {sorted(q.HISTORY_METRICS)}")
        return rows(self.conn.execute(q.DEVICE_HISTORY.format(column=column), (company_id, device_id)))


@dataclass
class ComplianceRepository:
    conn: sqlite3.Connection

    def query(
        self,
        company_id: str,
        check_id: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        latest_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Compliance rows (latest per device by default), filtered by check/severity/status."""
        where = ["c.company_id = :cid"]
        params: dict[str, Any] = {"cid": company_id}
        if check_id:
            where.append("c.check_id = :chk")
            params["chk"] = check_id
        if severity:
            where.append("c.severity = :sev")
            params["sev"] = severity
        if status:
            where.append("c.status = :st")
            params["st"] = status
        latest_join = q.COMPLIANCE_LATEST_JOIN if latest_only else ""
        sql = q.COMPLIANCE.format(latest_join=latest_join, where=" AND ".join(where))
        return rows(self.conn.execute(sql, params))

    def drift(
        self, company_id: str, check_id: str, direction: str = "pass_to_fail"
    ) -> list[dict[str, Any]]:
        """Devices whose check_id status flipped over the window, with the first-flip timestamp."""
        devices = [r[0] for r in self.conn.execute(q.DEVICE_IDS, (company_id,)).fetchall()]
        out: list[dict[str, Any]] = []
        want_to = "fail" if direction == "pass_to_fail" else "pass"
        want_from = "pass" if direction == "pass_to_fail" else "fail"
        for device_id in devices:
            seq = rows(self.conn.execute(q.COMPLIANCE_SEQUENCE, (company_id, device_id, check_id)))
            statuses = [r["status"] for r in seq]
            if "pass" not in statuses or "fail" not in statuses:
                continue
            first = next(
                (
                    seq[i]
                    for i in range(1, len(seq))
                    if seq[i]["status"] == want_to
                    and any(x["status"] == want_from for x in seq[:i])
                ),
                None,
            )
            if first:
                out.append({
                    "device_id": device_id,
                    "check_id": check_id,
                    "first_flip_at": first["collected_at"],
                    "collected_at": first["collected_at"],
                    "snapshot_id": first["snapshot_id"],
                    "from_status": want_from,
                    "to_status": want_to,
                })
        return out


@dataclass
class SoftwareRepository:
    conn: sqlite3.Connection

    def query(
        self, company_id: str, name: str | None = None, device_id: str | None = None
    ) -> list[dict[str, Any]]:
        where = ["sw.company_id = :cid"]
        params: dict[str, Any] = {"cid": company_id}
        if name:
            where.append("sw.name LIKE :nm")
            params["nm"] = f"%{name}%"
        if device_id:
            where.append("sw.device_id = :did")
            params["did"] = device_id
        return rows(self.conn.execute(q.SOFTWARE.format(where=" AND ".join(where)), params))


@dataclass
class AuditRepository:
    conn: sqlite3.Connection

    def append(
        self,
        *,
        ts: str,
        trace_id: str | None,
        company_id: str,
        actor: str,
        tool: str,
        params_json: str,
        decision: str,
        result_json: str | None,
    ) -> int:
        cur = self.conn.execute(
            q.INSERT_AUDIT,
            (ts, trace_id, company_id, actor, tool, params_json, decision, result_json),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def entries(self, company_id: str) -> list[dict[str, Any]]:
        return rows(self.conn.execute(q.AUDIT_ENTRIES, (company_id,)))
