from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from fermilink.workspace import filesystem as workspace_fs
from fermilink.workspace import payload as workspace_payload
from fermilink.workspace.common import (
    _cli,
    load_runner_scipkg_module,
    resolve_cli_path,
    resolve_exploop_agents_source,
    resolve_software_agents_source,
)


PACKAGE_INIT_WORKSPACE_MODE = "package_init"
PACKAGE_WORKFLOW_TYPE_KEY = "workflow-type"
PACKAGE_WORKFLOW_TYPE_COMPAT_KEY = "workflow_type"
DEFAULT_PACKAGE_WORKFLOW_TYPE = "simulation"
SUPPORTED_PACKAGE_WORKFLOW_TYPES = {"simulation", "experiment"}
PACKAGE_INIT_COPIED_OVERLAY_DIRECTORIES = {"skills"}


def _looks_like_path_spec(raw_value: str) -> bool:
    if raw_value in {".", ".."}:
        return True
    if raw_value.startswith("~"):
        return True
    if "/" in raw_value or "\\" in raw_value:
        return True

    candidate = Path(raw_value).expanduser()
    return candidate.is_absolute() or candidate.exists()


def _resolve_package_init_target(raw_value: str | None) -> str | None:
    if not isinstance(raw_value, str):
        return None
    trimmed = raw_value.strip()
    if not trimmed or _looks_like_path_spec(trimmed):
        return None

    scipkg = load_runner_scipkg_module()
    try:
        normalized = scipkg.normalize_package_id(trimmed)
    except Exception:
        return None

    registry = scipkg.load_registry(scipkg.resolve_scipkg_root())
    packages = registry.get("packages", {})
    if not isinstance(packages, dict):
        return None
    if not isinstance(packages.get(normalized), dict):
        return None
    return normalized


def _resolve_init_request(
    args: argparse.Namespace,
) -> tuple[str | None, Path]:
    primary_raw = str(getattr(args, "init_target", "") or "").strip()
    secondary_raw = str(getattr(args, "destination", "") or "").strip()

    if not primary_raw:
        return None, resolve_cli_path(".")

    package_id = _resolve_package_init_target(primary_raw)
    if secondary_raw:
        if package_id is None:
            raise ValueError(
                "A second positional argument is only supported for "
                "`fermilink init <pkg-id> <destination>`."
            )
        return package_id, resolve_cli_path(secondary_raw)

    if package_id is not None:
        return package_id, resolve_cli_path(".")
    return None, resolve_cli_path(primary_raw)


def _resolve_package_root(
    package_meta: dict[str, Any],
) -> Path:
    raw = package_meta.get("installed_path")
    if not isinstance(raw, str) or not raw.strip():
        raise FileNotFoundError("Package metadata is missing installed_path.")

    package_root = Path(raw).expanduser()
    if not package_root.is_absolute():
        package_root = (Path.cwd() / package_root).resolve()
    if not package_root.exists() or not package_root.is_dir():
        raise FileNotFoundError(f"Installed package path is invalid: {package_root}")
    return package_root


def _resolve_package_workflow_type(package_meta: dict[str, Any]) -> str:
    raw = package_meta.get(PACKAGE_WORKFLOW_TYPE_KEY)
    if raw is None:
        raw = package_meta.get(PACKAGE_WORKFLOW_TYPE_COMPAT_KEY)
    value = str(raw or DEFAULT_PACKAGE_WORKFLOW_TYPE).strip().lower()
    if not value:
        value = DEFAULT_PACKAGE_WORKFLOW_TYPE
    if value not in SUPPORTED_PACKAGE_WORKFLOW_TYPES:
        supported = ", ".join(sorted(SUPPORTED_PACKAGE_WORKFLOW_TYPES))
        raise ValueError(
            f"Unsupported package workflow type '{value}'. Supported values: {supported}"
        )
    return value


def _resolve_package_agents_source(package_meta: dict[str, Any]) -> Path:
    workflow_type = _resolve_package_workflow_type(package_meta)
    if workflow_type == "experiment":
        return resolve_exploop_agents_source()
    return resolve_software_agents_source()


def _package_init_reserved_entry_names() -> set[str]:
    return {
        workspace_payload.AGENTS_FILENAME,
        *workspace_payload.AGENTS_ALIAS_FILENAMES,
        "public",
    }


def _iter_package_init_entries(package_meta: dict[str, Any]) -> list[Path]:
    scipkg = load_runner_scipkg_module()
    package_root = _resolve_package_root(package_meta)
    reserved_names = _package_init_reserved_entry_names()

    configured_entries = scipkg._normalize_overlay_entries(
        package_meta.get("overlay_entries")
    )
    selected_entries, _ = scipkg.iter_package_entries(
        package_root,
        include_names=configured_entries,
    )
    return [entry for entry in selected_entries if entry.name not in reserved_names]


