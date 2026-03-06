from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fermilink.agent_runtime import (
    ENV_MODEL,
    ENV_PROVIDER,
    ENV_REASONING_EFFORT,
    ENV_SANDBOX_MODE,
    ENV_SANDBOX_POLICY,
    load_agent_runtime_policy,
)
from fermilink.config import resolve_fermilink_home
from fermilink.providers import collect_provider_service_env_overrides


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    command: list[str]
    env: dict[str, str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    # Reap child zombies when this process is their parent.
    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        waited_pid = 0
    if waited_pid == pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def _state_path(runtime_root: Path, service: str) -> Path:
    return runtime_root / f"{service}.json"


def _read_state(runtime_root: Path, service: str) -> dict[str, Any] | None:
    path = _state_path(runtime_root, service)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _write_state(runtime_root: Path, service: str, payload: dict[str, Any]) -> None:
    runtime_root.mkdir(parents=True, exist_ok=True)
    path = _state_path(runtime_root, service)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp_path.replace(path)


def _remove_state(runtime_root: Path, service: str) -> None:
    _state_path(runtime_root, service).unlink(missing_ok=True)


def _extract_flag_value(command: list[str], flag: str) -> str | None:
    for index, token in enumerate(command):
        if token == flag:
            if index + 1 < len(command):
                return command[index + 1]
            return None
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
    return None


def _extract_host_port(command: list[str]) -> tuple[str, int] | None:
    host_raw = _extract_flag_value(command, "--host")
    port_raw = _extract_flag_value(command, "--port")
    if not port_raw:
        return None
    try:
        port = int(port_raw)
    except ValueError:
        return None
    host = (host_raw or "127.0.0.1").strip() or "127.0.0.1"
    return host, port


def _probe_host_for_connect(host: str) -> str:
    lowered = host.strip().lower()
    if lowered in {"0.0.0.0", "::", "*"}:
        return "127.0.0.1"
    return host


def _is_port_in_use(host: str, port: int) -> bool:
    probe_host = _probe_host_for_connect(host)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            result = sock.connect_ex((probe_host, int(port)))
        except OSError:
            return False
    return result == 0


def _read_log_tail(path: Path, *, max_lines: int = 30) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if max_lines <= 0:
        return []
    return lines[-max_lines:]


def _kill_pid(pid: int, *, grace_seconds: float = 8.0) -> tuple[bool, str]:
    if not _pid_is_alive(pid):
        return True, "not_running"

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + max(grace_seconds, 0.0)
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True, "terminated"
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True, "terminated"

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True, "killed"
        time.sleep(0.1)
    return False, "still_running"


def _command_from_env(value: str | None) -> list[str] | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return shlex.split(stripped)


def default_service_specs(*, web_app_path: Path) -> dict[str, ServiceSpec]:
    """
    Build default service specifications for FermiLink components.

    Parameters
    ----------
    web_app_path : Path
        Path to the web application module entry file.

    Returns
    -------
    dict[str, ServiceSpec]
        Mapping from component name to `ServiceSpec`.
    """
    runner_cmd = _command_from_env(os.getenv("FERMILINK_RUNNER_CMD")) or [
        "uvicorn",
        "fermilink.runner.app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    web_cmd = _command_from_env(os.getenv("FERMILINK_WEB_CMD")) or [
        "chainlit",
        "run",
        str(web_app_path),
        "--host",
        "0.0.0.0",
        "--port",
        "7860",
    ]

    runner_url = os.getenv("FERMILINK_RUNNER_URL", "http://127.0.0.1:8000")
    chainlit_app_root_raw = os.getenv("FERMILINK_CHAINLIT_APP_ROOT")
    if not chainlit_app_root_raw or not chainlit_app_root_raw.strip():
        chainlit_app_root_raw = str(resolve_fermilink_home())
    chainlit_app_root = Path(chainlit_app_root_raw).expanduser()
    if not chainlit_app_root.is_absolute():
        chainlit_app_root = (Path.cwd() / chainlit_app_root).resolve()
    scipkg_root_raw = os.getenv(
        "FERMILINK_SCIPKG_ROOT", str(chainlit_app_root / "scientific_packages")
    )
    scipkg_root = Path(scipkg_root_raw).expanduser()
    if not scipkg_root.is_absolute():
        scipkg_root = (Path.cwd() / scipkg_root).resolve()
    workspaces_root_raw = os.getenv(
        "FERMILINK_WORKSPACES_ROOT", str(chainlit_app_root / "workspaces")
    )
    workspaces_root = Path(workspaces_root_raw).expanduser()
    if not workspaces_root.is_absolute():
        workspaces_root = (Path.cwd() / workspaces_root).resolve()

    runner_env = {
        "FERMILINK_SCIPKG_ROOT": str(scipkg_root),
        "FERMILINK_WORKSPACES_ROOT": str(workspaces_root),
    }
    web_env = {
        "FERMILINK_RUNNER_URL": runner_url,
        "FERMILINK_CHAINLIT_APP_ROOT": str(chainlit_app_root),
        "FERMILINK_SCIPKG_ROOT": str(scipkg_root),
        "FERMILINK_WORKSPACES_ROOT": str(workspaces_root),
    }

    provider_service_env = collect_provider_service_env_overrides(cwd=Path.cwd())
    if provider_service_env:
        runner_env.update(provider_service_env)
        web_env.update(provider_service_env)

    runtime_policy = load_agent_runtime_policy()
    if ENV_PROVIDER not in os.environ:
        runner_env[ENV_PROVIDER] = runtime_policy.provider
        web_env[ENV_PROVIDER] = runtime_policy.provider
    if ENV_SANDBOX_POLICY not in os.environ:
        runner_env[ENV_SANDBOX_POLICY] = runtime_policy.sandbox_policy
        web_env[ENV_SANDBOX_POLICY] = runtime_policy.sandbox_policy
    if ENV_SANDBOX_MODE not in os.environ:
        runner_env[ENV_SANDBOX_MODE] = runtime_policy.sandbox_mode
        web_env[ENV_SANDBOX_MODE] = runtime_policy.sandbox_mode
    if (
        ENV_MODEL not in os.environ
        and isinstance(runtime_policy.model, str)
        and runtime_policy.model
    ):
        runner_env[ENV_MODEL] = runtime_policy.model
        web_env[ENV_MODEL] = runtime_policy.model
    if (
        ENV_REASONING_EFFORT not in os.environ
        and isinstance(runtime_policy.reasoning_effort, str)
        and runtime_policy.reasoning_effort
    ):
        runner_env[ENV_REASONING_EFFORT] = runtime_policy.reasoning_effort
        web_env[ENV_REASONING_EFFORT] = runtime_policy.reasoning_effort

    return {
        "runner": ServiceSpec(
            name="runner",
            command=runner_cmd,
            env=runner_env,
        ),
        "web": ServiceSpec(
            name="web",
            command=web_cmd,
            env=web_env,
        ),
    }


def normalize_components(values: list[str] | None) -> list[str]:
    """
    Normalize requested service component names.

    Parameters
    ----------
    values : list[str] | None
        Requested component names from CLI input.

    Returns
    -------
    list[str]
        Normalized component list with canonical names.
    """
    if not values:
        return ["runner", "web"]

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip().lower()
        if not item:
            continue
        if item not in {"runner", "web"}:
            raise ValueError(f"Unknown component '{item}'. Valid: runner, web")
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized or ["runner", "web"]


def service_status(runtime_root: Path, service: str) -> dict[str, Any]:
    """
    Collect runtime status for a managed service component.

    Parameters
    ----------
    runtime_root : Path
        Runtime root used for service pid/status/log files.
    service : str
        Managed service component name.

    Returns
    -------
    dict[str, Any]
        Service status payload including pid, health, and metadata.
    """
    state = _read_state(runtime_root, service)
    if not isinstance(state, dict):
        return {"service": service, "running": False, "reason": "no_state"}

    pid = state.get("pid")
    if not isinstance(pid, int):
        return {"service": service, "running": False, "reason": "invalid_state"}

    if not _pid_is_alive(pid):
        return {
            "service": service,
            "running": False,
            "reason": "stale_pid",
            "pid": pid,
            "started_at": state.get("started_at"),
        }

    return {
        "service": service,
        "running": True,
        "pid": pid,
        "command": state.get("command"),
        "started_at": state.get("started_at"),
        "log_path": state.get("log_path"),
    }


def start_service(runtime_root: Path, spec: ServiceSpec) -> dict[str, Any]:
    """
    Start a managed service process and persist runtime metadata.

    Parameters
    ----------
    runtime_root : Path
        Runtime root used for service pid/status/log files.
    spec : ServiceSpec
        Service specification describing command and runtime paths.

    Returns
    -------
    dict[str, Any]
        Start result payload including final status and process metadata.
    """
    status = service_status(runtime_root, spec.name)
    if status.get("running"):
        return {
            "service": spec.name,
            "action": "start",
            "changed": False,
            "status": "already_running",
            "pid": status.get("pid"),
            "log_path": status.get("log_path"),
        }

    if status.get("reason") == "stale_pid":
        _remove_state(runtime_root, spec.name)

    host_port = _extract_host_port(spec.command)
    if host_port is not None:
        host, port = host_port
        if _is_port_in_use(host, port):
            return {
                "service": spec.name,
                "action": "start",
                "changed": False,
                "status": "port_in_use",
                "host": host,
                "port": port,
                "hint": (
                    f"Port {port} is already in use. Stop the conflicting process "
                    "or override command via FERMILINK_RUNNER_CMD/FERMILINK_WEB_CMD."
                ),
            }

    logs_root = runtime_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_path = logs_root / f"{spec.name}.log"

    merged_env = os.environ.copy()
    merged_env.update(spec.env)

    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(  # noqa: S603
            spec.command,
            cwd=Path.cwd(),
            env=merged_env,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
        )

    state = {
        "service": spec.name,
        "pid": process.pid,
        "command": spec.command,
        "started_at": _now_iso(),
        "log_path": str(log_path),
    }
    _write_state(runtime_root, spec.name, state)

    # Catch immediate startup failures (e.g., bind errors) before reporting success.
    deadline = time.monotonic() + 2.0
    exit_code: int | None = None
    while time.monotonic() < deadline:
        polled = process.poll()
        if polled is not None:
            exit_code = int(polled)
            break
        time.sleep(0.05)

    if exit_code is not None:
        _remove_state(runtime_root, spec.name)
        tail = _read_log_tail(log_path, max_lines=30)
        return {
            "service": spec.name,
            "action": "start",
            "changed": False,
            "status": "failed_to_start",
            "exit_code": exit_code,
            "command": spec.command,
            "log_path": str(log_path),
            "log_tail": tail,
        }

    return {
        "service": spec.name,
        "action": "start",
        "changed": True,
        "status": "started",
        "pid": process.pid,
        "command": spec.command,
        "log_path": str(log_path),
    }


def stop_service(runtime_root: Path, service: str) -> dict[str, Any]:
    """
    Stop a managed service process and update runtime metadata.

    Parameters
    ----------
    runtime_root : Path
        Runtime root used for service pid/status/log files.
    service : str
        Managed service component name.

    Returns
    -------
    dict[str, Any]
        Stop result payload including final status and cleanup metadata.
    """
    state = _read_state(runtime_root, service)
    if not isinstance(state, dict):
        return {
            "service": service,
            "action": "stop",
            "changed": False,
            "status": "not_running",
            "reason": "no_state",
        }

    pid = state.get("pid")
    if not isinstance(pid, int):
        _remove_state(runtime_root, service)
        return {
            "service": service,
            "action": "stop",
            "changed": True,
            "status": "not_running",
            "reason": "invalid_state",
        }

    stopped, stop_mode = _kill_pid(pid)
    if stopped:
        _remove_state(runtime_root, service)

    return {
        "service": service,
        "action": "stop",
        "changed": bool(stopped),
        "status": "stopped" if stopped else "error",
        "pid": pid,
        "reason": stop_mode,
    }
