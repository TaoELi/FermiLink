from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any


REGISTRY_FILENAME = "registry.json"
WORKSPACE_MANIFEST_FILENAME = ".package_manifest.json"
PACKAGE_OVERLAY_ENTRIES_KEY = "overlay_entries"
PACKAGE_DEPENDENCY_IDS_KEY = "dependency_package_ids"
PACKAGE_DEPENDENCIES_DIRNAME = "external_packages"

SKIP_ENTRY_NAMES = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
}
TEMPLATE_RESERVED_ENTRY_NAMES = {"agents.md"}


def normalize_package_id(value: str) -> str:
    cleaned = "".join(
        char.lower() if (char.isalnum() or char in {"-", "_"}) else "-"
        for char in (value or "").strip()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-_")
    if not cleaned:
        raise ValueError("Package id is empty after normalization.")
    return cleaned


def build_default_registry(*, updated_at: str) -> dict[str, Any]:
    return {
        "version": 1,
        "active_package": None,
        "packages": {},
        "updated_at": updated_at,
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp_path.replace(path)


def normalize_registry_payload(
    payload: Any,
    *,
    updated_at: str,
    normalize_package_id: Callable[[str], str],
    dependency_key: str = PACKAGE_DEPENDENCY_IDS_KEY,
    dependency_normalizer: Callable[[Any, str], list[str] | None] | None = None,
    coerce_non_dict_meta_to_empty: bool = True,
    fallback_active_to_first_package: bool = True,
) -> dict[str, Any]:
    registry = build_default_registry(updated_at=updated_at)
    if not isinstance(payload, dict):
        return registry

    packages_raw = payload.get("packages")
    normalized_packages: dict[str, dict[str, Any]] = {}
    if isinstance(packages_raw, dict):
        for raw_id, raw_meta in packages_raw.items():
            try:
                package_id = normalize_package_id(str(raw_id))
            except Exception:
                continue

            if not isinstance(raw_meta, dict):
                if not coerce_non_dict_meta_to_empty:
                    continue
                raw_meta = {}

            meta = dict(raw_meta)
            meta["id"] = package_id

            if dependency_normalizer is not None:
                try:
                    normalized_dependencies = dependency_normalizer(
                        meta.get(dependency_key), package_id
                    )
                except Exception:
                    normalized_dependencies = None
                if normalized_dependencies:
                    meta[dependency_key] = normalized_dependencies
                else:
                    meta.pop(dependency_key, None)

            normalized_packages[package_id] = meta

    registry["packages"] = normalized_packages

    active_id: str | None = None
    active_raw = payload.get("active_package")
    if isinstance(active_raw, str):
        try:
            maybe_active = normalize_package_id(active_raw)
        except Exception:
            maybe_active = None
        if maybe_active and maybe_active in normalized_packages:
            active_id = maybe_active

    if active_id is None and fallback_active_to_first_package and normalized_packages:
        active_id = sorted(normalized_packages.keys())[0]
    registry["active_package"] = active_id

    payload_updated_at = payload.get("updated_at")
    if isinstance(payload_updated_at, str) and payload_updated_at:
        registry["updated_at"] = payload_updated_at

    return registry


def load_registry_file(
    path: Path,
    *,
    default_registry: Callable[[], dict[str, Any]],
    normalize_registry: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    if not path.exists():
        return default_registry()
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default_registry()
    return normalize_registry(payload)


def save_registry_file(
    path: Path,
    payload: Any,
    *,
    updated_at: str,
    normalize_registry: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_registry(payload)
    normalized["updated_at"] = updated_at
    atomic_write_json(path, normalized)
    return normalized


def is_exportable_entry_name(name: str) -> bool:
    if not name:
        return False
    if name.startswith("."):
        return False
    if name in SKIP_ENTRY_NAMES:
        return False
    if name.casefold() in TEMPLATE_RESERVED_ENTRY_NAMES:
        return False
    return True


def extract_manifest_entry_names(manifest: dict[str, Any] | None) -> set[str]:
    names: set[str] = set()
    if not isinstance(manifest, dict):
        return names

    linked = manifest.get("linked_entries")
    if not isinstance(linked, list):
        return names

    for item in linked:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name:
                names.add(name)
        elif isinstance(item, str) and item:
            names.add(item)
    return names


def extract_manifest_dependency_ids(manifest: dict[str, Any] | None) -> set[str]:
    package_ids: set[str] = set()
    if not isinstance(manifest, dict):
        return package_ids

    linked = manifest.get("linked_dependency_packages")
    if not isinstance(linked, list):
        return package_ids

    for item in linked:
        maybe_id: str | None = None
        if isinstance(item, dict):
            raw = item.get("package_id") or item.get("name")
            if isinstance(raw, str) and raw:
                maybe_id = raw
        elif isinstance(item, str) and item:
            maybe_id = item

        if not maybe_id:
            continue

        try:
            package_ids.add(normalize_package_id(maybe_id))
        except ValueError:
            continue
    return package_ids


def remove_existing_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def link_or_copy_entry(src: Path, dst: Path) -> str:
    if dst.is_symlink() or dst.exists():
        return "existing"
    try:
        os.symlink(src.resolve(), dst, target_is_directory=src.is_dir())
        return "symlink"
    except OSError:
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        return "copy"


def remove_managed_entries(
    repo_dir: Path,
    manifest: dict[str, Any] | None,
    *,
    only_names: set[str] | None = None,
    remove_non_symlink_entries: bool = False,
    remove_existing: Callable[[Path], None] = remove_existing_entry,
) -> None:
    if not isinstance(manifest, dict):
        return
    linked = manifest.get("linked_entries")
    if not isinstance(linked, list):
        return

    for item in linked:
        if isinstance(item, dict):
            name = item.get("name")
            mode = item.get("mode", "symlink")
        elif isinstance(item, str):
            name = item
            mode = "symlink"
        else:
            continue

        if not isinstance(name, str) or not name:
            continue
        if only_names is not None and name not in only_names:
            continue

        target = repo_dir / name
        if mode == "symlink" and target.is_symlink():
            target.unlink(missing_ok=True)
        elif mode != "symlink" and remove_non_symlink_entries and target.exists():
            remove_existing(target)


def remove_managed_dependency_links(
    repo_dir: Path,
    manifest: dict[str, Any] | None,
    *,
    normalize_package_id: Callable[[str], str],
    only_package_ids: set[str] | None = None,
    dependencies_dirname: str = PACKAGE_DEPENDENCIES_DIRNAME,
    remove_existing: Callable[[Path], None] = remove_existing_entry,
) -> None:
    if not isinstance(manifest, dict):
        return
    linked = manifest.get("linked_dependency_packages")
    if not isinstance(linked, list):
        return

    dependency_root = repo_dir / dependencies_dirname
    for item in linked:
        package_id: str | None = None
        mode = "symlink"
        if isinstance(item, dict):
            maybe_id = item.get("package_id") or item.get("name")
            if isinstance(maybe_id, str) and maybe_id:
                package_id = maybe_id
            maybe_mode = item.get("mode")
            if isinstance(maybe_mode, str) and maybe_mode:
                mode = maybe_mode
        elif isinstance(item, str):
            package_id = item

        if not package_id:
            continue
        try:
            normalized_id = normalize_package_id(package_id)
        except Exception:
            continue
        if only_package_ids is not None and normalized_id not in only_package_ids:
            continue

        target = dependency_root / normalized_id
        if mode == "symlink" and target.is_symlink():
            target.unlink(missing_ok=True)
        elif mode != "symlink" and target.exists():
            remove_existing(target)

    if dependency_root.is_dir():
        try:
            next(dependency_root.iterdir())
        except StopIteration:
            dependency_root.rmdir()


def overlay_package_into_repo_core(
    *,
    repo_dir: Path,
    workspace_root: Path,
    package_id: str,
    package_meta: dict[str, Any],
    allow_replace_existing: bool,
    now_iso: Callable[[], str],
    resolve_package_meta_path: Callable[[dict[str, Any]], Path],
    normalize_overlay_entries: Callable[[Any], list[str] | None],
    normalize_dependency_ids: Callable[..., list[str] | None],
    iter_package_entries: Callable[
        [Path, list[str] | None], tuple[list[Path], list[str]]
    ],
    load_workspace_manifest: Callable[[Path], dict[str, Any] | None],
    save_workspace_manifest: Callable[[Path, dict[str, Any]], None],
    normalize_package_id: Callable[[str], str],
    get_package_map: Callable[[str, dict[str, Any]], dict[str, Any]],
    replace_existing_entries_for_previous_names: bool,
    remove_non_symlink_managed_entries: bool,
    overlay_entries_key: str = PACKAGE_OVERLAY_ENTRIES_KEY,
    dependency_ids_key: str = PACKAGE_DEPENDENCY_IDS_KEY,
    dependencies_dirname: str = PACKAGE_DEPENDENCIES_DIRNAME,
    remove_existing: Callable[[Path], None] = remove_existing_entry,
    link_or_copy: Callable[[Path, Path], str] = link_or_copy_entry,
) -> dict[str, Any]:
    package_root = resolve_package_meta_path(package_meta)
    configured_entries = normalize_overlay_entries(
        package_meta.get(overlay_entries_key)
    )
    configured_dependency_ids = (
        normalize_dependency_ids(
            package_meta.get(dependency_ids_key), package_id=package_id
        )
        or []
    )

    entries, missing_requested_entries = iter_package_entries(
        package_root,
        include_names=configured_entries,
    )
    target_entry_names = {entry.name for entry in entries}

    previous_manifest = load_workspace_manifest(workspace_root)
    previous_id: str | None = None
    if isinstance(previous_manifest, dict):
        previous_raw = previous_manifest.get("package_id")
        if isinstance(previous_raw, str):
            try:
                previous_id = normalize_package_id(previous_raw)
            except Exception:
                previous_id = None

    if previous_id is not None and previous_id != package_id:
        remove_managed_entries(
            repo_dir,
            previous_manifest,
            remove_non_symlink_entries=remove_non_symlink_managed_entries,
            remove_existing=remove_existing,
        )
        remove_managed_dependency_links(
            repo_dir,
            previous_manifest,
            normalize_package_id=normalize_package_id,
            dependencies_dirname=dependencies_dirname,
            remove_existing=remove_existing,
        )
    elif previous_id == package_id:
        stale_names = (
            extract_manifest_entry_names(previous_manifest) - target_entry_names
        )
        if stale_names:
            remove_managed_entries(
                repo_dir,
                previous_manifest,
                only_names=stale_names,
                remove_non_symlink_entries=remove_non_symlink_managed_entries,
                remove_existing=remove_existing,
            )

        stale_dependency_ids = extract_manifest_dependency_ids(previous_manifest) - set(
            configured_dependency_ids
        )
        if stale_dependency_ids:
            remove_managed_dependency_links(
                repo_dir,
                previous_manifest,
                normalize_package_id=normalize_package_id,
                only_package_ids=stale_dependency_ids,
                dependencies_dirname=dependencies_dirname,
                remove_existing=remove_existing,
            )

    previous_names = extract_manifest_entry_names(previous_manifest)
    previous_dependency_ids = extract_manifest_dependency_ids(previous_manifest)
    linked_entries: list[dict[str, str]] = []
    collisions: list[str] = []

    for src in entries:
        dst = repo_dir / src.name
        if dst.is_symlink():
            try:
                same_target = dst.resolve() == src.resolve()
            except OSError:
                same_target = False

            if same_target:
                linked_entries.append(
                    {
                        "name": src.name,
                        "mode": "symlink",
                        "source": str(src.resolve()),
                    }
                )
                continue

            if src.name in previous_names or allow_replace_existing:
                dst.unlink(missing_ok=True)
            else:
                collisions.append(src.name)
                continue
        elif dst.exists():
            if allow_replace_existing or (
                replace_existing_entries_for_previous_names
                and src.name in previous_names
            ):
                remove_existing(dst)
            else:
                collisions.append(src.name)
                continue

        mode = link_or_copy(src, dst)
        if mode == "existing":
            collisions.append(src.name)
            continue
        linked_entries.append(
            {"name": src.name, "mode": mode, "source": str(src.resolve())}
        )

    package_map = get_package_map(package_id, package_meta)
    if not isinstance(package_map, dict):
        package_map = {}
    if package_id not in package_map:
        package_map = dict(package_map)
        package_map[package_id] = package_meta

    linked_dependency_packages: list[dict[str, str]] = []
    missing_dependency_packages: list[str] = []
    dependency_collisions: list[str] = []

    dependency_root = repo_dir / dependencies_dirname
    dependency_root_ready = True
    if configured_dependency_ids:
        if dependency_root.exists() and not dependency_root.is_dir():
            if allow_replace_existing:
                remove_existing(dependency_root)
            else:
                dependency_root_ready = False
                dependency_collisions.extend(configured_dependency_ids)
        if dependency_root_ready:
            dependency_root.mkdir(parents=True, exist_ok=True)

    for dependency_id in configured_dependency_ids:
        if not dependency_root_ready:
            break
        dependency_meta = package_map.get(dependency_id)
        dependency_dst = dependency_root / dependency_id
        if not isinstance(dependency_meta, dict):
            missing_dependency_packages.append(dependency_id)
            if dependency_id in previous_dependency_ids:
                remove_existing(dependency_dst)
            continue
        try:
            dependency_src = resolve_package_meta_path(dependency_meta)
        except Exception:
            missing_dependency_packages.append(dependency_id)
            if dependency_id in previous_dependency_ids:
                remove_existing(dependency_dst)
            continue

        if dependency_dst.is_symlink():
            try:
                same_target = dependency_dst.resolve() == dependency_src.resolve()
            except OSError:
                same_target = False
            if same_target:
                linked_dependency_packages.append(
                    {
                        "package_id": dependency_id,
                        "mode": "symlink",
                        "source": str(dependency_src.resolve()),
                    }
                )
                continue
            if dependency_id in previous_dependency_ids or allow_replace_existing:
                dependency_dst.unlink(missing_ok=True)
            else:
                dependency_collisions.append(dependency_id)
                continue
        elif dependency_dst.exists():
            if dependency_id in previous_dependency_ids or allow_replace_existing:
                remove_existing(dependency_dst)
            else:
                dependency_collisions.append(dependency_id)
                continue

        mode = link_or_copy(dependency_src, dependency_dst)
        if mode == "existing":
            dependency_collisions.append(dependency_id)
            continue
        linked_dependency_packages.append(
            {
                "package_id": dependency_id,
                "mode": mode,
                "source": str(dependency_src.resolve()),
            }
        )

    if dependency_root.is_dir():
        try:
            next(dependency_root.iterdir())
        except StopIteration:
            dependency_root.rmdir()

    manifest = {
        "version": 1,
        "package_id": package_id,
        "package_path": str(package_root.resolve()),
        "requested_entries": configured_entries,
        "missing_requested_entries": missing_requested_entries,
        "linked_entries": linked_entries,
        "collisions": collisions,
        "configured_dependency_package_ids": configured_dependency_ids,
        "linked_dependency_packages": linked_dependency_packages,
        "missing_dependency_packages": sorted(set(missing_dependency_packages)),
        "dependency_collisions": sorted(set(dependency_collisions)),
        "updated_at": now_iso(),
    }
    save_workspace_manifest(workspace_root, manifest)

    return {
        "package_id": package_id,
        "package_path": str(package_root.resolve()),
        "linked_count": len(linked_entries),
        "collision_count": len(collisions),
        "collisions": collisions,
        "requested_entries": configured_entries,
        "missing_requested_entries": missing_requested_entries,
        "dependency_package_ids": configured_dependency_ids,
        "linked_dependency_count": len(linked_dependency_packages),
        "linked_dependency_packages": linked_dependency_packages,
        "missing_dependency_packages": sorted(set(missing_dependency_packages)),
        "dependency_collision_count": len(set(dependency_collisions)),
        "dependency_collisions": sorted(set(dependency_collisions)),
    }
