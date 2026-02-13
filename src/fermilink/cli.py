from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from pathlib import Path

from fermilink.config import resolve_runtime_root, resolve_scipkg_root
from fermilink.curated_channels import normalize_channel_id, resolve_curated_package
from fermilink.package_registry import (
    PackageError,
    PackageNotFoundError,
    PackageValidationError,
    activate_package,
    delete_package,
    install_from_local_path,
    install_from_zip,
    list_packages,
    load_registry,
    normalize_package_id,
    set_package_dependency_ids,
    set_package_overlay_entries,
)
from fermilink.router_rules import sync_router_rules
from fermilink.services import (
    default_service_specs,
    normalize_components,
    service_status,
    start_service,
    stop_service,
)


DEFAULT_MAX_ZIP_BYTES = int(os.getenv("SCIPKG_MAX_ZIP_BYTES", str(800 * 1024 * 1024)))
DEFAULT_BOOTSTRAP_PACKAGE_ID = "maxwelllink"
DEFAULT_BOOTSTRAP_CHANNEL = "tel-research-group"


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def _cmd_install(args: argparse.Namespace) -> int:
    scipkg_root = resolve_scipkg_root()
    package_id = normalize_package_id(args.package_id)

    title = args.title
    source: str
    if args.local_path:
        meta = install_from_local_path(
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
        if not zip_url:
            curated = resolve_curated_package(
                package_id,
                channel=normalize_channel_id(args.channel),
            )
            zip_url = curated.zip_url
            if title is None:
                title = curated.title

        meta = install_from_zip(
            scipkg_root,
            package_id,
            zip_url=zip_url,
            title=title,
            activate=args.activate,
            force=args.force,
            max_zip_bytes=args.max_zip_bytes,
        )
        source = str(zip_url)

    router = None
    if not args.no_router_sync:
        router = sync_router_rules(scipkg_root)

    _print_json(
        {
            "installed": meta,
            "source": source,
            "scipkg_root": str(scipkg_root),
            "router_sync": router,
        }
    )
    return 0


def _cmd_list(_: argparse.Namespace) -> int:
    scipkg_root = resolve_scipkg_root()
    registry = load_registry(scipkg_root)
    _print_json(
        {
            "scipkg_root": str(scipkg_root),
            "active_package": registry.get("active_package"),
            "packages": list_packages(scipkg_root),
        }
    )
    return 0


def _cmd_activate(args: argparse.Namespace) -> int:
    scipkg_root = resolve_scipkg_root()
    package_id = normalize_package_id(args.package_id)
    meta = activate_package(scipkg_root, package_id)
    _print_json(
        {
            "active_package": package_id,
            "meta": meta,
            "scipkg_root": str(scipkg_root),
        }
    )
    return 0


def _collect_csv_and_repeat(values: list[str] | None, csv_value: str | None) -> list[str]:
    collected: list[str] = []
    if values:
        collected.extend(values)
    if csv_value:
        collected.extend(csv_value.split(","))
    return collected


def _cmd_overlay(args: argparse.Namespace) -> int:
    scipkg_root = resolve_scipkg_root()
    package_id = normalize_package_id(args.package_id)

    collected = _collect_csv_and_repeat(args.entry, args.entries_csv)
    if args.clear and collected:
        raise PackageError("Cannot combine --clear with --entry/--entries.")

    if args.clear:
        entries: list[str] | None = None
    else:
        if not collected:
            raise PackageError(
                "Provide --entry/--entries to set exposed items, or use --clear."
            )
        entries = collected

    meta = set_package_overlay_entries(scipkg_root, package_id, entries)
    _print_json(
        {
            "package_id": package_id,
            "overlay_entries": meta.get("overlay_entries"),
            "meta": meta,
            "scipkg_root": str(scipkg_root),
        }
    )
    return 0


def _cmd_dependencies(args: argparse.Namespace) -> int:
    scipkg_root = resolve_scipkg_root()
    package_id = normalize_package_id(args.package_id)

    collected = _collect_csv_and_repeat(args.package, args.packages_csv)
    if args.clear and collected:
        raise PackageError("Cannot combine --clear with --package/--packages.")

    if args.clear:
        dependency_ids: list[str] | None = None
    else:
        if not collected:
            raise PackageError(
                "Provide --package/--packages to set dependencies, or use --clear."
            )
        dependency_ids = collected

    meta = set_package_dependency_ids(scipkg_root, package_id, dependency_ids)
    _print_json(
        {
            "package_id": package_id,
            "dependency_package_ids": meta.get("dependency_package_ids"),
            "meta": meta,
            "scipkg_root": str(scipkg_root),
        }
    )
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    scipkg_root = resolve_scipkg_root()
    package_id = normalize_package_id(args.package_id)
    result = delete_package(
        scipkg_root,
        package_id,
        remove_files=not args.keep_files,
    )

    router = None
    if not args.no_router_sync:
        router = sync_router_rules(scipkg_root)

    _print_json(
        {
            "deleted": result,
            "router_sync": router,
            "scipkg_root": str(scipkg_root),
        }
    )
    return 0


def _resolve_specs(component_names: list[str] | None) -> tuple[list[str], dict[str, object]]:
    names = normalize_components(component_names)
    web_app_path = Path(__file__).resolve().parent / "web" / "app.py"
    specs = default_service_specs(web_app_path=web_app_path)
    return names, specs


def _installed_package_count(registry: dict[str, object]) -> int:
    packages = registry.get("packages")
    if isinstance(packages, dict):
        return len(packages)
    return 0


def _ensure_bootstrap_package_for_services() -> dict[str, object]:
    """Ensure at least one scientific package exists before service startup."""

    scipkg_root = resolve_scipkg_root()
    registry = load_registry(scipkg_root)
    package_count = _installed_package_count(registry)

    if package_count > 0:
        return {
            "status": "skipped",
            "reason": "packages_present",
            "package_count": package_count,
            "scipkg_root": str(scipkg_root),
        }

    warning = (
        "No scientific package is installed yet. "
        "Auto-installing maxwelllink and activating it."
    )
    print(f"[warning] {warning}", file=sys.stderr)

    channel = normalize_channel_id(DEFAULT_BOOTSTRAP_CHANNEL)
    try:
        curated = resolve_curated_package(DEFAULT_BOOTSTRAP_PACKAGE_ID, channel=channel)
        installed = install_from_zip(
            scipkg_root,
            DEFAULT_BOOTSTRAP_PACKAGE_ID,
            zip_url=curated.zip_url,
            title=curated.title,
            activate=True,
            force=False,
            max_zip_bytes=DEFAULT_MAX_ZIP_BYTES,
        )
        router_sync = sync_router_rules(scipkg_root)
    except Exception as exc:  # pragma: no cover - defensive fail-open branch
        return {
            "status": "failed",
            "warning": warning,
            "error": str(exc),
            "package_id": DEFAULT_BOOTSTRAP_PACKAGE_ID,
            "channel": channel,
            "scipkg_root": str(scipkg_root),
        }

    return {
        "status": "installed",
        "warning": warning,
        "package_id": DEFAULT_BOOTSTRAP_PACKAGE_ID,
        "channel": channel,
        "installed": installed,
        "router_sync": router_sync,
        "scipkg_root": str(scipkg_root),
    }


def _is_start_result_failed(result: dict[str, object]) -> bool:
    status = result.get("status")
    if status in {"port_in_use", "failed_to_start", "error"}:
        return True
    return False


def _start_sequence(
    runtime_root: Path,
    names: list[str],
    specs: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]], bool]:
    results: list[dict[str, object]] = []
    rollback: list[dict[str, object]] = []
    started_now: list[str] = []
    failed = False

    for name in names:
        result = start_service(runtime_root, specs[name])
        results.append(result)
        if _is_start_result_failed(result):
            failed = True
            break
        if result.get("status") == "started":
            started_now.append(name)

    if failed and started_now:
        for started_name in reversed(started_now):
            rollback.append(stop_service(runtime_root, started_name))

    return results, rollback, failed


