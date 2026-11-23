"""Drive one turn of the graph, handling the approval interrupt/resume loop.
approver(request)->bool is the single point where a human decision enters."""

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
    """Run the agent and return the final state. No approver => actions are denied."""
    graph = build_graph(ctx, model)
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.invoke({"question": question}, config)

    approvals = 0
    while state.get("__interrupt__") and approvals < max_approvals:
        approvals += 1
        request = state["__interrupt__"][0].value
        approved = approver(request) if approver else False
        state = graph.invoke(Command(resume={"approved": approved}), config)

    return graph.get_state(config).values
