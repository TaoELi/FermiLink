from __future__ import annotations

import os
from pathlib import Path


def _resolve_path(raw: str | None, *, default: Path) -> Path:
    if raw:
        path = Path(raw).expanduser()
    else:
        path = default
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _first_env(*keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return None


def resolve_fermilink_home() -> Path:
    """Resolve global FermiLink home root and ensure it exists.

    Defaults to ``~/.fermilink`` when ``FERMILINK_HOME`` is unset.
    """

    root = _resolve_path(
        os.getenv("FERMILINK_HOME"),
        default=Path.home() / ".fermilink",
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_scipkg_root() -> Path:
    """Resolve scientific-package storage root and ensure it exists."""

    raw = _first_env("SCIPKG_ROOT", "SCIENTIFIC_PACKAGES_ROOT")
    if raw:
        root = _resolve_path(raw, default=Path.cwd())
    else:
        root = resolve_fermilink_home() / "scientific_packages"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_workspaces_root() -> Path:
    """Resolve workspaces root and ensure it exists."""

    raw = os.getenv("WORKSPACES_ROOT")
    if raw and raw.strip():
        root = _resolve_path(raw, default=Path.cwd())
    else:
        root = resolve_fermilink_home() / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_runtime_root() -> Path:
    """Resolve runtime state root for daemonized services."""

    raw = os.getenv("FERMILINK_RUNTIME_ROOT")
    if raw and raw.strip():
        root = _resolve_path(raw, default=Path.cwd())
    else:
        root = resolve_fermilink_home() / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root
