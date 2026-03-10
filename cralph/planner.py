"""Plan generation loop — 6 iterations of planner + reviewer (Claude Sonnet 4.6)."""

from __future__ import annotations

import anthropic
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from .config import MAX_PLAN_ITERATIONS, PLAN_MODEL
from .prompts import PLANNER_SYSTEM, REVIEWER_SYSTEM
from .state import Feature

console = Console()


def _stream_text(client: anthropic.Anthropic, system: str, messages: list[dict]) -> str:
    """Stream a Claude response and return the full text."""
    full_text = ""
    with client.messages.stream(
        model=PLAN_MODEL,
        max_tokens=8096,
        system=system,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full_text += text
    print()
    return full_text


def _run_planner(client: anthropic.Anthropic, task: str, draft: str, feedback: str, iteration: int) -> str:
    """Generate or refine the plan draft."""
    if iteration == 1:
        user_msg = f"## Task\n\n{task}\n\nCreate the implementation plan."
    else:
        user_msg = (
            f"## Original Task\n\n{task}\n\n"
            f"## Current Plan Draft\n\n{draft}\n\n"
            f"## Reviewer Feedback\n\n{feedback}\n\n"
            "Revise the plan addressing all feedback points."
        )

    console.print(Panel(f"[bold cyan]Planner[/] — iteration {iteration}/{MAX_PLAN_ITERATIONS}", expand=False))
    return _stream_text(client, PLANNER_SYSTEM, [{"role": "user", "content": user_msg}])


def _run_reviewer(client: anthropic.Anthropic, task: str, draft: str, iteration: int) -> tuple[str, str]:
    """Review the plan. Returns (APPROVED|REVISE, feedback)."""
    user_msg = (
        f"## Original Task\n\n{task}\n\n"
        f"## Plan to Review\n\n{draft}\n\n"
        "Review this plan."
    )

    console.print(Panel(f"[bold yellow]Reviewer[/] — iteration {iteration}/{MAX_PLAN_ITERATIONS}", expand=False))
    response = _stream_text(client, REVIEWER_SYSTEM, [{"role": "user", "content": user_msg}])

    # Parse decision
    result = "REVISE"
    feedback = response
    if "DECISION: APPROVED" in response:
        result = "APPROVED"
        feedback = "none"
    elif "DECISION: REVISE" in response:
        result = "REVISE"
        # Extract the FEEDBACK block
        if "FEEDBACK:" in response:
            feedback = response.split("FEEDBACK:", 1)[1].strip().rstrip("`").strip()

    return result, feedback


def run_plan_loop(feature: Feature, client: anthropic.Anthropic) -> None:
    """Run the full 6-iteration plan loop for a feature."""
    task = feature.read_task()
    draft = ""
    feedback = ""

    console.rule(f"[bold]Planning: {feature.feature_id}")

    for iteration in range(1, MAX_PLAN_ITERATIONS + 1):
        # — Planner phase (fresh context each time) —
        draft = _run_planner(client, task, draft, feedback, iteration)
        feature.write_plan_draft(draft, iteration)

        # — Reviewer phase (fresh context each time) —
        result, feedback = _run_reviewer(client, task, draft, iteration)
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
                console.print(f"[yellow]Max iterations reached — finalizing plan as-is[/]")

    feature.finalize_plan()
    console.rule("[bold green]Plan finalized")
    console.print(f"[dim]Saved to: {feature.root / 'plan-final.md'}[/]")
