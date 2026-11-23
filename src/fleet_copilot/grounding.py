"""Grounding primitives: evidence, read/action results, and the citation verifier.
The agent may only assert what the tools returned; the verifier rejects any device id
in the answer that no evidence supports."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

DEVICE_ID_RE = re.compile(r"\b[A-Z0-9]{12}\b")  # 12-char serial


class EvidenceRow(BaseModel):
    """One citable fact: device, snapshot, salient values."""

    device_id: str
    snapshot_id: int | None = None
    collected_at: str | None = None
    source_tool: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)

    def cite(self) -> str:
        when = self.collected_at or "?"
        return f"[{self.device_id} @ {when}]"


class ReadResult(BaseModel):
    summary: str
    evidence: list[EvidenceRow] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class ActionCheck(BaseModel):
    """Verdict of an action's pre-flight validation (tenant + evidence)."""

    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    preview: str = ""
    supporting_evidence: list[EvidenceRow] = Field(default_factory=list)


class ProposedAction(BaseModel):
    """A state-changing action, held for human approval."""

    tool: str
    params: dict[str, Any]
    risk: str = "normal"
    preview: str = ""
    supporting_evidence: list[EvidenceRow] = Field(default_factory=list)
    requires_approval: bool = True


def evidence_device_ids(evidence: list[EvidenceRow]) -> set[str]:
    return {e.device_id for e in evidence}


def unsupported_device_ids(answer: str, evidence: list[EvidenceRow]) -> list[str]:
    """Device ids in answer that no evidence supports (hallucination / tenant leak)."""
    mentioned = set(DEVICE_ID_RE.findall(answer))
    grounded = evidence_device_ids(evidence)
    return sorted(mentioned - grounded)
