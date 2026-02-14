from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def _cli():
    from fermilink import cli

    return cli


def _save_curated_install_metadata(
    scipkg_root: Path,
    package_id: str,
    *,
    channel: str,
    curated_package_id: str,
    version_id: str,
    source_archive_url: str,
    verified: bool,
    source_ref_type: str | None,
    source_ref_value: str | None,
) -> None:
    cli = _cli()
    normalized_id = cli.normalize_package_id(package_id)
    registry = cli.load_registry(scipkg_root)
    packages = registry.get("packages")
    if not isinstance(packages, dict):
        return

    meta = packages.get(normalized_id)
    if not isinstance(meta, dict):
        return

    updated = dict(meta)
    updated["curated"] = {
        "channel": channel,
        "package_id": curated_package_id,
        "version_id": version_id,
        "source_archive_url": source_archive_url,
        "verified": verified,
        "source_ref": {
            "type": source_ref_type,
            "value": source_ref_value,
        },
    }
    packages[normalized_id] = updated
    cli.save_registry(scipkg_root, registry)


def cmd_compile(args: argparse.Namespace) -> int:
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)
    project_root = cli._resolve_project_path(args.project_path)
    if not project_root.exists() or not project_root.is_dir():
        raise cli.PackageError(f"Compile path is not a directory: {project_root}")

    registry = cli.load_registry(scipkg_root)
    packages = registry.get("packages", {})
    if isinstance(packages, dict) and package_id in packages:
        raise cli.PackageError(
            f"Warning: package id '{package_id}' already exists. "
            "Choose a new package id for compile."
        )

    tool_source = cli._resolve_compile_tool_source()
    if not tool_source.is_dir():
        raise cli.PackageError(f"Missing compile tool source: {tool_source}")

    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    provider_bin = cli.resolve_provider_binary(
        provider,
        codex_bin=cli.DEFAULT_COMPILE_CODEX_BIN if provider == "codex" else None,
    )

    tool_dest = project_root / "sci-skills-generator"
    if tool_dest.exists():
        raise cli.PackageError(
            f"Compile path already contains {tool_dest.name}/. "
            "Remove it first or choose a different path."
        )

    shutil.copytree(tool_source, tool_dest)
    compile_runs: list[dict[str, object]] = []

    try:
        compile_runs.append(
            cli._run_codex_compile_pass(
                project_root,
                prompt=cli.COMPILE_PROMPT_1,
                pass_index=1,
                total_passes=3,
                provider=provider,
                provider_bin=provider_bin,
            )
        )
        compile_runs.append(
            cli._run_codex_compile_pass(
                project_root,
                prompt=cli.COMPILE_PROMPT_2,
                pass_index=2,
                total_passes=3,
                provider=provider,
                provider_bin=provider_bin,
            )
        )
    finally:
        shutil.rmtree(tool_dest, ignore_errors=True)

    if tool_dest.exists():
        raise cli.PackageError(
            f"Failed to clean up temporary tool directory: {tool_dest}"
        )

    compile_runs.append(
        cli._run_codex_compile_pass(
            project_root,
            prompt=cli.COMPILE_PROMPT_3,
            pass_index=3,
            total_passes=3,
            provider=provider,
            provider_bin=provider_bin,
        )
    )

    installed = cli.install_from_local_path(
        scipkg_root,
        package_id,
        local_path=project_root,
        title=args.title,
        activate=args.activate,
        force=False,
    )

    router_sync = None
    if not args.no_router_sync:
        router_sync = cli.sync_router_rules(scipkg_root)

    active = cli.load_registry(scipkg_root).get("active_package")
    payload = {
        "compiled_package_id": package_id,
        "project_root": str(project_root),
        "compile_runs": compile_runs,
        "installed": installed,
        "active_package": active,
        "router_sync": router_sync,
        "scipkg_root": str(scipkg_root),
    }
    lines = [
        f"Compiled skills for '{package_id}' from {project_root}.",
        (
            f"Installed to scientific packages. Active package: {active}."
            if isinstance(active, str) and active
            else "Installed to scientific packages."
        ),
    ]
    cli._emit_output(args, payload, lines)
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    raw_package_id = getattr(args, "package_id", None)
    if isinstance(raw_package_id, list):
        requested_ids = [
            item for item in raw_package_id if isinstance(item, str) and item.strip()
        ]
    elif isinstance(raw_package_id, str) and raw_package_id.strip():
        requested_ids = [raw_package_id.strip()]
    else:
        requested_ids = []
    if not requested_ids:
        raise cli.PackageError("Package id is required for fermilink install.")

    requested_version_raw = getattr(args, "version_id", None)
    requested_version = (
        str(requested_version_raw).strip()
        if isinstance(requested_version_raw, str)
        else ""
    )
    if requested_version == "":
        requested_version = None

    require_verified = bool(getattr(args, "require_verified", False))
    if requested_version and (args.local_path or args.zip_url):
        raise cli.PackageError("--version only applies to curated channel installs.")
    if require_verified and (args.local_path or args.zip_url):
        raise cli.PackageError(
            "--require-verified only applies to curated channel installs."
        )

    package_ids = [cli.normalize_package_id(item) for item in requested_ids]
    normalized_channel = cli.normalize_channel_id(args.channel)
    if len(package_ids) > 1:
        if args.activate:
            raise cli.PackageError(
                "Cannot combine multiple package ids with --activate/--active. "
                "Install them first, then run `fermilink activate <package_id>`."
            )
        if args.local_path:
            raise cli.PackageError(
                "Cannot combine multiple package ids with --local-path."
            )
        if args.zip_url:
            raise cli.PackageError(
                "Cannot combine multiple package ids with --zip-url."
            )
        if args.title:
            raise cli.PackageError("Cannot combine multiple package ids with --title.")
        if requested_version:
            raise cli.PackageError(
                "Cannot combine multiple package ids with --version."
            )

        installed: list[dict[str, object]] = []
        sources: dict[str, str] = {}
        selected_versions: dict[str, str] = {}
        unverified: list[str] = []
        for package_id in package_ids:
            curated = cli.resolve_curated_package(
                package_id, channel=normalized_channel
            )
            selected_version = cli.select_package_version(curated)
            if require_verified and not selected_version.verified:
                raise cli.PackageError(
                    f"Selected curated version '{selected_version.version_id}' for package "
                    f"'{package_id}' in channel '{normalized_channel}' is not verified. "
                    "Use a verified version or remove --require-verified."
                )
            if not selected_version.verified:
                unverified.append(f"{package_id}@{selected_version.version_id}")

            meta = cli.install_from_zip(
                scipkg_root,
                package_id,
                zip_url=selected_version.source_archive_url,
                title=curated.title,
                activate=False,
                force=args.force,
                max_zip_bytes=args.max_zip_bytes,
            )
            installed_id = str(meta.get("id") or package_id)
            _save_curated_install_metadata(
                scipkg_root,
                installed_id,
                channel=normalized_channel,
                curated_package_id=curated.package_id,
                version_id=selected_version.version_id,
                source_archive_url=selected_version.source_archive_url,
                verified=selected_version.verified,
                source_ref_type=selected_version.source_ref_type,
                source_ref_value=selected_version.source_ref_value,
            )
            installed.append(meta)
            sources[installed_id] = str(selected_version.source_archive_url)
            selected_versions[installed_id] = selected_version.version_id

        router = None
        if not args.no_router_sync:
            router = cli.sync_router_rules(scipkg_root)

        active = cli.load_registry(scipkg_root).get("active_package")
        payload = {
            "installed": installed,
            "sources": sources,
            "selected_versions": selected_versions,
            "require_verified": require_verified,
            "scipkg_root": str(scipkg_root),
            "router_sync": router,
            "active_package": active,
        }
        if unverified:
            payload["unverified_versions"] = unverified
        summary = ", ".join(
            str(item.get("id") or "") for item in installed if isinstance(item, dict)
        )
        summary = summary or ", ".join(package_ids)
        lines = [
            f"Installed {len(installed)} packages: {summary}.",
            (
                f"Active package: {active}."
                if isinstance(active, str) and active
                else "Active package unchanged."
            ),
        ]
        if unverified:
            lines.append(
                "Warning: installed unverified curated versions: "
                + ", ".join(unverified)
                + "."
            )
        cli._emit_output(args, payload, lines)
        return 0

    package_id = package_ids[0]

    title = args.title
    source: str
    selected_unverified_label: str | None = None
    if args.local_path:
        meta = cli.install_from_local_path(
            scipkg_root,
            package_id,
            local_path=Path(args.local_path),
            title=title,
            activate=args.activate,
            force=args.force,
        )
        source = f"local-path:{Path(args.local_path).expanduser().resolve()}"
    else:
        zip_url = args.zip_url
        selected_version_id: str | None = None
        selected_version_verified: bool | None = None
        selected_source_ref: dict[str, str | None] | None = None
        if not zip_url:
            curated = cli.resolve_curated_package(
                package_id, channel=normalized_channel
            )
            selected_version = cli.select_package_version(
                curated, version_id=requested_version
            )
            if require_verified and not selected_version.verified:
                raise cli.PackageError(
                    f"Selected curated version '{selected_version.version_id}' for package "
                    f"'{package_id}' in channel '{normalized_channel}' is not verified. "
                    "Use a verified version or remove --require-verified."
                )
            zip_url = selected_version.source_archive_url
            if title is None:
                title = curated.title
            selected_version_id = selected_version.version_id
            selected_version_verified = selected_version.verified
            selected_source_ref = {
                "type": selected_version.source_ref_type,
                "value": selected_version.source_ref_value,
            }
            if not selected_version.verified:
                selected_unverified_label = (
                    f"{package_id}@{selected_version.version_id}"
                )

        meta = cli.install_from_zip(
            scipkg_root,
            package_id,
            zip_url=zip_url,
            title=title,
            activate=args.activate,
            force=args.force,
            max_zip_bytes=args.max_zip_bytes,
        )
        installed_id = str(meta.get("id") or package_id)
        if not args.zip_url:
            _save_curated_install_metadata(
                scipkg_root,
                installed_id,
                channel=normalized_channel,
                curated_package_id=package_id,
                version_id=selected_version_id or "branch-head",
                source_archive_url=str(zip_url),
                verified=bool(selected_version_verified),
                source_ref_type=(
                    selected_source_ref.get("type") if selected_source_ref else None
                ),
                source_ref_value=(
                    selected_source_ref.get("value") if selected_source_ref else None
                ),
            )
        source = str(zip_url)

    router = None
    if not args.no_router_sync:
        router = cli.sync_router_rules(scipkg_root)

    payload = {
        "installed": meta,
        "source": source,
        "requested_version": requested_version,
        "require_verified": require_verified,
        "scipkg_root": str(scipkg_root),
        "router_sync": router,
    }
    active = cli.load_registry(scipkg_root).get("active_package")
    lines = [
        f"Installed package '{meta.get('id', package_id)}' from {source}.",
        (
            f"Active package: {active}."
            if isinstance(active, str) and active
            else "Active package unchanged."
        ),
    ]
    if selected_unverified_label:
        lines.append(
            f"Warning: installed unverified curated version: {selected_unverified_label}."
        )
    cli._emit_output(args, payload, lines)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    registry = cli.load_registry(scipkg_root)
    packages = cli.list_packages(scipkg_root)
    package_ids = sorted(packages.keys()) if isinstance(packages, dict) else []
    active = registry.get("active_package")
    payload = {
        "scipkg_root": str(scipkg_root),
        "active_package": active,
        "packages": packages,
    }
    summary = ", ".join(package_ids) if package_ids else "(none)"
    lines = [
        f"Installed packages: {len(package_ids)}. Active: {active or 'none'}.",
        f"Packages: {summary}.",
    ]
    cli._emit_output(args, payload, lines)
    return 0


