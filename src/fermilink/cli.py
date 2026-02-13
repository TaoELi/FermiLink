from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
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
DEFAULT_COMPILE_CODEX_BIN = os.getenv("CODEX_BIN", "codex")
DEFAULT_COMPILE_SANDBOX = os.getenv("FERMILINK_COMPILE_SANDBOX", "workspace-write")
COMPILE_PROMPT_1 = (
    "Please review the file structure of this scientific package, identify where the "
    "source code, examples, docs, testing, and tutorials are. Then, apply the "
    "sci-skills-generator skill at the project root to create the skills/ folder for "
    "this project. We need not only a file or code map, but also enrich the generated "
    "skills/ folder so that ai agents can start from the skills/ folder to optimally "
    "use this package for advanced scientific simulations or computing."
)
COMPILE_PROMPT_2 = (
    "Please review the file structure of this scientific package, identify where the "
    "source code, examples, docs, testing, and tutorials are.  Then, using the skill "
    "at sci-skills-generator/ at the project root to audit whether the skills/ folder "
    "is sufficient for ai agents to optimally use this package for advanced scientific "
    "simulations or computing. If not, please provide the modifications of skills/ "
    "folder accordingly."
)
COMPILE_PROMPT_3 = (
    "please examine whether the skills/ folder contains the file links that are "
    "consistent with the file structure of this code. If not, provide the "
    "modifications accordingly. Then, examine whether the skills/ folder is sufficient "
    "for ai agents to optimally use this package for advanced scientific simulations or "
    "computing, and please enrich the skills/ folder if not."
)
SUPPRESSED_COMPILE_OUTPUT_MARKERS = (
    "codex_core::rollout::list: state db missing rollout path for thread",
)


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        text = line.strip()
        if text:
            print(text)


def _emit_output(args: argparse.Namespace, payload: dict, lines: list[str]) -> None:
    if getattr(args, "json", False):
        _print_json(payload)
        return
    _print_lines(lines)


def _extract_flag_value(command: list[str], flag: str) -> str | None:
    for index, token in enumerate(command):
        if token == flag:
            if index + 1 < len(command):
                return command[index + 1]
            return None
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
    return None


def _extract_port_from_command(command: object) -> int | None:
    if not isinstance(command, list):
        return None
    raw = _extract_flag_value(command, "--port")
    if not isinstance(raw, str):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _service_start_line(result: dict[str, object]) -> str:
    service = str(result.get("service", "service"))
    status = str(result.get("status", "unknown"))
    port = result.get("port")
    if not isinstance(port, int):
        port = _extract_port_from_command(result.get("command"))
    pid = result.get("pid")
    pid_text = f", pid {pid}" if isinstance(pid, int) else ""
    port_text = f", port {port}" if isinstance(port, int) else ""

    if status == "started":
        return f"{service}: started{port_text}{pid_text}."
    if status == "already_running":
        return f"{service}: already running{port_text}{pid_text}."
    if status == "port_in_use":
        return f"{service}: blocked, port {port} is already in use."
    if status == "failed_to_start":
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int):
            return f"{service}: failed to start (exit code {exit_code})."
        return f"{service}: failed to start."
    if status == "error":
        return f"{service}: error while starting."
    return f"{service}: status={status}."


def _service_stop_line(result: dict[str, object]) -> str:
    service = str(result.get("service", "service"))
    status = str(result.get("status", "unknown"))
    pid = result.get("pid")
    pid_text = f" (pid {pid})" if isinstance(pid, int) else ""

    if status == "stopped":
        return f"{service}: stopped{pid_text}."
    if status == "not_running":
        return f"{service}: not running."
    if status == "error":
        return f"{service}: failed to stop{pid_text}."
    return f"{service}: status={status}."


def _service_status_line(result: dict[str, object]) -> str:
    service = str(result.get("service", "service"))
    running = bool(result.get("running"))
    if running:
        port = _extract_port_from_command(result.get("command"))
        pid = result.get("pid")
        pid_text = f", pid {pid}" if isinstance(pid, int) else ""
        port_text = f", port {port}" if isinstance(port, int) else ""
        return f"{service}: running{port_text}{pid_text}."
    reason = result.get("reason")
    if isinstance(reason, str) and reason:
        return f"{service}: not running ({reason})."
    return f"{service}: not running."