def _is_package_init_copied_overlay_entry(entry: Path) -> bool:
    return entry.name in PACKAGE_INIT_COPIED_OVERLAY_DIRECTORIES and entry.is_dir()


def _resolve_package_init_copied_overlay_sources(
    package_meta: dict[str, Any],
) -> list[Path]:
    return [
        entry
        for entry in _iter_package_init_entries(package_meta)
        if _is_package_init_copied_overlay_entry(entry)
    ]


def _filter_package_init_meta(
    *,
    package_meta: dict[str, Any],
) -> dict[str, Any]:
    scipkg = load_runner_scipkg_module()
    package_root = _resolve_package_root(package_meta)
    reserved_names = _package_init_reserved_entry_names()
    copied_entry_names = {
        entry.name
        for entry in _iter_package_init_entries(package_meta)
        if _is_package_init_copied_overlay_entry(entry)
    }

    configured_entries = scipkg._normalize_overlay_entries(
        package_meta.get("overlay_entries")
    )
    if configured_entries is None:
        selected_entries, _ = scipkg.iter_package_entries(
            package_root, include_names=None
        )
        filtered_entries = [
            entry.name
            for entry in selected_entries
            if entry.name not in reserved_names
            and entry.name not in copied_entry_names
        ]
    else:
        filtered_entries = [
            entry_name
            for entry_name in configured_entries
            if entry_name not in reserved_names
            and entry_name not in copied_entry_names
        ]

    sanitized = dict(package_meta)
    sanitized["overlay_entries"] = filtered_entries
    sanitized.setdefault("installed_path", str(package_root))
    return sanitized


def _preflight_package_init_copied_overlay_collisions(
    *,
    destination: Path,
    copied_entry_sources: list[Path],
    managed_entry_names: set[str],
) -> None:
    for source_path in copied_entry_sources:
        target_path = destination / source_path.name
        if not workspace_fs.path_exists(target_path):
            continue
        if target_path.is_symlink():
            if workspace_fs.symlink_matches(target_path, source_path):
                continue
            if source_path.name in managed_entry_names:
                continue
            raise FileExistsError(
                f"Conflict at {target_path}: already exists. Use --force to overwrite."
            )
        if target_path.is_dir():
            if workspace_fs.directories_match(target_path, source_path):
                continue
            raise FileExistsError(
                f"Conflict at {target_path}: local directory content differs "
                "from managed copy. Use --force to overwrite."
            )
        raise FileExistsError(
            f"Conflict at {target_path}: already exists. Use --force to overwrite."
        )


def _record_package_init_copied_overlay_entries(
    manifest: dict[str, object],
    copied_entry_sources: list[Path],
) -> None:
    if not copied_entry_sources:
        return

    copied_names = {source.name for source in copied_entry_sources}
    existing_linked = manifest.get("linked_entries")
    linked_entries: list[object] = []
    if isinstance(existing_linked, list):
        for item in existing_linked:
            if isinstance(item, dict):
                name = item.get("name")
            else:
                name = item
            if name not in copied_names:
                linked_entries.append(item)

    for source_path in copied_entry_sources:
        linked_entries.append(
            {
                "name": source_path.name,
                "mode": "copy",
                "source": str(source_path.resolve()),
            }
        )
    manifest["linked_entries"] = linked_entries

    requested_entries = manifest.get("requested_entries")
    if isinstance(requested_entries, list):
        for source_path in copied_entry_sources:
            if source_path.name not in requested_entries:
                requested_entries.append(source_path.name)


def _materialize_package_init_copied_overlays(
    *,
    destination: Path,
    copied_entry_sources: list[Path],
    force: bool,
    managed_entry_names: set[str],
) -> None:
    for source_path in copied_entry_sources:
        target_path = destination / source_path.name
        if (
            target_path.is_symlink()
            and source_path.name in managed_entry_names
            and not workspace_fs.symlink_matches(target_path, source_path)
        ):
            workspace_fs.remove_path(target_path)
        workspace_fs.ensure_copied_directory(
            source_path,
            target_path,
            force=force,
        )


