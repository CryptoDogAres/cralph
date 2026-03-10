"""Plan generation loop — 6 iterations of planner + reviewer (Claude Sonnet 4.6)."""

from __future__ import annotations

import subprocess
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from .config import MAX_PLAN_ITERATIONS, PLAN_MODEL
from .prompts import PLANNER_SYSTEM, REVIEWER_SYSTEM
from .state import Feature

console = Console()


def _run_claude(system: str, prompt: str) -> str:
    """Run a non-interactive claude call, stream to terminal, return full text."""
    cmd = [
        "claude", "-p", prompt,
        "--system-prompt", system,
        "--model", PLAN_MODEL,
        "--no-session-persistence",
        "--tools", "",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output = ""
    for line in proc.stdout:
        print(line, end="", flush=True)
        output += line
    proc.wait()
    if proc.returncode != 0:
        err = proc.stderr.read()
        console.print(f"\n[red]claude error (exit {proc.returncode}):[/] {err}")
        sys.exit(1)
    print()
    return output.strip()


def _run_planner(task: str, draft: str, feedback: str, iteration: int) -> str:
    if iteration == 1:
        prompt = f"## Task\n\n{task}\n\nCreate the implementation plan."
    else:
        prompt = (
            f"## Original Task\n\n{task}\n\n"
            f"## Current Plan Draft\n\n{draft}\n\n"
            f"## Reviewer Feedback\n\n{feedback}\n\n"
            "Revise the plan addressing all feedback points."
        )
    console.print(Panel(f"[bold cyan]Planner[/] — iteration {iteration}/{MAX_PLAN_ITERATIONS}", expand=False))
    return _run_claude(PLANNER_SYSTEM, prompt)


def _run_reviewer(task: str, draft: str, iteration: int) -> tuple[str, str]:
    prompt = (
        f"## Original Task\n\n{task}\n\n"
        f"## Plan to Review\n\n{draft}\n\n"
        "Review this plan."
    )
    console.print(Panel(f"[bold yellow]Reviewer[/] — iteration {iteration}/{MAX_PLAN_ITERATIONS}", expand=False))
    response = _run_claude(REVIEWER_SYSTEM, prompt)

    result = "REVISE"
    feedback = response
    if "DECISION: APPROVED" in response:
        result = "APPROVED"
        feedback = "none"
    elif "DECISION: REVISE" in response and "FEEDBACK:" in response:
        result = "REVISE"
        feedback = response.split("FEEDBACK:", 1)[1].strip().rstrip("`").strip()

    return result, feedback


def run_plan_loop(feature: Feature) -> None:
    """Run the full 6-iteration plan loop for a feature."""
    task = feature.read_task()
    draft = ""
    feedback = ""

    console.rule(f"[bold]Planning: {feature.feature_id}")

    for iteration in range(1, MAX_PLAN_ITERATIONS + 1):
        draft = _run_planner(task, draft, feedback, iteration)
        feature.write_plan_draft(draft, iteration)

        result, feedback = _run_reviewer(task, draft, iteration)
        feature.write_review(result, feedback)

        console.print()
        if result == "APPROVED":
            console.print(f"[bold green]✓ APPROVED[/] on iteration {iteration}")
            break
        else:
            console.print(f"[bold yellow]↺ REVISE[/] — iteration {iteration} feedback recorded")
            if iteration < MAX_PLAN_ITERATIONS:
                console.print(Markdown(f"**Feedback:**\n{feedback}"))
            else:
                console.print("[yellow]Max iterations reached — finalizing plan as-is[/]")

    feature.finalize_plan()
    console.rule("[bold green]Plan finalized")
    console.print(f"[dim]Saved to: {feature.root / 'plan-final.md'}[/]")
