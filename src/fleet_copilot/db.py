"""Tenant-scoped data access. Every query requires company_id (no way to read a row
without naming a tenant); result rows carry device_id + collected_at + snapshot_id."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HISTORY_METRICS: dict[str, str] = {
    "disk_free_pct": "disk_free_pct",
    "ram_used_pct": "ram_used_pct",
    "battery_cycle_count": "battery_cycle_count",
    "battery_pct": "battery_pct",
}

_LATEST_CTE = """
WITH latest AS (
    SELECT device_id, MAX(collected_at) AS mx
    FROM snapshots WHERE company_id = :cid GROUP BY device_id
)
"""


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r, strict=True)) for r in cursor.fetchall()]


@dataclass
class FleetDB:
    conn: sqlite3.Connection

    @classmethod
    def connect(cls, db_path: Path) -> FleetDB:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    def companies(self) -> list[str]:
        cur = self.conn.execute("SELECT DISTINCT company_id FROM snapshots ORDER BY company_id")
        return [r[0] for r in cur.fetchall()]

    def device_ids(self, company_id: str) -> list[str]:
        cur = self.conn.execute(
            "SELECT DISTINCT device_id FROM snapshots WHERE company_id = ? ORDER BY device_id",
            (company_id,),
        )
        return [r[0] for r in cur.fetchall()]

    def device_in_tenant(self, company_id: str, device_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM snapshots WHERE company_id = ? AND device_id = ? LIMIT 1",
            (company_id, device_id),
        )
        return cur.fetchone() is not None

    def employee_in_tenant(self, company_id: str, employee_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM snapshots WHERE company_id = ? AND employee_id = ? LIMIT 1",
            (company_id, employee_id),
        )
        return cur.fetchone() is not None

    def fleet_summary(self, company_id: str) -> dict[str, Any]:
        cur = self.conn.execute(
            _LATEST_CTE
            + """
            SELECT s.device_id, s.employee_id, s.product_name, s.os_version, s.model_name,
                   s.disk_free_pct, s.ram_used_pct, s.battery_cycle_count, s.battery_condition,
                   s.collected_at, s.snapshot_id
            FROM snapshots s JOIN latest l
              ON s.device_id = l.device_id AND s.collected_at = l.mx
            WHERE s.company_id = :cid
            ORDER BY s.device_id
            """,
            {"cid": company_id},
        )
        devices = _rows(cur)
        return {"company_id": company_id, "device_count": len(devices), "devices": devices}

    def devices_by_disk(
        self, company_id: str, max_free_pct: float, mode: str = "mean"
    ) -> list[dict[str, Any]]:
        """Root-volume free% at/below max_free_pct. mode: mean | latest | sustained(max)."""
        agg = {"mean": "AVG(disk_free_pct)", "sustained": "MAX(disk_free_pct)"}.get(mode)
        if mode == "latest":
            cur = self.conn.execute(
                _LATEST_CTE
                + """
                SELECT s.device_id, s.disk_free_pct AS value, s.disk_available_bytes,
                       s.collected_at, s.snapshot_id
                FROM snapshots s JOIN latest l
                  ON s.device_id = l.device_id AND s.collected_at = l.mx
                WHERE s.company_id = :cid AND s.disk_free_pct IS NOT NULL
                  AND s.disk_free_pct <= :thr
                ORDER BY s.disk_free_pct
                """,
                {"cid": company_id, "thr": max_free_pct},
            )
            return _rows(cur)
        cur = self.conn.execute(
            f"""
            SELECT device_id, {agg} AS value, MIN(disk_free_pct) AS min_free_pct,
                   MAX(collected_at) AS collected_at, MAX(snapshot_id) AS snapshot_id
            FROM snapshots
            WHERE company_id = :cid AND disk_free_pct IS NOT NULL
            GROUP BY device_id
            HAVING {agg} <= :thr
            ORDER BY value
            """,
            {"cid": company_id, "thr": max_free_pct},
        )
        return _rows(cur)

    def devices_by_memory(self, company_id: str, min_used_pct: float) -> list[dict[str, Any]]:
        """Devices whose mean RAM-used% is at/above min_used_pct."""
        cur = self.conn.execute(
            """
            SELECT device_id, AVG(ram_used_pct) AS value, MAX(ram_used_pct) AS max_used_pct,
                   MAX(collected_at) AS collected_at, MAX(snapshot_id) AS snapshot_id
            FROM snapshots
            WHERE company_id = :cid AND ram_used_pct IS NOT NULL
            GROUP BY device_id
            HAVING AVG(ram_used_pct) >= :thr
            ORDER BY value DESC
            """,
            {"cid": company_id, "thr": min_used_pct},
        )
        return _rows(cur)

    def devices_by_battery(
        self,
        company_id: str,
        min_cycle_count: int | None = None,
        conditions: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        """Qualifies if peak cycle_count >= min_cycle_count OR condition in conditions.
        Battery-less devices are excluded (no non-null rows)."""
        clauses = []
        params: dict[str, Any] = {"cid": company_id}
        if min_cycle_count is not None:
            clauses.append("MAX(battery_cycle_count) >= :mincyc")
            params["mincyc"] = min_cycle_count
        if conditions:
            placeholders = ",".join(f":c{i}" for i in range(len(conditions)))
            clauses.append(
                f"MAX(CASE WHEN battery_condition IN ({placeholders}) THEN 1 ELSE 0 END) = 1"
            )
            for i, c in enumerate(conditions):
                params[f"c{i}"] = c
        if not clauses:
            clauses.append("1=1")
        having = " OR ".join(clauses)
        cur = self.conn.execute(
            f"""
            SELECT device_id,
                   MAX(battery_cycle_count) AS max_cycle_count,
                   MAX(battery_condition) AS battery_condition,
                   MAX(collected_at) AS collected_at, MAX(snapshot_id) AS snapshot_id
            FROM snapshots
            WHERE company_id = :cid AND battery_cycle_count IS NOT NULL
            GROUP BY device_id
            HAVING {having}
            ORDER BY max_cycle_count DESC
            """,
            params,
        )
        return _rows(cur)

    def devices_by_os(
        self, company_id: str, product_name: str, older_than_major: int
    ) -> list[dict[str, Any]]:
        """OS-family-scoped version floor (product_name required, so families never cross-compare)."""
        cur = self.conn.execute(
            _LATEST_CTE
            + """
            SELECT s.device_id, s.product_name, s.os_version, s.os_major AS value,
                   s.collected_at, s.snapshot_id
            FROM snapshots s JOIN latest l
              ON s.device_id = l.device_id AND s.collected_at = l.mx
            WHERE s.company_id = :cid AND s.product_name = :pn
              AND s.os_major IS NOT NULL AND s.os_major < :floor
            ORDER BY s.os_major, s.device_id
            """,
            {"cid": company_id, "pn": product_name, "floor": older_than_major},
        )
        return _rows(cur)

    def compliance(
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
        latest_join = ""
        if latest_only:
            latest_join = (
                "JOIN (SELECT device_id, MAX(collected_at) AS mx FROM compliance_results "
                "WHERE company_id = :cid GROUP BY device_id) l "
                "ON c.device_id = l.device_id AND c.collected_at = l.mx"
            )
        cur = self.conn.execute(
            f"""
            SELECT DISTINCT c.device_id, c.check_id, c.status, c.severity,
                   c.collected_at, c.snapshot_id
            FROM compliance_results c {latest_join}
            WHERE {" AND ".join(where)}
            ORDER BY c.device_id, c.check_id
            """,
            params,
        )
        return _rows(cur)

    def compliance_drift(
        self, company_id: str, check_id: str, direction: str = "pass_to_fail"
    ) -> list[dict[str, Any]]:
        """Devices whose check_id status flipped over the window, with the first-flip timestamp."""
        rows: list[dict[str, Any]] = []
        for device_id in self.device_ids(company_id):
            cur = self.conn.execute(
                """
                SELECT collected_at, status, snapshot_id
                FROM compliance_results
                WHERE company_id = ? AND device_id = ? AND check_id = ?
                ORDER BY collected_at
                """,
                (company_id, device_id, check_id),
            )
            seq = _rows(cur)
            statuses = [r["status"] for r in seq]
            if "pass" not in statuses or "fail" not in statuses:
                continue
            if direction == "pass_to_fail":
                first = next(
                    (
                        seq[i]
                        for i in range(1, len(seq))
                        if seq[i]["status"] == "fail"
                        and any(x["status"] == "pass" for x in seq[:i])
                    ),
                    None,
                )
            else:
                first = next(
                    (
                        seq[i]
                        for i in range(1, len(seq))
                        if seq[i]["status"] == "pass"
                        and any(x["status"] == "fail" for x in seq[:i])
                    ),
                    None,
                )
            if first:
                rows.append(
                    {
                        "device_id": device_id,
                        "check_id": check_id,
                        "first_flip_at": first["collected_at"],
                        "collected_at": first["collected_at"],
                        "snapshot_id": first["snapshot_id"],
                        "from_status": "pass" if direction == "pass_to_fail" else "fail",
                        "to_status": "fail" if direction == "pass_to_fail" else "pass",
                    }
                )
        return rows

    def device_history(
        self, company_id: str, device_id: str, metric: str
    ) -> list[dict[str, Any]]:
        column = HISTORY_METRICS.get(metric)
        if column is None:
            raise ValueError(f"unknown metric {metric!r}; choose from {sorted(HISTORY_METRICS)}")
        cur = self.conn.execute(
            f"""
            SELECT collected_at, {column} AS value, snapshot_id
            FROM snapshots
            WHERE company_id = ? AND device_id = ? AND {column} IS NOT NULL
            ORDER BY collected_at
            """,
            (company_id, device_id),
        )
        return _rows(cur)

    def software(
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
        cur = self.conn.execute(
            f"""
            SELECT DISTINCT sw.device_id, sw.name, sw.version, sw.publisher,
                   sw.collected_at, sw.snapshot_id
            FROM installed_software sw
            WHERE {" AND ".join(where)}
            ORDER BY sw.device_id, sw.name
            """,
            params,
        )
        return _rows(cur)

    def append_audit(
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
            """
            INSERT INTO audit_log (ts, trace_id, company_id, actor, tool, params_json, decision, result_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, trace_id, company_id, actor, tool, params_json, decision, result_json),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def audit_entries(self, company_id: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM audit_log WHERE company_id = ? ORDER BY id", (company_id,)
        )
        return _rows(cur)