def _bootstrap_line(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if status == "installed":
        package_id = payload.get("package_id")
        if isinstance(package_id, str) and package_id:
            return (
                f"[bootstrap] No package detected. Auto-installed and activated "
                f"'{package_id}'."
            )
        return "[bootstrap] No package detected. Auto-installed default package."
    if status == "failed":
        package_id = payload.get("package_id")
        error = payload.get("error")
        package_text = f" '{package_id}'" if isinstance(package_id, str) and package_id else ""
        error_text = f": {error}" if isinstance(error, str) and error else "."
        return f"[bootstrap] Failed to auto-install default package{package_text}{error_text}"
    return None


def _resolve_compile_tool_source() -> Path:
    return Path(__file__).resolve().parent / "tools" / "sci-skills-generator"


def _should_suppress_compile_output_line(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    for marker in SUPPRESSED_COMPILE_OUTPUT_MARKERS:
        if marker in lowered:
            return True
    return False


def _emit_compile_process_output(completed: object) -> None:
    stdout_text = getattr(completed, "stdout", "")
    stderr_text = getattr(completed, "stderr", "")
    if isinstance(stdout_text, str) and stdout_text:
        for line in stdout_text.splitlines():
            if _should_suppress_compile_output_line(line):
                continue
            print(line)
    if isinstance(stderr_text, str) and stderr_text:
        for line in stderr_text.splitlines():
            if _should_suppress_compile_output_line(line):
                continue
            print(line, file=sys.stderr)


def _resolve_project_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _run_codex_compile_pass(
    project_root: Path,
    *,
    prompt: str,
    pass_index: int,
    total_passes: int,
) -> dict[str, object]:
    codex_bin = DEFAULT_COMPILE_CODEX_BIN
    sandbox = DEFAULT_COMPILE_SANDBOX
    cmd = [codex_bin, "exec", "--cd", str(project_root)]
    if sandbox:
        cmd.extend(["--sandbox", sandbox])
        if sandbox == "workspace-write":
            cmd.append("--full-auto")
    cmd.append(prompt)

    print(f"[compile] pass {pass_index}/{total_passes}: codex exec")
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PackageError(
            f"codex CLI not found: {codex_bin}. Install codex or set CODEX_BIN."
        ) from exc

    _emit_compile_process_output(completed)

    if completed.returncode != 0:
        raise PackageError(
            f"codex exec failed at compile pass {pass_index}/{total_passes} "
            f"with exit code {completed.returncode}."
        )

    return {
        "pass": pass_index,
        "status": "ok",
        "return_code": completed.returncode,
    }


def _cmd_compile(args: argparse.Namespace) -> int:
    scipkg_root = resolve_scipkg_root()
    package_id = normalize_package_id(args.package_id)
    project_root = _resolve_project_path(args.project_path)
    if not project_root.exists() or not project_root.is_dir():
        raise PackageError(f"Compile path is not a directory: {project_root}")

    registry = load_registry(scipkg_root)
    packages = registry.get("packages", {})
    if isinstance(packages, dict) and package_id in packages:
        raise PackageError(
            f"Warning: package id '{package_id}' already exists. "
            "Choose a new package id for compile."
        )

    tool_source = _resolve_compile_tool_source()
    if not tool_source.is_dir():
        raise PackageError(f"Missing compile tool source: {tool_source}")

    tool_dest = project_root / "sci-skills-generator"
    if tool_dest.exists():
        raise PackageError(
            f"Compile path already contains {tool_dest.name}/. "
            "Remove it first or choose a different path."
        )

    shutil.copytree(tool_source, tool_dest)
    compile_runs: list[dict[str, object]] = []

    try:
        compile_runs.append(
            _run_codex_compile_pass(
                project_root, prompt=COMPILE_PROMPT_1, pass_index=1, total_passes=3
            )
        )
        compile_runs.append(
            _run_codex_compile_pass(
                project_root, prompt=COMPILE_PROMPT_2, pass_index=2, total_passes=3
            )
        )
    finally:
        shutil.rmtree(tool_dest, ignore_errors=True)

    if tool_dest.exists():
        raise PackageError(f"Failed to clean up temporary tool directory: {tool_dest}")

    compile_runs.append(
        _run_codex_compile_pass(
            project_root, prompt=COMPILE_PROMPT_3, pass_index=3, total_passes=3
        )
    )

    installed = install_from_local_path(
        scipkg_root,
        package_id,
        local_path=project_root,
        title=args.title,
        activate=args.activate,
        force=False,
    )

    router_sync = None
    if not args.no_router_sync:
        router_sync = sync_router_rules(scipkg_root)

    active = load_registry(scipkg_root).get("active_package")
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
    _emit_output(args, payload, lines)
    return 0


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

    payload = {
        "installed": meta,
        "source": source,
        "scipkg_root": str(scipkg_root),
        "router_sync": router,
    }
    active = load_registry(scipkg_root).get("active_package")
    lines = [
        f"Installed package '{meta.get('id', package_id)}' from {source}.",
        (
            f"Active package: {active}."
            if isinstance(active, str) and active
            else "Active package unchanged."
        ),
    ]
    _emit_output(args, payload, lines)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    scipkg_root = resolve_scipkg_root()
    registry = load_registry(scipkg_root)
    packages = list_packages(scipkg_root)
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
    _emit_output(args, payload, lines)
    return 0


def _cmd_activate(args: argparse.Namespace) -> int:
    scipkg_root = resolve_scipkg_root()
    package_id = normalize_package_id(args.package_id)
    meta = activate_package(scipkg_root, package_id)
    payload = {
        "active_package": package_id,
        "meta": meta,
        "scipkg_root": str(scipkg_root),
    }
    _emit_output(args, payload, [f"Active package set to '{package_id}'."])
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
    _emit_output(args, payload, [f"Overlay entries for '{package_id}': {entry_text}."])
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
    _emit_output(args, payload, [f"Dependencies for '{package_id}': {deps_text}."])
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
    _emit_output(args, payload, lines)
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
    lines: list[str] = []
    bootstrap_text = _bootstrap_line(bootstrap)
    if bootstrap_text:
        lines.append(bootstrap_text)
    lines.extend(_service_start_line(result) for result in results)
    if rollback:
        lines.append("Rollback executed for previously started services.")
    _emit_output(args, payload, lines)
    return 2 if failed else 0


def _cmd_stop(args: argparse.Namespace) -> int:
    runtime_root = resolve_runtime_root()
    names = normalize_components(args.components)

    results = []
    for name in names:
        results.append(stop_service(runtime_root, name))

    payload = {"runtime_root": str(runtime_root), "results": results}
    lines = [_service_stop_line(result) for result in results]
    _emit_output(args, payload, lines)
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
    lines: list[str] = []
    bootstrap_text = _bootstrap_line(bootstrap)
    if bootstrap_text:
        lines.append(bootstrap_text)
    lines.extend(_service_start_line(result) for result in start_results)
    failed_stops = [item for item in stop_results if item.get("status") == "error"]
    if failed_stops:
        lines.append("Warning: one or more services failed to stop cleanly before restart.")
    if rollback:
        lines.append("Rollback executed for previously started services.")
    _emit_output(args, payload, lines)
    return 2 if failed else 0


def _cmd_status(args: argparse.Namespace) -> int:
    runtime_root = resolve_runtime_root()
    names = normalize_components(args.components)

    results = []
    for name in names:
        results.append(service_status(runtime_root, name))

    payload = {"runtime_root": str(runtime_root), "results": results}
    lines = [_service_status_line(result) for result in results]
    _emit_output(args, payload, lines)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fermilink",
        description="Unified FermiLink CLI for package management and service control.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_json_option(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--json",
            action="store_true",
            help="Print full JSON output instead of concise human-readable lines.",
        )

    install_parser = subparsers.add_parser(
        "install",
        help="Install scientific package from curated channel, zip URL, or local path.",
    )
    _add_json_option(install_parser)
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

    compile_parser = subparsers.add_parser(
        "compile",
        help=(
            "Compile a local scientific project into a fermilink package by running "
            "three codex passes with sci-skills-generator, then install locally."
        ),
    )
    _add_json_option(compile_parser)
    compile_parser.add_argument("package_id", help="Target package id to register.")
    compile_parser.add_argument(
        "project_path",
        nargs="?",
        default=".",
        help="Project root path to compile (default: current directory).",
    )
    compile_parser.add_argument(
        "--title",
        help="Optional display title for installed package metadata.",
    )
    compile_parser.add_argument(
        "--activate",
        "--active",
        action="store_true",
        help="Activate package after compile+install.",
    )
    compile_parser.add_argument(
        "--no-router-sync",
        action="store_true",
        help="Skip automatic router_rules.json synchronization.",
    )
    compile_parser.set_defaults(func=_cmd_compile)

    list_parser = subparsers.add_parser("list", help="List installed scientific packages.")
    _add_json_option(list_parser)
    list_parser.set_defaults(func=_cmd_list)

    activate_parser = subparsers.add_parser("activate", help="Set active package.")
    _add_json_option(activate_parser)
    activate_parser.add_argument("package_id")
    activate_parser.set_defaults(func=_cmd_activate)

    overlay_parser = subparsers.add_parser(
        "overlay",
        help="Set which top-level package entries are exposed in workspace repo.",
    )
    _add_json_option(overlay_parser)
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
    _add_json_option(dependencies_parser)
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
    _add_json_option(delete_parser)
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
    _add_json_option(start_parser)
    start_parser.add_argument("components", nargs="*", help="runner and/or web")
    start_parser.set_defaults(func=_cmd_start)

    stop_parser = subparsers.add_parser(
        "stop",
        help="Stop one or more services: runner, web. Default stops both.",
    )
    _add_json_option(stop_parser)
    stop_parser.add_argument("components", nargs="*", help="runner and/or web")
    stop_parser.set_defaults(func=_cmd_stop)

    restart_parser = subparsers.add_parser(
        "restart",
        help="Restart one or more services: runner, web. Default restarts both.",
    )
    _add_json_option(restart_parser)
    restart_parser.add_argument("components", nargs="*", help="runner and/or web")
    restart_parser.set_defaults(func=_cmd_restart)

    status_parser = subparsers.add_parser(
        "status",
        help="Show service status for runner/web. Default checks both.",
    )
    _add_json_option(status_parser)
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
