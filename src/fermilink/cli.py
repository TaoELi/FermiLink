from __future__ import annotations

import argparse
import functools
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from fermilink.agent_runtime import (
    SUPPORTED_PROVIDERS,
    load_agent_runtime_policy,
    resolve_agent_runtime_policy,
    save_agent_runtime_policy,
)
from fermilink.config import resolve_runtime_root, resolve_scipkg_root
from fermilink.curated_channels import (
    list_curated_packages,
    normalize_channel_id,
    resolve_curated_package,
    select_package_version,
)
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
    save_registry,
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
    os.getenv("FERMILINK_PACKAGE_SECOND_GUESS_ENABLED", "true").strip().lower()
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
WEB_ROUTER_ONLY_IMPORT_ENV = "FERMILINK_ROUTER_ONLY_IMPORT"

LOOP_MEMORY_DIRNAME = "projects"
LOOP_MEMORY_FILENAME = "memory.md"
LOOP_DONE_TOKEN = "<promise>DONE</promise>"
LOOP_WAIT_TOKEN_RE = re.compile(
    r"^\s*<wait_seconds>\s*([0-9]+(?:\.[0-9]+)?)\s*</wait_seconds>\s*$",
    re.MULTILINE,
)
REPRODUCE_PLAN_TOKEN_RE = re.compile(
    r"<reproduce_plan>\s*(\{.*?\})\s*</reproduce_plan>",
    re.DOTALL,
)
RESEARCH_PLAN_TOKEN_RE = re.compile(
    r"<research_plan>\s*(\{.*?\})\s*</research_plan>",
    re.DOTALL,
)
LOOP_PROMPT_PREFIX = (
    "You are running in **FermiLink loop mode**.\n"
    "\n"
    "Persistent memory lives at `projects/memory.md` (relative to the repo root).\n"
    "\n"
    "Long-running jobs (SLURM or similar): it is OK to submit a job, record job ids/paths\n"
    "in `projects/memory.md`, and end the iteration without waiting. A later iteration can\n"
    "check status and continue.\n"
    "\n"
    "At the start of this iteration:\n"
    "1) Read `projects/memory.md`.\n"
    "2) If it does not contain a clear checklist plan, create one (5-15 small steps).\n"
    "3) Execute exactly ONE next unchecked step.\n"
    "4) Update `projects/memory.md`:\n"
    "   - Check off the completed step.\n"
    "   - Append a short progress log entry (what changed + files touched).\n"
    "   - Append a short pending simulation log entry if you have submitted a long-running job, including job id and expected duration.\n"
    "   - Append a short additional notes log entry for the pitfalls you have avoided or key problems encountered.\n"
    "\n"
    "If the task is not complete, provide one machine-readable wait hint on its own line:\n"
    "<wait_seconds>NUMBER</wait_seconds>\n"
    "where NUMBER is a non-negative number of seconds (no units, no extra text).\n"
    "Do not include this wait tag once you are done.\n"
    "\n"
    f"When (and only when) ALL steps are complete and the request is satisfied, output exactly:\n"
    f"{LOOP_DONE_TOKEN}\n"
    "on its own line.\n"
    "\n"
    "Original request:\n"
)
REPRODUCE_PLAN_TAG = "reproduce_plan"
RESEARCH_PLAN_TAG = "research_plan"
REPRODUCE_STATE_FILENAME = "state.json"
REPRODUCE_PLAN_FILENAME = "plan.json"
REPRODUCE_PROMPTS_DIRNAME = "prompts"
REPRODUCE_LOGS_DIRNAME = "logs"
REPRODUCE_ARCHIVE_DIRNAME = "archive"
REPRODUCE_RUNS_DIR = "reproduce"
RESEARCH_RUNS_DIR = "research"
REPRODUCE_LATEST_RUN_FILENAME = "latest_run.txt"
WORKFLOW_SUMMARIES_DIRNAME = "summaries"
WORKFLOW_REPORT_FILENAME = "report.md"
REPRODUCE_PLANNER_PROMPT_PREFIX = (
    "You are running in **FermiLink reproduce planner mode**.\n"
    "\n"
    "Goal: split a paper-level reproduction request into medium, executable tasks.\n"
    "Each task should map to a full figure or a coherent fraction of one figure.\n"
    "\n"
    "Output exactly one XML-like block:\n"
    f"<{REPRODUCE_PLAN_TAG}>{{JSON}}</{REPRODUCE_PLAN_TAG}>\n"
    "\n"
    "JSON schema:\n"
    "{\n"
    '  "version": 1,\n'
    '  "paper_source": "short source description",\n'
    '  "assumptions": ["..."],\n'
    '  "tasks": [\n'
    "    {\n"
    '      "id": "task_001",\n'
    '      "title": "short title",\n'
    '      "figure_targets": ["Figure 1a"],\n'
    '      "objective": "what to reproduce",\n'
    '      "simulation_requirements": ["what to simulate"],\n'
    '      "parameter_constraints": ["parameters/conditions"],\n'
    '      "plot_requirements": ["axes/style/colors"],\n'
    '      "acceptance_checks": ["completion criteria"],\n'
    '      "prompt_markdown": "prompt text for one fermilink loop task"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "Rules:\n"
    "- Return valid JSON only inside the tag (no markdown fences).\n"
    "- Keep task count practical (3-12 tasks unless source is tiny).\n"
    "- Include concrete parameters/plot details when available; otherwise add assumptions.\n"
    "- Ensure each task prompt is self-contained and actionable.\n"
)
REPRODUCE_AUDITOR_PROMPT_PREFIX = (
    "You are running in **FermiLink reproduce auditor mode**.\n"
    "\n"
    "You will receive an original paper request and a proposed reproduce plan.\n"
    "Audit the plan for completeness, executability, and scientific consistency.\n"
    "Fix missing details, impossible ordering, or vague task prompts.\n"
    "\n"
    "Return exactly one corrected plan block:\n"
    f"<{REPRODUCE_PLAN_TAG}>{{JSON}}</{REPRODUCE_PLAN_TAG}>\n"
    "\n"
    "Use the same JSON schema as the planner and keep task ids stable when possible.\n"
    "Return valid JSON only inside the tag (no markdown fences).\n"
)
RESEARCH_PLANNER_PROMPT_PREFIX = (
    "You are running in **FermiLink research planner mode**.\n"
    "\n"
    "Goal: turn a short scientific research idea into a concrete, executable plan.\n"
    "Design tasks that can be executed sequentially and that cumulatively produce\n"
    "publication-quality simulation results and figures.\n"
    "\n"
    "Output exactly one XML-like block:\n"
    f"<{RESEARCH_PLAN_TAG}>{{JSON}}</{RESEARCH_PLAN_TAG}>\n"
    "\n"
    "Use the same JSON schema as reproduce planning (version/paper_source/assumptions/tasks).\n"
    "Each task must include `prompt_markdown` suitable for one fermilink loop task.\n"
    "Include baselines/controls/parameter sweeps and plotting requirements where relevant.\n"
    "Return valid JSON only inside the tag (no markdown fences).\n"
)
RESEARCH_AUDITOR_PROMPT_PREFIX = (
    "You are running in **FermiLink research auditor mode**.\n"
    "\n"
    "You will receive a research request and a candidate task plan.\n"
    "Audit for feasibility, ordering, scientific rigor, and reproducibility.\n"
    "Fix vague tasks, missing acceptance checks, and weak experimental controls.\n"
    "\n"
    "Return exactly one corrected plan block:\n"
    f"<{RESEARCH_PLAN_TAG}>{{JSON}}</{RESEARCH_PLAN_TAG}>\n"
    "\n"
    "Keep JSON schema identical to reproduce planning and keep task ids stable when possible.\n"
    "Return valid JSON only inside the tag (no markdown fences).\n"
)
WORKFLOW_REPORT_GENERATOR_PROMPT_PREFIX = (
    "You are running in **FermiLink workflow report generation mode**.\n"
    "\n"
    "You must generate concise per-task summaries and a polished top-level report.\n"
)
WORKFLOW_REPORT_AUDITOR_PROMPT_PREFIX = (
    "You are running in **FermiLink workflow report audit mode**.\n"
    "\n"
    "You are an independent reviewer. Read the generated report and improve it for\n"
    "scientific clarity, completeness, and reproducibility.\n"
)


