"""The database connection (unit of work). Owns the sqlite connection and exposes the
repositories; callers reach data via db.snapshots / db.compliance / db.software / db.audit."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .repositories import (
    AuditRepository,
    ComplianceRepository,
    SnapshotRepository,
    SoftwareRepository,
)


@dataclass
class Database:
    conn: sqlite3.Connection
    snapshots: SnapshotRepository = field(init=False)
    compliance: ComplianceRepository = field(init=False)
    software: SoftwareRepository = field(init=False)
    audit: AuditRepository = field(init=False)

    def __post_init__(self) -> None:
        self.snapshots = SnapshotRepository(self.conn)
        self.compliance = ComplianceRepository(self.conn)
        self.software = SoftwareRepository(self.conn)
        self.audit = AuditRepository(self.conn)

    @classmethod
    def connect(cls, db_path: Path) -> Database:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return cls(conn)

    def close(self) -> None:
        self.conn.close()
