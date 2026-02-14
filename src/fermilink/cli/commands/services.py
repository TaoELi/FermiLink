from __future__ import annotations

import argparse
from pathlib import Path


def _cli():
    from fermilink import cli

    return cli


def _resolve_specs(component_names: list[str] | None) -> tuple[list[str], dict[str, object]]:
    cli = _cli()
    names = cli.normalize_components(component_names)
    web_app_path = Path(__file__).resolve().parent.parent.parent / "web" / "app.py"
    specs = cli.default_service_specs(web_app_path=web_app_path)
    return names, specs


def _installed_package_count(registry: dict[str, object]) -> int:
    packages = registry.get("packages")
    if isinstance(packages, dict):
        return len(packages)
    return 0


def _ensure_bootstrap_package_for_services() -> dict[str, object]:
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    registry = cli.load_registry(scipkg_root)
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
    print(f"[warning] {warning}", file=cli.sys.stderr)

    channel = cli.normalize_channel_id(cli.DEFAULT_BOOTSTRAP_CHANNEL)
    try:
        curated = cli.resolve_curated_package(cli.DEFAULT_BOOTSTRAP_PACKAGE_ID, channel=channel)
        installed = cli.install_from_zip(
            scipkg_root,
            cli.DEFAULT_BOOTSTRAP_PACKAGE_ID,
            zip_url=curated.zip_url,
            title=curated.title,
            activate=True,
            force=False,
            max_zip_bytes=cli.DEFAULT_MAX_ZIP_BYTES,
        )
        router_sync = cli.sync_router_rules(scipkg_root)
    except Exception as exc:  # pragma: no cover - defensive fail-open branch
        return {
            "status": "failed",
            "warning": warning,
            "error": str(exc),
            "package_id": cli.DEFAULT_BOOTSTRAP_PACKAGE_ID,
            "channel": channel,
            "scipkg_root": str(scipkg_root),
        }

    return {
        "status": "installed",
        "warning": warning,
        "package_id": cli.DEFAULT_BOOTSTRAP_PACKAGE_ID,
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
    cli = _cli()
    results: list[dict[str, object]] = []
    rollback: list[dict[str, object]] = []
    started_now: list[str] = []
    failed = False

    for name in names:
        result = cli.start_service(runtime_root, specs[name])
        results.append(result)
        if _is_start_result_failed(result):
            failed = True
            break
        if result.get("status") == "started":
            started_now.append(name)

    if failed and started_now:
        for started_name in reversed(started_now):
            rollback.append(cli.stop_service(runtime_root, started_name))

    return results, rollback, failed


def cmd_start(args: argparse.Namespace) -> int:
    cli = _cli()
    runtime_root = cli.resolve_runtime_root()
    names, specs = cli._resolve_specs(args.components)
    bootstrap = cli._ensure_bootstrap_package_for_services()

    results, rollback, failed = cli._start_sequence(runtime_root, names, specs)
    payload: dict[str, object] = {
        "runtime_root": str(runtime_root),
        "bootstrap": bootstrap,
        "results": results,
    }
    if rollback:
        payload["rollback"] = rollback
    lines: list[str] = []
    bootstrap_text = cli._bootstrap_line(bootstrap)
    if bootstrap_text:
        lines.append(bootstrap_text)
    lines.extend(cli._service_start_line(result) for result in results)
    if rollback:
        lines.append("Rollback executed for previously started services.")
    cli._emit_output(args, payload, lines)
    return 2 if failed else 0


def cmd_stop(args: argparse.Namespace) -> int:
    cli = _cli()
    runtime_root = cli.resolve_runtime_root()
    names = cli.normalize_components(args.components)

    results = []
    for name in names:
        results.append(cli.stop_service(runtime_root, name))

    payload = {"runtime_root": str(runtime_root), "results": results}
    lines = [cli._service_stop_line(result) for result in results]
    cli._emit_output(args, payload, lines)
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    cli = _cli()
    runtime_root = cli.resolve_runtime_root()
    names, specs = cli._resolve_specs(args.components)
    bootstrap = cli._ensure_bootstrap_package_for_services()

    stop_results = []
    for name in names:
        stop_results.append(cli.stop_service(runtime_root, name))

    start_results, rollback, failed = cli._start_sequence(runtime_root, names, specs)
    payload: dict[str, object] = {
        "runtime_root": str(runtime_root),
        "bootstrap": bootstrap,
        "stopped": stop_results,
        "started": start_results,
    }
    if rollback:
        payload["rollback"] = rollback
    lines: list[str] = []
    bootstrap_text = cli._bootstrap_line(bootstrap)
    if bootstrap_text:
        lines.append(bootstrap_text)
    lines.extend(cli._service_start_line(result) for result in start_results)
    failed_stops = [item for item in stop_results if item.get("status") == "error"]
    if failed_stops:
        lines.append("Warning: one or more services failed to stop cleanly before restart.")
    if rollback:
        lines.append("Rollback executed for previously started services.")
    cli._emit_output(args, payload, lines)
    return 2 if failed else 0


def cmd_status(args: argparse.Namespace) -> int:
    cli = _cli()
    runtime_root = cli.resolve_runtime_root()
    names = cli.normalize_components(args.components)
    results = [cli.service_status(runtime_root, name) for name in names]
    payload = {"runtime_root": str(runtime_root), "results": results}
    lines = [cli._service_status_line(result) for result in results]
    cli._emit_output(args, payload, lines)
    return 0