def _should_style_cli_output() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("FERMILINK_NO_STYLE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    term = os.getenv("TERM", "").strip().lower()
    if term in {"", "dumb"}:
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
    # CLI exec/chat only need routing helpers; avoid web-only filesystem setup.
    os.environ.setdefault(WEB_ROUTER_ONLY_IMPORT_ENV, "1")
    # Some HPC systems export PROJECT as a filesystem path (e.g. /anvil/projects/...).
    # Chainlit parses PROJECT for a structured settings field named "project",
    # which can raise pydantic SettingsError during import when the value is not JSON.
    saved_project = os.environ.pop("PROJECT", None)
    try:
        return importlib.import_module("fermilink.web.app")
    finally:
        if saved_project is not None:
            os.environ["PROJECT"] = saved_project


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


def _inject_exec_option_before_prompt(command: list[str], *option_tokens: str) -> list[str]:
    """Insert option tokens before the final prompt argument."""

    if not command:
        return command
    prompt_arg = command[-1]
    return [*command[:-1], *option_tokens, prompt_arg]


def _is_public_overlay_name(name: str) -> bool:
    return name.strip().strip("/\\").lower() == "public"


def _filter_exec_overlay_package_meta(package_meta: dict[str, object]) -> dict[str, object]:
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
        candidates = [segment.strip() for segment in raw_entries if isinstance(segment, str)]
    else:
        return sanitized

    sanitized["overlay_entries"] = [
        name
        for name in candidates
        if name and not _is_public_overlay_name(name)
    ]
    return sanitized


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

        cmd = _inject_exec_option_before_prompt(cmd, "--color", "always")
        cmd = _inject_exec_option_before_prompt(
            cmd, "--output-last-message", str(last_message_path)
        )

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
    _print_tagged("agent", f"provider: {provider}, sandbox: {sandbox_text}")
    _print_tagged("chat", "Interactive mode. Type `exit` or `quit` to leave.")

    web_app = _load_web_router_module()
    history: list[tuple[str, str]] = []
    current_package_id: str | None = None
    current_source = PACKAGE_SOURCE_NONE

    while True:
        try:
            user_text = input(_chat_input_prompt())
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 0
        _chat_prompt_spacing_after_input()

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
        _print_tagged("package", f"Using {package_id} (selection: {source})")
        if note and note not in {"manual_pin", "default_fallback", "matched"}:
            _print_tagged("router", note)

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
        _print_tagged(
            "overlay",
            (
                "linked entries: "
                f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
            ),
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
            assistant_prefix = _style_text("Assistant>", "1", "38;5;111")
            print(f"{assistant_prefix} {assistant_text}")
        if return_code != 0:
            if stderr_text:
                print(stderr_text, file=sys.stderr)
            _print_tagged(
                "chat",
                f"provider exited with code {return_code}.",
                stderr=True,
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
    filtered_package_meta = _filter_exec_overlay_package_meta(package_meta)

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
                target = repo_dir / name
                if target.exists() or target.is_symlink():
                    if target.is_symlink() or target.is_file():
                        target.unlink(missing_ok=True)
                    elif target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
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
                        shutil.rmtree(target, ignore_errors=True)
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
    cmd = _inject_exec_option_before_prompt(cmd, "--color", "always")
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


def _resolve_exec_like_user_prompt(args: argparse.Namespace) -> tuple[str, str | None]:
    prompt_tokens = getattr(args, "prompt", None)
    command_name = str(getattr(args, "command", "command"))
    if not isinstance(prompt_tokens, list) or not prompt_tokens:
        raise PackageError(f"Prompt is required for fermilink {command_name}.")

    if len(prompt_tokens) == 1:
        candidate_path = Path(str(prompt_tokens[0])).expanduser()
        if not candidate_path.is_absolute():
            candidate_path = (Path.cwd() / candidate_path).resolve()
        try:
            is_file = candidate_path.is_file()
        except OSError:
            # Treat invalid/too-long path-like values as plain prompt text.
            is_file = False
        if is_file:
            try:
                content = candidate_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise PackageError(f"Failed to read prompt file: {candidate_path}: {exc}") from exc
            text = content.strip()
            if not text:
                raise PackageError(f"Prompt file is empty: {candidate_path}")
            return text, str(candidate_path)

    text = " ".join(str(token) for token in prompt_tokens).strip()
    if not text:
        raise PackageError(f"Prompt is required for fermilink {command_name}.")
    return text, None


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        raise PackageError(f"Failed to write file: {path}: {exc}") from exc


def _normalize_string_list(raw: object) -> list[str]:
    if isinstance(raw, list):
        values: list[str] = []
        for item in raw:
            text = str(item).strip()
            if text:
                values.append(text)
        return values
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    return []


def _sanitize_task_id(raw_id: object, index: int, used: set[str]) -> str:
    candidate = str(raw_id).strip().lower() if raw_id is not None else ""
    if not candidate:
        candidate = f"task_{index:03d}"
    candidate = re.sub(r"[^a-z0-9_-]+", "_", candidate).strip("_")
    if not candidate:
        candidate = f"task_{index:03d}"
    if not candidate.startswith("task_"):
        candidate = f"task_{candidate}"

    deduped = candidate
    suffix = 2
    while deduped in used:
        deduped = f"{candidate}_{suffix}"
        suffix += 1
    used.add(deduped)
    return deduped


def _render_reproduce_task_prompt(task: dict[str, object]) -> str:
    task_id = str(task.get("id") or "task")
    title = str(task.get("title") or "Reproduce task").strip()
    objective = str(task.get("objective") or "").strip()
    figure_targets = _normalize_string_list(task.get("figure_targets"))
    simulation_requirements = _normalize_string_list(task.get("simulation_requirements"))
    parameter_constraints = _normalize_string_list(task.get("parameter_constraints"))
    plot_requirements = _normalize_string_list(task.get("plot_requirements"))
    acceptance_checks = _normalize_string_list(task.get("acceptance_checks"))

    lines: list[str] = [
        f"# Reproduce Task {task_id}: {title}",
        "",
        "## Objective",
        objective or "Reproduce the requested scientific result for this task.",
    ]
    if figure_targets:
        lines.extend(["", "## Figure targets", *[f"- {item}" for item in figure_targets]])
    if simulation_requirements:
        lines.extend(
            ["", "## Simulation requirements", *[f"- {item}" for item in simulation_requirements]]
        )
    if parameter_constraints:
        lines.extend(
            ["", "## Parameter constraints", *[f"- {item}" for item in parameter_constraints]]
        )
    if plot_requirements:
        lines.extend(["", "## Plot requirements", *[f"- {item}" for item in plot_requirements]])
    if acceptance_checks:
        lines.extend(["", "## Acceptance checks", *[f"- {item}" for item in acceptance_checks]])
    lines.extend(
        [
            "",
            "## Execution notes",
            "- Keep scripts, data, and plots reproducible.",
            "- Save run details and blockers in projects/memory.md.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _extract_tagged_json_payload(
    assistant_text: str, *, token_re: re.Pattern[str]
) -> dict[str, object] | None:
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        return None
    matches = token_re.findall(assistant_text)
    if not matches:
        return None
    raw_payload = matches[-1].strip()
    if not raw_payload:
        return None
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _extract_reproduce_plan_payload(assistant_text: str) -> dict[str, object] | None:
    return _extract_tagged_json_payload(assistant_text, token_re=REPRODUCE_PLAN_TOKEN_RE)


def _extract_research_plan_payload(assistant_text: str) -> dict[str, object] | None:
    return _extract_tagged_json_payload(assistant_text, token_re=RESEARCH_PLAN_TOKEN_RE)


def _normalize_automation_plan(
    raw_plan: object,
    *,
    source_description: str,
) -> dict[str, object]:
    if not isinstance(raw_plan, dict):
        raise PackageError("Reproduce plan must be a JSON object.")

    raw_tasks = raw_plan.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PackageError("Reproduce plan must include a non-empty `tasks` list.")

    normalized_tasks: list[dict[str, object]] = []
    used_ids: set[str] = set()
    for index, raw_task in enumerate(raw_tasks, start=1):
        if not isinstance(raw_task, dict):
            raise PackageError(f"Task {index} in reproduce plan is not an object.")

        task_id = _sanitize_task_id(raw_task.get("id"), index, used_ids)
        title = str(raw_task.get("title") or f"Task {index}").strip()
        objective = str(raw_task.get("objective") or "").strip()
        figure_targets = _normalize_string_list(raw_task.get("figure_targets"))
        simulation_requirements = _normalize_string_list(raw_task.get("simulation_requirements"))
        parameter_constraints = _normalize_string_list(raw_task.get("parameter_constraints"))
        plot_requirements = _normalize_string_list(raw_task.get("plot_requirements"))
        acceptance_checks = _normalize_string_list(raw_task.get("acceptance_checks"))
        prompt_markdown = str(raw_task.get("prompt_markdown") or "").strip()

        normalized_task: dict[str, object] = {
            "id": task_id,
            "title": title,
            "figure_targets": figure_targets,
            "objective": objective,
            "simulation_requirements": simulation_requirements,
            "parameter_constraints": parameter_constraints,
            "plot_requirements": plot_requirements,
            "acceptance_checks": acceptance_checks,
        }
        if not prompt_markdown:
            prompt_markdown = _render_reproduce_task_prompt(normalized_task).strip()
        normalized_task["prompt_markdown"] = prompt_markdown
        normalized_tasks.append(normalized_task)

    return {
        "version": 1,
        "paper_source": str(raw_plan.get("paper_source") or source_description).strip()
        or source_description,
        "assumptions": _normalize_string_list(raw_plan.get("assumptions")),
        "tasks": normalized_tasks,
    }


def _normalize_reproduce_plan(
    raw_plan: object,
    *,
    source_description: str,
) -> dict[str, object]:
    return _normalize_automation_plan(raw_plan, source_description=source_description)


def _normalize_research_plan(
    raw_plan: object,
    *,
    source_description: str,
) -> dict[str, object]:
    return _normalize_automation_plan(raw_plan, source_description=source_description)


def _run_reproduce_exec_turn(
    *,
    repo_dir: Path,
    prompt: str,
    requested_package_id: str | None,
    sandbox_override: str | None,
    codex_bin: str,
) -> dict[str, object]:
    scipkg_root = resolve_scipkg_root()
    runtime_policy = resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(sandbox_override, str) and sandbox_override.strip():
        sandbox_policy = "enforce"
        sandbox_mode = sandbox_override.strip()

    provider_bin = codex_bin if provider == "codex" else None
    selection = _resolve_exec_package_selection(
        user_prompt=prompt,
        scipkg_root=scipkg_root,
        repo_dir=repo_dir,
        requested_package_id=requested_package_id,
        provider=provider,
        provider_bin=provider_bin,
        sandbox_policy=sandbox_policy,
    )
    package_id = selection.get("package_id")
    if not isinstance(package_id, str) or not package_id:
        raise PackageError("No package selected for reproduce execution.")

    source = str(selection.get("source") or "default")
    note = str(selection.get("note") or "").strip()
    _print_tagged("package", f"Using {package_id} (selection: {source})")
    if note and note not in {"manual_pin", "default_fallback", "matched"}:
        _print_tagged("router", note)
    sandbox_text = f"enforce({sandbox_mode})" if sandbox_policy == "enforce" else "bypass"
    _print_tagged("agent", f"provider: {provider}, sandbox: {sandbox_text}")

    overlay = _overlay_exec_package(
        repo_dir=repo_dir,
        scipkg_root=scipkg_root,
        package_id=package_id,
    )
    linked = int(overlay.get("linked_count", 0)) if isinstance(overlay, dict) else 0
    collisions = int(overlay.get("collision_count", 0)) if isinstance(overlay, dict) else 0
    linked_deps = (
        int(overlay.get("linked_dependency_count", 0))
        if isinstance(overlay, dict)
        else 0
    )
    _print_tagged(
        "overlay",
        (
            "linked entries: "
            f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
        ),
    )

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

    return run_result


def _generate_mode_plan(
    *,
    repo_dir: Path,
    source_text: str,
    source_description: str,
    requested_package_id: str | None,
    sandbox_override: str | None,
    codex_bin: str,
    planner_max_tries: int,
    auditor_max_tries: int,
    planner_prompt_prefix: str,
    auditor_prompt_prefix: str,
    plan_tag: str,
    extract_payload,
    normalize_plan,
    log_tag: str,
) -> dict[str, object]:
    planner_prompt = (
        f"{planner_prompt_prefix}\n\n"
        f"Paper source: {source_description}\n\n"
        "Paper content / request:\n"
        f"{source_text.strip()}\n"
    )
    planner_plan: dict[str, object] | None = None
    for attempt in range(1, planner_max_tries + 1):
        _print_tagged(log_tag, f"planner attempt {attempt}/{planner_max_tries}")
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=planner_prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            codex_bin=codex_bin,
        )
        return_code = int(run_result.get("return_code") or 0)
        if return_code != 0:
            raise PackageError(
                f"{log_tag.title()} planner agent run failed with exit code {return_code}."
            )
        assistant_text = str(run_result.get("assistant_text") or "")
        raw_payload = extract_payload(assistant_text)
        if raw_payload is None:
            _print_tagged(
                log_tag,
                f"planner response missing <{plan_tag}> JSON block.",
                stderr=True,
            )
            continue
        try:
            planner_plan = normalize_plan(raw_payload, source_description=source_description)
        except PackageError as exc:
            _print_tagged(log_tag, f"planner response invalid: {exc}", stderr=True)
            continue
        break
    if planner_plan is None:
        raise PackageError(f"Unable to generate a valid {log_tag} plan from planner response.")

    audited_plan: dict[str, object] | None = None
    auditor_prompt = (
        f"{auditor_prompt_prefix}\n\n"
        f"Paper source: {source_description}\n\n"
        "Original paper content / request:\n"
        f"{source_text.strip()}\n\n"
        "Candidate plan JSON:\n"
        f"{json.dumps(planner_plan, indent=2)}\n"
    )
    for attempt in range(1, auditor_max_tries + 1):
        _print_tagged(log_tag, f"auditor attempt {attempt}/{auditor_max_tries}")
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=auditor_prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            codex_bin=codex_bin,
        )
        return_code = int(run_result.get("return_code") or 0)
        if return_code != 0:
            raise PackageError(
                f"{log_tag.title()} auditor agent run failed with exit code {return_code}."
            )
        assistant_text = str(run_result.get("assistant_text") or "")
        raw_payload = extract_payload(assistant_text)
        if raw_payload is None:
            _print_tagged(
                log_tag,
                f"auditor response missing <{plan_tag}> JSON block.",
                stderr=True,
            )
            continue
        try:
            audited_plan = normalize_plan(raw_payload, source_description=source_description)
        except PackageError as exc:
            _print_tagged(log_tag, f"auditor response invalid: {exc}", stderr=True)
            continue
        break
    if audited_plan is None:
        raise PackageError(f"Unable to generate a valid {log_tag} plan from auditor response.")
    return audited_plan


def _generate_reproduce_plan(
    *,
    repo_dir: Path,
    source_text: str,
    source_description: str,
    requested_package_id: str | None,
    sandbox_override: str | None,
    codex_bin: str,
    planner_max_tries: int,
    auditor_max_tries: int,
) -> dict[str, object]:
    return _generate_mode_plan(
        repo_dir=repo_dir,
        source_text=source_text,
        source_description=source_description,
        requested_package_id=requested_package_id,
        sandbox_override=sandbox_override,
        codex_bin=codex_bin,
        planner_max_tries=planner_max_tries,
        auditor_max_tries=auditor_max_tries,
        planner_prompt_prefix=REPRODUCE_PLANNER_PROMPT_PREFIX,
        auditor_prompt_prefix=REPRODUCE_AUDITOR_PROMPT_PREFIX,
        plan_tag=REPRODUCE_PLAN_TAG,
        extract_payload=_extract_reproduce_plan_payload,
        normalize_plan=_normalize_reproduce_plan,
        log_tag="reproduce",
    )


def _generate_research_plan(
    *,
    repo_dir: Path,
    source_text: str,
    source_description: str,
    requested_package_id: str | None,
    sandbox_override: str | None,
    codex_bin: str,
    planner_max_tries: int,
    auditor_max_tries: int,
) -> dict[str, object]:
    return _generate_mode_plan(
        repo_dir=repo_dir,
        source_text=source_text,
        source_description=source_description,
        requested_package_id=requested_package_id,
        sandbox_override=sandbox_override,
        codex_bin=codex_bin,
        planner_max_tries=planner_max_tries,
        auditor_max_tries=auditor_max_tries,
        planner_prompt_prefix=RESEARCH_PLANNER_PROMPT_PREFIX,
        auditor_prompt_prefix=RESEARCH_AUDITOR_PROMPT_PREFIX,
        plan_tag=RESEARCH_PLAN_TAG,
        extract_payload=_extract_research_plan_payload,
        normalize_plan=_normalize_research_plan,
        log_tag="research",
    )


def _archive_loop_memory(*, repo_dir: Path, archive_dir: Path, task_id: str, run_count: int) -> None:
    memory_path = repo_dir / LOOP_MEMORY_DIRNAME / LOOP_MEMORY_FILENAME
    if not memory_path.is_file():
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"memory_{task_id}_run_{run_count:02d}.md"
    try:
        shutil.copy2(memory_path, archive_path)
    except OSError as exc:
        raise PackageError(f"Failed to archive loop memory to {archive_path}: {exc}") from exc


def _ensure_loop_memory(
    *,
    repo_dir: Path,
    user_prompt: str,
    prompt_file: str | None,
    overwrite: bool = False,
) -> Path:
    projects_dir = repo_dir / LOOP_MEMORY_DIRNAME
    if projects_dir.exists() and projects_dir.is_symlink():
        raise PackageError(
            f"{projects_dir} is a symlink. Remove it and create a real directory "
            "so fermilink loop can persist long-term memory safely."
        )
    if projects_dir.exists() and not projects_dir.is_dir():
        raise PackageError(f"{projects_dir} exists but is not a directory.")
    projects_dir.mkdir(parents=True, exist_ok=True)

    memory_path = projects_dir / LOOP_MEMORY_FILENAME
    if memory_path.exists():
        if memory_path.is_dir():
            raise PackageError(f"{memory_path} exists but is a directory.")
        if not overwrite:
            return memory_path

    started_at = _utc_now_z()
    source_line = f"- prompt_source: {prompt_file}\n" if prompt_file else ""
    initial = (
        "# FermiLink Loop Memory\n"
        "\n"
        f"- started_at_utc: {started_at}\n"
        f"{source_line}"
        "\n"
        "## Original request\n"
        f"{user_prompt.strip()}\n"
        "\n"
        "## Plan\n"
        "- [ ] (fill in a small checklist plan)\n"
        "\n"
        "## Progress log\n"
        "- initialized\n"
    )
    try:
        memory_path.write_text(initial, encoding="utf-8")
    except OSError as exc:
        raise PackageError(f"Failed to create loop memory file: {memory_path}: {exc}") from exc
    return memory_path


def _extract_loop_wait_seconds(assistant_text: str) -> float | None:
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        return None
    matches = LOOP_WAIT_TOKEN_RE.findall(assistant_text)
    if not matches:
        return None
    raw = matches[-1]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 or not math.isfinite(value):
        return None
    return value


def _cmd_loop(args: argparse.Namespace) -> int:
    repo_dir = Path.cwd().resolve()
    _ensure_exec_repo_ready(repo_dir, args)

    # Best-effort cleanup from previously interrupted overlays.
    _cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)

    user_prompt, prompt_file = _resolve_exec_like_user_prompt(args)
    memory_path = _ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt=user_prompt,
        prompt_file=prompt_file,
    )
    _print_tagged("loop", f"memory: {memory_path.relative_to(repo_dir)}")

    max_iterations_raw = getattr(args, "max_iterations", 10)
    try:
        max_iterations = int(max_iterations_raw)
    except (TypeError, ValueError) as exc:
        raise PackageError("--max-iterations must be an integer.") from exc
    if max_iterations < 1:
        raise PackageError("--max-iterations must be >= 1.")

    wait_seconds_raw = getattr(args, "wait_seconds", 0.0)
    try:
        wait_seconds = float(wait_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise PackageError("--wait-seconds must be a number.") from exc
    if wait_seconds < 0:
        raise PackageError("--wait-seconds must be >= 0.")

    max_wait_seconds_raw = getattr(args, "max_wait_seconds", 600.0)
    try:
        max_wait_seconds = float(max_wait_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise PackageError("--max-wait-seconds must be a number.") from exc
    if max_wait_seconds < 0:
        raise PackageError("--max-wait-seconds must be >= 0.")

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
        user_prompt=user_prompt,
        scipkg_root=scipkg_root,
        repo_dir=repo_dir,
        requested_package_id=args.package_id,
        provider=provider,
        provider_bin=provider_bin,
        sandbox_policy=sandbox_policy,
    )
    package_id = selection.get("package_id")
    if not isinstance(package_id, str) or not package_id:
        raise PackageError("No package selected for loop execution.")

    source = str(selection.get("source") or "default")
    note = str(selection.get("note") or "").strip()
    _print_tagged("package", f"Using {package_id} (selection: {source})")
    if note and note not in {"manual_pin", "default_fallback", "matched"}:
        _print_tagged("router", note)
    sandbox_text = (
        f"enforce({sandbox_mode})" if sandbox_policy == "enforce" else "bypass"
    )
    _print_tagged("agent", f"provider: {provider}, sandbox: {sandbox_text}")

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
    _print_tagged(
        "overlay",
        (
            "linked entries: "
            f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
        ),
    )

    prompt = f"{LOOP_PROMPT_PREFIX}{user_prompt.strip()}\n"
    try:
        for iteration in range(1, max_iterations + 1):
            _print_tagged("loop", f"iteration {iteration}/{max_iterations}")
            run_result = _run_exec_chat_turn(
                repo_dir=repo_dir,
                prompt=prompt,
                sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
                codex_bin=provider_bin,
                provider=provider,
                sandbox_policy=sandbox_policy,
            )

            assistant_text = str(run_result.get("assistant_text") or "")
            done = any(line.strip() == LOOP_DONE_TOKEN for line in assistant_text.splitlines())
            if done:
                print(LOOP_DONE_TOKEN)
                return 0

            return_code = int(run_result.get("return_code") or 0)
            if return_code != 0:
                return return_code

            if iteration < max_iterations:
                suggested_wait = _extract_loop_wait_seconds(assistant_text)
                wait_source = "agent" if suggested_wait is not None else "default"
                requested_wait = (
                    suggested_wait if suggested_wait is not None else wait_seconds
                )
                effective_wait = min(requested_wait, max_wait_seconds)
                if effective_wait > 0:
                    if requested_wait > max_wait_seconds:
                        _print_tagged(
                            "loop",
                            (
                                "sleeping "
                                f"{effective_wait:.1f}s before next iteration "
                                f"(source: {wait_source}, capped by --max-wait-seconds)"
                            ),
                        )
                    else:
                        _print_tagged(
                            "loop",
                            (
                                "sleeping "
                                f"{effective_wait:.1f}s before next iteration "
                                f"(source: {wait_source})"
                            ),
                        )
                    time.sleep(effective_wait)
    finally:
        _cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)

    _print_tagged(
        "loop",
        f"max iterations reached ({max_iterations}) without {LOOP_DONE_TOKEN}.",
        stderr=True,
    )
    return 1