def _preflight_package_workspace_collision(
    *,
    destination: Path,
    package_id: str,
    package_meta: dict[str, Any],
    scipkg_root: Path,
) -> None:
    scipkg = load_runner_scipkg_module()
    previous_manifest = scipkg.load_workspace_manifest(destination)
    previous_names = scipkg._manifest_entry_names(previous_manifest)
    previous_dependency_ids = scipkg._manifest_dependency_ids(previous_manifest)

    package_root = _resolve_package_root(package_meta)
    configured_entries = scipkg._normalize_overlay_entries(
        package_meta.get("overlay_entries")
    )
    entries, _ = scipkg.iter_package_entries(
        package_root, include_names=configured_entries
    )
    for source_path in entries:
        target_path = destination / source_path.name
        if target_path.is_symlink():
            try:
                if target_path.resolve() == source_path.resolve():
                    continue
            except OSError:
                pass
            if source_path.name in previous_names:
                continue
            raise FileExistsError(
                f"Conflict at {target_path}: already exists. Use --force to overwrite."
            )
        if workspace_fs.path_exists(target_path):
            raise FileExistsError(
                f"Conflict at {target_path}: already exists. Use --force to overwrite."
            )

    dependency_ids = (
        scipkg._normalize_dependency_package_ids(
            package_meta.get("dependency_package_ids"),
            package_id=package_id,
        )
        or []
    )
    dependency_root = destination / scipkg.PACKAGE_DEPENDENCIES_DIRNAME
    if dependency_ids and dependency_root.exists() and not dependency_root.is_dir():
        raise FileExistsError(
            f"Conflict at {dependency_root}: already exists. Use --force to overwrite."
        )

    registry = scipkg.load_registry(scipkg_root)
    package_map = registry.get("packages", {})
    if not isinstance(package_map, dict):
        package_map = {}
    if package_id not in package_map:
        package_map = dict(package_map)
        package_map[package_id] = package_meta

    for dependency_id in dependency_ids:
        dependency_meta = package_map.get(dependency_id)
        if not isinstance(dependency_meta, dict):
            continue
        dependency_source = _resolve_package_root(dependency_meta)
        dependency_target = dependency_root / dependency_id
        if dependency_target.is_symlink():
            try:
                if dependency_target.resolve() == dependency_source.resolve():
                    continue
            except OSError:
                pass
            if dependency_id in previous_dependency_ids:
                continue
            raise FileExistsError(
                f"Conflict at {dependency_target}: already exists. Use --force to overwrite."
            )
        if workspace_fs.path_exists(dependency_target):
            raise FileExistsError(
                f"Conflict at {dependency_target}: already exists. Use --force to overwrite."
            )


def initialize_package_workspace(
    destination: Path,
    package_id: str,
    *,
    force: bool = False,
) -> dict[str, object]:
    scipkg = load_runner_scipkg_module()
    scipkg_root = scipkg.resolve_scipkg_root()

    destination.mkdir(parents=True, exist_ok=True)
    resolved_id, package_meta = scipkg.resolve_session_package(
        scipkg_root=scipkg_root,
        workspace_root=destination,
        requested_package_id=package_id,
    )
    if not resolved_id or not isinstance(package_meta, dict):
        raise FileNotFoundError(f"Requested package not found: {package_id}")
    agents_source = _resolve_package_agents_source(package_meta)
    workflow_type = _resolve_package_workflow_type(package_meta)
    copied_entry_sources = _resolve_package_init_copied_overlay_sources(package_meta)
    previous_manifest = scipkg.load_workspace_manifest(destination)
    previous_entry_names = scipkg._manifest_entry_names(previous_manifest)

    filtered_package_meta = _filter_package_init_meta(
        package_meta=package_meta,
    )
    if not force:
        _preflight_package_workspace_collision(
            destination=destination,
            package_id=resolved_id,
            package_meta=filtered_package_meta,
            scipkg_root=scipkg_root,
        )
        _preflight_package_init_copied_overlay_collisions(
            destination=destination,
            copied_entry_sources=copied_entry_sources,
            managed_entry_names=previous_entry_names,
        )

    workspace_payload.ensure_agents_file(
        agents_source,
        destination / workspace_payload.AGENTS_FILENAME,
        force=force,
        managed_symlink_sources=(agents_source,),
    )
    workspace_payload.ensure_agents_aliases(destination, force=force)

    overlay = scipkg.overlay_package_into_repo(
        repo_dir=destination,
        workspace_root=destination,
        package_id=resolved_id,
        package_meta=filtered_package_meta,
        scipkg_root=scipkg_root,
        allow_replace_existing=force,
    )
    _materialize_package_init_copied_overlays(
        destination=destination,
        copied_entry_sources=copied_entry_sources,
        force=force,
        managed_entry_names=previous_entry_names,
    )

    # Re-assert local instruction files in case the overlaid package exports
    # reserved instruction filenames that were already present in the workspace.
    workspace_payload.ensure_agents_file(
        agents_source,
        destination / workspace_payload.AGENTS_FILENAME,
        force=force,
        managed_symlink_sources=(agents_source,),
    )
    workspace_payload.ensure_agents_aliases(destination, force=force)

    manifest = scipkg.load_workspace_manifest(destination)
    if not isinstance(manifest, dict):
        raise RuntimeError(
            f"Failed to persist package workspace manifest for {resolved_id}."
        )
    _record_package_init_copied_overlay_entries(manifest, copied_entry_sources)
    manifest["workspace_mode"] = PACKAGE_INIT_WORKSPACE_MODE
    manifest["template_agents_source"] = str(agents_source.resolve())
    manifest["package_workflow_type"] = workflow_type
    scipkg.save_workspace_manifest(destination, manifest)
    linked_entries = manifest.get("linked_entries")
    if isinstance(linked_entries, list):
        overlay["linked_count"] = len(linked_entries)
    requested_entries = manifest.get("requested_entries")
    if isinstance(requested_entries, list):
        overlay["requested_entries"] = list(requested_entries)
    return overlay


