from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fermilink.packages import (
    PACKAGE_DEPENDENCIES_DIRNAME,
    PACKAGE_DEPENDENCY_IDS_KEY,
    PACKAGE_OVERLAY_ENTRIES_KEY,
    REGISTRY_FILENAME,
    WORKSPACE_MANIFEST_FILENAME,
    atomic_write_json as _atomic_write_json_shared,
    build_default_registry,
    extract_manifest_dependency_ids,
    extract_manifest_entry_names,
    is_exportable_entry_name,
    link_or_copy_entry as _link_or_copy_entry_shared,
    load_registry_file,
    normalize_package_id as _normalize_package_id,
    normalize_registry_payload,
    overlay_package_into_repo_core,
    remove_existing_entry as _remove_existing_entry_shared,
    remove_managed_dependency_links as _remove_managed_dependency_links_shared,
    remove_managed_entries as _remove_managed_entries_shared,
    save_registry_file,
    TEMPLATE_RESERVED_ENTRY_NAMES,
)

REMOVED_INSTRUCTION_FILENAMES = {"agents.md", "claude.md", "gemini.md"}
REMOVED_ROOT_DIRECTORIES = {"projects"}
PROGRESS_REFRESH_SECONDS = 0.1
PROGRESS_BAR_WIDTH = 24
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
PROGRESS_DOT_FRAMES = (".  ", ".. ", "...")


class PackageError(RuntimeError):
    """Base package-management error."""


class PackageNotFoundError(PackageError):
    """Raised when a package id cannot be found."""


class PackageValidationError(PackageError):
    """Raised when package metadata is invalid."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_package_id(value: str) -> str:
    """
    Normalize and validate a package identifier.

    Parameters
    ----------
    value : str
        Raw value to normalize.

    Returns
    -------
    str
        Normalized package id.
    """
    try:
        return _normalize_package_id(value)
    except ValueError as exc:
        raise PackageValidationError(str(exc)) from exc


def packages_root(scipkg_root: Path) -> Path:
    """
    Return the package storage root and ensure it exists.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.

    Returns
    -------
    Path
        Directory path where installed packages are stored.
    """
    root = scipkg_root / "packages"
    root.mkdir(parents=True, exist_ok=True)
    return root


def registry_path(scipkg_root: Path) -> Path:
    """
    Return the package registry file path.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.

    Returns
    -------
    Path
        Path to the registry JSON file.
    """
    return scipkg_root / REGISTRY_FILENAME


def workspace_manifest_path(workspace_root: Path) -> Path:
    """
    Return the workspace manifest file path.

    Parameters
    ----------
    workspace_root : Path
        Workspace root where manifest state is stored.

    Returns
    -------
    Path
        Path to the workspace manifest JSON file.
    """
    return workspace_root / WORKSPACE_MANIFEST_FILENAME


def _default_registry() -> dict[str, Any]:
    return build_default_registry(updated_at=_now_iso())


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_json_shared(path, payload)


def _normalize_registry(payload: Any) -> dict[str, Any]:
    return normalize_registry_payload(
        payload,
        updated_at=_now_iso(),
        normalize_package_id=normalize_package_id,
        coerce_non_dict_meta_to_empty=True,
        fallback_active_to_first_package=True,
    )


def load_registry(scipkg_root: Path) -> dict[str, Any]:
    """
    Load the package registry for a scientific package root.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.

    Returns
    -------
    dict[str, Any]
        Normalized registry payload.
    """
    path = registry_path(scipkg_root)
    return load_registry_file(
        path,
        default_registry=_default_registry,
        normalize_registry=_normalize_registry,
    )


def save_registry(scipkg_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Save and normalize package registry state for a scientific package root.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.
    payload : dict[str, Any]
        JSON-like payload to normalize or persist.

    Returns
    -------
    dict[str, Any]
        Normalized registry payload after persistence.
    """
    return save_registry_file(
        registry_path(scipkg_root),
        payload,
        updated_at=_now_iso(),
        normalize_registry=_normalize_registry,
    )


