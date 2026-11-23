"""The per-session principal — the analogue of Afefe-AI's ``ExecutionContext``.

One object threads the *authenticated* tenant, the database handle, and request
identity into every tool. Crucially, ``company_id`` lives here, sourced from the
session (CLI flag / API principal) — **never from the model's arguments.** Tools read
``ctx.company_id``; they cannot be tricked into querying another tenant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .db import FleetDB
from .settings import Settings, get_settings


@dataclass
class SessionContext:
    company_id: str
    db: FleetDB
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
        db = FleetDB.connect(db_path or settings.db_path)
        valid = db.companies()
        if company_id not in valid:
            db.close()
            raise ValueError(f"unknown tenant {company_id!r}; known tenants: {valid}")
        return cls(company_id=company_id, db=db, settings=settings, actor=actor, trace_id=trace_id)

    def close(self) -> None:
        self.db.close()
