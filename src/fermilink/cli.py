from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
from pathlib import Path

from fermilink.agent_runtime import (
    SUPPORTED_PROVIDERS,
    load_agent_runtime_policy,
    resolve_agent_runtime_policy,
    save_agent_runtime_policy,
)
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
from fermilink.providers import (
    build_exec_command,
    provider_bin_env_key,
    resolve_provider_binary,
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
EXEC_ROUTER_ENABLED = (
    os.getenv("CHAINLIT_PACKAGE_ROUTER_ENABLED", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
EXEC_ROUTER_AUTO_DEFAULT = (
    os.getenv("CHAINLIT_PACKAGE_ROUTER_AUTO", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
EXEC_SECOND_GUESS_ENABLED = (
    os.getenv("CHAINLIT_PACKAGE_SECOND_GUESS_ENABLED", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
try:
    EXEC_SECOND_GUESS_MIN_CONFIDENCE = float(
        os.getenv("CHAINLIT_PACKAGE_SECOND_GUESS_MIN_CONFIDENCE", "0.75")
    )
except ValueError:
    EXEC_SECOND_GUESS_MIN_CONFIDENCE = 0.75
try:
    EXEC_SECOND_GUESS_TIMEOUT_SECONDS = float(
        os.getenv("CHAINLIT_PACKAGE_SECOND_GUESS_TIMEOUT_SECONDS", "25.0")
    )
except ValueError:
    EXEC_SECOND_GUESS_TIMEOUT_SECONDS = 25.0

PACKAGE_SOURCE_MANUAL = "manual"
PACKAGE_SOURCE_AUTO = "auto"
PACKAGE_SOURCE_DEFAULT = "default"
PACKAGE_SOURCE_SECOND_GUESS = "second_guess"
PACKAGE_SOURCE_NONE = "none"


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


@functools.lru_cache(maxsize=1)
def _load_web_router_module():
    from fermilink.web import app as web_app

    return web_app


@functools.lru_cache(maxsize=1)
def _load_runner_app_module():
    from fermilink.runner import app as runner_app

    return runner_app


def _normalize_installed_package_ids(
    registry_packages: object, *, web_app: object
) -> list[str]:
    package_ids: list[str] = []
    if isinstance(registry_packages, dict):
        for raw_id in registry_packages.keys():
            normalized = web_app._normalize_package_id_safe(str(raw_id))
            if normalized:
                package_ids.append(normalized)
    return sorted(set(package_ids))


def _collect_second_guess_assistant_text(raw_stream_text: str, *, web_app: object) -> str:
    chunks: list[str] = []
    for line in raw_stream_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            item = {}
        item_type = item.get("type") or event.get("type") or ""
        if not isinstance(item_type, str) or not item_type.startswith("agent_message"):
            continue
        text = web_app._extract_text(event) or ""
        if text:
            chunks.append(text)
    return "".join(chunks).strip()


def _run_exec_second_guess(
    *,
    user_text: str,
    repo_dir: Path,
    scipkg_root: Path,
    package_ids: list[str],
    active_package_id: str | None,
    base_package_id: str,
    provider: str = "codex",
    provider_bin: str | None = None,
    sandbox_policy: str = "enforce",
) -> dict[str, object]:
    web_app = _load_web_router_module()
    package_catalog = web_app._build_package_catalog(
        package_ids=package_ids,
        active_package_id=active_package_id,
        scipkg_root=scipkg_root,
    )
    prompt = web_app._build_second_guess_prompt(
        user_text=user_text,
        current_package_id=base_package_id,
        package_catalog=package_catalog,
    )
    provider_bin_value = resolve_provider_binary(provider, codex_bin=provider_bin)
    preflight_sandbox_mode = "read-only" if sandbox_policy == "enforce" else None
    try:
        cmd = build_exec_command(
            provider=provider,
            provider_bin=provider_bin_value,
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox_policy=sandbox_policy,
            sandbox_mode=preflight_sandbox_mode,
            json_output=True,
        )
    except NotImplementedError:
        return {
            "package_id": base_package_id,
            "source": "default",
            "switched": False,
            "note": "second_guess_provider_not_implemented",
        }
    timeout = EXEC_SECOND_GUESS_TIMEOUT_SECONDS
    timeout_value = timeout if timeout > 0 else None
    runner_app = _load_runner_app_module()
    env = os.environ.copy()
    env = runner_app._sanitize_env(env)
    env = runner_app._normalize_codex_home(env)
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_value,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "package_id": base_package_id,
            "source": "default",
            "switched": False,
            "note": "second_guess_timeout",
        }
    except FileNotFoundError as exc:
        env_key = provider_bin_env_key(provider)
        raise PackageError(
            f"{provider} CLI not found: {provider_bin_value}. "
            f"Install the provider CLI or set {env_key}."
        ) from exc

    if completed.returncode != 0:
        return {
            "package_id": base_package_id,
            "source": "default",
            "switched": False,
            "note": f"second_guess_error:exit_code_{completed.returncode}",
        }

    raw_text = _collect_second_guess_assistant_text(completed.stdout or "", web_app=web_app)
    if not raw_text:
        raw_text = "\n".join(
            part
            for part in ((completed.stdout or "").strip(), (completed.stderr or "").strip())
            if part
        )
    decision = web_app._extract_first_json_object(raw_text)
    if not isinstance(decision, dict):
        return {
            "package_id": base_package_id,
            "source": "default",
            "switched": False,
            "note": "second_guess_invalid_json",
        }

    route_raw = decision.get("route")
    route = str(route_raw).strip().lower() if route_raw is not None else ""
    suggested_package = web_app._normalize_package_id_safe(decision.get("package_id"))
    confidence = web_app._coerce_confidence(decision.get("confidence"))
    reason_raw = decision.get("reason")
    reason = str(reason_raw).strip() if isinstance(reason_raw, str) else ""
    reason_short = reason[:240] if reason else ""
    package_set = set(package_ids)

    if route not in {"keep", "switch"}:
        return {
            "package_id": base_package_id,
            "source": "default",
            "switched": False,
            "note": "second_guess_invalid_route",
        }

    if route == "keep":
        return {
            "package_id": base_package_id,
            "source": "default",
            "switched": False,
            "note": (
                f"second_guess_keep(conf={confidence:.2f}, reason={reason_short})"
                if reason_short
                else f"second_guess_keep(conf={confidence:.2f})"
            ),
        }

    if suggested_package not in package_set:
        return {
            "package_id": base_package_id,
            "source": "default",
            "switched": False,
            "note": "second_guess_invalid_target",
        }
    if suggested_package == base_package_id:
        return {
            "package_id": base_package_id,
            "source": "default",
            "switched": False,
            "note": "second_guess_same_target",
        }
    if confidence < EXEC_SECOND_GUESS_MIN_CONFIDENCE:
        return {
            "package_id": base_package_id,
            "source": "default",
            "switched": False,
            "note": f"second_guess_low_confidence({confidence:.2f})",
        }

    return {
        "package_id": suggested_package,
        "source": web_app.PACKAGE_SOURCE_SECOND_GUESS,
        "switched": True,
        "note": (
            f"second_guess_switch({base_package_id}->{suggested_package}, "
            f"conf={confidence:.2f}, reason={reason_short})"
            if reason_short
            else f"second_guess_switch({base_package_id}->{suggested_package}, conf={confidence:.2f})"
        ),
    }


def _resolve_exec_package_selection(
    *,
    user_prompt: str,
    scipkg_root: Path,
    repo_dir: Path,
    requested_package_id: str | None,
    provider: str = "codex",
    provider_bin: str | None = None,
    sandbox_policy: str = "enforce",
    current_package_id: str | None = None,
    current_source: str = PACKAGE_SOURCE_NONE,
) -> dict[str, object]:
    web_app = _load_web_router_module()
    registry = load_registry(scipkg_root)
    packages_payload = registry.get("packages", {})
    package_ids = _normalize_installed_package_ids(packages_payload, web_app=web_app)
    active_raw = registry.get("active_package")
    active_package_id = (
        web_app._normalize_package_id_safe(active_raw) if isinstance(active_raw, str) else None
    )
    package_set = set(package_ids)
    if active_package_id not in package_set:
        active_package_id = None

    normalized_current = (
        web_app._normalize_package_id_safe(current_package_id)
        if isinstance(current_package_id, str)
        else None
    )
    if normalized_current not in package_set:
        normalized_current = None
        current_source = PACKAGE_SOURCE_NONE
    elif not isinstance(current_source, str) or not current_source.strip():
        current_source = PACKAGE_SOURCE_NONE

    if not package_ids:
        raise PackageError(
            "No installed scientific packages found. Run `fermilink install <package> --activate` first."
        )

    if requested_package_id:
        normalized_requested = normalize_package_id(requested_package_id)
        if normalized_requested not in package_set:
            available = ", ".join(package_ids)
            raise PackageError(
                f"Unknown package '{normalized_requested}'. Available: {available}"
            )
        return {
            "package_id": normalized_requested,
            "source": PACKAGE_SOURCE_MANUAL,
            "reason": "manual_pin",
            "note": "manual_pin",
        }

    config = web_app._load_router_config(scipkg_root)
    selected_package_id: str | None = None
    selected_source = PACKAGE_SOURCE_NONE
    selected_reason = "no_selection"

    if current_source == PACKAGE_SOURCE_MANUAL and normalized_current:
        return {
            "package_id": normalized_current,
            "source": PACKAGE_SOURCE_MANUAL,
            "reason": "manual_pin",
            "note": "manual_pin",
        }

    if EXEC_ROUTER_ENABLED and EXEC_ROUTER_AUTO_DEFAULT:
        decision = web_app._route_package_candidate(
            user_text=user_prompt,
            package_ids=package_ids,
            current_package_id=normalized_current,
            config=config,
        )
        candidate = decision.get("selected_package_id")
        if isinstance(candidate, str) and candidate in package_set:
            if (
                bool(getattr(web_app, "PACKAGE_ROUTER_STICKY", False))
                and normalized_current
                and normalized_current != candidate
                and int(decision.get("margin", 0))
                < int(getattr(web_app, "PACKAGE_ROUTER_SWITCH_MARGIN", 2))
            ):
                selected_package_id = normalized_current
                selected_source = (
                    current_source
                    if current_source != PACKAGE_SOURCE_NONE
                    else PACKAGE_SOURCE_AUTO
                )
                selected_reason = "sticky_keep_current"
            else:
                selected_package_id = candidate
                selected_source = PACKAGE_SOURCE_AUTO
                selected_reason = str(decision.get("reason", "matched"))

    if not selected_package_id:
        if normalized_current:
            selected_package_id = normalized_current
            selected_source = (
                current_source
                if current_source != PACKAGE_SOURCE_NONE
                else PACKAGE_SOURCE_DEFAULT
            )
            selected_reason = "keep_current"
        else:
            fallback = web_app._resolve_default_package_id(
                package_ids=package_ids,
                active_package_id=active_package_id,
                config=config,
            )
            if not fallback:
                raise PackageError(
                    "No default package could be resolved from registry/router rules."
                )
            selected_package_id = fallback
            selected_source = PACKAGE_SOURCE_DEFAULT
            selected_reason = "default_fallback"

    note = selected_reason
    if (
        EXEC_SECOND_GUESS_ENABLED
        and selected_source != PACKAGE_SOURCE_MANUAL
        and len(package_ids) >= 2
    ):
        second_guess = _run_exec_second_guess(
            user_text=user_prompt,
            repo_dir=repo_dir,
            scipkg_root=scipkg_root,
            package_ids=package_ids,
            active_package_id=active_package_id,
            base_package_id=selected_package_id,
            provider=provider,
            provider_bin=provider_bin,
            sandbox_policy=sandbox_policy,
        )
        switched = bool(second_guess.get("switched"))
        second_package = second_guess.get("package_id")
        if switched and isinstance(second_package, str) and second_package in package_set:
            selected_package_id = second_package
            selected_source = str(second_guess.get("source") or PACKAGE_SOURCE_SECOND_GUESS)
        note = str(second_guess.get("note") or note)

    return {
        "package_id": selected_package_id,
        "source": selected_source,
        "reason": selected_reason,
        "note": note,
    }


def _run_exec_chat_turn(
    *,
    repo_dir: Path,
    prompt: str,
    sandbox: str | None,
    codex_bin: str | None,
    provider: str = "codex",
    sandbox_policy: str = "enforce",
) -> dict[str, object]:
    provider_bin = resolve_provider_binary(provider, codex_bin=codex_bin)
    with tempfile.TemporaryDirectory(prefix="fermilink-chat-") as temp_dir:
        last_message_path = Path(temp_dir) / "last_message.txt"
        try:
            cmd = build_exec_command(
                provider=provider,
                provider_bin=provider_bin,
                repo_dir=repo_dir,
                prompt=prompt,
                sandbox_policy=sandbox_policy,
                sandbox_mode=sandbox,
                json_output=False,
            )
        except NotImplementedError as exc:
            raise PackageError(str(exc)) from exc

        if cmd:
            prompt_arg = cmd[-1]
            cmd = cmd[:-1] + ["--output-last-message", str(last_message_path), prompt_arg]

        runner_app = _load_runner_app_module()
        env = os.environ.copy()
        env = runner_app._sanitize_env(env)
        env = runner_app._normalize_codex_home(env)

        stdout_text = ""
        stderr_text = ""
        if _should_use_direct_terminal_stream():
            try:
                completed = subprocess.run(
                    cmd,
                    cwd=str(repo_dir),
                    check=False,
                    env=env,
                )
            except FileNotFoundError as exc:
                env_key = provider_bin_env_key(provider)
                raise PackageError(
                    f"{provider} CLI not found: {provider_bin}. "
                    f"Install the provider CLI or set {env_key}."
                ) from exc
            return_code = int(completed.returncode)
        else:
            try:
                process = subprocess.Popen(
                    cmd,
                    cwd=str(repo_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )
            except FileNotFoundError as exc:
                env_key = provider_bin_env_key(provider)
                raise PackageError(
                    f"{provider} CLI not found: {provider_bin}. "
                    f"Install the provider CLI or set {env_key}."
                ) from exc
            return_code, stdout_text, stderr_text = _stream_exec_process_output_with_capture(process)

        assistant_text = ""
        try:
            assistant_text = last_message_path.read_text(encoding="utf-8").strip()
        except OSError:
            assistant_text = ""

        if not assistant_text and stdout_text:
            web_app = _load_web_router_module()
            assistant_text = _collect_second_guess_assistant_text(stdout_text, web_app=web_app)

        return {
            "assistant_text": assistant_text,
            "return_code": int(return_code),
            "stderr": stderr_text.strip(),
        }


def _cmd_chat(args: argparse.Namespace) -> int:
    repo_dir = Path.cwd().resolve()
    _ensure_exec_repo_ready(repo_dir, args)

    scipkg_root = resolve_scipkg_root()
    runtime_policy = resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(args.sandbox, str) and args.sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = args.sandbox.strip()

    provider_bin = args.codex_bin if provider == "codex" else None
    sandbox_text = (
        f"enforce({sandbox_mode})" if sandbox_policy == "enforce" else "bypass"
    )
    print(f"[agent] provider: {provider}, sandbox: {sandbox_text}")
    print("[chat] Interactive mode. Type `exit` or `quit` to leave.")

    web_app = _load_web_router_module()
    history: list[tuple[str, str]] = []
    current_package_id: str | None = None
    current_source = PACKAGE_SOURCE_NONE

    while True:
        try:
            user_text = input("You> ")
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 0

        prompt_text = user_text.strip()
        if not prompt_text:
            continue
        lowered = prompt_text.lower()
        if lowered in {"exit", "quit", "/exit", "/quit"}:
            return 0

        selection = _resolve_exec_package_selection(
            user_prompt=prompt_text,
            scipkg_root=scipkg_root,
            repo_dir=repo_dir,
            requested_package_id=args.package_id,
            provider=provider,
            provider_bin=provider_bin,
            sandbox_policy=sandbox_policy,
            current_package_id=current_package_id,
            current_source=current_source,
        )
        package_id = selection.get("package_id")
        if not isinstance(package_id, str) or not package_id:
            raise PackageError("No package selected for chat turn.")
        source = str(selection.get("source") or PACKAGE_SOURCE_DEFAULT)
        note = str(selection.get("note") or "").strip()
        print(f"[package] Using {package_id} (selection: {source})")
        if note and note not in {"manual_pin", "default_fallback", "matched"}:
            print(f"[router] {note}")

        overlay = _overlay_exec_package(
            repo_dir=repo_dir,
            scipkg_root=scipkg_root,
            package_id=package_id,
        )
        linked = int(overlay.get("linked_count", 0)) if isinstance(overlay, dict) else 0
        collisions = (
            int(overlay.get("collision_count", 0)) if isinstance(overlay, dict) else 0
        )
        linked_deps = (
            int(overlay.get("linked_dependency_count", 0))
            if isinstance(overlay, dict)
            else 0
        )
        print(
            "[overlay] linked entries: "
            f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
        )

        prompt = web_app._build_prompt(history, prompt_text)
        try:
            run_result = _run_exec_chat_turn(
                repo_dir=repo_dir,
                prompt=prompt,
                sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
                codex_bin=provider_bin,
                provider=provider,
                sandbox_policy=sandbox_policy,
            )
        finally:
            _cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)

        assistant_text = str(run_result.get("assistant_text") or "").strip()
        return_code = int(run_result.get("return_code") or 0)
        stderr_text = str(run_result.get("stderr") or "").strip()
        if assistant_text:
            print(f"Assistant> {assistant_text}")
        if return_code != 0:
            if stderr_text:
                print(stderr_text, file=sys.stderr)
            print(
                f"[chat] provider exited with code {return_code}.",
                file=sys.stderr,
            )

        history = web_app._append_history(history, "user", prompt_text)
        if assistant_text:
            history = web_app._append_history(history, "assistant", assistant_text)

        current_package_id = package_id
        current_source = source


def _ensure_exec_repo_ready(repo_dir: Path, args: argparse.Namespace) -> None:
    runner_app = _load_runner_app_module()
    if args.init_git and args.no_init_git:
        raise PackageError("Cannot combine --init-git and --no-init-git.")

    if not runner_app._is_valid_git_repo(repo_dir):
        if args.no_init_git:
            raise PackageError(
                "Current directory is not a git repository. Run `git init` or use --init-git."
            )
        initialize = bool(args.init_git)
        if not initialize:
            if not sys.stdin.isatty():
                raise PackageError(
                    "Current directory is not a git repository. Re-run with --init-git."
                )
            answer = input("Current directory is not a git repo. Run `git init` now? [y/N]: ")
            initialize = answer.strip().lower() in {"y", "yes"}
        if not initialize:
            raise PackageError(
                "Aborted: git repository required for fermilink exec/chat."
            )
        runner_app._ensure_git_repo(repo_dir)

    source_dir = runner_app._resolve_source_dir()
    runner_app._ensure_template_agents_file(source_dir, repo_dir)


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

    try:
        resolved_id, package_meta = resolve_session_package(
            scipkg_root=scipkg_root,
            workspace_root=repo_dir,
            requested_package_id=package_id,
        )
    except Exception as exc:
        raise PackageError(str(exc)) from exc

    if not resolved_id or not isinstance(package_meta, dict):
        raise PackageError(f"Package '{package_id}' could not be resolved for overlay.")

    try:
        overlay = overlay_package_into_repo(
            repo_dir=repo_dir,
            workspace_root=repo_dir,
            package_id=resolved_id,
            package_meta=package_meta,
            scipkg_root=scipkg_root,
            allow_replace_existing=False,
        )
    except Exception as exc:
        raise PackageError(str(exc)) from exc

    runner_app = _load_runner_app_module()
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


def _stream_exec_process_output(process: subprocess.Popen[str]) -> int:
    def _pump(stream: object, *, is_stderr: bool) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            text = line.rstrip("\n")
            print(text, file=sys.stderr if is_stderr else sys.stdout, flush=True)
        stream.close()

    stdout_thread = threading.Thread(
        target=_pump, args=(process.stdout,), kwargs={"is_stderr": False}, daemon=True
    )
    stderr_thread = threading.Thread(
        target=_pump, args=(process.stderr,), kwargs={"is_stderr": True}, daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    return_code = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    return return_code


def _stream_exec_process_output_with_capture(
    process: subprocess.Popen[str],
) -> tuple[int, str, str]:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _pump(stream: object, *, is_stderr: bool) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            if is_stderr:
                stderr_lines.append(line)
            else:
                stdout_lines.append(line)
            text = line.rstrip("\n")
            print(text, file=sys.stderr if is_stderr else sys.stdout, flush=True)
        stream.close()

    stdout_thread = threading.Thread(
        target=_pump, args=(process.stdout,), kwargs={"is_stderr": False}, daemon=True
    )
    stderr_thread = threading.Thread(
        target=_pump, args=(process.stderr,), kwargs={"is_stderr": True}, daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    return_code = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    return return_code, "".join(stdout_lines), "".join(stderr_lines)


def _should_use_direct_terminal_stream() -> bool:
    """Return whether Codex output should stream directly to the terminal.

    Direct passthrough preserves Codex's native rich TTY rendering (colors,
    sections, progress updates). Fallback piping is used in non-interactive
    contexts (tests, redirected output, background jobs).
    """

    try:
        return bool(
            sys.stdin.isatty()
            and sys.stdout.isatty()
            and sys.stderr.isatty()
        )
    except Exception:
        return False


def _run_exec_codex_prompt(
    *,
    repo_dir: Path,
    prompt: str,
    sandbox: str | None,
    codex_bin: str | None,
    provider: str = "codex",
    sandbox_policy: str = "enforce",
) -> int:
    provider_bin = resolve_provider_binary(provider, codex_bin=codex_bin)
    try:
        cmd = build_exec_command(
            provider=provider,
            provider_bin=provider_bin,
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox_policy=sandbox_policy,
            sandbox_mode=sandbox,
            json_output=False,
        )
    except NotImplementedError as exc:
        raise PackageError(str(exc)) from exc
    runner_app = _load_runner_app_module()
    env = os.environ.copy()
    env = runner_app._sanitize_env(env)
    env = runner_app._normalize_codex_home(env)
    if _should_use_direct_terminal_stream():
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(repo_dir),
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            env_key = provider_bin_env_key(provider)
            raise PackageError(
                f"{provider} CLI not found: {provider_bin}. "
                f"Install the provider CLI or set {env_key}."
            ) from exc
        return int(completed.returncode)

    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError as exc:
        env_key = provider_bin_env_key(provider)
        raise PackageError(
            f"{provider} CLI not found: {provider_bin}. "
            f"Install the provider CLI or set {env_key}."
        ) from exc
    return _stream_exec_process_output(process)


def _resolve_project_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _cmd_exec(args: argparse.Namespace) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise PackageError("Prompt is required for fermilink exec.")

    repo_dir = Path.cwd().resolve()
    _ensure_exec_repo_ready(repo_dir, args)

    scipkg_root = resolve_scipkg_root()
    runtime_policy = resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(args.sandbox, str) and args.sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = args.sandbox.strip()

    provider_bin = args.codex_bin if provider == "codex" else None
    selection = _resolve_exec_package_selection(
        user_prompt=prompt,
        scipkg_root=scipkg_root,
        repo_dir=repo_dir,
        requested_package_id=args.package_id,
        provider=provider,
        provider_bin=provider_bin,
        sandbox_policy=sandbox_policy,
    )
    package_id = selection.get("package_id")
    if not isinstance(package_id, str) or not package_id:
        raise PackageError("No package selected for execution.")

    source = str(selection.get("source") or "default")
    note = str(selection.get("note") or "").strip()
    print(f"[package] Using {package_id} (selection: {source})")
    if note and note not in {"manual_pin", "default_fallback", "matched"}:
        print(f"[router] {note}")
    sandbox_text = (
        f"enforce({sandbox_mode})"
        if sandbox_policy == "enforce"
        else "bypass"
    )
    print(f"[agent] provider: {provider}, sandbox: {sandbox_text}")

    overlay = _overlay_exec_package(
        repo_dir=repo_dir,
        scipkg_root=scipkg_root,
        package_id=package_id,
    )
    linked = int(overlay.get("linked_count", 0)) if isinstance(overlay, dict) else 0
    collisions = (
        int(overlay.get("collision_count", 0)) if isinstance(overlay, dict) else 0
    )
    linked_deps = (
        int(overlay.get("linked_dependency_count", 0))
        if isinstance(overlay, dict)
        else 0
    )
    print(
        "[overlay] linked entries: "
        f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
    )

    try:
        return _run_exec_codex_prompt(
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
            codex_bin=provider_bin,
            provider=provider,
            sandbox_policy=sandbox_policy,
        )
    finally:
        _cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)


def _run_codex_compile_pass(
    project_root: Path,
    *,
    prompt: str,
    pass_index: int,
    total_passes: int,
    provider: str,
    provider_bin: str,
) -> dict[str, object]:
    sandbox = DEFAULT_COMPILE_SANDBOX
    try:
        cmd = build_exec_command(
            provider=provider,
            provider_bin=provider_bin,
            repo_dir=project_root,
            prompt=prompt,
            sandbox_policy="enforce",
            sandbox_mode=sandbox,
            json_output=False,
        )
    except NotImplementedError as exc:
        raise PackageError(
            f"Compile provider '{provider}' is not implemented yet. "
            "Switch to codex via `fermilink agent codex`."
        ) from exc

    print(f"[compile] pass {pass_index}/{total_passes}: {provider} exec")
    try:
        completed = subprocess.run(cmd, check=False)
    except FileNotFoundError as exc:
        env_key = provider_bin_env_key(provider)
        raise PackageError(
            f"{provider} CLI not found: {provider_bin}. "
            f"Install {provider} or set {env_key}."
        ) from exc

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

    runtime_policy = resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    provider_bin = resolve_provider_binary(
        provider,
        codex_bin=DEFAULT_COMPILE_CODEX_BIN if provider == "codex" else None,
    )

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
                project_root,
                prompt=COMPILE_PROMPT_1,
                pass_index=1,
                total_passes=3,
                provider=provider,
                provider_bin=provider_bin,
            )
        )
        compile_runs.append(
            _run_codex_compile_pass(
                project_root,
                prompt=COMPILE_PROMPT_2,
                pass_index=2,
                total_passes=3,
                provider=provider,
                provider_bin=provider_bin,
            )
        )
    finally:
        shutil.rmtree(tool_dest, ignore_errors=True)

    if tool_dest.exists():
        raise PackageError(f"Failed to clean up temporary tool directory: {tool_dest}")

    compile_runs.append(
        _run_codex_compile_pass(
            project_root,
            prompt=COMPILE_PROMPT_3,
            pass_index=3,
            total_passes=3,
            provider=provider,
            provider_bin=provider_bin,
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


def _cmd_agent(args: argparse.Namespace) -> int:
    desired_provider = args.provider
    desired_sandbox_policy = None
    if args.sandbox:
        desired_sandbox_policy = "enforce"
    elif args.bypass_sandbox:
        desired_sandbox_policy = "bypass"

    if desired_provider is None and desired_sandbox_policy is None:
        policy = load_agent_runtime_policy()
        payload = policy.as_dict()
        lines = [
            f"Provider: {policy.provider}.",
            (
                f"Sandbox: enabled ({policy.sandbox_mode})."
                if policy.sandbox_policy == "enforce"
                else "Sandbox: bypassed."
            ),
        ]
        _emit_output(args, payload, lines)
        return 0

    updated = save_agent_runtime_policy(
        provider=desired_provider,
        sandbox_policy=desired_sandbox_policy,
    )
    payload = updated.as_dict()
    lines = [
        f"Provider set to {updated.provider}.",
        (
            f"Sandbox enforced with mode {updated.sandbox_mode}."
            if updated.sandbox_policy == "enforce"
            else (
                "Sandbox bypass enabled (Codex internal sandbox only; "
                "external host restrictions may still apply)."
            )
        ),
    ]
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

    exec_parser = subparsers.add_parser(
        "exec",
        help=(
            "Run one prompt locally with web-like package routing, second guess, "
            "package overlay symlinks, and AGENTS template sync."
        ),
    )
    exec_parser.add_argument(
        "prompt",
        nargs="+",
        help="Prompt text to run via codex.",
    )
    exec_parser.add_argument(
        "--package",
        dest="package_id",
        help="Pin one installed package id and skip auto routing.",
    )
    exec_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for this run. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    exec_parser.add_argument(
        "--codex-bin",
        default=DEFAULT_COMPILE_CODEX_BIN,
        help=(
            f"Codex executable path (default: {DEFAULT_COMPILE_CODEX_BIN}). "
            "Ignored when provider is not codex."
        ),
    )
    exec_parser.add_argument(
        "--init-git",
        action="store_true",
        help="Auto-run git init when current directory is not a git repository.",
    )
    exec_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of prompting/initializing when git repository is missing.",
    )
    exec_parser.set_defaults(func=_cmd_exec)

    chat_parser = subparsers.add_parser(
        "chat",
        help=(
            "Run interactive multi-turn local chat with web-like package routing, "
            "second guess, and package overlays."
        ),
    )
    chat_parser.add_argument(
        "--package",
        dest="package_id",
        help="Pin one installed package id for all turns and skip auto routing.",
    )
    chat_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for this chat session. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    chat_parser.add_argument(
        "--codex-bin",
        default=DEFAULT_COMPILE_CODEX_BIN,
        help=(
            f"Codex executable path (default: {DEFAULT_COMPILE_CODEX_BIN}). "
            "Ignored when provider is not codex."
        ),
    )
    chat_parser.add_argument(
        "--init-git",
        action="store_true",
        help="Auto-run git init when current directory is not a git repository.",
    )
    chat_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of prompting/initializing when git repository is missing.",
    )
    chat_parser.set_defaults(func=_cmd_chat)

    agent_parser = subparsers.add_parser(
        "agent",
        help=(
            "Manage global agent runtime policy for provider and sandbox behavior "
            "used by runner/web/exec/chat/compile."
        ),
    )
    _add_json_option(agent_parser)
    agent_parser.add_argument(
        "provider",
        nargs="?",
        choices=SUPPORTED_PROVIDERS,
        help="Agent provider selection (codex, claude, gemini).",
    )
    sandbox_group = agent_parser.add_mutually_exclusive_group(required=False)
    sandbox_group.add_argument(
        "--sandbox",
        action="store_true",
        help="Enforce sandbox mode.",
    )
    sandbox_group.add_argument(
        "--bypass-sandbox",
        action="store_true",
        help="Bypass sandbox mode.",
    )
    agent_parser.set_defaults(func=_cmd_agent)

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
