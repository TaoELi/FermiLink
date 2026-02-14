from __future__ import annotations

import argparse
import json
import os
import sys


def _should_style_cli_output() -> bool:
    if os.getenv("FERMILINK_NO_COLOR"):
        return False
    if os.getenv("FERMILINK_NO_STYLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    term = os.getenv("FERMILINK_TERM", "").strip().lower()
    if term == "dumb":
        return False
    try:
        return bool(sys.stdout.isatty() and sys.stderr.isatty())
    except Exception:
        return False


def _style_text(text: str, *codes: str) -> str:
    if not _should_style_cli_output() or not codes:
        return text
    seq = ";".join(code.strip() for code in codes if code.strip())
    if not seq:
        return text
    return f"\x1b[{seq}m{text}\x1b[0m"


def _format_cli_tag(tag: str) -> str:
    return _style_text(f"[{tag}]", "1")


def _format_tagged_line(tag: str, message: str) -> str:
    return f"{_format_cli_tag(tag)} {message}"


def _print_tagged(tag: str, message: str, *, stderr: bool = False) -> None:
    print(_format_tagged_line(tag, message), file=sys.stderr if stderr else sys.stdout)


def _chat_input_prompt() -> str:
    if not _should_style_cli_output():
        return "You> "
    prompt = _style_text(" You> ", "1", "38;5;255", "48;5;238")
    return f"\n{prompt} "


def _chat_prompt_spacing_after_input() -> None:
    if _should_style_cli_output():
        print()


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        text = line.strip()
        if text:
            print(text)


def _emit_output(args: argparse.Namespace, payload: dict, lines: list[str]) -> None:
    """Render command responses for all high-level CLI commands.

    Shared by package/service/agent commands. Keep behavior stable when changing
    output semantics so human and `--json` callers stay consistent.
    """

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
        package_text = (
            f" '{package_id}'" if isinstance(package_id, str) and package_id else ""
        )
        error_text = f": {error}" if isinstance(error, str) and error else "."
        return f"[bootstrap] Failed to auto-install default package{package_text}{error_text}"
    return None