def cmd_avail(args: argparse.Namespace) -> int:
    cli = _cli()
    query = str(getattr(args, "query", "") or "").strip()
    if not query:
        raise cli.PackageError("Query is required for fermilink avail.")
    normalized_channel = cli.normalize_channel_id(getattr(args, "channel", None))
    curated_packages = cli.list_curated_packages(channel=normalized_channel)

    lowered_query = query.lower()
    exact_match = curated_packages.get(lowered_query)
    matched: list[dict[str, object]] = []
    if exact_match is not None:
        versions = [
            {
                "version_id": version.version_id,
                "source_archive_url": version.source_archive_url,
                "verified": version.verified,
                "source_ref": {
                    "type": version.source_ref_type,
                    "value": version.source_ref_value,
                },
            }
            for version in exact_match.versions
        ]
        matched.append(
            {
                "package_id": exact_match.package_id,
                "title": exact_match.title,
                "zip_url": exact_match.zip_url,
                "match_type": "exact",
                "description": exact_match.description or "",
                "upstream_repo_url": exact_match.upstream_repo_url or "",
                "homepage_url": exact_match.homepage_url or "",
                "tags": list(exact_match.tags),
                "default_version": exact_match.default_version,
                "versions": versions,
            }
        )
    else:
        for package in curated_packages.values():
            package_id = package.package_id.lower()
            title = package.title.lower()
            if lowered_query in package_id or lowered_query in title:
                versions = [
                    {
                        "version_id": version.version_id,
                        "source_archive_url": version.source_archive_url,
                        "verified": version.verified,
                        "source_ref": {
                            "type": version.source_ref_type,
                            "value": version.source_ref_value,
                        },
                    }
                    for version in package.versions
                ]
                matched.append(
                    {
                        "package_id": package.package_id,
                        "title": package.title,
                        "zip_url": package.zip_url,
                        "match_type": "partial",
                        "description": package.description or "",
                        "upstream_repo_url": package.upstream_repo_url or "",
                        "homepage_url": package.homepage_url or "",
                        "tags": list(package.tags),
                        "default_version": package.default_version,
                        "versions": versions,
                    }
                )
    matched.sort(key=lambda item: str(item.get("package_id") or ""))
    payload = {
        "channel": normalized_channel,
        "query": query,
        "found": bool(matched),
        "results": matched,
        "total_curated_packages": len(curated_packages),
    }
    if matched:
        lines = [
            f"Found {len(matched)} package(s) in channel '{normalized_channel}' for '{query}'.",
        ]
        for item in matched:
            versions = item.get("versions")
            version_list = (
                ", ".join(
                    f"{str(version.get('version_id'))}{'' if bool(version.get('verified')) else ' (unverified)'}"
                    for version in versions
                    if isinstance(version, dict)
                )
                if isinstance(versions, list)
                else ""
            )
            default_version = str(item.get("default_version") or "branch-head")
            base_line = (
                f"{item['package_id']}: {item['title']} ({item['zip_url']}) "
                f"[default={default_version}]"
            )
            lines.append(base_line)
            description = str(item.get("description") or "").strip()
            if description:
                lines.append(f"  - {description}")
            if version_list:
                lines.append(f"  - versions: {version_list}")
    else:
        lines = [
            f"No curated package matched '{query}' in channel '{normalized_channel}'.",
            (
                "Try `fermilink list` to see installed packages, or "
                "`fermilink install <package_id>` for an exact curated id."
            ),
        ]
    cli._emit_output(args, payload, lines)
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)
    meta = cli.activate_package(scipkg_root, package_id)
    payload = {
        "active_package": package_id,
        "meta": meta,
        "scipkg_root": str(scipkg_root),
    }
    cli._emit_output(args, payload, [f"Active package set to '{package_id}'."])
    return 0


