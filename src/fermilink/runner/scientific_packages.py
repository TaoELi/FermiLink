from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
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
)
from fermilink.config import resolve_fermilink_home

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


def find_project_root(start: Path) -> Path:
    """
    Find the repository root by walking upward from a start path.

    Parameters
    ----------
    start : Path
        Starting path for upward project-root discovery.

    Returns
    -------
    Path
        Detected repository/project root path.
    """
    cur = start.resolve()
    for p in [cur.parent, *cur.parents]:
        if (p / "pyproject.toml").exists() or (p / ".git").exists():
            return p
    # Installed wheel/sdist layouts may not include project markers.
    return Path.cwd()


PROJECT_ROOT = find_project_root(Path(__file__))
DEFAULT_MAXWELLLINK_ROOT = PROJECT_ROOT / "maxwelllink"


class PackageError(RuntimeError):
    """Base error for scientific package management."""


class PackageNotFoundError(PackageError):
    """Raised when a requested package is missing."""


class PackageValidationError(PackageError):
    """Raised when package metadata is invalid."""


def _now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format.

    Returns
    -------
    str
        Current UTC timestamp string.
    """

    return datetime.now(timezone.utc).isoformat()


def _resolve_path(env_key: str, default: Path) -> Path:
    """Resolve a filesystem path from environment or fallback.

    Parameters
    ----------
    env_key : str
        Environment variable name to inspect.
    default : Path
        Fallback path when the variable is unset.

    Returns
    -------
    Path
        Expanded path, converted to absolute when needed.
    """

    raw = os.getenv(env_key)
    if raw:
        path = Path(raw).expanduser()
    else:
        path = default
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON payload atomically by replacing a temporary file.

    Parameters
    ----------
    path : Path
        Destination JSON file.
    payload : dict of str to Any
        Data to serialize.

    Returns
    -------
    None
        The file is replaced in place.
    """

    _atomic_write_json_shared(path, payload)


def normalize_package_id(value: str) -> str:
    """Normalize a package id to a stable, filesystem-safe token.

    Parameters
    ----------
    value : str
        Raw package id value.

    Returns
    -------
    str
        Normalized package id containing lowercase alnum, `-`, and `_`.

    Raises
    ------
    PackageValidationError
        Raised when the normalized id is empty.
    """

    try:
        return _normalize_package_id(value)
    except ValueError as exc:
        raise PackageValidationError(str(exc)) from exc


def resolve_scipkg_root() -> Path:
    """Resolve and create the scientific package root directory.

    Returns
    -------
    Path
        Existing or newly created scientific package root path.
    """

    raw_scipkg_root = os.getenv("FERMILINK_SCIPKG_ROOT")
    if raw_scipkg_root and raw_scipkg_root.strip():
        path = _resolve_path("FERMILINK_SCIPKG_ROOT", Path.cwd())
    else:
        raw_scientific_packages_root = os.getenv("FERMILINK_SCIENTIFIC_PACKAGES_ROOT")
        if raw_scientific_packages_root and raw_scientific_packages_root.strip():
            path = _resolve_path("FERMILINK_SCIENTIFIC_PACKAGES_ROOT", Path.cwd())
        else:
            path = resolve_fermilink_home() / "scientific_packages"
    path.mkdir(parents=True, exist_ok=True)
    return path


def packages_root(scipkg_root: Path) -> Path:
    """Return the managed `packages/` directory under the registry root.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.

    Returns
    -------
    Path
        Directory that stores installed package contents.
    """

    root = scipkg_root / "packages"
    root.mkdir(parents=True, exist_ok=True)
    return root


def registry_path(scipkg_root: Path) -> Path:
    """Build the path to the package registry JSON file.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.

    Returns
    -------
    Path
        Absolute or relative registry file path.
    """

    return scipkg_root / REGISTRY_FILENAME


def workspace_manifest_path(workspace_root: Path) -> Path:
    """Build the path to a workspace package manifest file.

    Parameters
    ----------
    workspace_root : Path
        Workspace root directory.

    Returns
    -------
    Path
        Manifest file path under the workspace root.
    """

    return workspace_root / WORKSPACE_MANIFEST_FILENAME


