from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fermilink.config import resolve_fermilink_home

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


def find_project_root(start: Path) -> Path:
    cur = start.resolve()
    for p in [cur.parent, *cur.parents]:
        if (p / "pyproject.toml").exists() or (p / ".git").exists():
            return p
    # Installed wheel/sdist layouts may not include project markers.
    return Path.cwd()


PROJECT_ROOT = find_project_root(Path(__file__))
DEFAULT_MAXWELLLINK_ROOT = PROJECT_ROOT / "maxwelllink"

REGISTRY_FILENAME = "registry.json"
WORKSPACE_MANIFEST_FILENAME = ".package_manifest.json"

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
PACKAGE_OVERLAY_ENTRIES_KEY = "overlay_entries"
PACKAGE_DEPENDENCY_IDS_KEY = "dependency_package_ids"
PACKAGE_DEPENDENCIES_DIRNAME = "external_packages"


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

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


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


def resolve_scipkg_root() -> Path:
    """Resolve and create the scientific package root directory.

    Returns
    -------
    Path
        Existing or newly created scientific package root path.
    """

    raw_scipkg_root = os.getenv("SCIPKG_ROOT")
    if raw_scipkg_root and raw_scipkg_root.strip():
        path = _resolve_path("SCIPKG_ROOT", Path.cwd())
    else:
        raw_scientific_packages_root = os.getenv("SCIENTIFIC_PACKAGES_ROOT")
        if raw_scientific_packages_root and raw_scientific_packages_root.strip():
            path = _resolve_path("SCIENTIFIC_PACKAGES_ROOT", Path.cwd())
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

    return {
        "version": 1,
        "active_package": None,
        "packages": {},
        "updated_at": _now_iso(),
    }


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

    data = _default_registry()
    if not isinstance(payload, dict):
        return data

    packages = payload.get("packages")
    if isinstance(packages, dict):
        normalized_packages: dict[str, Any] = {}
        for raw_id, raw_meta in packages.items():
            if not isinstance(raw_id, str) or not isinstance(raw_meta, dict):
                continue
            try:
                package_id = normalize_package_id(raw_id)
            except PackageValidationError:
                continue
            meta = dict(raw_meta)
            meta["id"] = package_id
            normalized_packages[package_id] = meta

        for package_id, meta in normalized_packages.items():
            raw_dependencies = meta.get(PACKAGE_DEPENDENCY_IDS_KEY)
            try:
                normalized_dependencies = _normalize_dependency_package_ids(
                    raw_dependencies,
                    package_id=package_id,
                )
            except PackageValidationError:
                normalized_dependencies = None
            if normalized_dependencies:
                meta[PACKAGE_DEPENDENCY_IDS_KEY] = normalized_dependencies
            else:
                meta.pop(PACKAGE_DEPENDENCY_IDS_KEY, None)
        data["packages"] = normalized_packages

    active = payload.get("active_package")
    if isinstance(active, str):
        try:
            normalized_active = normalize_package_id(active)
        except PackageValidationError:
            normalized_active = None
        if normalized_active in data["packages"]:
            data["active_package"] = normalized_active

    updated_at = payload.get("updated_at")
    if isinstance(updated_at, str) and updated_at:
        data["updated_at"] = updated_at

    return data


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
    if not path.exists():
        return _default_registry()
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return _default_registry()
    return _normalize_registry(loaded)


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

    env_path = _resolve_path("MAXWELLLINK_ROOT", DEFAULT_MAXWELLLINK_ROOT)
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
        os.getenv("LEGACY_MAXWELLLINK_PACKAGE_ID", "maxwelllink-local")
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

    package_ids: set[str] = set()
    if not isinstance(manifest, dict):
        return package_ids
    linked = manifest.get("linked_dependency_packages")
    if not isinstance(linked, list):
        return package_ids
    for item in linked:
        package_id: str | None = None
        if isinstance(item, dict):
            maybe_id = item.get("package_id") or item.get("name")
            if isinstance(maybe_id, str) and maybe_id:
                package_id = maybe_id
        elif isinstance(item, str) and item:
            package_id = item
        if not package_id:
            continue
        try:
            package_ids.add(normalize_package_id(package_id))
        except PackageValidationError:
            continue
    return package_ids


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
        elif mode != "symlink" and target.exists():
            _remove_existing_entry(target)


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

    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


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

    Resolution order is explicit request, workspace manifest pin, `SCIPKG_ACTIVE`,
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

    env_active = os.getenv("SCIPKG_ACTIVE", "").strip()
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

    package_root = _resolve_package_meta_path(package_meta)
    configured_entries = _normalize_overlay_entries(
        package_meta.get(PACKAGE_OVERLAY_ENTRIES_KEY)
    )
    configured_dependency_ids = (
        _normalize_dependency_package_ids(
            package_meta.get(PACKAGE_DEPENDENCY_IDS_KEY),
            package_id=package_id,
        )
        or []
    )
    entries, missing_requested_entries = iter_package_entries(
        package_root,
        include_names=configured_entries,
    )
    target_entry_names = {entry.name for entry in entries}
    previous_manifest = load_workspace_manifest(workspace_root)
    previous_id = None
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
        stale_names = _manifest_entry_names(previous_manifest) - target_entry_names
        if stale_names:
            _remove_managed_symlinks(
                repo_dir, previous_manifest, only_names=stale_names
            )
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
    linked_dependency_packages: list[dict[str, str]] = []
    missing_dependency_packages: list[str] = []
    dependency_collisions: list[str] = []

    for src in entries:
        dst = repo_dir / src.name
        if dst.is_symlink():
            try:
                same_target = dst.resolve() == src.resolve()
            except OSError:
                same_target = False
            if same_target:
                linked_entries.append(
                    {"name": src.name, "mode": "symlink", "source": str(src.resolve())}
                )
                continue
            if src.name in previous_names:
                dst.unlink(missing_ok=True)
            elif allow_replace_existing:
                dst.unlink(missing_ok=True)
            else:
                collisions.append(src.name)
                continue
        elif dst.exists():
            if allow_replace_existing:
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

    packages_payload: dict[str, Any] = {}
    try:
        effective_scipkg_root = (
            scipkg_root if isinstance(scipkg_root, Path) else resolve_scipkg_root()
        )
        registry = load_registry(effective_scipkg_root)
        maybe_packages = registry.get("packages", {})
        if isinstance(maybe_packages, dict):
            packages_payload = maybe_packages
    except Exception:
        packages_payload = {}

    if package_id not in packages_payload:
        packages_payload = dict(packages_payload)
        packages_payload[package_id] = package_meta

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
        dependency_meta = packages_payload.get(dependency_id)
        dependency_dst = dependency_root / dependency_id
        if not isinstance(dependency_meta, dict):
            missing_dependency_packages.append(dependency_id)
            if dependency_id in previous_dependency_ids:
                _remove_existing_entry(dependency_dst)
            continue
        try:
            dependency_src = _resolve_package_meta_path(dependency_meta)
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