def _collect_csv_and_repeat(
    values: list[str] | None, csv_value: str | None
) -> list[str]:
    collected: list[str] = []
    if values:
        collected.extend(values)
    if csv_value:
        collected.extend(csv_value.split(","))
    return collected


def cmd_overlay(args: argparse.Namespace) -> int:
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)

    collected = _collect_csv_and_repeat(args.entry, args.entries_csv)
    if args.clear and collected:
        raise cli.PackageError("Cannot combine --clear with --entry/--entries.")

    if args.clear:
        entries: list[str] | None = None
    else:
        if not collected:
            raise cli.PackageError(
                "Provide --entry/--entries to set exposed items, or use --clear."
            )
        entries = collected

    meta = cli.set_package_overlay_entries(scipkg_root, package_id, entries)
    overlay_entries = meta.get("overlay_entries")
    if isinstance(overlay_entries, list) and overlay_entries:
        entry_text = ", ".join(str(item) for item in overlay_entries)
    else:
        entry_text = "(all exportable entries)"
    payload = {
        "package_id": package_id,
        "overlay_entries": overlay_entries,
        "meta": meta,
        "scipkg_root": str(scipkg_root),
    }
    cli._emit_output(
        args, payload, [f"Overlay entries for '{package_id}': {entry_text}."]
    )
    return 0


