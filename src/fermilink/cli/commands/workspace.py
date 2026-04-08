from __future__ import annotations

import argparse
import filecmp
from importlib import resources
import os
from pathlib import Path
import shutil
import sys


def _cli():
    from fermilink import cli

    return cli


_REQUIRED_PAYLOAD_ITEMS = ("README.md", "src")
_PAYLOAD_INTERNAL_NAMES = {"__pycache__"}
_AGENTS_FILENAME = "AGENTS.md"
_AGENTS_ALIAS_FILENAMES = ("CLAUDE.md", "GEMINI.md")
_INIT_TEMPLATE_AGENTS_REL_PATH = Path("src/fermilink/init_template/AGENTS.md")


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    raise FileNotFoundError(path)


def _symlink_matches(target_path: Path, source_path: Path) -> bool:
    if not target_path.is_symlink():
        return False
    resolved = (target_path.parent / target_path.readlink()).resolve()
    return resolved == source_path.resolve()


def _ensure_symlink(source_path: Path, target_path: Path, force: bool) -> None:
    if not source_path.exists():
        raise FileNotFoundError(f"Missing source path for symlink: {source_path}")

    if _path_exists(target_path):
        if _symlink_matches(target_path, source_path):
            return
        if not force:
            raise FileExistsError(
                f"Conflict at {target_path}: already exists. Use --force to overwrite."
            )
        _remove_path(target_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    relative_target = Path(os.path.relpath(source_path, start=target_path.parent))
    target_path.symlink_to(relative_target, target_is_directory=source_path.is_dir())


def _remove_managed_symlink(
    target_path: Path, expected_source: Path, force: bool = False
) -> None:
    if not _path_exists(target_path):
        return
    if force:
        _remove_path(target_path)
        return
    if _symlink_matches(target_path, expected_source):
        _remove_path(target_path)
        return
    raise FileExistsError(
        f"Conflict at {target_path}: expected symlink to {expected_source}. "
        "Use --force to remove anyway."
    )


def _ensure_agents_aliases(destination: Path, *, force: bool) -> None:
    agents_path = destination / _AGENTS_FILENAME
    if not agents_path.is_file():
        raise FileNotFoundError(
            f"Missing {_AGENTS_FILENAME} in initialized workspace: {agents_path}"
        )
    for alias_name in _AGENTS_ALIAS_FILENAMES:
        _ensure_symlink(agents_path, destination / alias_name, force=force)


def _remove_agents_aliases(destination: Path, *, force: bool) -> None:
    agents_path = destination / _AGENTS_FILENAME
    for alias_name in _AGENTS_ALIAS_FILENAMES:
        _remove_managed_symlink(
            destination / alias_name,
            agents_path,
            force=force,
        )


def _files_match(path_a: Path, path_b: Path) -> bool:
    if not path_a.is_file() or not path_b.is_file():
        return False
    try:
        return filecmp.cmp(path_a, path_b, shallow=False)
    except OSError:
        return False


def _resolve_payload_agents_source(payload_root: Path) -> Path:
    template_source = payload_root / _INIT_TEMPLATE_AGENTS_REL_PATH
    if template_source.is_file():
        return template_source
    fallback = payload_root / _AGENTS_FILENAME
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(
        f"Missing {_AGENTS_FILENAME} template source under payload root {payload_root}"
    )


def _managed_agents_symlink_sources(
    payload_root: Path, agents_source: Path, payload_entry_source: Path
) -> tuple[Path, ...]:
    candidates = [agents_source, payload_entry_source, payload_root / _AGENTS_FILENAME]
    ordered: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        key = candidate.resolve()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return tuple(ordered)


def _ensure_agents_file(
    source_path: Path,
    target_path: Path,
    *,
    force: bool,
    managed_symlink_sources: tuple[Path, ...],
) -> None:
    if not source_path.is_file():
        raise FileNotFoundError(f"Missing source file for {_AGENTS_FILENAME}: {source_path}")

    if _path_exists(target_path):
        if target_path.is_symlink():
            if any(
                _symlink_matches(target_path, source_candidate)
                for source_candidate in managed_symlink_sources
            ):
                _remove_path(target_path)
            elif not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: already exists. "
                    "Use --force to overwrite."
                )
            else:
                _remove_path(target_path)
        elif target_path.is_file():
            if _files_match(target_path, source_path):
                return
            if not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: local file content differs from "
                    f"managed {_AGENTS_FILENAME}. Use --force to overwrite."
                )
            _remove_path(target_path)
        else:
            if not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: already exists. "
                    "Use --force to overwrite."
                )
            _remove_path(target_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)


