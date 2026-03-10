"""cralph CLI — entry points for generate, build, status, list, review, retry."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from .builder import _aggregate_results, _execute_levels, run_build, topological_levels
from .planner import run_plan_loop
from .state import Feature

console = Console()


def _project_root() -> Path:
    return Path.cwd()


def _resolve_feature(feature_id: str | None) -> Feature:
    root = _project_root()
    if feature_id:
        return Feature.load(feature_id, root)
    return Feature.latest(root)


# ── Commands ──────────────────────────────────────────────────────────────────

@click.group()
def main() -> None:
    """cralph — AI-powered feature planning and implementation.

    \b
    Typical workflow:
      cralph generate "Add OAuth2 authentication"
      cralph build
      cralph status
    """


@main.command()
@click.argument("task")
@click.option("--iterations", "-i", default=None, type=int, help="Max plan iterations (default: 6).")
def generate(task: str, iterations: int | None) -> None:
    """Generate an implementation plan for TASK.

    Runs up to 6 plan/review iterations using Claude Sonnet 4.6.
    Creates state in .cralph/<feature-id>/ in the current directory.
    """
    feature = Feature.create(task, _project_root())
    console.print(f"[dim]Feature ID: {feature.feature_id}[/]")
    run_plan_loop(feature, max_iterations=iterations)


@main.command()
@click.argument("feature_id", required=False)
def build(feature_id: str | None) -> None:
    """Build the implementation from an approved plan.

    Decomposes the plan into tasks and executes them with Codex.
    Uses FEATURE_ID or the most recently generated plan.
    """
    feature = _resolve_feature(feature_id)

    status = feature.get_status()
    if status == "planning":
        console.print("[red]Error:[/] Feature is still in planning state. Run `cralph generate` first.")
        sys.exit(1)
    if status in ("building", "decomposing"):
        console.print("[yellow]Warning:[/] Feature is already being built. Use `cralph retry` to retry failed tasks.")
        sys.exit(1)

    run_build(feature, _project_root())


@main.command()
@click.argument("feature_id", required=False)
def status(feature_id: str | None) -> None:
    """Show the status of a feature."""
    feature = _resolve_feature(feature_id)
    feat_status = feature.get_status()

    console.print(f"\n[bold]Feature:[/] {feature.feature_id}")
    console.print(f"[bold]Status:[/]  {feat_status}")

    branch = feature._read_status().get("branch")
    if branch:
        console.print(f"[bold]Branch:[/]  {branch}")

    iteration = feature.plan_iteration()
    if iteration > 0:
        plan_result = feature.read_plan_result()
        icon = "[green]✓[/]" if plan_result == "APPROVED" else "[yellow]~[/]"
        console.print(f"[bold]Plan:[/]    {icon} iteration {iteration}/6 — {plan_result}")

    task_statuses = feature.task_statuses()
    if task_statuses:
        console.print()
        table = Table(title="Build Tasks", show_header=True, header_style="bold")
        table.add_column("Task ID", style="dim")
        table.add_column("Status")

        status_colors = {
            "pending": "[dim]pending[/]",
            "running": "[cyan]running[/]",
            "done": "[green]done[/]",
            "failed": "[red]failed[/]",
        }
        for tid, ts in task_statuses.items():
            table.add_row(tid, status_colors.get(ts, ts))

        console.print(table)

    console.print()


@main.command(name="list")
def list_features() -> None:
    """List all features in the current project."""
    features = Feature.all(_project_root())
    if not features:
        console.print("[dim]No features found. Run `cralph generate` to create one.[/]")
        return

    table = Table(title="Features", show_header=True, header_style="bold")
    table.add_column("Feature ID")
    table.add_column("Status")
    table.add_column("Plan")
    table.add_column("Tasks")

    for f in features:
        feat_status = f.get_status()
        iteration = f.plan_iteration()
        plan_result = f.read_plan_result() if iteration > 0 else "—"
        task_statuses = f.task_statuses()
        if task_statuses:
            done = sum(1 for s in task_statuses.values() if s == "done")
            tasks_cell = f"{done}/{len(task_statuses)}"
        else:
            tasks_cell = "—"
        table.add_row(f.feature_id, feat_status, plan_result, tasks_cell)

    console.print(table)


@main.command()
@click.argument("feature_id", required=False)
def review(feature_id: str | None) -> None:
    """Display the approved plan for human review."""
    feature = _resolve_feature(feature_id)
    try:
        plan = feature.read_final_plan()
        console.print(f"\n[bold]Feature:[/] {feature.feature_id}\n")
        console.print(Markdown(plan))
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)


@main.command()
@click.argument("feature_id", required=False)
def report(feature_id: str | None) -> None:
    """Display the build report."""
    feature = _resolve_feature(feature_id)
    content = feature.read_build_report()
    if not content:
        console.print("[dim]No build report yet. Run `cralph build` first.[/]")
        return
    console.print(Markdown(content))


@main.command()
@click.argument("feature_id", required=False)
@click.option("--delete", is_flag=True, help="Also delete all state files for this feature.")
def abandon(feature_id: str | None, delete: bool) -> None:
    """Mark a feature as abandoned (or delete it entirely).

    Useful after Ctrl+C or when a feature is no longer needed.
    Without --delete, marks the feature as abandoned in .cralph/.
    With --delete, removes the feature directory completely.
    """
    feature = _resolve_feature(feature_id)

    if delete:
        shutil.rmtree(feature.root)
        console.print(f"[red]Deleted[/] {feature.feature_id}")
    else:
        feature.set_status("abandoned")
        console.print(f"[yellow]Abandoned[/] {feature.feature_id}")
        console.print(f"[dim]State preserved at {feature.root} — use --delete to remove.[/]")


@main.command()
@click.argument("feature_id", required=False)
def retry(feature_id: str | None) -> None:
    """Retry failed build tasks for a feature."""
    feature = _resolve_feature(feature_id)

    task_statuses = feature.task_statuses()
    failed_ids = {tid for tid, s in task_statuses.items() if s == "failed"}

    if not failed_ids:
        console.print("[green]No failed tasks to retry.[/]")
        return

    for tid in failed_ids:
        feature.update_task_status(tid, "pending")

    console.print(f"[yellow]Retrying {len(failed_ids)} failed task(s)…[/]")

    all_tasks = feature.read_tasks()
    failed_tasks = [t for t in all_tasks if t["id"] in failed_ids]

    levels = topological_levels(failed_tasks)
    feature.set_status("building")
    retry_results = asyncio.run(_execute_levels(levels, feature, _project_root()))

    still_failed = [r for r in retry_results if r["status"] == "failed"]
    feature.set_status("build-partial" if still_failed else "built")

    prev_done_ids = {tid for tid, s in task_statuses.items() if s == "done"}
    synthetic_done = [
        {"id": t["id"], "title": t["title"], "status": "done", "output": "", "error": ""}
        for t in all_tasks if t["id"] in prev_done_ids
    ]
    _aggregate_results(synthetic_done + retry_results, feature)
