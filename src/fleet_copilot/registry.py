"""Decorator tool registry, read/write-typed. Import-time invariants: an action tool must
declare requires_approval=True (no silent writes) and names cannot collide."""

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
        """Validate; if allowed, build the held proposal, else return the failing check."""
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
        """Anthropic-style function schemas for bind_tools, tagged read / action."""
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
    """Register a read-only (grounding) tool."""

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
    """Register a state-changing action tool (held for approval). Fails fast on bad declaration."""
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