def _cmd_start(args: argparse.Namespace) -> int:
    runtime_root = resolve_runtime_root()
    names, specs = _resolve_specs(args.components)
    bootstrap = _ensure_bootstrap_package_for_services()

    results, rollback, failed = _start_sequence(runtime_root, names, specs)
    payload: dict[str, object] = {
        "runtime_root": str(runtime_root),
        "bootstrap": bootstrap,
        "results": results,
    }
    if rollback:
        payload["rollback"] = rollback
    _print_json(payload)
    return 2 if failed else 0


def _cmd_stop(args: argparse.Namespace) -> int:
    runtime_root = resolve_runtime_root()
    names = normalize_components(args.components)

    results = []
    for name in names:
        results.append(stop_service(runtime_root, name))

    _print_json({"runtime_root": str(runtime_root), "results": results})
    return 0


def _cmd_restart(args: argparse.Namespace) -> int:
    runtime_root = resolve_runtime_root()
    names, specs = _resolve_specs(args.components)
    bootstrap = _ensure_bootstrap_package_for_services()

    stop_results = []
    for name in names:
        stop_results.append(stop_service(runtime_root, name))

    start_results, rollback, failed = _start_sequence(runtime_root, names, specs)
    payload: dict[str, object] = {
        "runtime_root": str(runtime_root),
        "bootstrap": bootstrap,
        "stopped": stop_results,
        "started": start_results,
    }
    if rollback:
        payload["rollback"] = rollback
    _print_json(payload)
    return 2 if failed else 0