def list_packages(scipkg_root: Path) -> dict[str, Any]:
    """
    Return package metadata entries from the registry.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.

    Returns
    -------
    dict[str, Any]
        Package metadata mapping keyed by package id.
    """
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
    """
    Register or update a package entry in the registry.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.
    package_id : str
        Normalized package identifier.
    installed_path : Path
        Filesystem path of the installed package content.
    source : str
        Source label recorded in package metadata.
    title : str | None
        Optional human-readable package title.
    activate : bool
        Whether to mark the package as active after operation completion.
    extra : dict[str, Any] | None
        Additional metadata fields merged into the package record.

    Returns
    -------
    dict[str, Any]
        Package metadata for the registered package.
    """
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
    """
    Set a package as the active package in registry state.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.
    package_id : str
        Normalized package identifier.

    Returns
    -------
    dict[str, Any]
        Package metadata for the newly active package.
    """
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
    """
    Delete a package from registry state and optionally remove files.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.
    package_id : str
        Normalized package identifier.
    remove_files : bool
        Whether installed package files should be deleted from disk.

    Returns
    -------
    dict[str, Any]
        Package metadata for the deleted package.
    """
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
        raise PackageValidationError(
            "dependency_package_ids must be a list or csv string."
        )

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
    """
    Persist overlay entry names for an installed package.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.
    package_id : str
        Normalized package identifier.
    entries : list[str] | None
        Overlay entry names to persist for this package.

    Returns
    -------
    dict[str, Any]
        Updated package metadata after overlay entry persistence.
    """
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
    """
    Persist dependency package ids for an installed package.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.
    package_id : str
        Normalized package identifier.
    dependency_package_ids : list[str] | None
        Dependency package ids to persist for this package.

    Returns
    -------
    dict[str, Any]
        Updated package metadata after dependency persistence.
    """
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
    def _truthy_env(name: str) -> bool:
        raw = os.getenv(name)
        if not isinstance(raw, str):
            return False
        return raw.strip().lower() in TRUTHY_ENV_VALUES

    def _should_show_progress() -> bool:
        if _truthy_env("FERMILINK_NO_PROGRESS"):
            return False
        if _truthy_env("FERMILINK_PROGRESS"):
            return True
        try:
            return bool(sys.stderr.isatty())
        except Exception:
            return False

    def _format_size(value: int) -> str:
        units = ("B", "KB", "MB", "GB", "TB")
        size = float(max(0, value))
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{int(size)} B"

    def _render_progress(
        downloaded: int, total_bytes: int | None, started_at: float
    ) -> str:
        elapsed = max(time.monotonic() - started_at, 1e-6)
        speed = int(downloaded / elapsed)
        speed_text = f"{_format_size(speed)}/s"
        downloaded_text = _format_size(downloaded)
        if isinstance(total_bytes, int) and total_bytes > 0:
            progress = min(downloaded / total_bytes, 1.0)
            filled = int(progress * PROGRESS_BAR_WIDTH)
            bar = ("#" * filled) + ("-" * max(0, PROGRESS_BAR_WIDTH - filled))
            total_text = _format_size(total_bytes)
            return (
                f"Downloading [{bar}] {progress * 100:6.2f}% "
                f"{downloaded_text}/{total_text} {speed_text}"
            )
        dot_index = int(elapsed / 0.25) % len(PROGRESS_DOT_FRAMES)
        dots = PROGRESS_DOT_FRAMES[dot_index]
        return f"Downloading{dots} {downloaded_text} {speed_text}"

    def _write_progress_line(line: str, *, previous_length: int) -> int:
        padding = " " * max(0, previous_length - len(line))
        sys.stderr.write("\r")
        sys.stderr.write(line)
        if padding:
            sys.stderr.write(padding)
        sys.stderr.flush()
        return len(line)

    def _extract_content_length(headers: Any) -> int | None:
        if headers is None or not hasattr(headers, "get"):
            return None
        raw = headers.get("Content-Length")
        if not isinstance(raw, str):
            return None
        raw_value = raw.strip()
        if not raw_value:
            return None
        try:
            parsed = int(raw_value)
        except ValueError:
            return None
        if parsed <= 0:
            return None
        return parsed

    def _extract_content_range_total(headers: Any) -> int | None:
        if headers is None or not hasattr(headers, "get"):
            return None
        raw = headers.get("Content-Range")
        if not isinstance(raw, str):
            return None
        match = re.search(r"/\s*(\d+)\s*$", raw)
        if match is None:
            return None
        try:
            parsed = int(match.group(1))
        except ValueError:
            return None
        if parsed <= 0:
            return None
        return parsed

    request_headers = {
        "User-Agent": "fermilink-installer/0.2",
        "Accept": "application/zip, application/octet-stream",
    }

    def _probe_total_bytes() -> int | None:
        try:
            head_req = urllib.request.Request(
                url, headers=request_headers, method="HEAD"
            )
            with urllib.request.urlopen(head_req) as response:
                total_bytes = _extract_content_length(
                    getattr(response, "headers", None)
                )
                if isinstance(total_bytes, int) and total_bytes > 0:
                    return total_bytes
                ranged_total = _extract_content_range_total(
                    getattr(response, "headers", None)
                )
                if isinstance(ranged_total, int) and ranged_total > 0:
                    return ranged_total
        except Exception:
            pass

        try:
            range_headers = dict(request_headers)
            range_headers["Range"] = "bytes=0-0"
            range_req = urllib.request.Request(url, headers=range_headers)
            with urllib.request.urlopen(range_req) as response:
                ranged_total = _extract_content_range_total(
                    getattr(response, "headers", None)
                )
                if isinstance(ranged_total, int) and ranged_total > 0:
                    return ranged_total
                total_bytes = _extract_content_length(
                    getattr(response, "headers", None)
                )
                if isinstance(total_bytes, int) and total_bytes > 1:
                    return total_bytes
        except Exception:
            pass

        return None

    req = urllib.request.Request(url, headers=request_headers)
    total = 0
    show_progress = _should_show_progress()
    progress_length = 0
    progress_rendered = False
    started_at = time.monotonic()
    last_progress_emit = 0.0
    preflight_total_bytes = _probe_total_bytes() if show_progress else None
    with urllib.request.urlopen(req) as response, destination.open("wb") as handle:
        headers = getattr(response, "headers", None)
        total_bytes = _extract_content_length(headers)
        if total_bytes is None:
            total_bytes = _extract_content_range_total(headers)
        if total_bytes is None:
            total_bytes = preflight_total_bytes
        try:
            if show_progress:
                progress_length = _write_progress_line(
                    _render_progress(0, total_bytes, started_at),
                    previous_length=progress_length,
                )
                progress_rendered = True

            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes > 0 and total > max_bytes:
                    raise PackageError(
                        f"Zip download exceeded max size {max_bytes} bytes."
                    )
                handle.write(chunk)

                if show_progress:
                    now = time.monotonic()
                    if now - last_progress_emit >= PROGRESS_REFRESH_SECONDS:
                        progress_length = _write_progress_line(
                            _render_progress(total, total_bytes, started_at),
                            previous_length=progress_length,
                        )
                        progress_rendered = True
                        last_progress_emit = now

            if show_progress:
                progress_length = _write_progress_line(
                    _render_progress(total, total_bytes, started_at),
                    previous_length=progress_length,
                )
                progress_rendered = True
        finally:
            if show_progress and progress_rendered:
                sys.stderr.write("\n")
                sys.stderr.flush()
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
    """
    Install a package from a zip archive URL and register it.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.
    package_id : str
        Normalized package identifier.
    zip_url : str
        URL of the zip archive to download and install.
    title : str | None
        Optional human-readable package title.
    activate : bool
        Whether to mark the package as active after operation completion.
    force : bool
        Whether existing package ids may be overwritten.
    max_zip_bytes : int
        Maximum allowed zip size in bytes before aborting download/install.

    Returns
    -------
    dict[str, Any]
        Package metadata for the installed package.
    """
    normalized_id = normalize_package_id(package_id)
    target_dir = packages_root(scipkg_root) / normalized_id

    if target_dir.exists():
        if not force:
            raise PackageError(
                f"Target package directory already exists: {target_dir}. Use --force to download again."
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
    """
    Install a package from a local path and register it.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.
    package_id : str
        Normalized package identifier.
    local_path : Path
        Local package directory path to install from.
    title : str | None
        Optional human-readable package title.
    activate : bool
        Whether to mark the package as active after operation completion.
    force : bool
        Whether existing package ids may be overwritten.

    Returns
    -------
    dict[str, Any]
        Package metadata for the installed package.
    """
    normalized_id = normalize_package_id(package_id)
    source = local_path.expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise PackageError(f"Local source path is invalid: {source}")

    target_dir = packages_root(scipkg_root) / normalized_id
    same_source_target = target_dir.exists() and source == target_dir.resolve()
    if target_dir.exists():
        if not force:
            raise PackageError(
                f"Target package directory already exists: {target_dir}. Use --force to download again."
            )
        if not same_source_target:
            shutil.rmtree(target_dir)

    if not same_source_target:
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
    """
    Load workspace overlay manifest state from disk.

    Parameters
    ----------
    workspace_root : Path
        Workspace root where manifest state is stored.

    Returns
    -------
    dict[str, Any] | None
        Workspace manifest payload, or `None` when absent/invalid.
    """
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
    """
    Save workspace overlay manifest state to disk.

    Parameters
    ----------
    workspace_root : Path
        Workspace root where manifest state is stored.
    payload : dict[str, Any]
        JSON-like payload to normalize or persist.

    Returns
    -------
    None
        No return value.
    """
    workspace_root.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(workspace_manifest_path(workspace_root), payload)


def resolve_session_package(
    scipkg_root: Path,
    workspace_root: Path,
    requested_package_id: str | None = None,
) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    """
    Resolve the package to use for the current session/workspace.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing registry and package files.
    workspace_root : Path
        Workspace root where manifest state is stored.
    requested_package_id : str | None
        Optional package id explicitly requested for the session.

    Returns
    -------
    tuple[str, dict[str, Any]] | tuple[None, None]
        Tuple containing resolved package id and metadata, or `(None, None)`.
    """
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

    env_active = os.getenv("FERMILINK_SCIPKG_ACTIVE", "").strip()
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
    return is_exportable_entry_name(entry.name)


def iter_package_entries(
    package_root: Path,
    include_names: list[str] | None = None,
) -> tuple[list[Path], list[str]]:
    """
    Enumerate installable package entries from a package directory.

    Parameters
    ----------
    package_root : Path
        Root directory of one installed package.
    include_names : list[str] | None
        Optional allowlist of entry names to include when enumerating package contents.

    Returns
    -------
    tuple[list[Path], list[str]]
        Tuple of `(entries, skipped_names)` from package directory traversal.
    """
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
    _remove_existing_entry_shared(path)


def _link_or_copy_entry(src: Path, dst: Path) -> str:
    return _link_or_copy_entry_shared(src, dst)


def _manifest_entry_names(manifest: dict[str, Any] | None) -> set[str]:
    return extract_manifest_entry_names(manifest)


def _manifest_dependency_ids(manifest: dict[str, Any] | None) -> set[str]:
    return extract_manifest_dependency_ids(manifest)


def _remove_managed_symlinks(
    repo_dir: Path,
    manifest: dict[str, Any] | None,
    *,
    only_names: set[str] | None = None,
) -> None:
    _remove_managed_entries_shared(
        repo_dir,
        manifest,
        only_names=only_names,
        remove_non_symlink_entries=False,
        remove_existing=_remove_existing_entry,
    )


def _remove_managed_dependency_links(
    repo_dir: Path,
    manifest: dict[str, Any] | None,
    *,
    only_package_ids: set[str] | None = None,
) -> None:
    _remove_managed_dependency_links_shared(
        repo_dir,
        manifest,
        normalize_package_id=normalize_package_id,
        only_package_ids=only_package_ids,
        dependencies_dirname=PACKAGE_DEPENDENCIES_DIRNAME,
        remove_existing=_remove_existing_entry,
    )


def overlay_package_into_repo(
    repo_dir: Path,
    workspace_root: Path,
    package_id: str,
    package_meta: dict[str, Any],
    *,
    scipkg_root: Path,
    allow_replace_existing: bool = False,
) -> dict[str, Any]:
    """
    Overlay an installed package into a workspace repository.

    Parameters
    ----------
    repo_dir : Path
        Workspace repository path receiving overlaid entries.
    workspace_root : Path
        Workspace root where manifest state is stored.
    package_id : str
        Normalized package identifier.
    package_meta : dict[str, Any]
        Installed package metadata record from the registry.
    scipkg_root : Path
        Scientific package root containing registry and package files.
    allow_replace_existing : bool
        Whether existing destination entries may be replaced.

    Returns
    -------
    dict[str, Any]
        Overlay result payload with applied entries and manifest metadata.
    """

    def _load_package_map(
        _package_id: str, _package_meta: dict[str, Any]
    ) -> dict[str, Any]:
        registry = load_registry(scipkg_root)
        maybe_packages = registry.get("packages", {})
        if isinstance(maybe_packages, dict):
            return maybe_packages
        return {}

    return overlay_package_into_repo_core(
        repo_dir=repo_dir,
        workspace_root=workspace_root,
        package_id=package_id,
        package_meta=package_meta,
        allow_replace_existing=allow_replace_existing,
        now_iso=_now_iso,
        resolve_package_meta_path=_resolve_meta_installed_path,
        normalize_overlay_entries=_normalize_overlay_entries,
        normalize_dependency_ids=_normalize_dependency_ids,
        iter_package_entries=iter_package_entries,
        load_workspace_manifest=load_workspace_manifest,
        save_workspace_manifest=save_workspace_manifest,
        normalize_package_id=normalize_package_id,
        get_package_map=_load_package_map,
        replace_existing_entries_for_previous_names=True,
        remove_non_symlink_managed_entries=False,
        overlay_entries_key=PACKAGE_OVERLAY_ENTRIES_KEY,
        dependency_ids_key=PACKAGE_DEPENDENCY_IDS_KEY,
        dependencies_dirname=PACKAGE_DEPENDENCIES_DIRNAME,
        remove_existing=_remove_existing_entry,
        link_or_copy=_link_or_copy_entry,
    )