def _materialize_mode_plan(
    *,
    run_dir: Path,
    plan: dict[str, object],
    state: dict[str, object],
    workflow_name: str,
) -> None:
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise PackageError(f"{workflow_name.title()} planner produced no executable tasks.")

    prompts_dir = run_dir / REPRODUCE_PROMPTS_DIRNAME
    prompts_dir.mkdir(parents=True, exist_ok=True)
    plan_tasks: list[dict[str, object]] = []
    state_tasks: list[dict[str, object]] = []
    task_runs: dict[str, int] = {}

    for index, task_obj in enumerate(tasks, start=1):
        if not isinstance(task_obj, dict):
            raise PackageError(f"Task {index} in {workflow_name} plan is invalid.")
        task_id = str(task_obj.get("id") or f"task_{index:03d}").strip() or f"task_{index:03d}"
        prompt_markdown = str(task_obj.get("prompt_markdown") or "").strip()
        if not prompt_markdown:
            raise PackageError(f"Task {task_id} has empty `prompt_markdown`.")
        prompt_rel = f"{REPRODUCE_PROMPTS_DIRNAME}/{task_id}.md"
        prompt_path = run_dir / prompt_rel
        try:
            prompt_path.write_text(prompt_markdown.strip() + "\n", encoding="utf-8")
        except OSError as exc:
            raise PackageError(f"Failed to write task prompt file: {prompt_path}: {exc}") from exc
        plan_task = dict(task_obj)
        plan_task["prompt_file"] = prompt_rel
        plan_tasks.append(plan_task)
        state_tasks.append(
            {
                "id": task_id,
                "title": str(task_obj.get("title") or task_id),
                "prompt_file": prompt_rel,
            }
        )
        task_runs[task_id] = 0

    plan["tasks"] = plan_tasks
    _write_json_atomic(run_dir / REPRODUCE_PLAN_FILENAME, plan)
    state["tasks"] = state_tasks
    state["task_runs"] = task_runs
    state["current_task_index"] = 0
    state["last_error"] = ""


