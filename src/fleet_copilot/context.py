"""Per-session principal: tenant + db handle, threaded into every tool. company_id is
sourced here (never from the model), so tools cannot query another tenant."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .data import Database
from .settings import Settings, get_settings


@dataclass
class SessionContext:
    company_id: str
    db: Database
    settings: Settings = field(default_factory=get_settings)
    actor: str = "copilot"
    trace_id: str = "trace-local"

    @classmethod
    def open(
        cls,
        company_id: str,
        *,
        db_path: Path | None = None,
        settings: Settings | None = None,
        actor: str = "copilot",
        trace_id: str = "trace-local",
    ) -> SessionContext:
        settings = settings or get_settings()
        db = Database.connect(db_path or settings.db_path)
        valid = db.snapshots.companies()
        if company_id not in valid:
            db.close()
            raise ValueError(f"unknown tenant {company_id!r}; known tenants: {valid}")
        return cls(company_id=company_id, db=db, settings=settings, actor=actor, trace_id=trace_id)

    def close(self) -> None:
        self.db.close()
