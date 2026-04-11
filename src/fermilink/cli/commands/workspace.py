from __future__ import annotations

import argparse
import filecmp
from importlib import resources
import json
import os
from pathlib import Path
import shutil
import sys

from fermilink.config import resolve_fermilink_home


def _cli():
    from fermilink import cli

    return cli


_REQUIRED_PAYLOAD_ITEMS = ("README.md", "src")
_PAYLOAD_INTERNAL_NAMES = {"__pycache__"}
_AGENTS_FILENAME = "AGENTS.md"
_AGENTS_ALIAS_FILENAMES = ("CLAUDE.md", "GEMINI.md")
_COPIED_PAYLOAD_DIRECTORIES = {"skills"}
_INIT_TEMPLATE_AGENTS_REL_PATH = Path("src/fermilink/init_template/AGENTS.md")
_HPC_PROFILE_FILENAME = "HPC_PROFILE.json"
_LEGACY_HPC_PROFILE_FILENAME = "hpc_profile.json"
_HPC_PROFILE_REQUIRED_KEYS = (
    "slurm_default_partition",
    "slurm_defaults",
    "slurm_resource_policy",
)
_DEFAULT_HPC_PROFILE_PAYLOAD = {
    "slurm_default_partition": "shared",
    "slurm_defaults": (
        "--nodes=1 --ntasks=1 --ntasks-per-node=1 "
        "--cpus-per-task=1 --time=24:00:00"
    ),
    "slurm_resource_policy": (
        "Use serial/single-node defaults unless the method explicitly "
        "requires MPI or multi-node scaling"
    ),
}


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


def _directories_match(path_a: Path, path_b: Path) -> bool:
    if not path_a.is_dir() or not path_b.is_dir():
        return False
    try:
        entries_a = sorted(path_a.iterdir(), key=lambda p: p.name)
        entries_b = sorted(path_b.iterdir(), key=lambda p: p.name)
    except OSError:
        return False

    if [entry.name for entry in entries_a] != [entry.name for entry in entries_b]:
        return False

    for entry_a, entry_b in zip(entries_a, entries_b):
        if entry_a.is_dir() and entry_b.is_dir():
            if not _directories_match(entry_a, entry_b):
                return False
            continue
        if entry_a.is_file() and entry_b.is_file():
            if not _files_match(entry_a, entry_b):
                return False
            continue
        return False
    return True


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


def _ensure_copied_directory(source_path: Path, target_path: Path, *, force: bool) -> None:
    if not source_path.is_dir():
        raise FileNotFoundError(f"Missing source directory for managed copy: {source_path}")

    if _path_exists(target_path):
        if target_path.is_symlink():
            if _symlink_matches(target_path, source_path):
                _remove_path(target_path)
            elif not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: already exists. "
                    "Use --force to overwrite."
                )
            else:
                _remove_path(target_path)
        elif target_path.is_dir():
            if _directories_match(target_path, source_path):
                return
            if not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: local directory content differs "
                    "from managed copy. Use --force to overwrite."
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
    shutil.copytree(source_path, target_path)


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