def cmd_dependencies(args: argparse.Namespace) -> int:
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)

    collected = _collect_csv_and_repeat(args.package, args.packages_csv)
    if args.clear and collected:
        raise cli.PackageError("Cannot combine --clear with --package/--packages.")

    if args.clear:
        dependency_ids: list[str] | None = None
    else:
        if not collected:
            raise cli.PackageError(
                "Provide --package/--packages to set dependencies, or use --clear."
            )
        dependency_ids = collected

    meta = cli.set_package_dependency_ids(scipkg_root, package_id, dependency_ids)
    dependency_ids = meta.get("dependency_package_ids")
    if isinstance(dependency_ids, list) and dependency_ids:
        deps_text = ", ".join(str(item) for item in dependency_ids)
    else:
        deps_text = "(none)"
    payload = {
        "package_id": package_id,
        "dependency_package_ids": dependency_ids,
        "meta": meta,
        "scipkg_root": str(scipkg_root),
    }
    cli._emit_output(args, payload, [f"Dependencies for '{package_id}': {deps_text}."])
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)
    result = cli.delete_package(
        scipkg_root,
        package_id,
        remove_files=not args.keep_files,
    )

    router = None
    if not args.no_router_sync:
        router = cli.sync_router_rules(scipkg_root)

    payload = {
        "deleted": result,
        "router_sync": router,
        "scipkg_root": str(scipkg_root),
    }
    removed_files = bool(result.get("removed_files"))
    active = result.get("active_package")
    lines = [
        f"Deleted package '{package_id}' from registry. Removed files: {'yes' if removed_files else 'no'}.",
        (
            f"Active package: {active}."
            if isinstance(active, str) and active
            else "No active package set."
        ),
    ]
    cli._emit_output(args, payload, lines)
    return 0
