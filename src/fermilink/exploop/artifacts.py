from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fermilink.exploop.memory import append_to_memory_section
from fermilink.exploop.prompts import (
    EXPLOOP_LEGACY_STATE_DIRNAME,
    EXPLOOP_MEMORY_DIRNAME,
    EXPLOOP_MEMORY_FILENAME,
    EXPLOOP_STATE_DIRNAME,
    EXPLOOP_STATE_FILENAME,
)


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mtime_utc(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def state_path_for(repo_dir: Path) -> Path:
    return repo_dir / EXPLOOP_STATE_DIRNAME / EXPLOOP_STATE_FILENAME


def legacy_state_path_for(repo_dir: Path) -> Path:
    return repo_dir / EXPLOOP_LEGACY_STATE_DIRNAME / EXPLOOP_STATE_FILENAME


def load_state(repo_dir: Path) -> dict[str, Any]:
    path = state_path_for(repo_dir)
    if not path.is_file():
        legacy_path = legacy_state_path_for(repo_dir)
        if legacy_path.is_file():
            path = legacy_path
    if not path.is_file():
        return {"version": 1, "known_artifacts": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "known_artifacts": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "known_artifacts": {}}
    if not isinstance(payload.get("known_artifacts"), dict):
        payload["known_artifacts"] = {}
    payload.setdefault("version", 1)
    return payload


def save_state(repo_dir: Path, state: dict[str, Any]) -> None:
    path = state_path_for(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temp_path.replace(path)


def snapshot_project_artifacts(repo_dir: Path) -> dict[str, dict[str, Any]]:
    projects_dir = repo_dir / EXPLOOP_MEMORY_DIRNAME
    if not projects_dir.is_dir():
        return {}

    memory_rel = f"{EXPLOOP_MEMORY_DIRNAME}/{EXPLOOP_MEMORY_FILENAME}"
    snapshot: dict[str, dict[str, Any]] = {}
    for path in sorted(projects_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(repo_dir).as_posix()
        except ValueError:
            continue
        if rel == memory_rel:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[rel] = {
            "size_bytes": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "modified_utc": _mtime_utc(path),
        }
    return snapshot


def detect_artifact_changes(
    previous: dict[str, Any],
    current: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    known = previous.get("known_artifacts")
    if not isinstance(known, dict):
        known = {}

    changes: list[dict[str, Any]] = []
    for rel, record in sorted(current.items()):
        old = known.get(rel)
        if not isinstance(old, dict):
            changes.append({"path": rel, "status": "new", **record})
            continue
        if old.get("size_bytes") != record.get("size_bytes") or old.get(
            "mtime_ns"
        ) != record.get("mtime_ns"):
            changes.append({"path": rel, "status": "modified", **record})
    return changes


def record_artifact_changes(repo_dir: Path, memory_path: Path) -> list[dict[str, Any]]:
    """Scan `projects/`, append new/modified artifacts to memory, and save state."""

    state = load_state(repo_dir)
    current = snapshot_project_artifacts(repo_dir)
    first_scan = not bool(state.get("last_scan_at_utc"))
    changes = [] if first_scan else detect_artifact_changes(state, current)

    if changes:
        lines = [
            (
                f"- {item['path']} | {item['status']} | "
                f"{item['size_bytes']} bytes | modified {item['modified_utc']}"
            )
            for item in changes
        ]
        append_to_memory_section(
            memory_path,
            heading="### Measurement data inventory",
            lines=lines,
        )
        append_to_memory_section(
            memory_path,
            heading="### Progress log",
            lines=[
                f"- observed {len(changes)} new/modified measurement artifact(s) before agent turn"
            ],
        )

    state["known_artifacts"] = current
    state["last_scan_at_utc"] = _utc_now_z()
    save_state(repo_dir, state)
    return changes