def _default_registry() -> dict[str, Any]:
    """Create the default in-memory registry structure.

    Returns
    -------
    dict of str to Any
        Registry skeleton with version, active package, and package map.
    """

    return build_default_registry(updated_at=_now_iso())


def _normalize_registry(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize untrusted registry payload into canonical structure.

    Parameters
    ----------
    payload : dict of str to Any or None
        Registry JSON payload loaded from disk.

    Returns
    -------
    dict of str to Any
        Canonical registry with normalized package ids and active selection.
    """

    return normalize_registry_payload(
        payload,
        updated_at=_now_iso(),
        normalize_package_id=normalize_package_id,
        dependency_key=PACKAGE_DEPENDENCY_IDS_KEY,
        dependency_normalizer=lambda raw, package_id: _normalize_dependency_package_ids(
            raw,
            package_id=package_id,
        ),
        coerce_non_dict_meta_to_empty=False,
        fallback_active_to_first_package=False,
    )


@contextmanager
def _registry_lock(scipkg_root: Path):
    """Provide an exclusive registry lock while mutating package metadata.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root containing the lock file.

    Yields
    ------
    None
        Control is yielded while the lock is held.
    """

    scipkg_root.mkdir(parents=True, exist_ok=True)
    lock_path = scipkg_root / ".registry.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_registry_unlocked(scipkg_root: Path) -> dict[str, Any]:
    """Load registry JSON from disk without acquiring a lock.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.

    Returns
    -------
    dict of str to Any
        Parsed and normalized registry payload, or defaults on read failures.
    """

    path = registry_path(scipkg_root)
    return load_registry_file(
        path,
        default_registry=_default_registry,
        normalize_registry=_normalize_registry,
    )


def load_registry(scipkg_root: Path) -> dict[str, Any]:
    """Load registry metadata under a file lock.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.

    Returns
    -------
    dict of str to Any
        Current registry snapshot.
    """

    with _registry_lock(scipkg_root):
        return _load_registry_unlocked(scipkg_root)


def list_packages(scipkg_root: Path) -> dict[str, Any]:
    """Return all registered packages.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.

    Returns
    -------
    dict of str to Any
        Package metadata keyed by package id.
    """

    registry = load_registry(scipkg_root)
    return registry.get("packages", {})


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
    """Create or update package metadata in the registry.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.
    package_id : str
        Raw or normalized package id.
    installed_path : Path
        On-disk package directory to register.
    source : str
        Source descriptor (URL or local path marker).
    title : str or None, optional
        Human-readable package title.
    activate : bool, optional
        Whether to set this package as active immediately.
    extra : dict of str to Any or None, optional
        Additional metadata fields to merge.

    Returns
    -------
    dict of str to Any
        Stored metadata for the package.

    Raises
    ------
    PackageValidationError
        Raised when `installed_path` is missing or not a directory.
    """

    normalized_id = normalize_package_id(package_id)
    if not installed_path.exists() or not installed_path.is_dir():
        raise PackageValidationError(
            f"Installed path does not exist or is not a directory: {installed_path}"
        )

    with _registry_lock(scipkg_root):
        registry = _load_registry_unlocked(scipkg_root)
        packages = registry.setdefault("packages", {})
        meta = packages.get(normalized_id, {})
        if not isinstance(meta, dict):
            meta = {}

        meta.update(
            {
                "id": normalized_id,
                "title": title or meta.get("title") or normalized_id,
                "installed_path": str(installed_path.resolve()),
                "source": source,
                "updated_at": _now_iso(),
                "status": "installed",
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

        registry["updated_at"] = _now_iso()
        _atomic_write_json(registry_path(scipkg_root), registry)
        return meta


def activate_package(scipkg_root: Path, package_id: str) -> dict[str, Any]:
    """Set a package as active for future session resolution.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.
    package_id : str
        Raw or normalized package id.

    Returns
    -------
    dict of str to Any
        Metadata for the activated package.

    Raises
    ------
    PackageNotFoundError
        Raised when the package id is not registered.
    """

    normalized_id = normalize_package_id(package_id)
    with _registry_lock(scipkg_root):
        registry = _load_registry_unlocked(scipkg_root)
        packages = registry.get("packages", {})
        if normalized_id not in packages:
            raise PackageNotFoundError(f"Package not found: {normalized_id}")
        registry["active_package"] = normalized_id
        registry["updated_at"] = _now_iso()
        _atomic_write_json(registry_path(scipkg_root), registry)
        return packages[normalized_id]


def delete_package(
    scipkg_root: Path,
    package_id: str,
    *,
    remove_files: bool = True,
) -> dict[str, Any]:
    """Delete a registered package and optionally remove its managed files.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.
    package_id : str
        Raw or normalized package id.
    remove_files : bool, optional
        Whether to delete package files when they are under managed
        `packages/`. Paths outside managed storage are never removed.

    Returns
    -------
    dict[str, Any]
        Summary of deletion status and active-package update.

    Raises
    ------
    PackageNotFoundError
        Raised when the package id is not registered.
    """

    normalized_id = normalize_package_id(package_id)
    with _registry_lock(scipkg_root):
        registry = _load_registry_unlocked(scipkg_root)
        packages = registry.get("packages", {})
        package_meta = packages.get(normalized_id)
        if not isinstance(package_meta, dict):
            raise PackageNotFoundError(f"Package not found: {normalized_id}")

        managed_root = packages_root(scipkg_root).resolve()
        removed_files = False
        skipped_file_removal_reason: str | None = None
        installed_path: Path | None = None
        raw_installed_path = package_meta.get("installed_path")
        if isinstance(raw_installed_path, str) and raw_installed_path:
            installed_path = Path(raw_installed_path).expanduser()
            if not installed_path.is_absolute():
                installed_path = Path.cwd() / installed_path

        if remove_files:
            if installed_path is None:
                skipped_file_removal_reason = "missing_installed_path"
            elif not installed_path.exists():
                removed_files = True
            else:
                resolved = installed_path.resolve()
                is_managed = False
                try:
                    resolved.relative_to(managed_root)
                    is_managed = True
                except ValueError:
                    is_managed = False

                if not is_managed:
                    skipped_file_removal_reason = (
                        "installed_path_outside_managed_packages"
                    )
                else:
                    shutil.rmtree(resolved, ignore_errors=True)
                    removed_files = not resolved.exists()
                    if not removed_files:
                        skipped_file_removal_reason = "failed_to_remove_managed_path"

        removed_meta = packages.pop(normalized_id, None)
        _ = removed_meta

        previous_active = registry.get("active_package")
        if previous_active == normalized_id:
            registry["active_package"] = None
        if registry.get("active_package") not in packages:
            registry["active_package"] = (
                sorted(packages.keys())[0] if packages else None
            )

        registry["updated_at"] = _now_iso()
        _atomic_write_json(registry_path(scipkg_root), registry)

    return {
        "package_id": normalized_id,
        "removed_from_registry": True,
        "removed_files": removed_files,
        "file_removal_requested": remove_files,
        "file_removal_skipped_reason": skipped_file_removal_reason,
        "active_package": registry.get("active_package"),
    }


def set_package_overlay_entries(
    scipkg_root: Path,
    package_id: str,
    entries: list[str] | None,
) -> dict[str, Any]:
    """Update package-level overlay entry selection metadata.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.
    package_id : str
        Raw or normalized package id.
    entries : list of str or None
        Allowed top-level entry names. `None` clears the custom selection.

    Returns
    -------
    dict of str to Any
        Updated package metadata.

    Raises
    ------
    PackageNotFoundError
        Raised when the package id is not registered.
    """

    normalized_id = normalize_package_id(package_id)
    normalized_entries = _normalize_overlay_entries(entries)
    with _registry_lock(scipkg_root):
        registry = _load_registry_unlocked(scipkg_root)
        packages = registry.get("packages", {})
        package_meta = packages.get(normalized_id)
        if not isinstance(package_meta, dict):
            raise PackageNotFoundError(f"Package not found: {normalized_id}")

        if normalized_entries is None:
            package_meta.pop(PACKAGE_OVERLAY_ENTRIES_KEY, None)
        else:
            package_meta[PACKAGE_OVERLAY_ENTRIES_KEY] = normalized_entries
        package_meta["updated_at"] = _now_iso()
        registry["updated_at"] = _now_iso()
        _atomic_write_json(registry_path(scipkg_root), registry)
        return package_meta


def set_package_dependency_ids(
    scipkg_root: Path,
    package_id: str,
    dependency_package_ids: list[str] | None,
) -> dict[str, Any]:
    """Update package-level dependency package metadata.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.
    package_id : str
        Raw or normalized package id.
    dependency_package_ids : list of str or None
        Dependency package ids required by this package. `None` clears
        configured dependencies.

    Returns
    -------
    dict of str to Any
        Updated package metadata.

    Raises
    ------
    PackageNotFoundError
        Raised when the package id is not registered or dependency package ids
        reference unknown packages.
    """

    normalized_id = normalize_package_id(package_id)
    normalized_dependencies = _normalize_dependency_package_ids(
        dependency_package_ids,
        package_id=normalized_id,
    )
    with _registry_lock(scipkg_root):
        registry = _load_registry_unlocked(scipkg_root)
        packages = registry.get("packages", {})
        package_meta = packages.get(normalized_id)
        if not isinstance(package_meta, dict):
            raise PackageNotFoundError(f"Package not found: {normalized_id}")

        missing_dependencies: list[str] = []
        if normalized_dependencies:
            for dependency_id in normalized_dependencies:
                if dependency_id not in packages:
                    missing_dependencies.append(dependency_id)

        if missing_dependencies:
            missing_text = ", ".join(sorted(set(missing_dependencies)))
            raise PackageNotFoundError(
                f"Dependency package(s) not found: {missing_text}"
            )

        if normalized_dependencies:
            package_meta[PACKAGE_DEPENDENCY_IDS_KEY] = normalized_dependencies
        else:
            package_meta.pop(PACKAGE_DEPENDENCY_IDS_KEY, None)
        package_meta["updated_at"] = _now_iso()
        registry["updated_at"] = _now_iso()
        _atomic_write_json(registry_path(scipkg_root), registry)
        return package_meta


def _resolve_maxwelllink_root() -> Path | None:
    """Resolve local legacy MaxwellLink package path when available.

    Returns
    -------
    Path or None
        Existing directory for legacy package registration, else `None`.
    """

    env_path = _resolve_path("FERMILINK_MAXWELLLINK_ROOT", DEFAULT_MAXWELLLINK_ROOT)
    if env_path.exists() and env_path.is_dir():
        return env_path
    return None


def bootstrap_legacy_maxwelllink_package(scipkg_root: Path) -> str | None:
    """Register a legacy local MaxwellLink package when present.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.

    Returns
    -------
    str or None
        Registered package id when local MaxwellLink exists, else `None`.
    """

    maxwelllink_root = _resolve_maxwelllink_root()
    if maxwelllink_root is None:
        return None

    package_id = normalize_package_id(
        os.getenv("FERMILINK_LEGACY_MAXWELLLINK_PACKAGE_ID", "maxwelllink-local")
    )
    register_package(
        scipkg_root,
        package_id,
        installed_path=maxwelllink_root,
        source=f"local-path:{maxwelllink_root.resolve()}",
        title="MaxwellLink (local)",
        activate=False,
        extra={"legacy": True},
    )
    return package_id


def load_workspace_manifest(workspace_root: Path) -> dict[str, Any] | None:
    """Load workspace package overlay manifest from disk.

    Parameters
    ----------
    workspace_root : Path
        Workspace root directory.

    Returns
    -------
    dict of str to Any or None
        Manifest payload when readable and valid, otherwise `None`.
    """

    path = workspace_manifest_path(workspace_root)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def save_workspace_manifest(workspace_root: Path, payload: dict[str, Any]) -> None:
    """Persist workspace package overlay manifest.

    Parameters
    ----------
    workspace_root : Path
        Workspace root directory.
    payload : dict of str to Any
        Manifest payload to write.

    Returns
    -------
    None
        Manifest file is updated in place.
    """

    workspace_root.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(workspace_manifest_path(workspace_root), payload)


def _entry_is_exportable(entry: Path) -> bool:
    """Return whether a package top-level entry can be overlaid.

    Parameters
    ----------
    entry : Path
        Candidate package root entry.

    Returns
    -------
    bool
        `True` when entry is not hidden/reserved and not in skip lists.
    """

    return is_exportable_entry_name(entry.name)


def _normalize_overlay_entry_name(raw: str) -> str:
    """Validate and normalize one overlay entry name.

    Parameters
    ----------
    raw : str
        Candidate top-level entry name.

    Returns
    -------
    str
        Validated entry name.

    Raises
    ------
    PackageValidationError
        Raised when the entry is empty, nested, or non-exportable.
    """

    name = raw.strip()
    if not name:
        raise PackageValidationError("Overlay entry names cannot be empty.")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise PackageValidationError(
            "Overlay entry names must be top-level names (no path separators)."
        )
    if not _entry_is_exportable(Path(name)):
        raise PackageValidationError(f"Overlay entry is not exportable: {name}")
    return name


def _normalize_overlay_entries(raw: Any) -> list[str] | None:
    """Normalize overlay selection input from metadata or CLI values.

    Parameters
    ----------
    raw : Any
        `None`, comma-separated string, or list of entry names.

    Returns
    -------
    list of str or None
        Deduplicated, validated overlay names or `None` for no restriction.

    Raises
    ------
    PackageValidationError
        Raised when the input type or entries are invalid.
    """

    if raw is None:
        return None

    if isinstance(raw, str):
        candidates = [segment for segment in raw.split(",")]
    elif isinstance(raw, list):
        candidates = raw
    else:
        raise PackageValidationError(
            "Package metadata field overlay_entries must be a list or comma-separated string."
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            raise PackageValidationError("overlay_entries can only contain strings.")
        if not candidate.strip():
            continue
        entry = _normalize_overlay_entry_name(candidate)
        if entry in seen:
            continue
        seen.add(entry)
        normalized.append(entry)
    return normalized


def _normalize_dependency_package_ids(
    raw: Any,
    *,
    package_id: str | None = None,
) -> list[str] | None:
    """Normalize dependency package id metadata input.

    Parameters
    ----------
    raw : Any
        `None`, comma-separated string, or list of dependency package ids.
    package_id : str or None, optional
        Owning package id. Self-dependencies are ignored.

    Returns
    -------
    list of str or None
        Deduplicated, normalized package ids or `None` for no dependencies.

    Raises
    ------
    PackageValidationError
        Raised when input type is invalid or an item cannot be normalized.
    """

    if raw is None:
        return None

    if isinstance(raw, str):
        candidates = [segment for segment in raw.split(",")]
    elif isinstance(raw, list):
        candidates = raw
    else:
        raise PackageValidationError(
            "Package metadata field dependency_package_ids must be a list or comma-separated string."
        )

    owner_id: str | None = None
    if isinstance(package_id, str) and package_id:
        owner_id = normalize_package_id(package_id)

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            raise PackageValidationError(
                "dependency_package_ids can only contain strings."
            )
        trimmed = candidate.strip()
        if not trimmed:
            continue
        try:
            dependency_id = normalize_package_id(trimmed)
        except PackageValidationError as exc:
            raise PackageValidationError(
                f"Invalid dependency package id: {trimmed!r}"
            ) from exc
        if owner_id and dependency_id == owner_id:
            continue
        if dependency_id in seen:
            continue
        seen.add(dependency_id)
        normalized.append(dependency_id)
    return normalized


def iter_package_entries(
    package_root: Path,
    include_names: list[str] | None = None,
) -> tuple[list[Path], list[str]]:
    """List exportable package root entries with optional filtering.

    Parameters
    ----------
    package_root : Path
        Installed package root directory.
    include_names : list of str or None, optional
        Optional allow-list of top-level names to include.

    Returns
    -------
    tuple of (list of Path, list of str)
        Selected entry paths and requested names that were missing.

    Raises
    ------
    PackageValidationError
        Raised when `package_root` is missing or not a directory.
    """

    if not package_root.exists() or not package_root.is_dir():
        raise PackageValidationError(f"Package root is invalid: {package_root}")
    exportable_entries = [
        entry
        for entry in sorted(package_root.iterdir(), key=lambda p: p.name)
        if _entry_is_exportable(entry)
    ]
    if include_names is None:
        return exportable_entries, []

    by_name = {entry.name: entry for entry in exportable_entries}
    selected: list[Path] = []
    missing: list[str] = []
    for name in include_names:
        found = by_name.get(name)
        if found is None:
            missing.append(name)
        else:
            selected.append(found)
    return selected, missing


def _manifest_entry_names(manifest: dict[str, Any] | None) -> set[str]:
    """Extract linked entry names from a workspace manifest payload.

    Parameters
    ----------
    manifest : dict of str to Any or None
        Workspace manifest payload.

    Returns
    -------
    set of str
        Linked entry names found in the manifest.
    """

    return extract_manifest_entry_names(manifest)


def _manifest_dependency_ids(manifest: dict[str, Any] | None) -> set[str]:
    """Extract linked dependency package ids from a workspace manifest payload.

    Parameters
    ----------
    manifest : dict of str to Any or None
        Workspace manifest payload.

    Returns
    -------
    set of str
        Dependency package ids found in the manifest.
    """

    return extract_manifest_dependency_ids(manifest)


def _remove_managed_symlinks(
    repo_dir: Path,
    manifest: dict[str, Any] | None,
    *,
    only_names: set[str] | None = None,
) -> None:
    """Remove entries previously managed by package overlay.

    Parameters
    ----------
    repo_dir : Path
        Workspace repository directory.
    manifest : dict of str to Any or None
        Previous workspace manifest containing linked entries.
    only_names : set of str or None, optional
        Optional subset of entry names to remove.

    Returns
    -------
    None
        Matching managed entries are removed in place.
    """

    _remove_managed_entries_shared(
        repo_dir,
        manifest,
        only_names=only_names,
        remove_non_symlink_entries=True,
        remove_existing=_remove_existing_entry,
    )


def _remove_managed_dependency_links(
    repo_dir: Path,
    manifest: dict[str, Any] | None,
    *,
    only_package_ids: set[str] | None = None,
) -> None:
    """Remove dependency package links previously managed by package overlay.

    Parameters
    ----------
    repo_dir : Path
        Workspace repository directory.
    manifest : dict of str to Any or None
        Previous workspace manifest containing dependency link entries.
    only_package_ids : set of str or None, optional
        Optional subset of dependency package ids to remove.

    Returns
    -------
    None
        Matching dependency links are removed in place.
    """

    _remove_managed_dependency_links_shared(
        repo_dir,
        manifest,
        normalize_package_id=normalize_package_id,
        only_package_ids=only_package_ids,
        dependencies_dirname=PACKAGE_DEPENDENCIES_DIRNAME,
        remove_existing=_remove_existing_entry,
    )


def _link_or_copy_entry(src: Path, dst: Path) -> str:
    """Create a symlink for a package entry, falling back to copy on failure.

    Parameters
    ----------
    src : Path
        Source file or directory in the installed package.
    dst : Path
        Destination path in the workspace repository.

    Returns
    -------
    str
        `"symlink"` when linked, `"copy"` when copied, or `"existing"` when
        destination already exists.
    """

    return _link_or_copy_entry_shared(src, dst)


def _remove_existing_entry(path: Path) -> None:
    """Delete a file/symlink/directory path if it already exists.

    Parameters
    ----------
    path : Path
        Entry to remove.

    Returns
    -------
    None
        Existing path is removed in place.
    """

    _remove_existing_entry_shared(path)


def _resolve_package_meta_path(package_meta: dict[str, Any]) -> Path:
    """Validate and resolve package install path from metadata.

    Parameters
    ----------
    package_meta : dict of str to Any
        Package metadata containing an `installed_path`.

    Returns
    -------
    Path
        Existing package directory path.

    Raises
    ------
    PackageValidationError
        Raised when metadata is missing `installed_path` or path is invalid.
    """

    raw = package_meta.get("installed_path")
    if not isinstance(raw, str) or not raw:
        raise PackageValidationError("Package metadata is missing installed_path.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists() or not path.is_dir():
        raise PackageValidationError(f"Installed package path is invalid: {path}")
    return path


def resolve_session_package(
    scipkg_root: Path,
    workspace_root: Path,
    requested_package_id: str | None = None,
) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    """Resolve which package should be overlaid for a workspace session.

    Resolution order is explicit request, workspace manifest pin, `FERMILINK_SCIPKG_ACTIVE`,
    then registry active package.

    Parameters
    ----------
    scipkg_root : Path
        Scientific package root.
    workspace_root : Path
        Session workspace root.
    requested_package_id : str or None, optional
        Optional explicit package id from the run request.

    Returns
    -------
    tuple
        `(package_id, package_meta)` when a valid package is selected;
        `(None, None)` when no package is available.

    Raises
    ------
    PackageNotFoundError
        Raised when the requested package id does not exist.
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
        _resolve_package_meta_path(requested_meta)
        return requested_id, requested_meta

    manifest = load_workspace_manifest(workspace_root)
    if isinstance(manifest, dict):
        pinned = manifest.get("package_id")
        if isinstance(pinned, str):
            try:
                pinned = normalize_package_id(pinned)
            except PackageValidationError:
                pinned = None
            if pinned:
                pinned_meta = packages.get(pinned)
                if isinstance(pinned_meta, dict):
                    try:
                        _resolve_package_meta_path(pinned_meta)
                        return pinned, pinned_meta
                    except PackageValidationError:
                        pass

    env_active = os.getenv("FERMILINK_SCIPKG_ACTIVE", "").strip()
    if env_active:
        try:
            env_id = normalize_package_id(env_active)
            env_meta = packages.get(env_id)
            if isinstance(env_meta, dict):
                _resolve_package_meta_path(env_meta)
                return env_id, env_meta
        except (PackageValidationError, PackageNotFoundError):
            pass

    registry_active = registry.get("active_package")
    if isinstance(registry_active, str):
        try:
            active_id = normalize_package_id(registry_active)
            active_meta = packages.get(active_id)
            if isinstance(active_meta, dict):
                _resolve_package_meta_path(active_meta)
                return active_id, active_meta
        except PackageValidationError:
            pass

    return None, None


def overlay_package_into_repo(
    repo_dir: Path,
    workspace_root: Path,
    package_id: str,
    package_meta: dict[str, Any],
    scipkg_root: Path | None = None,
    *,
    allow_replace_existing: bool = False,
) -> dict[str, Any]:
    """Overlay selected package entries into a workspace repository.

    Parameters
    ----------
    repo_dir : Path
        Workspace repository path that receives linked/copied entries.
    workspace_root : Path
        Session workspace root storing the overlay manifest.
    package_id : str
        Selected package id.
    package_meta : dict of str to Any
        Package metadata containing installation details.
    scipkg_root : Path or None, optional
        Scientific package root used to resolve dependency package metadata.
        Defaults to the configured global package root.
    allow_replace_existing : bool, optional
        Whether existing repo entries may be replaced during overlay.

    Returns
    -------
    dict[str, Any]
        Overlay summary including linked entries, collisions, and requested
        entry diagnostics, plus dependency package link diagnostics.
    """

    def _load_package_map(
        _package_id: str, _package_meta: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            effective_scipkg_root = (
                scipkg_root if isinstance(scipkg_root, Path) else resolve_scipkg_root()
            )
            registry = load_registry(effective_scipkg_root)
            maybe_packages = registry.get("packages", {})
            if isinstance(maybe_packages, dict):
                return maybe_packages
            return {}
        except Exception:
            return {}

    return overlay_package_into_repo_core(
        repo_dir=repo_dir,
        workspace_root=workspace_root,
        package_id=package_id,
        package_meta=package_meta,
        allow_replace_existing=allow_replace_existing,
        now_iso=_now_iso,
        resolve_package_meta_path=_resolve_package_meta_path,
        normalize_overlay_entries=_normalize_overlay_entries,
        normalize_dependency_ids=_normalize_dependency_package_ids,
        iter_package_entries=iter_package_entries,
        load_workspace_manifest=load_workspace_manifest,
        save_workspace_manifest=save_workspace_manifest,
        normalize_package_id=normalize_package_id,
        get_package_map=_load_package_map,
        replace_existing_entries_for_previous_names=False,
        remove_non_symlink_managed_entries=True,
        overlay_entries_key=PACKAGE_OVERLAY_ENTRIES_KEY,
        dependency_ids_key=PACKAGE_DEPENDENCY_IDS_KEY,
        dependencies_dirname=PACKAGE_DEPENDENCIES_DIRNAME,
        remove_existing=_remove_existing_entry,
        link_or_copy=_link_or_copy_entry,
    )
