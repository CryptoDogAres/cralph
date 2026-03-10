"""Build phase — task decomposition (Claude) + parallel Codex execution."""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from pathlib import Path

import anthropic
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn
from rich.table import Table

from .config import BUILD_MODEL, CODEX_CMD, MAX_BUILD_SUBAGENTS, PLAN_MODEL
from .prompts import AGGREGATOR_SYSTEM, DECOMPOSER_SYSTEM
from .state import Feature

console = Console()


# ── Task decomposition ────────────────────────────────────────────────────────

def decompose_plan(feature: Feature, client: anthropic.Anthropic) -> list[dict]:
    """Ask Claude to break the final plan into a task DAG."""
    plan = feature.read_final_plan()
    task_desc = feature.read_task()

    user_msg = (
        f"## Original Task\n\n{task_desc}\n\n"
        f"## Implementation Plan\n\n{plan}\n\n"
        "Decompose this plan into implementation tasks."
    )

    console.print("[cyan]Decomposing plan into tasks…[/]")
    response = client.messages.create(
        model=PLAN_MODEL,
        max_tokens=8096,
        system=DECOMPOSER_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    tasks = json.loads(raw)
    console.print(f"[green]✓ {len(tasks)} tasks identified[/]")
    return tasks


# ── Topological ordering ──────────────────────────────────────────────────────

def topological_levels(tasks: list[dict]) -> list[list[dict]]:
    """Group tasks into parallel execution levels via topological sort."""
    task_map = {t["id"]: t for t in tasks}
    completed: set[str] = set()
    remaining = [t["id"] for t in tasks]
    levels = []

    while remaining:
        ready = [
            tid for tid in remaining
            if all(dep in completed for dep in task_map[tid].get("dependencies", []))
        ]
        if not ready:
            # Circular dependency guard — unblock by taking first
            ready = [remaining[0]]
        levels.append([task_map[tid] for tid in ready])
        completed.update(ready)
        remaining = [tid for tid in remaining if tid not in completed]

    return levels


# ── Codex invocation ──────────────────────────────────────────────────────────

async def _run_codex_task(
    task: dict,
    feature: Feature,
    project_root: Path,
    semaphore: asyncio.Semaphore,
    progress: Progress,
    progress_task: TaskID,
) -> dict:
    """Run one Codex task and return a result dict."""
    task_id = task["id"]
    files_hint = ", ".join(task.get("files", [])) or "as needed"

    prompt = (
        f"Task: {task['title']}\n\n"
        f"{task['description']}\n\n"
        f"Files to create/modify: {files_hint}\n"
        "Focus only on this task. Do not modify files outside your scope."
    )

    # Build the command from the template
    cmd_str = CODEX_CMD.format(model=BUILD_MODEL, task=shlex.quote(prompt))
    cmd = shlex.split(cmd_str)

    feature.update_task_status(task_id, "running")

    async with semaphore:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_root,
        )
        stdout, stderr = await proc.communicate()

    output = stdout.decode(errors="replace")
    error = stderr.decode(errors="replace")
    status = "done" if proc.returncode == 0 else "failed"

    feature.update_task_status(task_id, status)
    feature.write_task_log(
        task_id,
        f"# {task['title']}\n\n"
        f"**Status:** {status}  \n"
        f"**Return code:** {proc.returncode}\n\n"
        f"## Output\n\n```\n{output}\n```\n\n"
        f"## Stderr\n\n```\n{error}\n```\n",
    )

    progress.advance(progress_task)
    icon = "[green]✓[/]" if status == "done" else "[red]✗[/]"
    console.print(f"  {icon} {task_id}: {task['title']}")

    return {"id": task_id, "title": task["title"], "status": status, "output": output, "error": error}


# ── Build orchestration ───────────────────────────────────────────────────────

async def _execute_levels(
    levels: list[list[dict]],
    feature: Feature,
    project_root: Path,
) -> list[dict]:
    """Execute task levels in order, parallelising within each level."""
    semaphore = asyncio.Semaphore(MAX_BUILD_SUBAGENTS)
    all_results: list[dict] = []

    total_tasks = sum(len(lvl) for lvl in levels)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        progress_task = progress.add_task("Building…", total=total_tasks)

        for i, level in enumerate(levels, 1):
            console.print(f"\n[bold]Level {i}[/] — {len(level)} task(s) in parallel")
            results = await asyncio.gather(
                *[
                    _run_codex_task(task, feature, project_root, semaphore, progress, progress_task)
                    for task in level
                ]
            )
            all_results.extend(results)

    return all_results


def _aggregate_results(
    results: list[dict],
    feature: Feature,
    client: anthropic.Anthropic,
) -> None:
    """Ask Claude to write a build report from task results."""
    done = [r for r in results if r["status"] == "done"]
    failed = [r for r in results if r["status"] == "failed"]

    summary_lines = []
    for r in results:
        icon = "✓" if r["status"] == "done" else "✗"
        summary_lines.append(f"- [{icon}] {r['id']}: {r['title']}\n  Output snippet: {r['output'][:200]}")
        if r["status"] == "failed":
            summary_lines.append(f"  Error: {r['error'][:200]}")

    user_msg = (
        f"## Build Results\n\n"
        f"Total: {len(results)} | Done: {len(done)} | Failed: {len(failed)}\n\n"
        + "\n".join(summary_lines)
    )

    console.print("\n[cyan]Generating build report…[/]")
    response = client.messages.create(
        model=PLAN_MODEL,
        max_tokens=4096,
        system=AGGREGATOR_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    report = response.content[0].text
    feature.write_build_report(report)


def run_build(feature: Feature, client: anthropic.Anthropic, project_root: Path) -> None:
    """Full build pipeline: decompose → execute → aggregate."""
    console.rule(f"[bold]Building: {feature.feature_id}")

    # 1. Decompose
    feature.set_status("decomposing")
    tasks = decompose_plan(feature, client)
    feature.write_tasks(tasks)

    # 2. Topological sort
    levels = topological_levels(tasks)
    console.print(
        f"[dim]{len(tasks)} tasks across {len(levels)} execution level(s)[/]"
    )

    # 3. Execute
    feature.set_status("building")
    results = asyncio.run(_execute_levels(levels, feature, project_root))

    # 4. Finalize status
    failed = [r for r in results if r["status"] == "failed"]
    feature.set_status("build-partial" if failed else "built")

    # 5. Aggregate
    _aggregate_results(results, feature, client)

    # 6. Summary
    console.rule("[bold green]Build complete" if not failed else "[bold yellow]Build partial")
    console.print(
        f"[green]{len(results) - len(failed)} succeeded[/]"
        + (f", [red]{len(failed)} failed[/]" if failed else "")
    )
    console.print(f"[dim]Report: {feature.root / 'build-report.md'}[/]")
