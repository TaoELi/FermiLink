import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from fermilink.agent_runtime import resolve_agent_runtime_policy
from fermilink.cli.commands import workflows as workflow_commands
from fermilink.cli.workflow_prompts import UNIFIED_MEMORY_PROMPT_PREFIX
from fermilink.config import resolve_workspaces_root as resolve_default_workspaces_root
from fermilink.providers import (
    build_exec_command,
    provider_bin_env_key,
    resolve_provider_binary,
)
from fermilink.runner.admission import QueueFullError, RunAdmissionController
from fermilink.runner.scientific_packages import (
    PackageNotFoundError,
    PackageValidationError,
    bootstrap_legacy_maxwelllink_package,
    overlay_package_into_repo,
    resolve_scipkg_root,
    resolve_session_package,
)


def find_project_root(start: Path) -> Path:
    """
    Find the repository root by walking upward from a start path.

    Parameters
    ----------
    start : Path
        Starting path for upward project-root discovery.

    Returns
    -------
    Path
        Detected repository/project root path.
    """
    cur = start.resolve()
    for p in [cur.parent, *cur.parents]:
        if (p / "pyproject.toml").exists() or (p / ".git").exists():
            return p
    # Installed wheel/sdist layouts may not include project markers.
    return Path.cwd()


PROJECT_ROOT = find_project_root(Path(__file__))
PACKAGE_SOFTWARE_ROOT = Path(__file__).resolve().parents[1] / "software"
PROVIDER_AGENT_MD_ALIASES = {
    "claude": "CLAUDE.md",
    "gemini": "GEMINI.md",
}


def _get_int_env(name: str, default: int, minimum: int | None = None) -> int:
    """Read integer environment variables with fallback bounds.

    Parameters
    ----------
    name : str
        Environment variable name.
    default : int
        Fallback value when the variable is unset or invalid.
    minimum : int or None
        Optional minimum bound applied after parsing.

    Returns
    -------
    int
        Parsed value clamped to `minimum` when provided.
    """

    raw = os.getenv(name)
    if raw is None or raw == "":
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    if minimum is not None and value < minimum:
        return minimum
    return value


CODEX_BIN = os.getenv("FERMILINK_CODEX_BIN", "codex")
MAX_RUNTIME_SECONDS = int(os.getenv("FERMILINK_RUNNER_MAX_RUNTIME_SECONDS", "600"))
MAX_PROMPT_CHARS = 10_000
RUNNER_GLOBAL_CONCURRENT_RUNS = _get_int_env(
    "FERMILINK_RUNNER_GLOBAL_CONCURRENT_RUNS", 200, minimum=1
)
RUNNER_PER_USER_CONCURRENT_RUNS = _get_int_env(
    "FERMILINK_RUNNER_PER_USER_CONCURRENT_RUNS", 10, minimum=1
)
RUNNER_MAX_QUEUE_SIZE = _get_int_env("FERMILINK_RUNNER_MAX_QUEUE_SIZE", 2000, minimum=0)
RUNNER_MAX_PENDING_PER_USER = _get_int_env(
    "FERMILINK_RUNNER_MAX_PENDING_PER_USER", 10, minimum=0
)
RUNNER_METRICS_TOKEN = os.getenv("FERMILINK_RUNNER_METRICS_TOKEN", "").strip()
PLACEHOLDER_KEYS = {
    "YOUR_KEY_HERE",
    "YOUR_REAL_OPENAI_API_KEY",
    "YOUR_KEY*HERE",
    "CHANGEME",
}
ALLOWED_REQUEST_SANDBOXES = {"read-only", "workspace-write"}


RUN_ADMISSION_CONTROLLER = RunAdmissionController(
    global_limit=RUNNER_GLOBAL_CONCURRENT_RUNS,
    per_user_limit=RUNNER_PER_USER_CONCURRENT_RUNS,
    max_queue_size=RUNNER_MAX_QUEUE_SIZE,
    max_pending_per_user=RUNNER_MAX_PENDING_PER_USER,
)


