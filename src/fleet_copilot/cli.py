"""Fleet Copilot CLI: seed / ask / demo / audit-log."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from .context import SessionContext
from .ingest import ingest
from .llm import build_model
from .runner import run_turn
from .settings import get_settings

app = typer.Typer(add_completion=False, help="Agentic AI Fleet Copilot over device telemetry.")
console = Console()


@app.command()
def seed() -> None:
    """(Re)build the SQLite database from the telemetry NDJSON."""
    s = get_settings()
    counts = ingest(s.dataset_path, s.db_path)
    console.print(f"[green]Seeded[/green] {s.db_path}")
    for table, n in counts.items():
        console.print(f"  {table}: {n}")


def _interactive_approver(request: dict) -> bool:
    console.print("\n[yellow bold]⚠ Action requires approval[/yellow bold]")
    console.print(f"  tool:    {request['tool']}")
    console.print(f"  risk:    {request['risk']}")
    console.print(f"  params:  {json.dumps(request['params'])}")
    console.print(f"  preview: {request['preview']}")
    return typer.confirm("Approve this action?", default=False)


@app.command()
def ask(
    question: str = typer.Argument(..., help="The question to ask."),
    company: str = typer.Option(..., "--company", "-c", help="Tenant / company_id (the principal)."),
    approve: bool | None = typer.Option(
        None, "--approve/--deny",
        help="Auto-approve or auto-deny proposed actions (default: prompt interactively).",
    ),
    show_evidence: bool = typer.Option(True, help="Print the gathered evidence."),
) -> None:
    """Ask a grounded question; propose and (with approval) take actions."""
    s = get_settings()
    ctx = SessionContext.open(company, settings=s)
    try:
        model = build_model(s)

        def approver(_req: dict) -> bool:
            return _interactive_approver(_req) if approve is None else bool(approve)

        state = run_turn(ctx, model, question, approver=approver)
    finally:
        ctx.close()

    console.print("\n[bold cyan]Answer[/bold cyan]")
    console.print(state.get("answer", "(no answer)"))

    if state.get("refusals"):
        console.print("\n[bold red]Refusals[/bold red]")
        for r in state["refusals"]:
            console.print(f"  • {r}")

    if state.get("action_results"):
        console.print("\n[bold green]Executed actions[/bold green]")
        for a in state["action_results"]:
            console.print(f"  • {json.dumps(a)}")

    if state.get("unsupported_ids"):
        console.print(f"\n[red]Grounding warning:[/red] unsupported ids: {state['unsupported_ids']}")

    if show_evidence and state.get("evidence"):
        table = Table(title="Evidence", show_lines=False)
        table.add_column("device_id")
        table.add_column("collected_at")
        table.add_column("source")
        table.add_column("detail")
        seen = set()
        for e in state["evidence"]:
            key = (e["device_id"], e.get("source_tool"), json.dumps(e.get("detail"), sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            table.add_row(e["device_id"], str(e.get("collected_at")), e.get("source_tool", ""),
                          json.dumps(e.get("detail", {}), default=str))
        console.print(table)


@app.command()
def demo() -> None:
    """Run the full agent pipeline offline (scripted model, no API key) across five scenarios."""
    s = get_settings()
    if not s.db_path.exists():
        ingest(s.dataset_path, s.db_path)
    from .demo import run_demo

    run_demo(console, s)


@app.command("audit-log")
def audit_log(
    company: str = typer.Option(..., "--company", "-c", help="Tenant / company_id."),
) -> None:
    """Show the append-only audit trail for a tenant."""
    s = get_settings()
    ctx = SessionContext.open(company, settings=s)
    try:
        rows = ctx.db.audit_entries(company)
    finally:
        ctx.close()
    if not rows:
        console.print("(no audit entries)")
        return
    table = Table(title=f"Audit log — {company}")
    for col in ("id", "ts", "tool", "decision"):
        table.add_column(col)
    for r in rows:
        table.add_row(str(r["id"]), r["ts"], r["tool"], r["decision"])
    console.print(table)


if __name__ == "__main__":
    app()
