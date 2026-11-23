"""Append-only audit trail for every tool call, proposal, approval and refusal.

Writes to two places: the ``audit_log`` table (queryable, tenant-scoped) and a JSONL
mirror on disk (tamper-evident, easy to ship to a SIEM). Nothing is ever updated or
deleted — each event is a new row/line.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .context import SessionContext

# decision vocabulary
PROPOSED = "proposed"
APPROVED = "approved"
EXECUTED = "executed"
DENIED = "denied"
REFUSED = "refused"
READ = "read"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def record(
    ctx: SessionContext,
    *,
    tool: str,
    params: dict[str, Any],
    decision: str,
    result: dict[str, Any] | None = None,
    jsonl_path: Path | None = None,
) -> int:
    """Append one audit event. Returns the audit row id."""
    ts = _now()
    params_json = json.dumps(params, sort_keys=True, default=str)
    result_json = json.dumps(result, sort_keys=True, default=str) if result is not None else None
    row_id = ctx.db.append_audit(
        ts=ts,
        trace_id=ctx.trace_id,
        company_id=ctx.company_id,
        actor=ctx.actor,
        tool=tool,
        params_json=params_json,
        decision=decision,
        result_json=result_json,
    )
    path = jsonl_path if jsonl_path is not None else ctx.settings.audit_jsonl_path
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "id": row_id,
                        "ts": ts,
                        "trace_id": ctx.trace_id,
                        "company_id": ctx.company_id,
                        "actor": ctx.actor,
                        "tool": tool,
                        "decision": decision,
                        "params": params,
                        "result": result,
                    },
                    default=str,
                )
                + "\n"
            )
    except OSError:
        pass  # the DB row is the source of truth; JSONL mirror is best-effort
    return row_id