def _remove_managed_copied_directory(
    target_path: Path,
    expected_source: Path,
    *,
    force: bool,
) -> None:
    if not _path_exists(target_path):
        return
    if force:
        _remove_path(target_path)
        return

    if target_path.is_symlink():
        if _symlink_matches(target_path, expected_source):
            _remove_path(target_path)
            return
        raise FileExistsError(
            f"Conflict at {target_path}: expected managed copied directory "
            f"or symlink to {expected_source}. Use --force to remove anyway."
        )
    if target_path.is_dir():
        if _directories_match(target_path, expected_source):
            _remove_path(target_path)
            return
        raise FileExistsError(
            f"Conflict at {target_path}: expected managed copied directory content. "
            "Use --force to remove anyway."
        )
    raise FileExistsError(
        f"Conflict at {target_path}: expected managed copied directory. "
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
        if source_path.name in _COPIED_PAYLOAD_DIRECTORIES:
            _ensure_copied_directory(source_path, target_path, force=force)
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
        if source_path.name in _COPIED_PAYLOAD_DIRECTORIES:
            _remove_managed_copied_directory(
                target_path,
                source_path,
                force=force,
            )
            continue
        _remove_managed_symlink(
            target_path,
            source_path,
            force=force,
        )
    if has_agents_entry:
        _remove_agents_aliases(destination, force=force)


def _normalize_hpc_profile_payload(
    raw_profile: dict[str, object], *, profile_label: str
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key in _HPC_PROFILE_REQUIRED_KEYS:
        raw_value = raw_profile.get(key)
        if raw_value is None:
            raise ValueError(
                f"HPC profile missing required `{key}` in {profile_label}."
            )
        if not isinstance(raw_value, str):
            raise ValueError(
                f"HPC profile `{key}` must be a non-empty string in {profile_label}."
            )
        text_value = " ".join(raw_value.strip().split())
        if not text_value:
            raise ValueError(
                f"HPC profile `{key}` must be a non-empty string in {profile_label}."
            )
        normalized[key] = text_value
    return normalized


def _load_hpc_profile_payload(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"HPC profile does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"HPC profile must be a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Failed to read HPC profile: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"HPC profile must contain valid JSON: {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"HPC profile root JSON must be an object: {path}")
    return _normalize_hpc_profile_payload(payload, profile_label=str(path))


def _default_hpc_profile_path() -> Path:
    return resolve_fermilink_home() / _HPC_PROFILE_FILENAME


def _legacy_hpc_profile_path() -> Path:
    return resolve_fermilink_home() / _LEGACY_HPC_PROFILE_FILENAME


def _ensure_default_hpc_profile() -> tuple[Path, bool, bool]:
    destination = _default_hpc_profile_path()
    if destination.is_file():
        return destination, False, False

    legacy = _legacy_hpc_profile_path()
    if legacy.is_file():
        normalized = _load_hpc_profile_payload(legacy)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
        return destination, True, True

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(_DEFAULT_HPC_PROFILE_PAYLOAD, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination, True, False


def _set_hpc_profile(source_file: Path, destination_file: Path | None = None) -> Path:
    normalized = _load_hpc_profile_payload(source_file)
    destination = destination_file or _default_hpc_profile_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    return destination


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


def cmd_hpc(args: argparse.Namespace) -> int:
    cli = _cli()
    hpc_command = str(getattr(args, "hpc_command", "") or "").strip().lower()
    try:
        if not hpc_command:
            _, created, migrated = _ensure_default_hpc_profile()
            home = resolve_fermilink_home()
            canonical_path = home / _HPC_PROFILE_FILENAME
            if created:
                if migrated:
                    print(
                        f"[fermilink-hpc] Migrated legacy {_LEGACY_HPC_PROFILE_FILENAME} "
                        f"to {canonical_path}"
                    )
                else:
                    print(
                        "[fermilink-hpc] Created default HPC profile at "
                        f"{canonical_path}"
                    )
            else:
                print(
                    f"[fermilink-hpc] {canonical_path} already exists. "
                    "No copy was made."
                )
            print(
                "[fermilink-hpc] You can adjust the profile as needed for your "
                "HPC environment."
            )
            return 0

        if hpc_command == "set":
            raw_source = str(getattr(args, "file", "") or "").strip()
            if not raw_source:
                raise ValueError("Please provide a JSON profile path.")
            source = Path(raw_source).expanduser()
            if not source.is_absolute():
                source = (Path.cwd() / source).resolve()
            destination = _set_hpc_profile(source)
            print(f"[fermilink-hpc] Installed profile at {destination}")
            return 0
    except Exception as exc:
        raise cli.PackageError(str(exc)) from exc

    raise cli.PackageError(f"Unknown hpc command: {hpc_command}")


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