def initialize_workspace(
    destination: Path, payload_root: Path, force: bool = False
) -> None:
    workspace_payload.ensure_valid_payload_root(payload_root)

    destination.mkdir(parents=True, exist_ok=True)
    payload_entries = workspace_payload.iter_payload_entries(payload_root)
    has_agents_entry = any(
        entry.name == workspace_payload.AGENTS_FILENAME for entry in payload_entries
    )
    agents_source = (
        workspace_payload.resolve_payload_agents_source(payload_root)
        if has_agents_entry
        else None
    )
    for source_path in payload_entries:
        target_path = destination / source_path.name
        if source_path.name == workspace_payload.AGENTS_FILENAME:
            if agents_source is None:
                raise FileNotFoundError(
                    f"Missing {workspace_payload.AGENTS_FILENAME} source under payload root {payload_root}"
                )
            workspace_payload.ensure_agents_file(
                agents_source,
                target_path,
                force=force,
                managed_symlink_sources=workspace_payload.managed_agents_symlink_sources(
                    payload_root, agents_source, source_path
                ),
            )
            continue
        if source_path.name in workspace_payload.COPIED_PAYLOAD_DIRECTORIES:
            workspace_fs.ensure_copied_directory(source_path, target_path, force=force)
            continue
        workspace_fs.ensure_symlink(source_path, target_path, force=force)
    if has_agents_entry:
        workspace_payload.ensure_agents_aliases(destination, force=force)


def cmd_init(args: argparse.Namespace) -> int:
    cli = _cli()
    try:
        package_id, destination = _resolve_init_request(args)
        if package_id is None:
            payload_root = workspace_payload.resolve_payload_root()
            initialize_workspace(destination, payload_root, force=bool(args.force))
        else:
            initialize_package_workspace(
                destination,
                package_id,
                force=bool(args.force),
            )
    except Exception as exc:
        raise cli.PackageError(str(exc)) from exc

    print(f"[fermilink-init] Workspace initialized in {destination}")
    print(
        "[fermilink-init] You can run `fermilink clean` to remove the workspace links/files if needed."
    )
    return 0


def fermilink_init_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fermilink-init",
        description=(
            "Initialize a destination directory from the installed FermiLink "
            "payload, or initialize a package-linked workspace via "
            "`fermilink-init <pkg-id>`."
        ),
    )
    parser.add_argument(
        "init_target",
        nargs="?",
        default=".",
        help=(
            "Destination directory for classic init, or an installed package id "
            "for package-linked init."
        ),
    )
    parser.add_argument(
        "destination",
        nargs="?",
        default=None,
        help="Optional destination directory for `fermilink-init <pkg-id> <destination>`.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite conflicting files/symlinks in the destination directory.",
    )
    args = parser.parse_args(argv)

    try:
        package_id, destination = _resolve_init_request(args)
        if package_id is None:
            payload_root = workspace_payload.resolve_payload_root()
            initialize_workspace(destination, payload_root, force=bool(args.force))
        else:
            initialize_package_workspace(
                destination,
                package_id,
                force=bool(args.force),
            )
    except Exception as exc:
        print(f"[fermilink-init] ERROR: {exc}", file=sys.stderr)
        return 2

    print("[fermilink-init] Workspace initialized in", destination)
    print(
        "[fermilink-init] You can run `fermilink clean` to remove the workspace links/files if needed."
    )
    return 0