def _maybe_sync_mode_plan_from_disk(
    *,
    run_dir: Path,
    state: dict[str, object],
    source_description: str,
    workflow_name: str,
) -> bool:
    state_status = str(state.get("status") or "")
    current_index_raw = state.get("current_task_index", 0)
    try:
        current_index = int(current_index_raw)
    except (TypeError, ValueError):
        current_index = 0
    if state_status != "plan_ready" or current_index != 0:
        return False

    plan_path = run_dir / REPRODUCE_PLAN_FILENAME
    if not plan_path.is_file():
        return False

    try:
        raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"Failed to read {workflow_name} plan file: {plan_path}: {exc}") from exc
    if not isinstance(raw_plan, dict):
        raise PackageError(f"{workflow_name.title()} plan file is not a JSON object: {plan_path}")

    normalized_plan = _normalize_automation_plan(
        raw_plan,
        source_description=source_description,
    )
    _materialize_mode_plan(
        run_dir=run_dir,
        plan=normalized_plan,
        state=state,
        workflow_name=workflow_name,
    )
    state["status"] = "plan_ready"
    state["updated_at_utc"] = _utc_now_z()
    _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
    return True


def _finalize_workflow_report(
    *,
    repo_dir: Path,
    run_dir: Path,
    runs_root: Path,
    workflow_name: str,
    source_description: str,
    tasks_state: list[dict[str, object]],
    requested_package_id: str | None,
    sandbox_override: str | None,
    codex_bin: str,
) -> dict[str, object]:
    plan_path = run_dir / REPRODUCE_PLAN_FILENAME
    if not plan_path.is_file():
        raise PackageError(f"Missing plan file for {workflow_name} report generation: {plan_path}")

    summaries_root = run_dir / WORKFLOW_SUMMARIES_DIRNAME
    summaries_root.mkdir(parents=True, exist_ok=True)
    report_path = runs_root / WORKFLOW_REPORT_FILENAME

    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(repo_dir))
        except ValueError:
            return str(path)

    summary_paths: list[Path] = []
    task_lines: list[str] = []
    for index, task in enumerate(tasks_state, start=1):
        task_id = str(task.get("id") or f"task_{index:03d}").strip() or f"task_{index:03d}"
        summary_path = summaries_root / task_id / "summary.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_paths.append(summary_path)
        task_title = str(task.get("title") or task_id).strip() or task_id
        task_lines.append(f"- {task_id}: {task_title} -> {_display_path(summary_path)}")

    generator_prompt = (
        f"{WORKFLOW_REPORT_GENERATOR_PROMPT_PREFIX}\n"
        f"Workflow: {workflow_name}\n"
        f"Source description: {source_description}\n"
        "\n"
        "Use these artifacts:\n"
        f"- Plan JSON: {_display_path(plan_path)}\n"
        f"- Task prompt directory: {_display_path(run_dir / REPRODUCE_PROMPTS_DIRNAME)}\n"
        f"- Task memory archive directory: {_display_path(run_dir / REPRODUCE_ARCHIVE_DIRNAME)}\n"
        f"- Existing run logs directory: {_display_path(run_dir / REPRODUCE_LOGS_DIRNAME)}\n"
        "\n"
        "Create/update one summary for each completed task at:\n"
        + "\n".join(task_lines)
        + "\n\n"
        "Then create/update a polished top-level markdown report at:\n"
        f"- {_display_path(report_path)}\n"
        "\n"
        "Report requirements:\n"
        "1) Brief objective and methodology sections.\n"
        "2) Per-task results linked to corresponding task summaries.\n"
        "3) Include markdown figure/image links to generated outputs whenever files exist.\n"
        "4) Explicitly note missing artifacts or limitations.\n"
        "5) End with concise conclusions and next-step suggestions.\n"
    )

    for attempt in range(1, 3):
        _print_tagged(workflow_name, f"report generation attempt {attempt}/2")
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=generator_prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            codex_bin=codex_bin,
        )
        return_code = int(run_result.get("return_code") or 0)
        if return_code == 0 and report_path.is_file():
            break
        if attempt == 2:
            raise PackageError(
                f"{workflow_name.title()} report generation failed (exit code {return_code})."
            )

    auditor_prompt = (
        f"{WORKFLOW_REPORT_AUDITOR_PROMPT_PREFIX}\n"
        f"Workflow: {workflow_name}\n"
        f"Source description: {source_description}\n"
        "\n"
        "Start from scratch as an independent reviewer.\n"
        "Read and audit these files:\n"
        f"- Plan JSON: {_display_path(plan_path)}\n"
        f"- Per-task summaries root: {_display_path(summaries_root)}\n"
        f"- Top-level report to audit in place: {_display_path(report_path)}\n"
        "\n"
        "Required audit actions:\n"
        "1) Verify report consistency with plan and summaries.\n"
        "2) Improve structure, clarity, and scientific correctness.\n"
        "3) Keep/repair figure links and explain missing figures explicitly.\n"
        "4) Update the same report file in place.\n"
    )
    for attempt in range(1, 3):
        _print_tagged(workflow_name, f"report audit attempt {attempt}/2")
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=auditor_prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            codex_bin=codex_bin,
        )
        return_code = int(run_result.get("return_code") or 0)
        if return_code == 0 and report_path.is_file():
            break
        if attempt == 2:
            raise PackageError(
                f"{workflow_name.title()} report audit failed (exit code {return_code})."
            )

    return {
        "report_path": str(report_path),
        "summaries_root": str(summaries_root),
        "summary_count": len(summary_paths),
    }


