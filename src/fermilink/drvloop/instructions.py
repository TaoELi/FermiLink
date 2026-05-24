from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil

from fermilink.drvloop.prompts import load_drvloop_guide


DRVLOOP_AGENTS_FILENAME = "AGENTS.md"
DRVLOOP_AGENTS_ALIAS_FILENAMES = ("CLAUDE.md", "GEMINI.md")
DRVLOOP_BACKUP_SUFFIX = ".pre-drvloop-backup"


@dataclass(frozen=True)
class DrvloopInstructionFiles:
    agents_path: Path
    alias_paths: tuple[Path, ...]


def materialize_drvloop_instructions(repo_dir: Path) -> DrvloopInstructionFiles:
    """Copy drvloop agent guidance into a workspace without any git setup."""

    repo_dir.mkdir(parents=True, exist_ok=True)
    guide = load_drvloop_guide()
    if not guide.strip():
        raise FileNotFoundError("Missing packaged drvloop AGENTS.md guide.")

    agents_path = repo_dir / DRVLOOP_AGENTS_FILENAME
    _write_authoritative_file(agents_path, guide)

    alias_paths: list[Path] = []
    for alias_name in DRVLOOP_AGENTS_ALIAS_FILENAMES:
        alias_path = repo_dir / alias_name
        _ensure_agents_alias(agents_path, alias_path)
        alias_paths.append(alias_path)

    return DrvloopInstructionFiles(
        agents_path=agents_path,
        alias_paths=tuple(alias_paths),
    )


def _write_authoritative_file(path: Path, content: str) -> None:
    if path.exists() or path.is_symlink():
        existing_text = _safe_read_text(path)
        if not path.is_symlink() and path.is_file() and existing_text == content:
            return
        if path.is_dir() and not path.is_symlink():
            raise FileExistsError(
                f"Cannot write drvloop AGENTS.md over directory: {path}"
            )
        if existing_text and existing_text != content:
            _backup_existing_content(path)
        path.unlink()

    path.write_text(content, encoding="utf-8")


def _ensure_agents_alias(agents_path: Path, alias_path: Path) -> None:
    if alias_path.exists() or alias_path.is_symlink():
        if _symlink_points_to(alias_path, agents_path):
            return
        if alias_path.is_file() and _safe_read_text(alias_path) == _safe_read_text(
            agents_path
        ):
            alias_path.unlink()
        elif alias_path.is_dir() and not alias_path.is_symlink():
            raise FileExistsError(
                f"Cannot write drvloop AGENTS.md alias over directory: {alias_path}"
            )
        else:
            _backup_existing_content(alias_path)
            alias_path.unlink()

    _create_relative_symlink_or_copy(agents_path, alias_path)


def _create_relative_symlink_or_copy(source_path: Path, target_path: Path) -> None:
    relative_target = Path(os.path.relpath(source_path, start=target_path.parent))
    try:
        target_path.symlink_to(relative_target)
    except OSError:
        shutil.copy2(source_path, target_path)


def _symlink_points_to(target_path: Path, source_path: Path) -> bool:
    if not target_path.is_symlink():
        return False
    return (
        target_path.parent / target_path.readlink()
    ).resolve() == source_path.resolve()


def _backup_existing_content(path: Path) -> None:
    backup_path = _next_backup_path(path)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    backup_path.write_text(content, encoding="utf-8")


def _next_backup_path(path: Path) -> Path:
    first = path.with_name(path.name + DRVLOOP_BACKUP_SUFFIX)
    if not first.exists() and not first.is_symlink():
        return first
    index = 1
    while True:
        candidate = path.with_name(f"{path.name}{DRVLOOP_BACKUP_SUFFIX}.{index}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        index += 1


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
