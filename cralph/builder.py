"""Build phase — task decomposition (Claude) + parallel Codex execution."""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn

from .config import BUILD_MODEL, CODEX_CMD, MAX_BUILD_SUBAGENTS, PLAN_MODEL
from .git import setup_build_branch
from .prompts import AGGREGATOR_SYSTEM, DECOMPOSER_SYSTEM
from .state import Feature

console = Console()


# ── Claude subprocess helper ──────────────────────────────────────────────────

def _run_claude(system: str, prompt: str) -> str:
    """Run a non-interactive claude call and return the response text."""
    import os
    cmd = [
        "claude", "-p", prompt,
        "--system-prompt", system,
        "--model", PLAN_MODEL,
        "--no-session-persistence",
        "--tools", "",
    ]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        console.print(f"[red]claude error (exit {result.returncode}):[/] {result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


# ── Task decomposition ────────────────────────────────────────────────────────

def decompose_plan(feature: Feature) -> list[dict]:
    """Ask Claude to break the final plan into a task DAG."""
    plan = feature.read_final_plan()
    task_desc = feature.read_task()

    prompt = (
        f"## Original Task\n\n{task_desc}\n\n"
        f"## Implementation Plan\n\n{plan}\n\n"
        "Decompose this plan into implementation tasks."
    )

    console.print("[cyan]Decomposing plan into tasks…[/]")
    raw = _run_claude(DECOMPOSER_SYSTEM, prompt)

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
            ready = [remaining[0]]  # circular dependency guard
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


def _aggregate_results(results: list[dict], feature: Feature) -> None:
    """Ask Claude to write a build report from task results."""
    done = [r for r in results if r["status"] == "done"]
    failed = [r for r in results if r["status"] == "failed"]

    summary_lines = []
    for r in results:
        icon = "✓" if r["status"] == "done" else "✗"
        summary_lines.append(f"- [{icon}] {r['id']}: {r['title']}\n  Output: {r['output'][:200]}")
        if r["status"] == "failed":
            summary_lines.append(f"  Error: {r['error'][:200]}")

    prompt = (
        f"## Build Results\n\n"
        f"Total: {len(results)} | Done: {len(done)} | Failed: {len(failed)}\n\n"
        + "\n".join(summary_lines)
    )

    console.print("\n[cyan]Generating build report…[/]")
    report = _run_claude(AGGREGATOR_SYSTEM, prompt)
    feature.write_build_report(report)


def run_build(feature: Feature, project_root: Path) -> None:
    """Full build pipeline: branch → decompose → execute → aggregate."""
    console.rule(f"[bold]Building: {feature.feature_id}")

    # Create and checkout a dedicated branch off the default branch
    branch = setup_build_branch(feature.feature_id, project_root)
    if branch:
        data = feature._read_status()
        data["branch"] = branch
        feature._write_status(data)

    feature.set_status("decomposing")
    tasks = decompose_plan(feature)
    feature.write_tasks(tasks)

    levels = topological_levels(tasks)
    console.print(f"[dim]{len(tasks)} tasks across {len(levels)} execution level(s)[/]")

    feature.set_status("building")
    results = asyncio.run(_execute_levels(levels, feature, project_root))

    failed = [r for r in results if r["status"] == "failed"]
    feature.set_status("build-partial" if failed else "built")

    _aggregate_results(results, feature)

    console.rule("[bold green]Build complete" if not failed else "[bold yellow]Build partial")
    console.print(
        f"[green]{len(results) - len(failed)} succeeded[/]"
        + (f", [red]{len(failed)} failed[/]" if failed else "")
    )
    console.print(f"[dim]Report: {feature.root / 'build-report.md'}[/]")
