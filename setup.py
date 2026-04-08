"""Setuptools build hooks for packaging the FermiLink workspace payload.

This module customizes ``build_py`` so tracked repository files are copied into
``fermilink/_workspace_payload`` in the built wheel. The CLI ``init``/``clean``
commands use this payload when FermiLink is installed from PyPI.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


_FALLBACK_TOP_LEVEL_ITEMS: tuple[str, ...] = (
    "AGENTS.md",
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "src",
    "tests",
    "docs",
    "scripts",
    "bin",
    "skills",
)
_INIT_TEMPLATE_AGENTS_REL_PATH = Path("src/fermilink/init_template/AGENTS.md")
_ALWAYS_INCLUDE_TOP_LEVEL_DIRS: tuple[str, ...] = ("skills",)
_EMPTY_DIR_SENTINEL_FILENAME = "_fermilink_keep"


def _iter_tracked_files(repo_root: Path) -> list[Path]:
    """Return tracked repository files as paths relative to ``repo_root``."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception:
        tracked: list[Path] = []
        for rel in _FALLBACK_TOP_LEVEL_ITEMS:
            source = repo_root / rel
            if source.is_file():
                tracked.append(Path(rel))
                continue
            if source.is_dir():
                for nested in source.rglob("*"):
                    if nested.is_file():
                        tracked.append(nested.relative_to(repo_root))
        return _include_required_top_level_dir_files(repo_root, tracked)

    tracked_paths: list[Path] = []
    for item in completed.stdout.split(b"\0"):
        if not item:
            continue
        rel_path = Path(item.decode("utf-8"))
        tracked_paths.append(rel_path)
    return _include_required_top_level_dir_files(repo_root, tracked_paths)


def _is_hidden_path(rel_path: Path) -> bool:
    return any(part.startswith(".") for part in rel_path.parts)


def _include_required_top_level_dir_files(
    repo_root: Path, rel_paths: list[Path]
) -> list[Path]:
    """Include files under always-included top-level directories."""
    combined: set[Path] = set(rel_paths)
    for top_level in _ALWAYS_INCLUDE_TOP_LEVEL_DIRS:
        source_dir = repo_root / top_level
        if not source_dir.is_dir():
            continue
        for nested in source_dir.rglob("*"):
            if nested.is_file():
                combined.add(nested.relative_to(repo_root))
    return sorted(path for path in combined if not _is_hidden_path(path))


def _resolve_payload_source(repo_root: Path, rel_path: Path) -> Path:
    """Resolve source path used to stage one payload entry."""
    if rel_path == Path("AGENTS.md"):
        template_source = repo_root / _INIT_TEMPLATE_AGENTS_REL_PATH
        if template_source.is_file():
            return template_source
    return repo_root / rel_path


def _copy_workspace_payload(repo_root: Path, payload_root: Path) -> None:
    """Copy tracked files into the wheel build payload directory."""
    if payload_root.exists():
        shutil.rmtree(payload_root)
    payload_root.mkdir(parents=True, exist_ok=True)

    tracked_files = _iter_tracked_files(repo_root)
    if not tracked_files:
        raise FileNotFoundError("No tracked files found for workspace payload.")

    for rel_path in tracked_files:
        source = _resolve_payload_source(repo_root, rel_path)
        if not source.exists():
            raise FileNotFoundError(
                "Missing tracked file for workspace payload: "
                f"{source} (from {rel_path})"
            )
        if source.is_dir():
            continue
        destination = payload_root / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    _materialize_required_top_level_dirs(repo_root, payload_root)


def _materialize_required_top_level_dirs(repo_root: Path, payload_root: Path) -> None:
    """Ensure required top-level payload directories exist, even when empty."""
    for top_level in _ALWAYS_INCLUDE_TOP_LEVEL_DIRS:
        source_dir = repo_root / top_level
        if not source_dir.is_dir():
            continue
        destination_dir = payload_root / top_level
        destination_dir.mkdir(parents=True, exist_ok=True)
        if any(destination_dir.iterdir()):
            continue
        sentinel = destination_dir / _EMPTY_DIR_SENTINEL_FILENAME
        sentinel.write_text(
            "# Keep otherwise-empty payload directory materialized.\n",
            encoding="utf-8",
        )


class build_py(_build_py):
    """Custom ``build_py`` command that stages the workspace payload."""

    def run(self) -> None:
        super().run()
        repo_root = Path(__file__).resolve().parent
        payload_root = Path(self.build_lib) / "fermilink" / "_workspace_payload"
        _copy_workspace_payload(repo_root, payload_root)


setup(
    cmdclass={
        "build_py": build_py,
    }
)