def _cmd_status(args: argparse.Namespace) -> int:
    runtime_root = resolve_runtime_root()
    names = normalize_components(args.components)

    results = []
    for name in names:
        results.append(service_status(runtime_root, name))

    _print_json({"runtime_root": str(runtime_root), "results": results})
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fermilink",
        description="Unified FermiLink CLI for package management and service control.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser(
        "install",
        help="Install scientific package from curated channel, zip URL, or local path.",
    )
    install_parser.add_argument("package_id", help="Package id to install, e.g. ase")
    install_parser.add_argument(
        "--channel",
        default="tel-research-group",
        help="Curated source channel (default: tel-research-group).",
    )
    source_group = install_parser.add_mutually_exclusive_group(required=False)
    source_group.add_argument("--zip-url", help="Override with custom zip URL.")
    source_group.add_argument("--local-path", help="Install from local package directory.")
    install_parser.add_argument("--title", help="Display title for package metadata.")
    install_parser.add_argument(
        "--activate",
        "--active",
        action="store_true",
        help="Activate package for new sessions after install.",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing managed package folder.",
    )
    install_parser.add_argument(
        "--max-zip-bytes",
        type=int,
        default=DEFAULT_MAX_ZIP_BYTES,
        help=f"Maximum zip download size in bytes (default: {DEFAULT_MAX_ZIP_BYTES}).",
    )
    install_parser.add_argument(
        "--no-router-sync",
        action="store_true",
        help="Skip automatic router_rules.json synchronization.",
    )
    install_parser.set_defaults(func=_cmd_install)

    list_parser = subparsers.add_parser("list", help="List installed scientific packages.")
    list_parser.set_defaults(func=_cmd_list)

    activate_parser = subparsers.add_parser("activate", help="Set active package.")
    activate_parser.add_argument("package_id")
    activate_parser.set_defaults(func=_cmd_activate)

    overlay_parser = subparsers.add_parser(
        "overlay",
        help="Set which top-level package entries are exposed in workspace repo.",
    )
    overlay_parser.add_argument("package_id")
    overlay_parser.add_argument(
        "--entry",
        action="append",
        help="Top-level package entry name (repeat for multiple).",
    )
    overlay_parser.add_argument(
        "--entries",
        dest="entries_csv",
        help="Comma-separated top-level package entry names.",
    )
    overlay_parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear overlay restriction and expose all exportable entries.",
    )
    overlay_parser.set_defaults(func=_cmd_overlay)

    dependencies_parser = subparsers.add_parser(
        "dependencies",
        help="Set dependency package links under repo/external_packages/.",
    )
    dependencies_parser.add_argument("package_id")
    dependencies_parser.add_argument(
        "--package",
        action="append",
        help="Dependency package id (repeat for multiple).",
    )
    dependencies_parser.add_argument(
        "--packages",
        dest="packages_csv",
        help="Comma-separated dependency package ids.",
    )
    dependencies_parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear dependency package configuration.",
    )
    dependencies_parser.set_defaults(func=_cmd_dependencies)

    delete_parser = subparsers.add_parser("delete", help="Delete installed scientific package.")
    delete_parser.add_argument("package_id")
    delete_parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Only remove registry entry and keep managed files.",
    )
    delete_parser.add_argument(
        "--no-router-sync",
        action="store_true",
        help="Skip automatic router_rules.json synchronization.",
    )
    delete_parser.set_defaults(func=_cmd_delete)

    start_parser = subparsers.add_parser(
        "start",
        help="Start one or more services: runner, web. Default starts both.",
    )
    start_parser.add_argument("components", nargs="*", help="runner and/or web")
    start_parser.set_defaults(func=_cmd_start)

    stop_parser = subparsers.add_parser(
        "stop",
        help="Stop one or more services: runner, web. Default stops both.",
    )
    stop_parser.add_argument("components", nargs="*", help="runner and/or web")
    stop_parser.set_defaults(func=_cmd_stop)

    restart_parser = subparsers.add_parser(
        "restart",
        help="Restart one or more services: runner, web. Default restarts both.",
    )
    restart_parser.add_argument("components", nargs="*", help="runner and/or web")
    restart_parser.set_defaults(func=_cmd_restart)

    status_parser = subparsers.add_parser(
        "status",
        help="Show service status for runner/web. Default checks both.",
    )
    status_parser.add_argument("components", nargs="*", help="runner and/or web")
    status_parser.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except urllib.error.URLError as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 2
    except (PackageError, PackageNotFoundError, PackageValidationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
