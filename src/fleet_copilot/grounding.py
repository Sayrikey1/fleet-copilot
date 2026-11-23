"""Grounding primitives: evidence, read/action results, and the citation verifier.

Grounding strategy in one line: **the agent may only assert what the tools returned.**
Read tools attach :class:`EvidenceRow`s (each carrying a snapshot id + timestamp);
the verifier then rejects any device id mentioned in the final answer that is not
present in the gathered evidence. This catches hallucinated device ids directly.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

# A device id in this dataset is a 12-char uppercase alphanumeric serial.
DEVICE_ID_RE = re.compile(r"\b[A-Z0-9]{12}\b")


class EvidenceRow(BaseModel):
    """One citable fact: which device, from which snapshot, and the salient values."""

    device_id: str
    snapshot_id: int | None = None
    collected_at: str | None = None
    source_tool: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)

    def cite(self) -> str:
        when = self.collected_at or "?"
        return f"[{self.device_id} @ {when}]"


class ReadResult(BaseModel):
    """What a read tool hands back: a short summary, the evidence, and raw data."""

    summary: str
    evidence: list[EvidenceRow] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class ActionCheck(BaseModel):
    """The verdict of an action tool's pre-flight validation (tenant + evidence)."""

    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    preview: str = ""
    supporting_evidence: list[EvidenceRow] = Field(default_factory=list)


class ProposedAction(BaseModel):
    """A state-changing action the agent wants to take — held for human approval."""

    tool: str
    params: dict[str, Any]
    risk: str = "normal"
    preview: str = ""
    supporting_evidence: list[EvidenceRow] = Field(default_factory=list)
    requires_approval: bool = True


def evidence_device_ids(evidence: list[EvidenceRow]) -> set[str]:
    return {e.device_id for e in evidence}


def unsupported_device_ids(answer: str, evidence: list[EvidenceRow]) -> list[str]:
    """Device ids named in ``answer`` that no gathered evidence supports.

    A non-empty result means the answer referenced a device the tools never
    returned for this tenant — i.e. a hallucination or a tenant leak.
    """
    mentioned = set(DEVICE_ID_RE.findall(answer))
    grounded = evidence_device_ids(evidence)
    return sorted(mentioned - grounded)
