"""Tool registry — decorator-registered, read/write-typed, with import-time invariants.

Combines the two patterns worth copying from the reference codebases:

* **Afefe-AI** — a decorator registry that splits read (``INFO``) from write (``ACTION``)
  tools, where actions declare a risk and are gated behind confirmation.
* **SpinStrip-AI** — invariants enforced *at registration* (import time), so a
  mis-declared tool fails fast rather than at first call. Here: **an action tool must
  declare ``requires_approval=True``** (there is no such thing as a silent write), and
  read and action names share one namespace that cannot collide.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from .context import SessionContext
from .grounding import ActionCheck, ProposedAction, ReadResult

RISK_LEVELS = {"normal", "danger"}

ReadHandler = Callable[[SessionContext, Any], ReadResult]
ActionValidator = Callable[[SessionContext, Any], ActionCheck]
ActionExecutor = Callable[[SessionContext, Any], dict[str, Any]]


@dataclass(frozen=True)
class ReadSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: ReadHandler
    kind: Literal["read"] = "read"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    risk: str
    validator: ActionValidator
    executor: ActionExecutor
    requires_approval: bool = True
    kind: Literal["action"] = "action"

    def propose(self, ctx: SessionContext, params: BaseModel) -> ProposedAction | ActionCheck:
        """Run pre-flight validation and, if allowed, build the held proposal."""
        check = self.validator(ctx, params)
        if not check.allowed:
            return check
        return ProposedAction(
            tool=self.name,
            params=params.model_dump(),
            risk=self.risk,
            preview=check.preview,
            supporting_evidence=check.supporting_evidence,
            requires_approval=self.requires_approval,
        )


@dataclass
class _Registry:
    reads: dict[str, ReadSpec] = field(default_factory=dict)
    actions: dict[str, ActionSpec] = field(default_factory=dict)

    def _guard_name(self, name: str) -> None:
        if name in self.reads or name in self.actions:
            raise ValueError(f"tool name {name!r} is already registered")

    def all_specs(self) -> dict[str, ReadSpec | ActionSpec]:
        return {**self.reads, **self.actions}

    def tool_schemas(self) -> list[dict[str, Any]]:
        """OpenAI/Anthropic-style function schemas for ``bind_tools``.

        The description is suffixed with ``[read]`` / ``[action, requires approval]`` so
        the model knows, from the schema alone, which calls are state-changing.
        """
        schemas: list[dict[str, Any]] = []
        for spec in self.all_specs().values():
            tag = "read" if spec.kind == "read" else "action, requires approval"
            schemas.append(
                {
                    "name": spec.name,
                    "description": f"{spec.description} [{tag}]",
                    "parameters": spec.input_model.model_json_schema(),
                }
            )
        return schemas


REGISTRY = _Registry()


def read_tool(
    *, name: str, description: str, input_model: type[BaseModel]
) -> Callable[[ReadHandler], ReadSpec]:
    """Register a read-only (grounding) tool. Executes immediately when called."""

    def decorate(handler: ReadHandler) -> ReadSpec:
        REGISTRY._guard_name(name)
        spec = ReadSpec(name=name, description=description, input_model=input_model, handler=handler)
        REGISTRY.reads[name] = spec
        return spec

    return decorate


def action_tool(
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    validator: ActionValidator,
    risk: str = "normal",
    requires_approval: bool = True,
) -> Callable[[ActionExecutor], ActionSpec]:
    """Register a state-changing action tool. Held for human approval, never auto-run.

    Import-time invariants (fail fast):
      * ``requires_approval`` must be True — a write cannot be silent.
      * ``risk`` must be a known level.
    """
    if not requires_approval:
        raise ValueError(f"action tool {name!r} must set requires_approval=True (no silent writes)")
    if risk not in RISK_LEVELS:
        raise ValueError(f"action tool {name!r} has unknown risk {risk!r}; choose from {RISK_LEVELS}")

    def decorate(executor: ActionExecutor) -> ActionSpec:
        REGISTRY._guard_name(name)
        spec = ActionSpec(
            name=name,
            description=description,
            input_model=input_model,
            risk=risk,
            validator=validator,
            executor=executor,
            requires_approval=requires_approval,
        )
        REGISTRY.actions[name] = spec
        return spec

    return decorate
