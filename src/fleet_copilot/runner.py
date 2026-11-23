"""Drive one turn of the graph, including the human-approval interrupt/resume loop.

Shared by the CLI and the live evaluation. ``approver`` is a callback that receives the
approval request dict and returns True/False — the single point where a human decision
enters the loop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.types import Command

from .context import SessionContext
from .graph import build_graph
from .llm import ChatModel

Approver = Callable[[dict[str, Any]], bool]


def run_turn(
    ctx: SessionContext,
    model: ChatModel,
    question: str,
    *,
    approver: Approver | None = None,
    thread_id: str = "turn-1",
    max_approvals: int = 8,
) -> dict[str, Any]:
    """Run the agent on ``question`` and return the final state dict.

    When the graph pauses for approval, ``approver`` is consulted; with no approver,
    the action is treated as denied (safe default).
    """
    graph = build_graph(ctx, model)
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.invoke({"question": question}, config)

    approvals = 0
    while state.get("__interrupt__") and approvals < max_approvals:
        approvals += 1
        request = state["__interrupt__"][0].value
        approved = approver(request) if approver else False
        state = graph.invoke(Command(resume={"approved": approved}), config)

    # merge the final persisted state (invoke returns the last node's view)
    final = graph.get_state(config).values
    return final
