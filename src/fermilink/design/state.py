"""State-path helpers for `.fermilink-design/`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class DesignPaths:
    project_root: Path
    state_root: Path
    baseline_dir: Path
    archive_dir: Path
    candidates_dir: Path
    reports_dir: Path
    sessions_dir: Path

    @property
    def goal_json_path(self) -> Path:
        return self.state_root / "goal.json"

    @property
    def evidence_manifest_path(self) -> Path:
        return self.state_root / "evidence_manifest.json"

    @property
    def baseline_sketch_path(self) -> Path:
        return self.baseline_dir / "sketch.json"

    @property
    def baseline_extractor_path(self) -> Path:
        return self.baseline_dir / "extractor.json"

    @property
    def baseline_audit_path(self) -> Path:
        return self.baseline_dir / "audit.json"

    @property
    def baseline_publication_path(self) -> Path:
        return self.baseline_dir / "publication_check.json"

    @property
    def baseline_report_path(self) -> Path:
        return self.baseline_dir / "report.md"

    @property
    def archive_jsonl_path(self) -> Path:
        return self.archive_dir / "archive.jsonl"

    @property
    def shortlist_report_path(self) -> Path:
        return self.reports_dir / "shortlist.md"

    @property
    def summary_json_path(self) -> Path:
        return self.reports_dir / "summary.json"

    def candidate_dir(self, hypothesis_id: str) -> Path:
        return self.candidates_dir / hypothesis_id

    def candidate_sketch_path(self, hypothesis_id: str) -> Path:
        return self.candidate_dir(hypothesis_id) / "sketch.json"

    def candidate_pseudocode_path(self, hypothesis_id: str) -> Path:
        return self.candidate_dir(hypothesis_id) / "pseudocode.md"

    def candidate_novelty_path(self, hypothesis_id: str) -> Path:
        return self.candidate_dir(hypothesis_id) / "novelty.md"

    def candidate_comparison_path(self, hypothesis_id: str) -> Path:
        return self.candidate_dir(hypothesis_id) / "comparison.md"

    def new_session_manifest_path(self) -> Path:
        return self.sessions_dir / _now_stamp() / "manifest.json"


def resolve_design_paths(
    project_root: Path,
    *,
    output_root: Path | None = None,
) -> DesignPaths:
    root = (output_root or (project_root / ".fermilink-design")).resolve()
    return DesignPaths(
        project_root=project_root.resolve(),
        state_root=root,
        baseline_dir=root / "baseline",
        archive_dir=root / "archive",
        candidates_dir=root / "candidates",
        reports_dir=root / "reports",
        sessions_dir=root / "sessions",
    )


def ensure_design_dirs(paths: DesignPaths) -> None:
    for path in (
        paths.state_root,
        paths.baseline_dir,
        paths.archive_dir,
        paths.candidates_dir,
        paths.reports_dir,
        paths.sessions_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    ensure_parent(path)
    path.write_text(str(text or ""), encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    lines = [json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