class RunRequest(BaseModel):
    """Payload accepted by the runner execution endpoint."""

    session_id: str | None = None
    user_id: str | None = None
    user_prompt: str = Field(..., min_length=1)
    package_id: str | None = None
    sandbox: str | None = None
    provider: str | None = None


def verify_provider_bin() -> None:
    """Validate that the configured provider CLI binary is available.

    Raises
    ------
    RuntimeError
        Raised when provider CLI binary cannot be resolved on `PATH`.
    """

    policy = resolve_agent_runtime_policy()
    provider_bin = resolve_provider_binary(policy.provider, codex_bin=CODEX_BIN)
    if shutil.which(provider_bin) is None:
        env_key = provider_bin_env_key(policy.provider)
        raise RuntimeError(
            f"{policy.provider} CLI not found. Install it in the runner image or set "
            f"{env_key} to its path."
        )


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    verify_provider_bin()
    yield


app = FastAPI(lifespan=_lifespan)


@app.get("/ops/concurrency")
async def ops_concurrency(request: Request) -> dict[str, object]:
    """Return current in-process concurrency/backpressure metrics as JSON."""

    _ensure_metrics_access(request)
    return await _build_concurrency_metrics_payload()


@app.get("/ops/concurrency.prom", response_class=PlainTextResponse)
async def ops_concurrency_prometheus(request: Request) -> str:
    """Return current in-process concurrency metrics in Prometheus format."""

    _ensure_metrics_access(request)
    payload = await _build_concurrency_metrics_payload()
    return _format_prometheus_metrics(payload)


@app.get("/ops/admission")
async def ops_admission(
    request: Request, user_id: str | None = None, session_id: str | None = None
) -> dict[str, object]:
    """Return per-user admission readiness snapshot.

    Parameters
    ----------
    request : Request
        FastAPI request object.
    user_id : str or None, optional
        Authenticated user identifier when available.
    session_id : str or None, optional
        Session identifier fallback when `user_id` is not provided.

    Returns
    -------
    dict[str, object]
        Admission counters and whether a run can start immediately.
    """

    _ensure_metrics_access(request)
    fallback_session = (session_id or "").strip() or "anonymous"
    user_key = _resolve_run_user_key(user_id, fallback_session)
    snapshot = await RUN_ADMISSION_CONTROLLER.snapshot_for_user(user_key)
    payload: dict[str, object] = dict(snapshot)
    payload["timestamp_utc"] = _now_utc_iso()
    return payload


def sse(event: str, data: str | dict) -> str:
    """Format a server-sent event frame.

    Parameters
    ----------
    event : str
        SSE event name.
    data : str or dict
        Event payload text or an object that will be JSON serialized.

    Returns
    -------
    str
        Wire-format SSE frame terminated by a blank line.
    """

    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


def _resolve_path(env_key: str, default: str) -> Path:
    """Resolve a path from an environment variable with fallback.

    Parameters
    ----------
    env_key : str
        Environment variable name to read.
    default : str
        Fallback path when the variable is unset.

    Returns
    -------
    Path
        Absolute/expanded path when env is set, otherwise the fallback path.
    """

    raw = os.getenv(env_key)
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path
    return Path(default)