def _cmd_plan_workflow(
    args: argparse.Namespace,
    *,
    workflow_name: str,
    runs_dir_name: str,
    generate_plan,
) -> int:
    repo_dir = Path.cwd().resolve()
    _ensure_exec_repo_ready(repo_dir, args)

    user_prompt, prompt_file = _resolve_exec_like_user_prompt(args)
    source_description = prompt_file or "inline prompt"
    plan_only = bool(getattr(args, "plan_only", False))
    report_only = bool(getattr(args, "report_only", False))
    skip_report = bool(getattr(args, "skip_report", False))
    if plan_only and report_only:
        raise PackageError("Cannot combine --plan-only and --report-only.")
    if report_only and skip_report:
        raise PackageError("Cannot combine --report-only and --skip-report.")

    task_max_runs_raw = getattr(args, "task_max_runs", 5)
    try:
        task_max_runs = int(task_max_runs_raw)
    except (TypeError, ValueError) as exc:
        raise PackageError("--task-max-runs must be an integer.") from exc
    if task_max_runs < 1:
        raise PackageError("--task-max-runs must be >= 1.")

    planner_max_tries_raw = getattr(args, "planner_max_tries", 2)
    try:
        planner_max_tries = int(planner_max_tries_raw)
    except (TypeError, ValueError) as exc:
        raise PackageError("--planner-max-tries must be an integer.") from exc
    if planner_max_tries < 1:
        raise PackageError("--planner-max-tries must be >= 1.")

    auditor_max_tries_raw = getattr(args, "auditor_max_tries", 2)
    try:
        auditor_max_tries = int(auditor_max_tries_raw)
    except (TypeError, ValueError) as exc:
        raise PackageError("--auditor-max-tries must be an integer.") from exc
    if auditor_max_tries < 1:
        raise PackageError("--auditor-max-tries must be >= 1.")

    max_iterations_raw = getattr(args, "max_iterations", 10)
    try:
        max_iterations = int(max_iterations_raw)
    except (TypeError, ValueError) as exc:
        raise PackageError("--max-iterations must be an integer.") from exc
    if max_iterations < 1:
        raise PackageError("--max-iterations must be >= 1.")

    wait_seconds_raw = getattr(args, "wait_seconds", 0.0)
    try:
        wait_seconds = float(wait_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise PackageError("--wait-seconds must be a number.") from exc
    if wait_seconds < 0:
        raise PackageError("--wait-seconds must be >= 0.")

    max_wait_seconds_raw = getattr(args, "max_wait_seconds", 600.0)
    try:
        max_wait_seconds = float(max_wait_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise PackageError("--max-wait-seconds must be a number.") from exc
    if max_wait_seconds < 0:
        raise PackageError("--max-wait-seconds must be >= 0.")

    projects_dir = repo_dir / LOOP_MEMORY_DIRNAME
    runs_root = projects_dir / runs_dir_name
    latest_path = runs_root / REPRODUCE_LATEST_RUN_FILENAME
    runs_root.mkdir(parents=True, exist_ok=True)

    source_fingerprint = hashlib.sha256(user_prompt.strip().encode("utf-8")).hexdigest()
    resume_enabled = bool(getattr(args, "resume", True))
    if report_only and not resume_enabled:
        raise PackageError("--report-only requires --resume (do not use --restart).")
    run_dir: Path | None = None
    state: dict[str, object] | None = None

    if resume_enabled and latest_path.is_file():
        try:
            latest_run_id = latest_path.read_text(encoding="utf-8").strip()
            if latest_run_id:
                candidate = runs_root / latest_run_id
                candidate_state_path = candidate / REPRODUCE_STATE_FILENAME
                if candidate_state_path.is_file():
                    loaded = json.loads(candidate_state_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        same_source = (
                            str(loaded.get("source_fingerprint") or "") == source_fingerprint
                        )
                        status = str(loaded.get("status") or "")
                        if same_source and status not in {"completed"}:
                            run_dir = candidate
                            state = loaded
        except (OSError, json.JSONDecodeError):
            run_dir = None
            state = None

    if run_dir is None or state is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_dir = runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "version": 1,
            "run_id": run_id,
            "status": "planning",
            "created_at_utc": _utc_now_z(),
            "updated_at_utc": _utc_now_z(),
            "source_fingerprint": source_fingerprint,
            "source_description": source_description,
            "source_prompt_file": prompt_file,
            "source_prompt_preview": user_prompt[:400],
            "current_task_index": 0,
            "tasks": [],
            "task_runs": {},
            "last_error": "",
        }
        _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
        try:
            latest_path.write_text(run_id + "\n", encoding="utf-8")
        except OSError as exc:
            raise PackageError(f"Failed to write latest {workflow_name} run file: {latest_path}: {exc}") from exc
    else:
        _print_tagged(workflow_name, f"resuming run: {run_dir.name}")

    prompts_dir = run_dir / REPRODUCE_PROMPTS_DIRNAME
    logs_dir = run_dir / REPRODUCE_LOGS_DIRNAME
    archive_dir = run_dir / REPRODUCE_ARCHIVE_DIRNAME
    prompts_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    _print_tagged(workflow_name, f"run dir: {run_dir.relative_to(repo_dir)}")

    state_status = str(state.get("status") or "planning")
    tasks_state_raw = state.get("tasks")
    has_existing_tasks = isinstance(tasks_state_raw, list) and bool(tasks_state_raw)
    if state_status == "planning" or not has_existing_tasks:
        plan = generate_plan(
            repo_dir=repo_dir,
            source_text=user_prompt,
            source_description=source_description,
            requested_package_id=args.package_id,
            sandbox_override=args.sandbox,
            codex_bin=args.codex_bin,
            planner_max_tries=planner_max_tries,
            auditor_max_tries=auditor_max_tries,
        )
        _materialize_mode_plan(
            run_dir=run_dir,
            plan=plan,
            state=state,
            workflow_name=workflow_name,
        )
        state["status"] = "plan_ready" if bool(getattr(args, "plan_only", False)) else "running_tasks"
        state["updated_at_utc"] = _utc_now_z()
        _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
        _print_tagged(workflow_name, f"plan ready with {len(state.get('tasks') or [])} tasks")
    elif state_status == "completed" and not report_only:
        _print_tagged(workflow_name, "run already completed")
        print(LOOP_DONE_TOKEN)
        return 0

    plan_synced = _maybe_sync_mode_plan_from_disk(
        run_dir=run_dir,
        state=state,
        source_description=source_description,
        workflow_name=workflow_name,
    )
    if plan_synced:
        _print_tagged(workflow_name, "synced plan from plan.json")

    if plan_only:
        _print_tagged(workflow_name, f"plan-only mode: {run_dir.relative_to(repo_dir)}")
        return 0

    tasks_state = state.get("tasks")
    if not isinstance(tasks_state, list) or not tasks_state:
        raise PackageError(f"No tasks found in {workflow_name} state.")
    task_runs_state = state.get("task_runs")
    if not isinstance(task_runs_state, dict):
        task_runs_state = {}
        state["task_runs"] = task_runs_state

    if report_only:
        try:
            report_info = _finalize_workflow_report(
                repo_dir=repo_dir,
                run_dir=run_dir,
                runs_root=runs_root,
                workflow_name=workflow_name,
                source_description=source_description,
                tasks_state=tasks_state,
                requested_package_id=args.package_id,
                sandbox_override=args.sandbox,
                codex_bin=args.codex_bin,
            )
        except PackageError as exc:
            state["last_error"] = str(exc)
            state["updated_at_utc"] = _utc_now_z()
            _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
            _print_tagged(workflow_name, str(exc), stderr=True)
            return 1
        state["report"] = report_info
        state["last_error"] = ""
        state["updated_at_utc"] = _utc_now_z()
        _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
        report_path = str(report_info.get("report_path") or "").strip()
        if report_path:
            try:
                relative_report = str(Path(report_path).relative_to(repo_dir))
            except Exception:
                relative_report = report_path
            _print_tagged(workflow_name, f"report: {relative_report}")
        return 0

    state["status"] = "running_tasks"
    state["updated_at_utc"] = _utc_now_z()
    _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)

    while True:
        current_index_raw = state.get("current_task_index", 0)
        try:
            current_index = int(current_index_raw)
        except (TypeError, ValueError):
            current_index = 0
        if current_index < 0:
            current_index = 0

        if current_index >= len(tasks_state):
            if skip_report:
                state["report"] = {
                    "skipped": True,
                    "reason": "skip_report_flag",
                    "updated_at_utc": _utc_now_z(),
                }
                _print_tagged(workflow_name, "skipping report generation (--skip-report)")
            else:
                try:
                    report_info = _finalize_workflow_report(
                        repo_dir=repo_dir,
                        run_dir=run_dir,
                        runs_root=runs_root,
                        workflow_name=workflow_name,
                        source_description=source_description,
                        tasks_state=tasks_state,
                        requested_package_id=args.package_id,
                        sandbox_override=args.sandbox,
                        codex_bin=args.codex_bin,
                    )
                except PackageError as exc:
                    state["status"] = "failed"
                    state["last_error"] = str(exc)
                    state["updated_at_utc"] = _utc_now_z()
                    _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
                    _print_tagged(workflow_name, str(exc), stderr=True)
                    return 1
                state["report"] = report_info
            state["status"] = "completed"
            state["last_error"] = ""
            state["updated_at_utc"] = _utc_now_z()
            _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
            report_payload = state.get("report")
            report_path = (
                str(report_payload.get("report_path") or "").strip()
                if isinstance(report_payload, dict)
                else ""
            )
            if report_path:
                try:
                    relative_report = str(Path(report_path).relative_to(repo_dir))
                except Exception:
                    relative_report = report_path
                _print_tagged(workflow_name, f"report: {relative_report}")
            print(LOOP_DONE_TOKEN)
            return 0

        task = tasks_state[current_index]
        if not isinstance(task, dict):
            raise PackageError(f"Task index {current_index} in {workflow_name} state is invalid.")
        task_id = str(task.get("id") or f"task_{current_index + 1:03d}").strip()
        prompt_rel = str(task.get("prompt_file") or "").strip()
        if not prompt_rel:
            raise PackageError(f"Task {task_id} is missing `prompt_file` in {workflow_name} state.")
        prompt_path = run_dir / prompt_rel
        if not prompt_path.is_file():
            raise PackageError(f"Task prompt file does not exist: {prompt_path}")

        task_runs = int(task_runs_state.get(task_id, 0))
        if task_runs == 0:
            try:
                task_prompt_text = prompt_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise PackageError(f"Failed to read task prompt file: {prompt_path}: {exc}") from exc
            _ensure_loop_memory(
                repo_dir=repo_dir,
                user_prompt=task_prompt_text,
                prompt_file=str(prompt_path),
                overwrite=True,
            )

        run_number = task_runs + 1
        _print_tagged(
            workflow_name,
            (
                f"task {current_index + 1}/{len(tasks_state)} "
                f"{task_id} run {run_number}/{task_max_runs}"
            ),
        )
        loop_args = argparse.Namespace(
            command="loop",
            prompt=[str(prompt_path)],
            package_id=args.package_id,
            sandbox=args.sandbox,
            codex_bin=args.codex_bin,
            max_iterations=max_iterations,
            wait_seconds=wait_seconds,
            max_wait_seconds=max_wait_seconds,
            init_git=args.init_git,
            no_init_git=args.no_init_git,
        )
        started_at = _utc_now_z()
        code = _cmd_loop(loop_args)
        finished_at = _utc_now_z()
        task_runs_state[task_id] = run_number
        state["updated_at_utc"] = finished_at
        try:
            (logs_dir / f"{task_id}_run_{run_number:02d}.json").write_text(
                json.dumps(
                    {
                        "task_id": task_id,
                        "task_index": current_index + 1,
                        "run_number": run_number,
                        "started_at_utc": started_at,
                        "finished_at_utc": finished_at,
                        "loop_exit_code": code,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

        if code == 0:
            _archive_loop_memory(
                repo_dir=repo_dir,
                archive_dir=archive_dir,
                task_id=task_id,
                run_count=run_number,
            )
            state["current_task_index"] = current_index + 1
            state["last_error"] = ""
            _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
            continue

        if code == 1 and run_number < task_max_runs:
            state["last_error"] = (
                f"Task {task_id} did not reach {LOOP_DONE_TOKEN}; retrying "
                f"({run_number}/{task_max_runs})."
            )
            _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
            continue

        if code == 1:
            state["status"] = "failed"
            state["last_error"] = (
                f"Task {task_id} exceeded --task-max-runs ({task_max_runs}) "
                f"without {LOOP_DONE_TOKEN}."
            )
            state["updated_at_utc"] = _utc_now_z()
            _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
            _print_tagged(workflow_name, str(state["last_error"]), stderr=True)
            return 1

        state["status"] = "failed"
        state["last_error"] = f"Task {task_id} failed with loop exit code {code}."
        state["updated_at_utc"] = _utc_now_z()
        _write_json_atomic(run_dir / REPRODUCE_STATE_FILENAME, state)
        _print_tagged(workflow_name, str(state["last_error"]), stderr=True)
        return code


def _cmd_reproduce(args: argparse.Namespace) -> int:
    return _cmd_plan_workflow(
        args,
        workflow_name="reproduce",
        runs_dir_name=REPRODUCE_RUNS_DIR,
        generate_plan=_generate_reproduce_plan,
    )


def _cmd_research(args: argparse.Namespace) -> int:
    return _cmd_plan_workflow(
        args,
        workflow_name="research",
        runs_dir_name=RESEARCH_RUNS_DIR,
        generate_plan=_generate_research_plan,
    )


def _cmd_exec(args: argparse.Namespace) -> int:
    prompt, _ = _resolve_exec_like_user_prompt(args)

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
    _print_tagged("package", f"Using {package_id} (selection: {source})")
    if note and note not in {"manual_pin", "default_fallback", "matched"}:
        _print_tagged("router", note)
    sandbox_text = (
        f"enforce({sandbox_mode})"
        if sandbox_policy == "enforce"
        else "bypass"
    )
    _print_tagged("agent", f"provider: {provider}, sandbox: {sandbox_text}")

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
    _print_tagged(
        "overlay",
        (
            "linked entries: "
            f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
        ),
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
    normalized_id = normalize_package_id(package_id)
    registry = load_registry(scipkg_root)
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
    save_registry(scipkg_root, registry)


def _cmd_install(args: argparse.Namespace) -> int:
    scipkg_root = resolve_scipkg_root()
    raw_package_id = getattr(args, "package_id", None)
    if isinstance(raw_package_id, list):
        requested_ids = [item for item in raw_package_id if isinstance(item, str) and item.strip()]
    elif isinstance(raw_package_id, str) and raw_package_id.strip():
        requested_ids = [raw_package_id.strip()]
    else:
        requested_ids = []
    if not requested_ids:
        raise PackageError("Package id is required for fermilink install.")

    requested_version_raw = getattr(args, "version_id", None)
    requested_version = (
        str(requested_version_raw).strip() if isinstance(requested_version_raw, str) else ""
    )
    if requested_version == "":
        requested_version = None

    require_verified = bool(getattr(args, "require_verified", False))
    if requested_version and (args.local_path or args.zip_url):
        raise PackageError("--version only applies to curated channel installs.")
    if require_verified and (args.local_path or args.zip_url):
        raise PackageError("--require-verified only applies to curated channel installs.")

    package_ids = [normalize_package_id(item) for item in requested_ids]
    normalized_channel = normalize_channel_id(args.channel)
    if len(package_ids) > 1:
        if args.activate:
            raise PackageError(
                "Cannot combine multiple package ids with --activate/--active. "
                "Install them first, then run `fermilink activate <package_id>`."
            )
        if args.local_path:
            raise PackageError("Cannot combine multiple package ids with --local-path.")
        if args.zip_url:
            raise PackageError("Cannot combine multiple package ids with --zip-url.")
        if args.title:
            raise PackageError("Cannot combine multiple package ids with --title.")
        if requested_version:
            raise PackageError("Cannot combine multiple package ids with --version.")

        installed: list[dict[str, object]] = []
        sources: dict[str, str] = {}
        selected_versions: dict[str, str] = {}
        unverified: list[str] = []
        for package_id in package_ids:
            curated = resolve_curated_package(package_id, channel=normalized_channel)
            selected_version = select_package_version(curated)
            if require_verified and not selected_version.verified:
                raise PackageError(
                    f"Selected curated version '{selected_version.version_id}' for package "
                    f"'{package_id}' in channel '{normalized_channel}' is not verified. "
                    "Use a verified version or remove --require-verified."
                )
            if not selected_version.verified:
                unverified.append(f"{package_id}@{selected_version.version_id}")

            meta = install_from_zip(
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
            router = sync_router_rules(scipkg_root)

        active = load_registry(scipkg_root).get("active_package")
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
        summary = ", ".join(str(item.get("id") or "") for item in installed if isinstance(item, dict))
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
                "Warning: installed unverified curated versions: " + ", ".join(unverified) + "."
            )
        _emit_output(args, payload, lines)
        return 0

    package_id = package_ids[0]

    title = args.title
    source: str
    selected_unverified_label: str | None = None
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
        selected_version_id: str | None = None
        selected_version_verified: bool | None = None
        selected_source_ref: dict[str, str | None] | None = None
        if not zip_url:
            curated = resolve_curated_package(package_id, channel=normalized_channel)
            selected_version = select_package_version(curated, version_id=requested_version)
            if require_verified and not selected_version.verified:
                raise PackageError(
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
                selected_unverified_label = f"{package_id}@{selected_version.version_id}"

        meta = install_from_zip(
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
                source_ref_type=selected_source_ref.get("type") if selected_source_ref else None,
                source_ref_value=selected_source_ref.get("value") if selected_source_ref else None,
            )
        source = str(zip_url)

    router = None
    if not args.no_router_sync:
        router = sync_router_rules(scipkg_root)

    payload = {
        "installed": meta,
        "source": source,
        "requested_version": requested_version,
        "require_verified": require_verified,
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
    if selected_unverified_label:
        lines.append(f"Warning: installed unverified curated version: {selected_unverified_label}.")
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


def _cmd_avail(args: argparse.Namespace) -> int:
    query = str(getattr(args, "query", "") or "").strip()
    if not query:
        raise PackageError("Query is required for fermilink avail.")
    normalized_channel = normalize_channel_id(getattr(args, "channel", None))
    curated_packages = list_curated_packages(channel=normalized_channel)

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
    install_parser.add_argument(
        "package_id",
        nargs="+",
        help="One or more package ids to install, e.g. ase meep qutip",
    )
    install_parser.add_argument(
        "--channel",
        default="tel-research-group",
        help="Curated source channel (default: tel-research-group).",
    )
    install_parser.add_argument(
        "--version",
        dest="version_id",
        help=(
            "Curated version id to install (for example: branch-head or a tagged version). "
            "Only valid when source is curated channel."
        ),
    )
    install_parser.add_argument(
        "--require-verified",
        action="store_true",
        help=(
            "Fail if the selected curated version is not marked verified. "
            "Only valid when source is curated channel."
        ),
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
        help=(
            "Either prompt text, or a path to a markdown/text file containing "
            "the prompt (e.g. prompt.md)."
        ),
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

    loop_parser = subparsers.add_parser(
        "loop",
        help=(
            "Run one autonomous loop iteration locally (web-like routing + overlay), "
            "persisting state in projects/memory.md until <promise>DONE</promise>."
        ),
    )
    loop_parser.add_argument(
        "prompt",
        nargs="+",
        help=(
            "Either prompt text, or a path to a markdown file containing the prompt "
            "(e.g. prompt.md)."
        ),
    )
    loop_parser.add_argument(
        "--package",
        dest="package_id",
        help="Pin one installed package id and skip auto routing.",
    )
    loop_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for this iteration. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    loop_parser.add_argument(
        "--codex-bin",
        default=DEFAULT_COMPILE_CODEX_BIN,
        help=(
            f"Codex executable path (default: {DEFAULT_COMPILE_CODEX_BIN}). "
            "Ignored when provider is not codex."
        ),
    )
    loop_parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Maximum loop iterations to run before stopping (default: 10).",
    )
    loop_parser.add_argument(
        "--wait-seconds",
        type=float,
        default=0.0,
        help=(
            "Fallback sleep seconds between iterations when no valid "
            "<wait_seconds> tag is returned (default: 0)."
        ),
    )
    loop_parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=600.0,
        help=(
            "Hard cap on per-iteration sleep seconds after applying agent "
            "wait hints (default: 600)."
        ),
    )
    loop_parser.add_argument(
        "--init-git",
        action="store_true",
        help="Auto-run git init when current directory is not a git repository.",
    )
    loop_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of prompting/initializing when git repository is missing.",
    )
    loop_parser.set_defaults(func=_cmd_loop)

    reproduce_parser = subparsers.add_parser(
        "reproduce",
        help=(
            "Run paper-level orchestration: generate/audit a multi-task plan, then "
            "execute each task via fermilink loop until all tasks are done."
        ),
    )
    reproduce_parser.add_argument(
        "prompt",
        nargs="+",
        help=(
            "Either paper request text, or a path to a file containing source content "
            "(e.g. paper.tex, paper.md, notes.txt, paper.pdf)."
        ),
    )
    reproduce_parser.add_argument(
        "--package",
        dest="package_id",
        help="Pin one installed package id and skip auto routing.",
    )
    reproduce_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for planning/auditing/loop runs. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    reproduce_parser.add_argument(
        "--codex-bin",
        default=DEFAULT_COMPILE_CODEX_BIN,
        help=(
            f"Codex executable path (default: {DEFAULT_COMPILE_CODEX_BIN}). "
            "Ignored when provider is not codex."
        ),
    )
    reproduce_parser.add_argument(
        "--task-max-runs",
        type=int,
        default=5,
        help="Maximum outer loop reruns per task when loop is not done (default: 5).",
    )
    reproduce_parser.add_argument(
        "--planner-max-tries",
        type=int,
        default=2,
        help="Maximum planner retries to obtain valid plan JSON (default: 2).",
    )
    reproduce_parser.add_argument(
        "--auditor-max-tries",
        type=int,
        default=2,
        help="Maximum auditor retries to obtain valid plan JSON (default: 2).",
    )
    reproduce_parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Forwarded to inner loop: max iterations per loop run (default: 10).",
    )
    reproduce_parser.add_argument(
        "--wait-seconds",
        type=float,
        default=0.0,
        help=(
            "Forwarded to inner loop fallback wait when no valid <wait_seconds> tag "
            "is returned (default: 0)."
        ),
    )
    reproduce_parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=600.0,
        help=(
            "Forwarded to inner loop hard cap for per-iteration waits "
            "(default: 600)."
        ),
    )
    reproduce_parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only generate and persist plan/prompts; do not execute tasks.",
    )
    reproduce_parser.add_argument(
        "--report-only",
        action="store_true",
        help=(
            "Only generate/audit top-level report from existing run artifacts "
            "(requires --resume)."
        ),
    )
    reproduce_parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Skip final report generation after all tasks complete.",
    )
    resume_group = reproduce_parser.add_mutually_exclusive_group(required=False)
    resume_group.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
        help="Resume the latest matching in-progress reproduce run (default).",
    )
    resume_group.add_argument(
        "--restart",
        dest="resume",
        action="store_false",
        help="Start a fresh reproduce run and ignore resumable state.",
    )
    reproduce_parser.add_argument(
        "--init-git",
        action="store_true",
        help="Auto-run git init when current directory is not a git repository.",
    )
    reproduce_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of prompting/initializing when git repository is missing.",
    )
    reproduce_parser.set_defaults(func=_cmd_reproduce)

    research_parser = subparsers.add_parser(
        "research",
        help=(
            "Run research orchestration: generate/audit a multi-task research plan, then "
            "execute each task via fermilink loop until all tasks are done."
        ),
    )
    research_parser.add_argument(
        "prompt",
        nargs="+",
        help=(
            "Either research request text, or a path to a file containing source content "
            "(e.g. idea.md, proposal.txt)."
        ),
    )
    research_parser.add_argument(
        "--package",
        dest="package_id",
        help="Pin one installed package id and skip auto routing.",
    )
    research_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for planning/auditing/loop runs. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    research_parser.add_argument(
        "--codex-bin",
        default=DEFAULT_COMPILE_CODEX_BIN,
        help=(
            f"Codex executable path (default: {DEFAULT_COMPILE_CODEX_BIN}). "
            "Ignored when provider is not codex."
        ),
    )
    research_parser.add_argument(
        "--task-max-runs",
        type=int,
        default=5,
        help="Maximum outer loop reruns per task when loop is not done (default: 5).",
    )
    research_parser.add_argument(
        "--planner-max-tries",
        type=int,
        default=2,
        help="Maximum planner retries to obtain valid plan JSON (default: 2).",
    )
    research_parser.add_argument(
        "--auditor-max-tries",
        type=int,
        default=2,
        help="Maximum auditor retries to obtain valid plan JSON (default: 2).",
    )
    research_parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Forwarded to inner loop: max iterations per loop run (default: 10).",
    )
    research_parser.add_argument(
        "--wait-seconds",
        type=float,
        default=0.0,
        help=(
            "Forwarded to inner loop fallback wait when no valid <wait_seconds> tag "
            "is returned (default: 0)."
        ),
    )
    research_parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=600.0,
        help=(
            "Forwarded to inner loop hard cap for per-iteration waits "
            "(default: 600)."
        ),
    )
    research_parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only generate and persist plan/prompts; do not execute tasks.",
    )
    research_parser.add_argument(
        "--report-only",
        action="store_true",
        help=(
            "Only generate/audit top-level report from existing run artifacts "
            "(requires --resume)."
        ),
    )
    research_parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Skip final report generation after all tasks complete.",
    )
    research_resume_group = research_parser.add_mutually_exclusive_group(required=False)
    research_resume_group.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
        help="Resume the latest matching in-progress research run (default).",
    )
    research_resume_group.add_argument(
        "--restart",
        dest="resume",
        action="store_false",
        help="Start a fresh research run and ignore resumable state.",
    )
    research_parser.add_argument(
        "--init-git",
        action="store_true",
        help="Auto-run git init when current directory is not a git repository.",
    )
    research_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of prompting/initializing when git repository is missing.",
    )
    research_parser.set_defaults(func=_cmd_research)

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

    avail_parser = subparsers.add_parser(
        "avail",
        help="Search curated channel packages available for installation.",
    )
    _add_json_option(avail_parser)
    avail_parser.add_argument("query", help="Package id or keyword to search in curated channel.")
    avail_parser.add_argument(
        "--channel",
        default="tel-research-group",
        help="Curated source channel to query (default: tel-research-group).",
    )
    avail_parser.set_defaults(func=_cmd_avail)

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