def _remove_managed_agents_file(
    target_path: Path,
    expected_source: Path,
    *,
    force: bool,
    managed_symlink_sources: tuple[Path, ...],
) -> None:
    if not _path_exists(target_path):
        return
    if force:
        _remove_path(target_path)
        return

    if target_path.is_symlink():
        if any(
            _symlink_matches(target_path, source_candidate)
            for source_candidate in managed_symlink_sources
        ):
            _remove_path(target_path)
            return
        raise FileExistsError(
            f"Conflict at {target_path}: expected managed {_AGENTS_FILENAME} symlink. "
            "Use --force to remove anyway."
        )
    if target_path.is_file():
        if _files_match(target_path, expected_source):
            _remove_path(target_path)
            return
        raise FileExistsError(
            f"Conflict at {target_path}: expected managed {_AGENTS_FILENAME} file "
            "content. Use --force to remove anyway."
        )
    raise FileExistsError(
        f"Conflict at {target_path}: expected managed {_AGENTS_FILENAME} file/symlink. "
        "Use --force to remove anyway."
    )


def _repo_root_fallback() -> Path:
    for parent in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (parent / "pyproject.toml").is_file() and (parent / "src").is_dir():
            return parent
    return Path(__file__).resolve().parents[4]


def _is_valid_payload_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    return all((path / item).exists() for item in _REQUIRED_PAYLOAD_ITEMS)


def _resolve_payload_root() -> Path:
    try:
        candidate = resources.files("fermilink").joinpath("_workspace_payload")
        candidate_path = Path(str(candidate))
    except Exception:
        candidate_path = None

    if candidate_path is not None and _is_valid_payload_root(candidate_path):
        return candidate_path

    repo_root = _repo_root_fallback()
    if _is_valid_payload_root(repo_root):
        return repo_root

    searched: list[str] = []
    if candidate_path is not None:
        searched.append(str(candidate_path))
    searched.append(str(repo_root))
    raise FileNotFoundError(
        "Could not locate FermiLink workspace payload. "
        f"Searched: {', '.join(searched)}"
    )


def _iter_payload_entries(payload_root: Path) -> list[Path]:
    return [
        entry
        for entry in sorted(payload_root.iterdir(), key=lambda p: p.name)
        if entry.name not in _PAYLOAD_INTERNAL_NAMES and not entry.name.startswith(".")
    ]


def initialize_workspace(
    destination: Path, payload_root: Path, force: bool = False
) -> None:
    if not _is_valid_payload_root(payload_root):
        raise FileNotFoundError(
            f"Invalid payload root {payload_root}. "
            f"Expected: {', '.join(_REQUIRED_PAYLOAD_ITEMS)}"
        )

    destination.mkdir(parents=True, exist_ok=True)
    payload_entries = _iter_payload_entries(payload_root)
    has_agents_entry = any(entry.name == _AGENTS_FILENAME for entry in payload_entries)
    agents_source = (
        _resolve_payload_agents_source(payload_root) if has_agents_entry else None
    )
    for source_path in payload_entries:
        target_path = destination / source_path.name
        if source_path.name == _AGENTS_FILENAME:
            if agents_source is None:
                raise FileNotFoundError(
                    f"Missing {_AGENTS_FILENAME} source under payload root {payload_root}"
                )
            _ensure_agents_file(
                agents_source,
                target_path,
                force=force,
                managed_symlink_sources=_managed_agents_symlink_sources(
                    payload_root, agents_source, source_path
                ),
            )
            continue
        _ensure_symlink(source_path, target_path, force=force)
    if has_agents_entry:
        _ensure_agents_aliases(destination, force=force)