def _now_utc_iso() -> str:
    """Return current UTC timestamp in ISO-8601 with trailing `Z`."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_metrics_access(request: Request) -> None:
    """Enforce optional token guard for operational metrics endpoints.

    Metrics are public only when `RUNNER_METRICS_TOKEN` is unset. When set,
    callers must provide matching header `X-Runner-Metrics-Token`.
    """

    if not RUNNER_METRICS_TOKEN:
        return
    presented = request.headers.get("X-Runner-Metrics-Token", "").strip()
    if not presented:
        raise HTTPException(status_code=401, detail="Missing X-Runner-Metrics-Token.")
    if presented != RUNNER_METRICS_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid X-Runner-Metrics-Token.")


async def _build_concurrency_metrics_payload() -> dict[str, object]:
    """Build current in-process runner concurrency metrics payload."""

    snapshot = await RUN_ADMISSION_CONTROLLER.snapshot()
    active_total = int(snapshot["active_total"])
    global_limit = int(snapshot["global_limit"])
    pending_total = int(snapshot["pending_total"])
    per_user_limit = int(snapshot["per_user_limit"])
    queue_bounded = RUNNER_MAX_QUEUE_SIZE > 0
    queue_capacity = RUNNER_MAX_QUEUE_SIZE if queue_bounded else None

    payload: dict[str, object] = {
        "active_total": active_total,
        "global_limit": global_limit,
        "global_utilization": active_total / global_limit if global_limit > 0 else 0.0,
        "pending_total": pending_total,
        "queue_bounded": queue_bounded,
        "queue_capacity": queue_capacity,
        "queue_utilization": (
            pending_total / RUNNER_MAX_QUEUE_SIZE if queue_bounded else None
        ),
        "per_user_limit": per_user_limit,
        "timestamp_utc": _now_utc_iso(),
    }
    return payload


def _format_prometheus_metrics(payload: dict[str, object]) -> str:
    """Render concurrency metrics payload in Prometheus exposition format."""

    active_total = int(payload["active_total"])
    global_limit = int(payload["global_limit"])
    global_utilization = float(payload["global_utilization"])
    pending_total = int(payload["pending_total"])
    per_user_limit = int(payload["per_user_limit"])
    queue_bounded = bool(payload["queue_bounded"])
    queue_capacity = payload["queue_capacity"]
    queue_utilization = payload["queue_utilization"]

    lines = [
        "# HELP runner_active_runs Active admitted runs in this runner process.",
        "# TYPE runner_active_runs gauge",
        f"runner_active_runs {active_total}",
        "# HELP runner_global_run_limit Configured global active-run limit.",
        "# TYPE runner_global_run_limit gauge",
        f"runner_global_run_limit {global_limit}",
        "# HELP runner_global_run_utilization Active/global limit ratio.",
        "# TYPE runner_global_run_utilization gauge",
        f"runner_global_run_utilization {global_utilization}",
        "# HELP runner_pending_runs Number of runs waiting in the admission queue.",
        "# TYPE runner_pending_runs gauge",
        f"runner_pending_runs {pending_total}",
        "# HELP runner_per_user_run_limit Configured per-user active-run limit.",
        "# TYPE runner_per_user_run_limit gauge",
        f"runner_per_user_run_limit {per_user_limit}",
        "# HELP runner_queue_bounded Whether queue capacity is bounded (1) or unbounded (0).",
        "# TYPE runner_queue_bounded gauge",
        f"runner_queue_bounded {1 if queue_bounded else 0}",
    ]
    if queue_bounded and isinstance(queue_capacity, int) and queue_capacity > 0:
        lines.extend(
            [
                "# HELP runner_queue_capacity Configured max number of pending queued runs.",
                "# TYPE runner_queue_capacity gauge",
                f"runner_queue_capacity {queue_capacity}",
                "# HELP runner_queue_utilization Pending/queue capacity ratio.",
                "# TYPE runner_queue_utilization gauge",
                f"runner_queue_utilization {float(queue_utilization)}",
            ]
        )
    return "\n".join(lines) + "\n"


def _ensure_dir(path: Path) -> Path:
    """Create a directory tree if needed and return it.

    Parameters
    ----------
    path : Path
        Directory to ensure.

    Returns
    -------
    Path
        The same input path.
    """

    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_dir_safe(path: Path) -> Path | None:
    """Attempt to create a directory tree without raising on OS errors.

    Parameters
    ----------
    path : Path
        Directory to create.

    Returns
    -------
    Path or None
        The path when creation succeeds, otherwise `None`.
    """

    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return None


def _resolve_workspaces_root() -> Path:
    """Resolve a writable workspace root for session repositories.

    Returns
    -------
    Path
        Configured workspace root, or a local fallback under the current
        working directory.
    """

    try:
        return _ensure_dir(resolve_default_workspaces_root())
    except OSError:
        # Fall back to a local workspace root when the configured global root
        # cannot be created (e.g., permission-restricted filesystem).
        fallback = Path.cwd() / "workspaces"
        return _ensure_dir(fallback)


def _resolve_source_dir() -> Path:
    """Resolve the software template source directory.

    Returns
    -------
    Path
        The configured software root when present, otherwise the repository
        `software/` directory.
    """

    source = _resolve_path("FERMILINK_SOFTWARE_ROOT", "/opt/software")
    if source.exists():
        return source
    if PACKAGE_SOFTWARE_ROOT.exists():
        return PACKAGE_SOFTWARE_ROOT
    return PROJECT_ROOT / "software"


def _resolve_template_agents_path(source_dir: Path) -> Path | None:
    """Find the AGENTS template file used for workspace repos.

    Parameters
    ----------
    source_dir : Path
        Resolved software source directory.

    Returns
    -------
    Path or None
        First matching `AGENTS.md` candidate, else `None`.
    """

    candidates = [
        PACKAGE_SOFTWARE_ROOT / "AGENTS.md",
        PROJECT_ROOT / "software" / "AGENTS.md",
        source_dir / "AGENTS.md",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _ensure_agent_md_alias(repo_dir: Path, alias_name: str) -> None:
    """Ensure a provider alias file in the workspace repo points to ``AGENTS.md``.

    Creates a symlink ``<alias_name> -> AGENTS.md`` so that provider-native
    instruction discovery follows the same policy contract as Codex. Falls
    back to a file copy when symlink creation is unavailable (e.g. some
    Windows environments). A pre-existing real file is left untouched.

    Parameters
    ----------
    repo_dir : Path
        Workspace repository root that already contains ``AGENTS.md``.
    alias_name : str
        Alias filename to point at ``AGENTS.md``.
    """

    repo_agents = repo_dir / "AGENTS.md"
    if not repo_agents.is_file():
        return

    repo_alias = repo_dir / alias_name

    if repo_alias.is_symlink():
        try:
            if repo_alias.resolve() == repo_agents.resolve():
                return
        except OSError:
            pass
        try:
            repo_alias.unlink()
        except OSError:
            return
    elif repo_alias.exists():
        # Leave a real provider alias file written by the user untouched.
        return

    try:
        os.symlink("AGENTS.md", repo_alias)
    except OSError:
        try:
            shutil.copy2(repo_agents, repo_alias)
        except OSError:
            pass


def _ensure_claude_md_symlink(repo_dir: Path) -> None:
    """Ensure ``CLAUDE.md`` in the workspace repo points to ``AGENTS.md``."""

    _ensure_agent_md_alias(repo_dir, "CLAUDE.md")


def _ensure_gemini_md_symlink(repo_dir: Path) -> None:
    """Ensure ``GEMINI.md`` in the workspace repo points to ``AGENTS.md``."""

    _ensure_agent_md_alias(repo_dir, "GEMINI.md")


def _remove_agent_md_alias_symlink(repo_dir: Path, alias_name: str) -> None:
    """Remove a managed provider alias symlink when it is not the active provider."""

    repo_alias = repo_dir / alias_name
    if not repo_alias.is_symlink():
        return
    try:
        repo_alias.unlink()
    except OSError:
        pass


def _sync_provider_agent_md_alias(repo_dir: Path) -> None:
    """Provision only the active provider alias for ``AGENTS.md`` in the repo.

    The active provider is read from the current runtime policy. Inactive
    provider alias symlinks are removed so the workspace reflects the current
    agent selection, while real files are left untouched.
    """

    policy = resolve_agent_runtime_policy()
    active_alias = PROVIDER_AGENT_MD_ALIASES.get(policy.provider)

    for alias_name in PROVIDER_AGENT_MD_ALIASES.values():
        if alias_name == active_alias:
            continue
        _remove_agent_md_alias_symlink(repo_dir, alias_name)

    if active_alias == "CLAUDE.md":
        _ensure_claude_md_symlink(repo_dir)
    elif active_alias == "GEMINI.md":
        _ensure_gemini_md_symlink(repo_dir)


def _ensure_template_agents_file(source_dir: Path, repo_dir: Path) -> None:
    """Synchronize `AGENTS.md` into the workspace repository root.

    Also ensures only the active provider alias (currently ``CLAUDE.md`` for
    Claude or ``GEMINI.md`` for Gemini) points to ``AGENTS.md`` so
    provider-native instruction discovery follows the same policy contract.

    Parameters
    ----------
    source_dir : Path
        Software source directory used to find the template.
    repo_dir : Path
        Workspace repository path receiving the file.

    Returns
    -------
    None
        This function updates files in place when needed.
    """

    template_agents = _resolve_template_agents_path(source_dir)
    if template_agents is None:
        return

    repo_agents = repo_dir / "AGENTS.md"
    if repo_agents.is_dir():
        shutil.rmtree(repo_agents, ignore_errors=True)
        if repo_agents.exists():
            return
    else:
        try:
            repo_agents.unlink(missing_ok=True)
        except OSError:
            return

    shutil.copy2(template_agents, repo_agents)
    _sync_provider_agent_md_alias(repo_dir)


def _ensure_repo_memory_file(repo_dir: Path, user_prompt: str) -> None:
    """Ensure shared memory file exists and matches current schema."""

    normalized_prompt = str(user_prompt or "")
    if normalized_prompt.startswith(UNIFIED_MEMORY_PROMPT_PREFIX):
        normalized_prompt = normalized_prompt[
            len(UNIFIED_MEMORY_PROMPT_PREFIX) :
        ].lstrip()
    if not normalized_prompt.strip():
        normalized_prompt = "(request unavailable)"

    try:
        workflow_commands._ensure_loop_memory(
            repo_dir=repo_dir,
            user_prompt=normalized_prompt,
            prompt_file=None,
            overwrite=False,
        )
    except Exception as exc:  # defensive: convert any memory init failure to HTTP 500
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize workspace memory file: {exc}",
        ) from exc


def _is_valid_git_repo(path: Path) -> bool:
    """Check whether a directory contains a usable Git repository.

    Parameters
    ----------
    path : Path
        Repository candidate directory.

    Returns
    -------
    bool
        `True` when `.git` is either a valid directory repo or gitdir pointer.
    """

    git_entry = path / ".git"
    if git_entry.is_dir():
        return (git_entry / "HEAD").exists()
    if git_entry.is_file():
        try:
            content = git_entry.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return False
        return content.startswith("gitdir:")
    return False


def _ensure_git_repo(path: Path) -> None:
    """Ensure a workspace directory is initialized as a Git repository.

    Parameters
    ----------
    path : Path
        Repository directory to validate or initialize.

    Raises
    ------
    RuntimeError
        Raised if Git is unavailable or repository initialization fails.
    """

    if _is_valid_git_repo(path):
        return

    git_bin = shutil.which("git")
    if not git_bin:
        raise RuntimeError(
            f"Git is required but not found on PATH, and {path} is not a valid git repo."
        )

    proc = subprocess.run(
        [git_bin, "init", "-q"],
        cwd=str(path),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "unknown error"
        raise RuntimeError(f"Failed to initialize git repo at {path}: {detail}")

    if not _is_valid_git_repo(path):
        raise RuntimeError(f"Failed to initialize a valid git repo at {path}")


def _sanitize_env(env: dict) -> dict:
    """Remove disallowed or placeholder API keys from process environment.

    Parameters
    ----------
    env : dict
        Environment mapping copied for the Codex subprocess.

    Returns
    -------
    dict
        Sanitized environment mapping.
    """

    _promote_prefixed_codex_env(env)
    auth_mode = (env.get("FERMILINK_CODEX_AUTH_MODE") or "").strip().lower()
    if auth_mode in {"login", "oauth", "keychain", "stored"}:
        env.pop("FERMILINK_CODEX_API_KEY", None)
        env.pop("FERMILINK_OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)
        return env

    key = env.get("FERMILINK_CODEX_API_KEY") or env.get("FERMILINK_OPENAI_API_KEY")
    if not key:
        key = env.get("CODEX_API_KEY") or env.get("OPENAI_API_KEY")
    if key and key.strip() in PLACEHOLDER_KEYS:
        env.pop("FERMILINK_CODEX_API_KEY", None)
        env.pop("FERMILINK_OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)
    return env


def _promote_prefixed_codex_env(env: dict) -> None:
    """Populate Codex-native env names from FermiLink-prefixed inputs."""

    if env.get("FERMILINK_CODEX_AUTH_MODE") and not env.get("CODEX_AUTH_MODE"):
        env["CODEX_AUTH_MODE"] = str(env["FERMILINK_CODEX_AUTH_MODE"])
    if env.get("FERMILINK_CODEX_API_KEY") and not env.get("CODEX_API_KEY"):
        env["CODEX_API_KEY"] = str(env["FERMILINK_CODEX_API_KEY"])
    if env.get("FERMILINK_OPENAI_API_KEY") and not env.get("OPENAI_API_KEY"):
        env["OPENAI_API_KEY"] = str(env["FERMILINK_OPENAI_API_KEY"])
    if env.get("FERMILINK_CODEX_HOME") and not env.get("CODEX_HOME"):
        env["CODEX_HOME"] = str(env["FERMILINK_CODEX_HOME"])


def _normalize_codex_home(env: dict) -> dict:
    """Normalize `CODEX_HOME` to a writable directory.

    Parameters
    ----------
    env : dict
        Environment mapping that may include `CODEX_HOME`.

    Returns
    -------
    dict
        Environment mapping with a rewritten `CODEX_HOME` fallback when needed.
    """

    raw = env.get("FERMILINK_CODEX_HOME") or env.get("CODEX_HOME")
    if not raw:
        return env

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path

    if _ensure_dir_safe(path):
        env["CODEX_HOME"] = str(path)
        env["FERMILINK_CODEX_HOME"] = str(path)
        return env

    home_fallback = Path.home() / ".codex"
    if _ensure_dir_safe(home_fallback):
        env["CODEX_HOME"] = str(home_fallback)
        env["FERMILINK_CODEX_HOME"] = str(home_fallback)
        return env

    local_fallback = Path.cwd() / ".codex"
    _ensure_dir_safe(local_fallback)
    env["CODEX_HOME"] = str(local_fallback)
    env["FERMILINK_CODEX_HOME"] = str(local_fallback)
    return env


def _resolve_run_user_key(user_id: str | None, session_id: str) -> str:
    """Resolve runner admission identity for per-user concurrency tracking.

    Parameters
    ----------
    user_id : str or None
        User identifier from the web service.
    session_id : str
        Session id associated with this run.

    Returns
    -------
    str
        Stable key for per-user run-limiting. Falls back to per-session key
        for unauthenticated requests.
    """

    cleaned = (user_id or "").strip().lower()
    if cleaned:
        return f"user:{cleaned}"
    return f"session:{session_id}"


def _resolve_run_policy(
    req: RunRequest,
) -> tuple[str, str, str | None, str | None, str | None]:
    """Resolve effective provider and sandbox policy for one run request."""

    policy = resolve_agent_runtime_policy()
    provider = policy.provider

    if isinstance(req.provider, str) and req.provider.strip():
        requested_provider = req.provider.strip().lower()
        if requested_provider != provider:
            # Provider is admin-controlled via policy/config.
            _ = requested_provider

    sandbox_policy = policy.sandbox_policy
    sandbox_mode: str | None = (
        policy.sandbox_mode if sandbox_policy == "enforce" else None
    )

    requested_sandbox = (req.sandbox or "").strip()
    if sandbox_policy == "enforce" and requested_sandbox:
        lowered = requested_sandbox.lower()
        if lowered in ALLOWED_REQUEST_SANDBOXES:
            if lowered == "read-only" or lowered == policy.sandbox_mode:
                sandbox_mode = lowered

    model = policy.model
    reasoning_effort = policy.reasoning_effort

    return provider, sandbox_policy, sandbox_mode, model, reasoning_effort


async def _read_stream(
    stream: asyncio.StreamReader, event_type: str, queue: asyncio.Queue
) -> None:
    """Read subprocess output lines and forward them into an event queue.

    Parameters
    ----------
    stream : asyncio.StreamReader
        Stream to consume (`stdout` or `stderr`).
    event_type : str
        Event type label pushed to the queue.
    queue : asyncio.Queue
        Queue receiving `(event_type, payload)` tuples.

    Returns
    -------
    None
        A sentinel `_reader_done` marker is always pushed before returning.
    """

    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace").rstrip("\n")
            if not text:
                continue
            if event_type == "codex":
                payload = text
            else:
                payload = json.dumps({"text": text})
            await queue.put((event_type, payload))
    finally:
        await queue.put(("_reader_done", event_type))


@app.post("/run")
async def run(req: RunRequest):
    """Execute a Codex run inside a session workspace and stream SSE events.

    Parameters
    ----------
    req : RunRequest
        Request payload with session id, prompt text, package selection, and
        sandbox mode.

    Returns
    -------
    StreamingResponse
        Event-stream response containing runner metadata, Codex output, logs,
        and a final exit event.

    Raises
    ------
    HTTPException
        Raised for invalid input, package resolution errors, or overlay errors.
    """

    if len(req.user_prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(status_code=413, detail="Prompt too long.")

    session_id = req.session_id or uuid.uuid4().hex
    user_key = _resolve_run_user_key(req.user_id, session_id)
    try:
        await RUN_ADMISSION_CONTROLLER.acquire(user_key)
    except QueueFullError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    slot_released = False

    async def _release_slot_once() -> None:
        """Release one admission slot only once for this request."""

        nonlocal slot_released
        if slot_released:
            return
        slot_released = True
        await RUN_ADMISSION_CONTROLLER.release(user_key)

    try:
        workspaces_root = _resolve_workspaces_root()
        workspace_root = workspaces_root / session_id
        repo_dir = workspace_root / "repo"
        source_dir = _resolve_source_dir()
        scipkg_root = resolve_scipkg_root()

        workspace_root.mkdir(parents=True, exist_ok=True)
        bootstrap_legacy_maxwelllink_package(scipkg_root)

        created_repo = False
        if not repo_dir.exists():
            created_repo = True
            shutil.copytree(source_dir, repo_dir, dirs_exist_ok=True)

        _ensure_template_agents_file(source_dir, repo_dir)
        _ensure_git_repo(repo_dir)

        try:
            package_id, package_meta = resolve_session_package(
                scipkg_root=scipkg_root,
                workspace_root=workspace_root,
                requested_package_id=req.package_id,
            )
        except PackageNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PackageValidationError as exc:
            status = 400 if req.package_id else 500
            raise HTTPException(status_code=status, detail=str(exc)) from exc

        package_overlay: dict | None = None
        if package_id and package_meta:
            try:
                package_overlay = overlay_package_into_repo(
                    repo_dir=repo_dir,
                    workspace_root=workspace_root,
                    package_id=package_id,
                    package_meta=package_meta,
                    scipkg_root=scipkg_root,
                    allow_replace_existing=created_repo,
                )
            except PackageValidationError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        # Enforce template AGENTS.md even if a stale overlay path attempts to replace it.
        _ensure_template_agents_file(source_dir, repo_dir)
        _ensure_repo_memory_file(repo_dir, req.user_prompt)

        (repo_dir / "outputs").mkdir(parents=True, exist_ok=True)

        provider, sandbox_policy, sandbox_mode, model, reasoning_effort = (
            _resolve_run_policy(req)
        )
        provider_bin = resolve_provider_binary(provider, codex_bin=CODEX_BIN)
        try:
            cmd = build_exec_command(
                provider=provider,
                provider_bin=provider_bin,
                repo_dir=repo_dir,
                prompt=req.user_prompt,
                sandbox_policy=sandbox_policy,
                sandbox_mode=sandbox_mode,
                model=model,
                reasoning_effort=reasoning_effort,
                json_output=True,
            )
        except NotImplementedError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc

        env = os.environ.copy()
        env = _sanitize_env(env)
        env = _normalize_codex_home(env)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(repo_dir),
        )

        queue: asyncio.Queue = asyncio.Queue()
        stdout_task = asyncio.create_task(_read_stream(process.stdout, "codex", queue))
        stderr_task = asyncio.create_task(_read_stream(process.stderr, "log", queue))
        wait_task = asyncio.create_task(process.wait())
    except asyncio.CancelledError:
        await _release_slot_once()
        raise
    except Exception:
        await _release_slot_once()
        raise

    async def event_generator():
        """Yield normalized SSE messages for the current subprocess lifecycle.

        Yields
        ------
        str
            SSE frames containing metadata, command/log output, and terminal
            exit status.
        """

        start = time.monotonic()
        readers_done = 0
        timed_out = False

        try:
            meta_payload: dict[str, object] = {"session_id": session_id}
            if package_overlay:
                meta_payload["package"] = package_overlay
            meta_payload["agent"] = {
                "provider": provider,
                "sandbox_policy": sandbox_policy,
                "sandbox_mode": sandbox_mode,
                "model": model,
                "reasoning_effort": reasoning_effort,
            }
            yield sse("meta", meta_payload)

            while True:
                if time.monotonic() - start > MAX_RUNTIME_SECONDS:
                    timed_out = True
                    if process.returncode is None:
                        process.terminate()
                    break

                try:
                    event_type, payload = await asyncio.wait_for(
                        queue.get(), timeout=0.1
                    )
                except asyncio.TimeoutError:
                    event_type = None
                    payload = None

                if event_type == "_reader_done":
                    readers_done += 1
                elif event_type:
                    yield sse(event_type, payload)

                if wait_task.done() and readers_done >= 2 and queue.empty():
                    break

            if timed_out and not wait_task.done():
                try:
                    await asyncio.wait_for(wait_task, timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    try:
                        await asyncio.wait_for(wait_task, timeout=5)
                    except asyncio.TimeoutError:
                        pass

            reason = "timeout" if timed_out else "completed"
            yield sse(
                "runner.exit", {"reason": reason, "return_code": process.returncode}
            )
        finally:
            try:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(wait_task, timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()
                        try:
                            await asyncio.wait_for(wait_task, timeout=5)
                        except asyncio.TimeoutError:
                            pass
            finally:
                for task in (stdout_task, stderr_task, wait_task):
                    if not task.done():
                        task.cancel()

                # Ensure admission slot release still completes under cancellation.
                release_task = asyncio.create_task(_release_slot_once())
                try:
                    await release_task
                except asyncio.CancelledError:
                    await release_task
                    raise

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_generator(), media_type="text/event-stream", headers=headers
    )
