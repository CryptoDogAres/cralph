"""Feature state management — reads/writes .cralph/<feature-id>/ files."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from .config import STATE_DIR

FeatureStatus = Literal[
    "planning",
    "plan-approved",
    "decomposing",
    "building",
    "built",
    "build-partial",
    "abandoned",
]


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:40].rstrip("-")


@dataclass
class Feature:
    feature_id: str
    root: Path  # .cralph/<feature-id>/

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def create(cls, task: str, project_root: Path) -> "Feature":
        slug = _slugify(task)
        short_id = uuid.uuid4().hex[:8]
        feature_id = f"{slug}-{short_id}"
        root = project_root / STATE_DIR / feature_id
        root.mkdir(parents=True, exist_ok=True)
        (root / "build-log").mkdir(exist_ok=True)
        feature = cls(feature_id=feature_id, root=root)
        feature.write_task(task)
        feature.set_status("planning")
        return feature

    @classmethod
    def load(cls, feature_id: str, project_root: Path) -> "Feature":
        root = project_root / STATE_DIR / feature_id
        if not root.exists():
            raise FileNotFoundError(f"Feature {feature_id!r} not found in {project_root / STATE_DIR}")
        return cls(feature_id=feature_id, root=root)

    @classmethod
    def latest(cls, project_root: Path) -> "Feature":
        state_dir = project_root / STATE_DIR
        if not state_dir.exists():
            raise FileNotFoundError("No .cralph/ directory found. Run `cralph generate` first.")
        dirs = sorted(state_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        dirs = [d for d in dirs if d.is_dir()]
        if not dirs:
            raise FileNotFoundError("No features found. Run `cralph generate` first.")
        return cls(feature_id=dirs[0].name, root=dirs[0])

    @classmethod
    def all(cls, project_root: Path) -> list["Feature"]:
        state_dir = project_root / STATE_DIR
        if not state_dir.exists():
            return []
        return [
            cls(feature_id=d.name, root=d)
            for d in sorted(state_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            if d.is_dir()
        ]

    # ── status ────────────────────────────────────────────────────────────────

    def set_status(self, status: FeatureStatus) -> None:
        data = self._read_status()
        data["status"] = status
        data["updated_at"] = datetime.now().isoformat()
        self._write_status(data)

    def get_status(self) -> FeatureStatus:
        return self._read_status().get("status", "planning")

    def _read_status(self) -> dict:
        p = self.root / "feature-status.json"
        if p.exists():
            return json.loads(p.read_text())
        return {"created_at": datetime.now().isoformat()}

    def _write_status(self, data: dict) -> None:
        (self.root / "feature-status.json").write_text(json.dumps(data, indent=2))

    # ── plan files ────────────────────────────────────────────────────────────

    def write_task(self, task: str) -> None:
        (self.root / "task.md").write_text(task)

    def read_task(self) -> str:
        return (self.root / "task.md").read_text()

    def write_plan_draft(self, plan: str, iteration: int) -> None:
        (self.root / "plan-draft.md").write_text(plan)
        (self.root / "plan-iteration.txt").write_text(str(iteration))

    def read_plan_draft(self) -> str:
        p = self.root / "plan-draft.md"
        return p.read_text() if p.exists() else ""

    def write_review(self, result: Literal["APPROVED", "REVISE"], feedback: str) -> None:
        (self.root / "plan-result.txt").write_text(result)
        (self.root / "plan-feedback.txt").write_text(feedback)

    def read_feedback(self) -> str:
        p = self.root / "plan-feedback.txt"
        return p.read_text() if p.exists() else ""

    def read_plan_result(self) -> str:
        p = self.root / "plan-result.txt"
        return p.read_text().strip() if p.exists() else "REVISE"

    def finalize_plan(self) -> None:
        draft = self.read_plan_draft()
        (self.root / "plan-final.md").write_text(draft)
        self.set_status("plan-approved")

    def read_final_plan(self) -> str:
        p = self.root / "plan-final.md"
        if not p.exists():
            raise FileNotFoundError(
                f"No approved plan found for {self.feature_id}. Run `cralph generate` first."
            )
        return p.read_text()

    def plan_iteration(self) -> int:
        p = self.root / "plan-iteration.txt"
        return int(p.read_text().strip()) if p.exists() else 0

    # ── build files ───────────────────────────────────────────────────────────

    def write_tasks(self, tasks: list[dict]) -> None:
        (self.root / "build-tasks.json").write_text(json.dumps(tasks, indent=2))
        status = self._read_status()
        status["build_tasks"] = {t["id"]: "pending" for t in tasks}
        self._write_status(status)

    def read_tasks(self) -> list[dict]:
        p = self.root / "build-tasks.json"
        if not p.exists():
            raise FileNotFoundError("No build tasks found. Run `cralph build` to decompose the plan.")
        return json.loads(p.read_text())

    def update_task_status(self, task_id: str, status: str) -> None:
        data = self._read_status()
        data.setdefault("build_tasks", {})[task_id] = status
        self._write_status(data)

    def task_statuses(self) -> dict[str, str]:
        return self._read_status().get("build_tasks", {})

    def write_task_log(self, task_id: str, content: str) -> None:
        (self.root / "build-log" / f"{task_id}.md").write_text(content)

    def write_build_report(self, report: str) -> None:
        (self.root / "build-report.md").write_text(report)

    def read_build_report(self) -> str:
        p = self.root / "build-report.md"
        return p.read_text() if p.exists() else ""