def clean_workspace(destination: Path, payload_root: Path, force: bool = False) -> None:
    if not _is_valid_payload_root(payload_root):
        raise FileNotFoundError(
            f"Invalid payload root {payload_root}. "
            f"Expected: {', '.join(_REQUIRED_PAYLOAD_ITEMS)}"
        )
    payload_entries = _iter_payload_entries(payload_root)
    has_agents_entry = any(entry.name == _AGENTS_FILENAME for entry in payload_entries)
    agents_source = (
        _resolve_payload_agents_source(payload_root) if has_agents_entry else None
    )
    for source_path in payload_entries:
        target_path = destination / source_path.name
        if source_path.name == _AGENTS_FILENAME:
            if agents_source is None:
                raise FileNotFoundError(
                    f"Missing {_AGENTS_FILENAME} source under payload root {payload_root}"
                )
            _remove_managed_agents_file(
                target_path,
                agents_source,
                force=force,
                managed_symlink_sources=_managed_agents_symlink_sources(
                    payload_root, agents_source, source_path
                ),
            )
            continue
        _remove_managed_symlink(
            target_path,
            source_path,
            force=force,
        )
    if has_agents_entry:
        _remove_agents_aliases(destination, force=force)


def cmd_init(args: argparse.Namespace) -> int:
    cli = _cli()
    destination = Path(args.destination).expanduser().resolve()
    try:
        payload_root = _resolve_payload_root()
        initialize_workspace(destination, payload_root, force=bool(args.force))
    except Exception as exc:
        raise cli.PackageError(str(exc)) from exc

    print(f"[fermilink-init] Workspace initialized in {destination}")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    cli = _cli()
    destination = Path(args.destination).expanduser().resolve()
    try:
        payload_root = _resolve_payload_root()
        clean_workspace(destination, payload_root, force=bool(args.force))
    except Exception as exc:
        raise cli.PackageError(str(exc)) from exc

    print(f"[fermilink-clean] Workspace cleaned in {destination}")
    return 0


def fermilink_init_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fermilink-init",
        description=(
            "Initialize a destination directory by creating managed workspace "
            "links/files from the installed FermiLink payload."
        ),
    )
    parser.add_argument(
        "destination",
        nargs="?",
        default=".",
        help="Destination directory (default: current directory).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite conflicting files/symlinks in the destination directory.",
    )
    args = parser.parse_args(argv)
    destination = Path(args.destination).expanduser().resolve()

    try:
        payload_root = _resolve_payload_root()
        initialize_workspace(destination, payload_root, force=args.force)
    except Exception as exc:
        print(f"[fermilink-init] ERROR: {exc}", file=sys.stderr)
        return 2

    print("[fermilink-init] Workspace initialized in", destination)
    return 0


def fermilink_clean_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fermilink-clean",
        description=(
            "Clean workspace artifacts created by `fermilink init` / "
            "`fermilink-init`."
        ),
    )
    parser.add_argument(
        "destination",
        nargs="?",
        default=".",
        help="Workspace directory to clean (default: current directory).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Remove conflicting managed paths even when they were modified.",
    )
    args = parser.parse_args(argv)
    destination = Path(args.destination).expanduser().resolve()

    try:
        payload_root = _resolve_payload_root()
        clean_workspace(destination, payload_root, force=args.force)
    except Exception as exc:
        print(f"[fermilink-clean] ERROR: {exc}", file=sys.stderr)
        return 2

    print("[fermilink-clean] Workspace cleaned in", destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(fermilink_init_main())
