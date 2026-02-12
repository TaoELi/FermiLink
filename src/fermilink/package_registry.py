from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
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
REMOVED_INSTRUCTION_FILENAMES = {"agents.md", "claude.md"}
REMOVED_ROOT_DIRECTORIES = {"projects"}


class PackageError(RuntimeError):
    """Base package-management error."""


class PackageNotFoundError(PackageError):
    """Raised when a package id cannot be found."""


class PackageValidationError(PackageError):
    """Raised when package metadata is invalid."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_package_id(value: str) -> str:
    cleaned = "".join(
        char.lower() if (char.isalnum() or char in {"-", "_"}) else "-"
        for char in (value or "").strip()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-_")
    if not cleaned:
        raise PackageValidationError("Package id is empty after normalization.")
    return cleaned


def packages_root(scipkg_root: Path) -> Path:
    root = scipkg_root / "packages"
    root.mkdir(parents=True, exist_ok=True)
    return root


def registry_path(scipkg_root: Path) -> Path:
    return scipkg_root / REGISTRY_FILENAME


def workspace_manifest_path(workspace_root: Path) -> Path:
    return workspace_root / WORKSPACE_MANIFEST_FILENAME


def _default_registry() -> dict[str, Any]:
    return {
        "version": 1,
        "active_package": None,
        "packages": {},
        "updated_at": _now_iso(),
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp_path.replace(path)


def _normalize_registry(payload: Any) -> dict[str, Any]:
    registry = _default_registry()
    if not isinstance(payload, dict):
        return registry

    packages_raw = payload.get("packages")
    normalized_packages: dict[str, dict[str, Any]] = {}
    if isinstance(packages_raw, dict):
        for raw_id, raw_meta in packages_raw.items():
            try:
                package_id = normalize_package_id(str(raw_id))
            except PackageValidationError:
                continue
            if not isinstance(raw_meta, dict):
                raw_meta = {}
            meta = dict(raw_meta)
            meta["id"] = package_id
            normalized_packages[package_id] = meta

    registry["packages"] = normalized_packages

    active_raw = payload.get("active_package")
    if isinstance(active_raw, str):
        try:
            active_id = normalize_package_id(active_raw)
        except PackageValidationError:
            active_id = None
    else:
        active_id = None

    if active_id and active_id in normalized_packages:
        registry["active_package"] = active_id
    elif normalized_packages:
        registry["active_package"] = sorted(normalized_packages.keys())[0]
    else:
        registry["active_package"] = None

    updated_at = payload.get("updated_at")
    if isinstance(updated_at, str) and updated_at:
        registry["updated_at"] = updated_at
    return registry


def load_registry(scipkg_root: Path) -> dict[str, Any]:
    path = registry_path(scipkg_root)
    if not path.exists():
        return _default_registry()

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _default_registry()
    return _normalize_registry(payload)


def save_registry(scipkg_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_registry(payload)
    normalized["updated_at"] = _now_iso()
    _atomic_write_json(registry_path(scipkg_root), normalized)
    return normalized


def list_packages(scipkg_root: Path) -> dict[str, Any]:
    return load_registry(scipkg_root).get("packages", {})


def _resolve_meta_installed_path(package_meta: dict[str, Any]) -> Path:
    raw_path = package_meta.get("installed_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise PackageValidationError("Package metadata is missing installed_path.")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists() or not path.is_dir():
        raise PackageValidationError(f"Installed package path is invalid: {path}")
    return path


def register_package(
    scipkg_root: Path,
    package_id: str,
    *,
    installed_path: Path,
    source: str,
    title: str | None = None,
    activate: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_id = normalize_package_id(package_id)
    resolved_path = installed_path.expanduser().resolve()
    if not resolved_path.exists() or not resolved_path.is_dir():
        raise PackageValidationError(f"Installed path is invalid: {resolved_path}")

    registry = load_registry(scipkg_root)
    packages = registry.setdefault("packages", {})
    current_meta = packages.get(normalized_id)
    if not isinstance(current_meta, dict):
        current_meta = {}

    meta = dict(current_meta)
    meta.update(
        {
            "id": normalized_id,
            "title": title or current_meta.get("title") or normalized_id,
            "installed_path": str(resolved_path),
            "source": source,
            "status": "installed",
            "updated_at": _now_iso(),
        }
    )
    if "installed_at" not in meta:
        meta["installed_at"] = _now_iso()
    if extra:
        meta.update(extra)

    packages[normalized_id] = meta

    if activate:
        registry["active_package"] = normalized_id
    elif registry.get("active_package") not in packages:
        registry["active_package"] = normalized_id

    save_registry(scipkg_root, registry)
    return meta


def activate_package(scipkg_root: Path, package_id: str) -> dict[str, Any]:
    normalized_id = normalize_package_id(package_id)
    registry = load_registry(scipkg_root)
    packages = registry.get("packages", {})
    meta = packages.get(normalized_id)
    if not isinstance(meta, dict):
        raise PackageNotFoundError(f"Package not found: {normalized_id}")
    registry["active_package"] = normalized_id
    save_registry(scipkg_root, registry)
    return meta


def delete_package(
    scipkg_root: Path,
    package_id: str,
    *,
    remove_files: bool = True,
) -> dict[str, Any]:
    normalized_id = normalize_package_id(package_id)
    registry = load_registry(scipkg_root)
    packages = registry.get("packages", {})
    package_meta = packages.get(normalized_id)
    if not isinstance(package_meta, dict):
        raise PackageNotFoundError(f"Package not found: {normalized_id}")

    removed_files = False
    skipped_reason: str | None = None

    installed_path: Path | None = None
    raw_path = package_meta.get("installed_path")
    if isinstance(raw_path, str) and raw_path:
        installed_path = Path(raw_path).expanduser()
        if not installed_path.is_absolute():
            installed_path = Path.cwd() / installed_path

    if remove_files:
        managed_root = packages_root(scipkg_root).resolve()
        if installed_path is None:
            skipped_reason = "missing_installed_path"
        elif not installed_path.exists():
            removed_files = True
        else:
            resolved = installed_path.resolve()
            try:
                resolved.relative_to(managed_root)
                inside_managed_root = True
            except ValueError:
                inside_managed_root = False

            if not inside_managed_root:
                skipped_reason = "installed_path_outside_managed_packages"
            else:
                shutil.rmtree(resolved, ignore_errors=True)
                removed_files = not resolved.exists()
                if not removed_files:
                    skipped_reason = "failed_to_remove_installed_path"

    packages.pop(normalized_id, None)
    active = registry.get("active_package")
    if active == normalized_id or active not in packages:
        registry["active_package"] = sorted(packages.keys())[0] if packages else None

    save_registry(scipkg_root, registry)

    return {
        "package_id": normalized_id,
        "removed_from_registry": True,
        "removed_files": removed_files,
        "file_removal_requested": remove_files,
        "file_removal_skipped_reason": skipped_reason,
        "active_package": registry.get("active_package"),
    }


def _normalize_overlay_entry_name(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise PackageValidationError("Overlay entry names cannot be empty.")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise PackageValidationError(
            "Overlay entry names must be top-level names without path separators."
        )
    if value.casefold() in TEMPLATE_RESERVED_ENTRY_NAMES:
        raise PackageValidationError(f"Overlay entry is reserved: {value}")
    if value.startswith("."):
        raise PackageValidationError(f"Overlay entry is hidden: {value}")
    return value


def _normalize_overlay_entries(raw: Any) -> list[str] | None:
    if raw is None:
        return None

    if isinstance(raw, str):
        candidates = raw.split(",")
    elif isinstance(raw, list):
        candidates = raw
    else:
        raise PackageValidationError("overlay_entries must be a list or csv string.")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, str):
            raise PackageValidationError("overlay_entries can only contain strings.")
        if not item.strip():
            continue
        entry = _normalize_overlay_entry_name(item)
        if entry in seen:
            continue
        seen.add(entry)
        normalized.append(entry)
    return normalized


def _normalize_dependency_ids(
    raw: Any,
    *,
    package_id: str | None = None,
) -> list[str] | None:
    if raw is None:
        return None

    if isinstance(raw, str):
        candidates = raw.split(",")
    elif isinstance(raw, list):
        candidates = raw
    else:
        raise PackageValidationError("dependency_package_ids must be a list or csv string.")

    owner = normalize_package_id(package_id) if package_id else None
    normalized: list[str] = []
    seen: set[str] = set()

    for item in candidates:
        if not isinstance(item, str):
            raise PackageValidationError(
                "dependency_package_ids can only contain strings."
            )
        if not item.strip():
            continue
        dep_id = normalize_package_id(item)
        if owner and dep_id == owner:
            continue
        if dep_id in seen:
            continue
        seen.add(dep_id)
        normalized.append(dep_id)
    return normalized


def set_package_overlay_entries(
    scipkg_root: Path,
    package_id: str,
    entries: list[str] | None,
) -> dict[str, Any]:
    normalized_id = normalize_package_id(package_id)
    normalized_entries = _normalize_overlay_entries(entries)

    registry = load_registry(scipkg_root)
    packages = registry.get("packages", {})
    meta = packages.get(normalized_id)
    if not isinstance(meta, dict):
        raise PackageNotFoundError(f"Package not found: {normalized_id}")

    if normalized_entries is None:
        meta.pop(PACKAGE_OVERLAY_ENTRIES_KEY, None)
    else:
        meta[PACKAGE_OVERLAY_ENTRIES_KEY] = normalized_entries
    meta["updated_at"] = _now_iso()

    save_registry(scipkg_root, registry)
    return meta


def set_package_dependency_ids(
    scipkg_root: Path,
    package_id: str,
    dependency_package_ids: list[str] | None,
) -> dict[str, Any]:
    normalized_id = normalize_package_id(package_id)
    normalized_dependencies = _normalize_dependency_ids(
        dependency_package_ids,
        package_id=normalized_id,
    )

    registry = load_registry(scipkg_root)
    packages = registry.get("packages", {})
    meta = packages.get(normalized_id)
    if not isinstance(meta, dict):
        raise PackageNotFoundError(f"Package not found: {normalized_id}")

    missing = []
    if normalized_dependencies:
        for dep_id in normalized_dependencies:
            if dep_id not in packages:
                missing.append(dep_id)

    if missing:
        missing_text = ", ".join(sorted(set(missing)))
        raise PackageNotFoundError(f"Dependency package(s) not found: {missing_text}")

    if normalized_dependencies:
        meta[PACKAGE_DEPENDENCY_IDS_KEY] = normalized_dependencies
    else:
        meta.pop(PACKAGE_DEPENDENCY_IDS_KEY, None)
    meta["updated_at"] = _now_iso()

    save_registry(scipkg_root, registry)
    return meta


def _download_zip(url: str, destination: Path, max_bytes: int) -> int:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "fermilink-installer/0.2",
            "Accept": "application/zip, application/octet-stream",
        },
    )
    total = 0
    with urllib.request.urlopen(req) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes > 0 and total > max_bytes:
                raise PackageError(f"Zip download exceeded max size {max_bytes} bytes.")
            handle.write(chunk)
    return total


def _safe_extract_zip(zip_path: Path, extract_root: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute():
                raise PackageError(f"Zip contains absolute path: {member.filename}")
            if ".." in member_path.parts:
                raise PackageError(f"Zip contains unsafe path: {member.filename}")
        archive.extractall(extract_root)


def _detect_extracted_root(extract_root: Path) -> Path:
    children = [child for child in extract_root.iterdir() if child.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extract_root


def _strip_instruction_files(package_root: Path) -> list[str]:
    removed: list[str] = []
    for path in package_root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.casefold() not in REMOVED_INSTRUCTION_FILENAMES:
            continue
        rel = path.relative_to(package_root)
        path.unlink(missing_ok=True)
        removed.append(str(rel))
    return removed


def _strip_root_directories(package_root: Path) -> list[str]:
    removed: list[str] = []
    for name in REMOVED_ROOT_DIRECTORIES:
        path = package_root / name
        if not path.exists():
            continue
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        removed.append(name)
    return removed


def install_from_zip(
    scipkg_root: Path,
    package_id: str,
    *,
    zip_url: str,
    title: str | None = None,
    activate: bool = False,
    force: bool = False,
    max_zip_bytes: int = 800 * 1024 * 1024,
) -> dict[str, Any]:
    normalized_id = normalize_package_id(package_id)
    target_dir = packages_root(scipkg_root) / normalized_id

    if target_dir.exists():
        if not force:
            raise PackageError(
                f"Target package directory already exists: {target_dir}. Use --force."
            )
        shutil.rmtree(target_dir)

    with tempfile.TemporaryDirectory(prefix="fermilink-install-") as temp_dir:
        temp_root = Path(temp_dir)
        zip_path = temp_root / "package.zip"
        extract_root = temp_root / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)

        _download_zip(zip_url, zip_path, max_zip_bytes)
        _safe_extract_zip(zip_path, extract_root)

        source_root = _detect_extracted_root(extract_root)
        if not source_root.exists() or not source_root.is_dir():
            raise PackageError(f"Extracted source root is invalid: {source_root}")

        shutil.copytree(source_root, target_dir)

    removed_instruction_files = _strip_instruction_files(target_dir)
    removed_root_directories = _strip_root_directories(target_dir)

    return register_package(
        scipkg_root,
        normalized_id,
        installed_path=target_dir,
        source=zip_url,
        title=title,
        activate=activate,
        extra={
            "removed_instruction_files": removed_instruction_files,
            "removed_root_directories": removed_root_directories,
        },
    )


def install_from_local_path(
    scipkg_root: Path,
    package_id: str,
    *,
    local_path: Path,
    title: str | None = None,
    activate: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    normalized_id = normalize_package_id(package_id)
    source = local_path.expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise PackageError(f"Local source path is invalid: {source}")

    target_dir = packages_root(scipkg_root) / normalized_id
    if target_dir.exists():
        if not force:
            raise PackageError(
                f"Target package directory already exists: {target_dir}. Use --force."
            )
        shutil.rmtree(target_dir)

    shutil.copytree(source, target_dir)

    return register_package(
        scipkg_root,
        normalized_id,
        installed_path=target_dir,
        source=f"local-path:{source}",
        title=title,
        activate=activate,
    )


def load_workspace_manifest(workspace_root: Path) -> dict[str, Any] | None:
    path = workspace_manifest_path(workspace_root)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def save_workspace_manifest(workspace_root: Path, payload: dict[str, Any]) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(workspace_manifest_path(workspace_root), payload)


def resolve_session_package(
    scipkg_root: Path,
    workspace_root: Path,
    requested_package_id: str | None = None,
) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    registry = load_registry(scipkg_root)
    packages = registry.get("packages", {})
    if not isinstance(packages, dict) or not packages:
        return None, None

    if requested_package_id:
        requested_id = normalize_package_id(requested_package_id)
        requested_meta = packages.get(requested_id)
        if not isinstance(requested_meta, dict):
            raise PackageNotFoundError(f"Requested package not found: {requested_id}")
        _resolve_meta_installed_path(requested_meta)
        return requested_id, requested_meta

    manifest = load_workspace_manifest(workspace_root)
    if isinstance(manifest, dict):
        pinned = manifest.get("package_id")
        if isinstance(pinned, str):
            try:
                pinned_id = normalize_package_id(pinned)
            except PackageValidationError:
                pinned_id = None
            if pinned_id:
                pinned_meta = packages.get(pinned_id)
                if isinstance(pinned_meta, dict):
                    try:
                        _resolve_meta_installed_path(pinned_meta)
                        return pinned_id, pinned_meta
                    except PackageValidationError:
                        pass

    env_active = os.getenv("SCIPKG_ACTIVE", "").strip()
    if env_active:
        try:
            env_id = normalize_package_id(env_active)
            env_meta = packages.get(env_id)
            if isinstance(env_meta, dict):
                _resolve_meta_installed_path(env_meta)
                return env_id, env_meta
        except PackageValidationError:
            pass

    active = registry.get("active_package")
    if isinstance(active, str):
        try:
            active_id = normalize_package_id(active)
        except PackageValidationError:
            active_id = None
        if active_id:
            active_meta = packages.get(active_id)
            if isinstance(active_meta, dict):
                _resolve_meta_installed_path(active_meta)
                return active_id, active_meta

    return None, None


def _entry_is_exportable(entry: Path) -> bool:
    name = entry.name
    if not name:
        return False
    if name.startswith("."):
        return False
    if name in SKIP_ENTRY_NAMES:
        return False
    if name.casefold() in TEMPLATE_RESERVED_ENTRY_NAMES:
        return False
    return True


def iter_package_entries(
    package_root: Path,
    include_names: list[str] | None = None,
) -> tuple[list[Path], list[str]]:
    if not package_root.exists() or not package_root.is_dir():
        raise PackageValidationError(f"Package root is invalid: {package_root}")

    exportable = [
        entry
        for entry in sorted(package_root.iterdir(), key=lambda item: item.name)
        if _entry_is_exportable(entry)
    ]
    if include_names is None:
        return exportable, []

    by_name = {entry.name: entry for entry in exportable}
    selected: list[Path] = []
    missing: list[str] = []
    for name in include_names:
        hit = by_name.get(name)
        if hit is None:
            missing.append(name)
        else:
            selected.append(hit)
    return selected, missing


def _remove_existing_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _link_or_copy_entry(src: Path, dst: Path) -> str:
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


def _manifest_entry_names(manifest: dict[str, Any] | None) -> set[str]:
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


def _manifest_dependency_ids(manifest: dict[str, Any] | None) -> set[str]:
    package_ids: set[str] = set()
    if not isinstance(manifest, dict):
        return package_ids
    linked = manifest.get("linked_dependency_packages")
    if not isinstance(linked, list):
        return package_ids

    for item in linked:
        if isinstance(item, dict):
            maybe = item.get("package_id") or item.get("name")
            if isinstance(maybe, str) and maybe:
                try:
                    package_ids.add(normalize_package_id(maybe))
                except PackageValidationError:
                    pass
        elif isinstance(item, str) and item:
            try:
                package_ids.add(normalize_package_id(item))
            except PackageValidationError:
                pass
    return package_ids


def _remove_managed_symlinks(
    repo_dir: Path,
    manifest: dict[str, Any] | None,
    *,
    only_names: set[str] | None = None,
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


def _remove_managed_dependency_links(
    repo_dir: Path,
    manifest: dict[str, Any] | None,
    *,
    only_package_ids: set[str] | None = None,
) -> None:
    if not isinstance(manifest, dict):
        return

    linked = manifest.get("linked_dependency_packages")
    if not isinstance(linked, list):
        return

    dependency_root = repo_dir / PACKAGE_DEPENDENCIES_DIRNAME
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
        except PackageValidationError:
            continue

        if only_package_ids is not None and normalized_id not in only_package_ids:
            continue

        target = dependency_root / normalized_id
        if mode == "symlink" and target.is_symlink():
            target.unlink(missing_ok=True)
        elif mode != "symlink" and target.exists():
            _remove_existing_entry(target)

    if dependency_root.is_dir():
        try:
            next(dependency_root.iterdir())
        except StopIteration:
            dependency_root.rmdir()


def overlay_package_into_repo(
    repo_dir: Path,
    workspace_root: Path,
    package_id: str,
    package_meta: dict[str, Any],
    *,
    scipkg_root: Path,
    allow_replace_existing: bool = False,
) -> dict[str, Any]:
    package_root = _resolve_meta_installed_path(package_meta)
    configured_entries = _normalize_overlay_entries(package_meta.get(PACKAGE_OVERLAY_ENTRIES_KEY))
    configured_dependency_ids = _normalize_dependency_ids(
        package_meta.get(PACKAGE_DEPENDENCY_IDS_KEY),
        package_id=package_id,
    ) or []

    entries, missing_requested_entries = iter_package_entries(
        package_root,
        include_names=configured_entries,
    )
    target_names = {entry.name for entry in entries}

    previous_manifest = load_workspace_manifest(workspace_root)
    previous_id: str | None = None
    if isinstance(previous_manifest, dict):
        previous_raw = previous_manifest.get("package_id")
        if isinstance(previous_raw, str):
            try:
                previous_id = normalize_package_id(previous_raw)
            except PackageValidationError:
                previous_id = None

    if previous_id is not None and previous_id != package_id:
        _remove_managed_symlinks(repo_dir, previous_manifest)
        _remove_managed_dependency_links(repo_dir, previous_manifest)
    elif previous_id == package_id:
        stale_names = _manifest_entry_names(previous_manifest) - target_names
        if stale_names:
            _remove_managed_symlinks(repo_dir, previous_manifest, only_names=stale_names)

        stale_dependency_ids = _manifest_dependency_ids(previous_manifest) - set(
            configured_dependency_ids
        )
        if stale_dependency_ids:
            _remove_managed_dependency_links(
                repo_dir,
                previous_manifest,
                only_package_ids=stale_dependency_ids,
            )

    previous_names = _manifest_entry_names(previous_manifest)
    previous_dependency_ids = _manifest_dependency_ids(previous_manifest)

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
            if src.name in previous_names or allow_replace_existing:
                _remove_existing_entry(dst)
            else:
                collisions.append(src.name)
                continue

        mode = _link_or_copy_entry(src, dst)
        if mode == "existing":
            collisions.append(src.name)
            continue

        linked_entries.append(
            {"name": src.name, "mode": mode, "source": str(src.resolve())}
        )

    registry = load_registry(scipkg_root)
    package_map = registry.get("packages", {}) if isinstance(registry.get("packages"), dict) else {}
    if package_id not in package_map:
        package_map = dict(package_map)
        package_map[package_id] = package_meta

    linked_dependency_packages: list[dict[str, str]] = []
    missing_dependency_packages: list[str] = []
    dependency_collisions: list[str] = []

    dependency_root = repo_dir / PACKAGE_DEPENDENCIES_DIRNAME
    dependency_root_ready = True

    if configured_dependency_ids:
        if dependency_root.exists() and not dependency_root.is_dir():
            if allow_replace_existing:
                _remove_existing_entry(dependency_root)
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
                _remove_existing_entry(dependency_dst)
            continue

        try:
            dependency_src = _resolve_meta_installed_path(dependency_meta)
        except PackageValidationError:
            missing_dependency_packages.append(dependency_id)
            if dependency_id in previous_dependency_ids:
                _remove_existing_entry(dependency_dst)
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
                _remove_existing_entry(dependency_dst)
            else:
                dependency_collisions.append(dependency_id)
                continue

        mode = _link_or_copy_entry(dependency_src, dependency_dst)
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
        "updated_at": _now_iso(),
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
