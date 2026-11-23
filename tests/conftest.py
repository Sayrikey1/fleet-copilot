"""Shared fixtures: a seeded SQLite db (built once) and tenant-scoped contexts.

Everything here is deterministic and offline — no LLM, no network. The db is built from
the real dataset so the golden assertions test the true numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleet_copilot.context import SessionContext
from fleet_copilot.ingest import ingest
from fleet_copilot.settings import REPO_ROOT, Settings

DATASET = REPO_ROOT / "data" / "device-telemetry-dataset.ndjson"
GOLDEN_QA = json.loads((Path(__file__).parent / "golden" / "golden_qa.json").read_text())


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    # Function-scoped so each test (and its audit_log) is fully isolated; ingest is fast.
    db_path = tmp_path / "fleet.db"
    ingest(DATASET, db_path)
    return db_path


@pytest.fixture
def settings(seeded_db: Path, tmp_path: Path) -> Settings:
    return Settings(
        db_path=seeded_db,
        dataset_path=DATASET,
        audit_jsonl_path=tmp_path / "audit.jsonl",
    )


@pytest.fixture
def make_ctx(settings: Settings):
    created: list[SessionContext] = []

    def _make(company_id: str) -> SessionContext:
        ctx = SessionContext.open(company_id, db_path=settings.db_path, settings=settings)
        created.append(ctx)
        return ctx

    yield _make
    for ctx in created:
        ctx.close()


@pytest.fixture
def golden_qa() -> list[dict]:
    return GOLDEN_QA
