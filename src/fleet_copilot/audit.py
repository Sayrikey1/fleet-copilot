"""Append-only audit trail (audit_log table + JSONL mirror) for every tool interaction."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .context import SessionContext

# decisions
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
    ts = _now()
    params_json = json.dumps(params, sort_keys=True, default=str)
    result_json = json.dumps(result, sort_keys=True, default=str) if result is not None else None
    row_id = ctx.db.audit.append(
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
        pass  # JSONL mirror is best-effort; the DB row is authoritative
    return row_id
