from __future__ import annotations

import argparse
from pathlib import Path
import sys

from fermilink.workspace import filesystem as workspace_fs
from fermilink.workspace import payload as workspace_payload
from fermilink.workspace.common import (
    _cli,
    load_runner_scipkg_module,
    resolve_cli_path,
    resolve_exploop_agents_source,
    resolve_software_agents_source,
)
from fermilink.workspace.init import PACKAGE_INIT_WORKSPACE_MODE


def _load_package_init_manifest(destination: Path) -> dict[str, object] | None:
    scipkg = load_runner_scipkg_module()
    manifest = scipkg.load_workspace_manifest(destination)
    if not isinstance(manifest, dict):
        return None
    if manifest.get("workspace_mode") != PACKAGE_INIT_WORKSPACE_MODE:
        return None
    return manifest


def _resolve_package_init_agents_source(manifest: dict[str, object]) -> Path:
    raw = manifest.get("template_agents_source")
    if isinstance(raw, str) and raw.strip():
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if candidate.is_file():
            return candidate
    workflow_type = str(manifest.get("package_workflow_type") or "").strip().lower()
    if workflow_type == "experiment":
        return resolve_exploop_agents_source()
    return resolve_software_agents_source()


def clean_package_workspace(
    destination: Path,
    manifest: dict[str, object],
    *,
    force: bool = False,
) -> None:
    scipkg = load_runner_scipkg_module()
    agents_source = _resolve_package_init_agents_source(manifest)

    workspace_payload.remove_managed_agents_file(
        destination / workspace_payload.AGENTS_FILENAME,
        agents_source,
        force=force,
        managed_symlink_sources=(agents_source,),
    )
    workspace_payload.remove_agents_aliases(destination, force=force)
    scipkg._remove_managed_symlinks(destination, manifest)
    scipkg._remove_managed_dependency_links(destination, manifest)

    manifest_path = scipkg.workspace_manifest_path(destination)
    if workspace_fs.path_exists(manifest_path):
        workspace_fs.remove_path(manifest_path)


def clean_workspace(
    destination: Path,
    payload_root: Path | None = None,
    force: bool = False,
) -> None:
    package_init_manifest = _load_package_init_manifest(destination)
    if package_init_manifest is not None:
        clean_package_workspace(destination, package_init_manifest, force=force)
        return

    if payload_root is None:
        payload_root = workspace_payload.resolve_payload_root()
    workspace_payload.ensure_valid_payload_root(payload_root)

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
            workspace_payload.remove_managed_agents_file(
                target_path,
                agents_source,
                force=force,
                managed_symlink_sources=workspace_payload.managed_agents_symlink_sources(
                    payload_root, agents_source, source_path
                ),
            )
            continue
        if source_path.name in workspace_payload.COPIED_PAYLOAD_DIRECTORIES:
            workspace_fs.remove_managed_copied_directory(
                target_path,
                source_path,
                force=force,
            )
            continue
        workspace_fs.remove_managed_symlink(
            target_path,
            source_path,
            force=force,
        )
    if has_agents_entry:
        workspace_payload.remove_agents_aliases(destination, force=force)


def cmd_clean(args: argparse.Namespace) -> int:
    cli = _cli()
    destination = resolve_cli_path(getattr(args, "destination", "."))
    try:
        clean_workspace(destination, force=bool(args.force))
    except Exception as exc:
        raise cli.PackageError(str(exc)) from exc

    print(f"[fermilink-clean] Workspace cleaned in {destination}")
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
    destination = resolve_cli_path(getattr(args, "destination", "."))

    try:
        clean_workspace(destination, force=bool(args.force))
    except Exception as exc:
        print(f"[fermilink-clean] ERROR: {exc}", file=sys.stderr)
        return 2

    print("[fermilink-clean] Workspace cleaned in", destination)
    return 0
