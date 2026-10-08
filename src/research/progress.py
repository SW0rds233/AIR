from __future__ import annotations

"""Inspectable run snapshots; the ResearchStore remains the single authority."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from src.research.projection import object_rows
from src.utils.file_utils import sanitize_filename


def progress_dir(project_id: str, run_id: str) -> Path:
    from src.config import OUTPUT_DIR

    project = sanitize_filename(project_id).strip("._")
    run = sanitize_filename(run_id).strip("._")
    if not project or not run:
        raise ValueError("project_id and run_id are required")
    return OUTPUT_DIR / "research" / project / run / "progress"


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".progress-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_plan_progress(*, project_id: str, problem_id: str, run_id: str,
                        brief: dict[str, Any], plan: dict[str, Any],
                        spec: dict[str, Any] | None = None) -> Path:
    """Expose the understood problem and dispatch plan before the first agent runs."""
    root = progress_dir(project_id, run_id)
    for name, payload in (("brief.json", brief), ("plan.json", plan),
                          ("research_spec.json", spec or {})):
        _atomic_text(root / name, json.dumps(payload, ensure_ascii=False, indent=2))
    _atomic_text(root / "progress.json", json.dumps({
        "project_id": project_id, "problem_id": problem_id, "run_id": run_id,
        "stage": "planned", "counts": {},
        "note": "研究计划已落盘；后续角色成果将在本目录逐步更新",
    }, ensure_ascii=False, indent=2))
    return root


def write_run_progress(store: Any, *, project_id: str, problem_id: str,
                       run_id: str, task: Any, result: Any) -> Path:
    """After each committed task, publish readable, non-authoritative views."""
    root = progress_dir(project_id, run_id)
    kinds = ("evidence", "model", "claim", "obligation", "verification",
             "validation_plan", "figure", "review_issue", "manuscript")
    counts: dict[str, int] = {}
    for kind in kinds:
        rows = object_rows(store, kind, limit=100, run_id=run_id)
        counts[kind] = len(rows)
        if rows:
            _atomic_text(root / f"{kind}.json",
                         json.dumps(rows, ensure_ascii=False, indent=2))
        if kind == "manuscript" and rows:
            from src.agents.writing import render_markdown
            from src.publication.schemas import Manuscript

            payload = rows[-1].get("manuscript") or rows[-1]
            manuscript = Manuscript.model_validate(
                {key: value for key, value in payload.items() if not key.startswith("_")})
            _atomic_text(root / "manuscript-draft.md", render_markdown(manuscript))
    report = {
        "project_id": project_id, "problem_id": problem_id, "run_id": run_id,
        "latest_task": {"task_id": task.task_id, "agent": task.agent,
                        "objective": task.objective, "outcome": result.outcome.value,
                        "summary": result.summary},
        "counts": counts,
        "review": dict(result.payload or {}) if task.agent == "review" else None,
        "note": "过程快照；科研对象以 ResearchStore 为准，稿件尚未完成最终审阅",
    }
    if task.agent == "review":
        _atomic_text(root / "review-report.json",
                     json.dumps(dict(result.payload or {}), ensure_ascii=False, indent=2))
    elif (root / "review-report.json").is_file():
        try:
            report["review"] = json.loads((root / "review-report.json").read_text("utf-8"))
        except (OSError, ValueError):
            pass
    _atomic_text(root / "progress.json", json.dumps(report, ensure_ascii=False, indent=2))
    return root
