"""Git helpers for cralph build branch management."""

from __future__ import annotations

import subprocess
from pathlib import Path

from rich.console import Console

console = Console()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def is_git_repo(path: Path) -> bool:
    result = _git(["rev-parse", "--is-inside-work-tree"], path)
    return result.returncode == 0


def current_branch(path: Path) -> str:
    result = _git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    return result.stdout.strip()


def default_branch(path: Path) -> str:
    """Return the default branch (main or master) of the repo."""
    # Try to get it from the remote HEAD ref
    result = _git(["symbolic-ref", "refs/remotes/origin/HEAD"], path)
    if result.returncode == 0:
        # refs/remotes/origin/main -> main
        return result.stdout.strip().split("/")[-1]

    # Fallback: check which of main/master exists locally
    for candidate in ("main", "master"):
        r = _git(["show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"], path)
        if r.returncode == 0:
            return candidate

    # Last resort: use current branch
    return current_branch(path)


def branch_exists(name: str, path: Path) -> bool:
    result = _git(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], path)
    return result.returncode == 0


def create_and_checkout_branch(branch: str, base: str, path: Path) -> None:
    """Create a new branch off base and check it out."""
    # Make sure we're up to date on the base branch first
    _git(["fetch", "--quiet"], path)

    # Check out base branch cleanly
    result = _git(["checkout", base], path)
    if result.returncode != 0:
        raise RuntimeError(f"Could not checkout base branch '{base}': {result.stderr.strip()}")

    # Create and switch to new branch
    result = _git(["checkout", "-b", branch], path)
    if result.returncode != 0:
        raise RuntimeError(f"Could not create branch '{branch}': {result.stderr.strip()}")

    console.print(f"[green]✓ Created branch[/] [bold]{branch}[/] off [dim]{base}[/]")


def setup_build_branch(feature_id: str, project_root: Path) -> str | None:
    """
    If inside a git repo, create and checkout a build branch for the feature.
    Returns the branch name, or None if not a git repo.
    """
    if not is_git_repo(project_root):
        console.print("[dim]Not a git repo — skipping branch creation.[/]")
        return None

    branch = f"cralph/{feature_id}"

    if branch_exists(branch, project_root):
        # Branch already exists (e.g. resuming after failure) — just check it out
        _git(["checkout", branch], project_root)
        console.print(f"[yellow]Resuming existing branch[/] [bold]{branch}[/]")
        return branch

    base = default_branch(project_root)
    create_and_checkout_branch(branch, base, project_root)
    return branch
