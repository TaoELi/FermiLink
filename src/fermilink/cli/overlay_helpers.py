from __future__ import annotations

from pathlib import Path


def _cli():
    from fermilink import cli

    return cli


def _is_public_overlay_name(name: str) -> bool:
    return name.strip().strip("/\\").lower() == "public"


def _filter_exec_overlay_package_meta(
    package_meta: dict[str, object],
) -> dict[str, object]:
    """Filter local exec/chat overlay metadata to avoid injecting `public/`."""

    from fermilink.runner.scientific_packages import iter_package_entries

    sanitized: dict[str, object] = dict(package_meta)
    raw_entries = package_meta.get("overlay_entries")
    if raw_entries is None:
        raw_installed_path = package_meta.get("installed_path")
        if not isinstance(raw_installed_path, str) or not raw_installed_path.strip():
            return sanitized
        package_root = Path(raw_installed_path).expanduser()
        if not package_root.is_absolute():
            package_root = (Path.cwd() / package_root).resolve()
        try:
            all_entries, _ = iter_package_entries(package_root, include_names=None)
        except Exception:
            return sanitized
        sanitized["overlay_entries"] = [
            entry.name
            for entry in all_entries
            if not _is_public_overlay_name(entry.name)
        ]
        return sanitized

    if isinstance(raw_entries, str):
        candidates = [segment.strip() for segment in raw_entries.split(",")]
    elif isinstance(raw_entries, list):
        candidates = [
            segment.strip() for segment in raw_entries if isinstance(segment, str)
        ]
    else:
        return sanitized

    sanitized["overlay_entries"] = [
        name for name in candidates if name and not _is_public_overlay_name(name)
    ]
    return sanitized


def _overlay_exec_package(
    *,
    repo_dir: Path,
    scipkg_root: Path,
    package_id: str,
) -> dict[str, object]:
    from fermilink.runner.scientific_packages import (
        overlay_package_into_repo,
        resolve_session_package,
    )

    cli = _cli()
    try:
        resolved_id, package_meta = resolve_session_package(
            scipkg_root=scipkg_root,
            workspace_root=repo_dir,
            requested_package_id=package_id,
        )
    except Exception as exc:
        raise cli.PackageError(str(exc)) from exc

    if not resolved_id or not isinstance(package_meta, dict):
        raise cli.PackageError(
            f"Package '{package_id}' could not be resolved for overlay."
        )
    filtered_package_meta = cli._filter_exec_overlay_package_meta(package_meta)

    try:
        overlay = overlay_package_into_repo(
            repo_dir=repo_dir,
            workspace_root=repo_dir,
            package_id=resolved_id,
            package_meta=filtered_package_meta,
            scipkg_root=scipkg_root,
            allow_replace_existing=False,
        )
    except Exception as exc:
        raise cli.PackageError(str(exc)) from exc

    runner_app = cli._load_runner_app_module()
    source_dir = runner_app._resolve_source_dir()
    runner_app._ensure_template_agents_file(source_dir, repo_dir)
    return overlay


def _cleanup_exec_overlay_symlinks(*, repo_dir: Path, workspace_root: Path) -> None:
    from fermilink.runner import scientific_packages as scipkg

    manifest = scipkg.load_workspace_manifest(workspace_root)
    if not isinstance(manifest, dict):
        return

    linked_entries = manifest.get("linked_entries")
    if isinstance(linked_entries, list):
        for item in linked_entries:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            mode = item.get("mode")
            source = item.get("source")
            if not isinstance(name, str) or not name:
                continue
            if mode != "symlink":
                target = repo_dir / name
                if target.exists() or target.is_symlink():
                    if target.is_symlink() or target.is_file():
                        target.unlink(missing_ok=True)
                    elif target.is_dir():
                        _cli().shutil.rmtree(target, ignore_errors=True)
                continue
            target = repo_dir / name
            if not target.is_symlink():
                continue
            if isinstance(source, str) and source:
                source_path = Path(source).expanduser()
                if not source_path.is_absolute():
                    source_path = (repo_dir / source_path).resolve()
                try:
                    if target.resolve() != source_path.resolve():
                        continue
                except OSError:
                    continue
            target.unlink(missing_ok=True)

    linked_dependencies = manifest.get("linked_dependency_packages")
    dependency_root = repo_dir / scipkg.PACKAGE_DEPENDENCIES_DIRNAME
    if isinstance(linked_dependencies, list):
        for item in linked_dependencies:
            if not isinstance(item, dict):
                continue
            package_id = item.get("package_id")
            mode = item.get("mode")
            source = item.get("source")
            if not isinstance(package_id, str) or not package_id:
                continue
            if mode != "symlink":
                target = dependency_root / package_id
                if target.exists() or target.is_symlink():
                    if target.is_symlink() or target.is_file():
                        target.unlink(missing_ok=True)
                    elif target.is_dir():
                        _cli().shutil.rmtree(target, ignore_errors=True)
                continue
            target = dependency_root / package_id
            if not target.is_symlink():
                continue
            if isinstance(source, str) and source:
                source_path = Path(source).expanduser()
                if not source_path.is_absolute():
                    source_path = (repo_dir / source_path).resolve()
                try:
                    if target.resolve() != source_path.resolve():
                        continue
                except OSError:
                    continue
            target.unlink(missing_ok=True)

    if dependency_root.is_dir():
        try:
            next(dependency_root.iterdir())
        except StopIteration:
            dependency_root.rmdir()
