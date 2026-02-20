from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import shutil
import subprocess
import time
from pathlib import Path


def _cli():
    from fermilink import cli

    return cli


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    # Reap local child zombies when this process is their parent.
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


PID_STALL_PROGRESS_EPSILON_SECONDS = 0.25
POLL_STATUS_HEARTBEAT_SECONDS = 600.0
SLURM_QUERY_TIMEOUT_SECONDS = 8.0
SLURM_UNKNOWN_CONSECUTIVE_LIMIT = 3


@dataclass(frozen=True)
class _PidSnapshot:
    pid: int
    start_token: str
    cpu_seconds: float | None


@dataclass(frozen=True)
class _PidMonitor:
    start_token: str
    last_cpu_seconds: float | None
    last_progress_monotonic: float
    progress_observable: bool


@dataclass(frozen=True)
class _SlurmMonitor:
    last_state: str
    last_state_change_monotonic: float
    unknown_polls: int


def _read_ps_field(pid: int, field: str) -> str:
    ps_bin = shutil.which("ps")
    if ps_bin is None:
        return ""
    try:
        result = subprocess.run(
            [ps_bin, "-p", str(pid), "-o", f"{field}="],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):
        return ""
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        token = line.strip()
        if token:
            return token
    return ""


def _parse_ps_duration_seconds(raw: str) -> float | None:
    token = str(raw or "").strip()
    if not token:
        return None
    days = 0.0
    if "-" in token:
        day_text, _, rest = token.partition("-")
        try:
            days = float(day_text.strip())
        except ValueError:
            return None
        token = rest.strip()
    parts = token.split(":")
    try:
        if len(parts) == 3:
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
        elif len(parts) == 2:
            hours = 0.0
            minutes = float(parts[0])
            seconds = float(parts[1])
        elif len(parts) == 1:
            hours = 0.0
            minutes = 0.0
            seconds = float(parts[0])
        else:
            return None
    except ValueError:
        return None
    return days * 86400.0 + hours * 3600.0 + minutes * 60.0 + seconds


def _query_pid_snapshot(pid: int) -> _PidSnapshot | None:
    if not _pid_is_alive(pid):
        return None
    start_token = _read_ps_field(pid, "lstart")
    if not start_token:
        start_token = _read_ps_field(pid, "etime")
    cpu_seconds = _parse_ps_duration_seconds(_read_ps_field(pid, "time"))
    return _PidSnapshot(pid=pid, start_token=start_token, cpu_seconds=cpu_seconds)


def _initialize_pid_monitors(
    pid_numbers: list[int], *, now_monotonic: float
) -> tuple[list[int], dict[int, _PidMonitor], list[int]]:
    alive: list[int] = []
    monitors: dict[int, _PidMonitor] = {}
    dead: list[int] = []
    for pid in pid_numbers:
        snapshot = _query_pid_snapshot(pid)
        if snapshot is None:
            dead.append(pid)
            continue
        progress_observable = snapshot.cpu_seconds is not None
        monitors[pid] = _PidMonitor(
            start_token=snapshot.start_token,
            last_cpu_seconds=snapshot.cpu_seconds,
            last_progress_monotonic=now_monotonic,
            progress_observable=progress_observable,
        )
        alive.append(pid)
    return alive, monitors, dead


def _refresh_pid_monitors(
    pid_numbers: list[int],
    monitors: dict[int, _PidMonitor],
    *,
    now_monotonic: float,
    stall_seconds: float,
) -> tuple[list[int], dict[int, _PidMonitor], list[tuple[str, int]]]:
    alive: list[int] = []
    next_monitors: dict[int, _PidMonitor] = {}
    issues: list[tuple[str, int]] = []
    for pid in pid_numbers:
        monitor = monitors.get(pid)
        snapshot = _query_pid_snapshot(pid)
        if snapshot is None:
            issues.append(("dead", pid))
            continue
        if monitor is None:
            issues.append(("reused", pid))
            continue
        if (
            monitor.start_token
            and snapshot.start_token
            and monitor.start_token != snapshot.start_token
        ):
            issues.append(("reused", pid))
            continue
        progress_observable = bool(monitor.progress_observable)
        last_cpu_seconds = monitor.last_cpu_seconds
        last_progress_monotonic = monitor.last_progress_monotonic
        if snapshot.cpu_seconds is None:
            progress_observable = False
        elif (
            last_cpu_seconds is None
            or snapshot.cpu_seconds
            > (last_cpu_seconds + PID_STALL_PROGRESS_EPSILON_SECONDS)
        ):
            last_progress_monotonic = now_monotonic
            last_cpu_seconds = snapshot.cpu_seconds
        if (
            stall_seconds > 0
            and progress_observable
            and (now_monotonic - last_progress_monotonic) >= stall_seconds
        ):
            issues.append(("stalled", pid))
            continue
        next_monitors[pid] = _PidMonitor(
            start_token=monitor.start_token or snapshot.start_token,
            last_cpu_seconds=last_cpu_seconds,
            last_progress_monotonic=last_progress_monotonic,
            progress_observable=progress_observable,
        )
        alive.append(pid)
    return alive, next_monitors, issues


def _format_pid_issues(issues: list[tuple[str, int]]) -> str:
    groups: dict[str, list[int]] = {"dead": [], "reused": [], "stalled": []}
    for status, pid in issues:
        if status in groups:
            groups[status].append(pid)
    parts: list[str] = []
    for status in ("dead", "reused", "stalled"):
        pids = groups[status]
        if not pids:
            continue
        label = status
        parts.append(f"{label}: {', '.join(str(pid) for pid in pids)}")
    return "; ".join(parts)


def _format_waiting_targets(*, alive: list[int], pending_slurm_jobs: list[str]) -> str:
    waiting_on: list[str] = []
    if alive:
        waiting_on.append("pid(s): " + ", ".join(str(pid) for pid in alive))
    if pending_slurm_jobs:
        waiting_on.append("slurm job(s): " + ", ".join(pending_slurm_jobs))
    return "; ".join(waiting_on)


def _utc_now_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SLURM_FAILURE_STATES = {
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "BOOT_FAIL",
    "DEADLINE",
    "REVOKED",
    "SPECIAL_EXIT",
    "STOPPED",
}


def _slurm_wait_tools_available() -> bool:
    return shutil.which("sacct") is not None or shutil.which("squeue") is not None


def _run_slurm_query(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=SLURM_QUERY_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def _query_slurm_job_state(job_id: str) -> str:
    state = ""
    sacct_bin = shutil.which("sacct")
    if sacct_bin:
        result = _run_slurm_query([sacct_bin, "-n", "-o", "State", "-j", str(job_id)])
        if result is not None:
            for line in result.stdout.splitlines():
                token = line.strip()
                if token:
                    state = token
                    break
    if not state:
        squeue_bin = shutil.which("squeue")
        if squeue_bin:
            result = _run_slurm_query([squeue_bin, "-h", "-j", str(job_id)])
            if result is not None and result.stdout.strip():
                state = "PENDING"
    if not state:
        return "UNKNOWN"
    normalized = state.split("+", 1)[0].strip()
    if normalized:
        normalized = normalized.split()[0]
    normalized = normalized.upper()
    if not normalized:
        return "UNKNOWN"
    return normalized


def _poll_pending_slurm_jobs(
    slurm_job_numbers: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    pending: list[str] = []
    failed: list[tuple[str, str]] = []
    for job_id in slurm_job_numbers:
        state = _query_slurm_job_state(job_id)
        if state == "COMPLETED":
            continue
        if state in SLURM_FAILURE_STATES:
            failed.append((job_id, state))
            continue
        pending.append(job_id)
    return pending, failed


def _refresh_slurm_monitors(
    slurm_job_numbers: list[str],
    monitors: dict[str, _SlurmMonitor],
    *,
    now_monotonic: float,
    unknown_poll_limit: int,
) -> tuple[list[str], list[tuple[str, str]], list[tuple[str, str]], dict[str, _SlurmMonitor]]:
    pending: list[str] = []
    failed: list[tuple[str, str]] = []
    issues: list[tuple[str, str]] = []
    next_monitors: dict[str, _SlurmMonitor] = {}

    for job_id in slurm_job_numbers:
        previous = monitors.get(job_id)
        state = _query_slurm_job_state(job_id)

        if previous is None:
            last_change = now_monotonic
            unknown_polls = 1 if state == "UNKNOWN" else 0
        else:
            last_change = (
                now_monotonic
                if state != previous.last_state
                else previous.last_state_change_monotonic
            )
            unknown_polls = (
                previous.unknown_polls + 1 if state == "UNKNOWN" else 0
            )

        monitor = _SlurmMonitor(
            last_state=state,
            last_state_change_monotonic=last_change,
            unknown_polls=unknown_polls,
        )

        if state == "COMPLETED":
            continue
        if state in SLURM_FAILURE_STATES:
            failed.append((job_id, state))
            continue
        if state == "UNKNOWN" and unknown_polls >= unknown_poll_limit:
            issues.append(("unqueryable", job_id))
            continue

        pending.append(job_id)
        next_monitors[job_id] = monitor

    return pending, failed, issues, next_monitors


def _format_slurm_issues(issues: list[tuple[str, str]]) -> str:
    groups: dict[str, list[str]] = {"unqueryable": []}
    for status, job_id in issues:
        if status in groups:
            groups[status].append(job_id)
    parts: list[str] = []
    for status in ("unqueryable",):
        job_ids = groups[status]
        if not job_ids:
            continue
        parts.append(f"{status}: {', '.join(job_ids)}")
    return "; ".join(parts)


def cmd_chat(args: argparse.Namespace) -> int:
    """Run interactive chat mode.

    Dependency note:
    - This command shares the same routing and execution stack as `exec`.
    - Routing/overlay behavior is resolved through the same helpers used by `exec`.
    """

    cli = _cli()
    repo_dir = Path.cwd().resolve()
    cli._ensure_exec_repo_ready(repo_dir, args)

    scipkg_root = cli.resolve_scipkg_root()
    runtime_policy = cli.resolve_agent_runtime_policy()
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
    cli._print_tagged("agent", f"provider: {provider}, sandbox: {sandbox_text}")
    cli._print_tagged("chat", "Interactive mode. Type `exit` or `quit` to leave.")

    web_app = cli._load_web_router_module()
    history: list[tuple[str, str]] = []
    current_package_id: str | None = None
    current_source = cli.PACKAGE_SOURCE_NONE

    while True:
        try:
            user_text = input(cli._chat_input_prompt())
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 0
        cli._chat_prompt_spacing_after_input()

        prompt_text = user_text.strip()
        if not prompt_text:
            continue
        lowered = prompt_text.lower()
        if lowered in {"exit", "quit", "/exit", "/quit"}:
            return 0

        cli._ensure_loop_memory(
            repo_dir=repo_dir,
            user_prompt=prompt_text,
            prompt_file=None,
            overwrite=False,
        )

        selection = cli._resolve_exec_package_selection(
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
            raise cli.PackageError("No package selected for chat turn.")
        source = str(selection.get("source") or cli.PACKAGE_SOURCE_DEFAULT)
        note = str(selection.get("note") or "").strip()
        cli._print_tagged("package", f"Using {package_id} (selection: {source})")
        if note and note not in {"manual_pin", "default_fallback", "matched"}:
            cli._print_tagged("router", note)

        overlay = cli._overlay_exec_package(
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
        cli._print_tagged(
            "overlay",
            (
                "linked entries: "
                f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
            ),
        )

        prompt_body = web_app._build_prompt(history, prompt_text)
        prompt = f"{cli.UNIFIED_MEMORY_PROMPT_PREFIX}{prompt_body.strip()}\n"
        try:
            run_result = cli._run_exec_chat_turn(
                repo_dir=repo_dir,
                prompt=prompt,
                sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
                codex_bin=provider_bin,
                provider=provider,
                sandbox_policy=sandbox_policy,
            )
        finally:
            cli._cleanup_exec_overlay_symlinks(
                repo_dir=repo_dir, workspace_root=repo_dir
            )

        assistant_text = str(run_result.get("assistant_text") or "").strip()
        return_code = int(run_result.get("return_code") or 0)
        stderr_text = str(run_result.get("stderr") or "").strip()
        if assistant_text:
            assistant_prefix = cli._style_text("Assistant>", "1", "38;5;111")
            print(f"{assistant_prefix} {assistant_text}")
        if return_code != 0:
            if stderr_text:
                print(stderr_text, file=cli.sys.stderr)
            cli._print_tagged(
                "chat",
                f"provider exited with code {return_code}.",
                stderr=True,
            )

        history = web_app._append_history(history, "user", prompt_text)
        if assistant_text:
            history = web_app._append_history(history, "assistant", assistant_text)

        current_package_id = package_id
        current_source = source


def cmd_loop(args: argparse.Namespace) -> int:
    """Run autonomous loop mode.

    Dependency note:
    - `loop` reuses the same routing + execution pathway as `exec`.
    - `research`/`reproduce` orchestration depends on this command for task execution.
    """

    cli = _cli()

    def _record_loop_outcome(
        *,
        status: str,
        reason: str,
        provider_exit_code: int | None = None,
    ) -> None:
        setattr(
            args,
            "_fermilink_loop_outcome",
            {
                "status": status,
                "reason": reason,
                "provider_exit_code": provider_exit_code,
            },
        )

    repo_dir = Path.cwd().resolve()
    cli._ensure_exec_repo_ready(repo_dir, args)

    cli._cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)

    user_prompt, prompt_file = cli._resolve_exec_like_user_prompt(args)
    workflow_prompt_preamble = getattr(args, "workflow_prompt_preamble", None)
    if isinstance(workflow_prompt_preamble, str) and workflow_prompt_preamble.strip():
        user_prompt = f"{workflow_prompt_preamble.strip()}\n\n{user_prompt}"
    memory_path = cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt=user_prompt,
        prompt_file=prompt_file,
    )
    cli._print_tagged("loop", f"memory: {memory_path.relative_to(repo_dir)}")

    max_iterations_raw = getattr(args, "max_iterations", 10)
    try:
        max_iterations = int(max_iterations_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--max-iterations must be an integer.") from exc
    if max_iterations < 1:
        raise cli.PackageError("--max-iterations must be >= 1.")

    wait_seconds_raw = getattr(args, "wait_seconds", 0.0)
    try:
        wait_seconds = float(wait_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--wait-seconds must be a number.") from exc
    if wait_seconds < 0:
        raise cli.PackageError("--wait-seconds must be >= 0.")

    max_wait_seconds_raw = getattr(args, "max_wait_seconds", 600.0)
    try:
        max_wait_seconds = float(max_wait_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--max-wait-seconds must be a number.") from exc
    if max_wait_seconds < 0:
        raise cli.PackageError("--max-wait-seconds must be >= 0.")

    pid_stall_seconds_raw = getattr(args, "pid_stall_seconds", 900.0)
    try:
        pid_stall_seconds = float(pid_stall_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--pid-stall-seconds must be a number.") from exc
    if pid_stall_seconds < 0:
        raise cli.PackageError("--pid-stall-seconds must be >= 0.")
    effective_pid_stall_seconds = pid_stall_seconds
    if max_wait_seconds > 0 and effective_pid_stall_seconds > max_wait_seconds:
        effective_pid_stall_seconds = max_wait_seconds

    scipkg_root = cli.resolve_scipkg_root()
    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(args.sandbox, str) and args.sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = args.sandbox.strip()

    provider_bin = args.codex_bin if provider == "codex" else None
    selection = cli._resolve_exec_package_selection(
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
        raise cli.PackageError("No package selected for loop execution.")

    source = str(selection.get("source") or "default")
    note = str(selection.get("note") or "").strip()
    cli._print_tagged("package", f"Using {package_id} (selection: {source})")
    if note and note not in {"manual_pin", "default_fallback", "matched"}:
        cli._print_tagged("router", note)
    sandbox_text = (
        f"enforce({sandbox_mode})" if sandbox_policy == "enforce" else "bypass"
    )
    cli._print_tagged("agent", f"provider: {provider}, sandbox: {sandbox_text}")

    overlay = cli._overlay_exec_package(
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
    cli._print_tagged(
        "overlay",
        (
            "linked entries: "
            f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
        ),
    )

    prompt = f"{cli.LOOP_PROMPT_PREFIX}{user_prompt.strip()}\n"
    try:
        for iteration in range(1, max_iterations + 1):
            cli._print_tagged("loop", f"iteration {iteration}/{max_iterations}")
            run_result = cli._run_exec_chat_turn(
                repo_dir=repo_dir,
                prompt=prompt,
                sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
                codex_bin=provider_bin,
                provider=provider,
                sandbox_policy=sandbox_policy,
            )

            assistant_text = str(run_result.get("assistant_text") or "")
            done = any(
                line.strip() == cli.LOOP_DONE_TOKEN
                for line in assistant_text.splitlines()
            )
            if done:
                _record_loop_outcome(status="done", reason="done_token")
                print(cli.LOOP_DONE_TOKEN)
                return 0

            return_code = int(run_result.get("return_code") or 0)
            if return_code != 0:
                _record_loop_outcome(
                    status="provider_failure",
                    reason=f"provider_exit_code_{return_code}",
                    provider_exit_code=return_code,
                )
                return return_code

            if iteration < max_iterations:
                pid_numbers = cli._extract_loop_pid_numbers(assistant_text)
                slurm_job_numbers = cli._extract_loop_slurm_job_numbers(assistant_text)
                if pid_numbers or slurm_job_numbers:
                    poll_interval = wait_seconds if wait_seconds > 0 else 1.0
                    poll_started = time.monotonic()
                    (
                        alive,
                        pid_monitors,
                        initially_dead_pids,
                    ) = _initialize_pid_monitors(
                        pid_numbers,
                        now_monotonic=poll_started,
                    )
                    pending_slurm_jobs = list(slurm_job_numbers)
                    slurm_monitors: dict[str, _SlurmMonitor] = {}
                    if pending_slurm_jobs and not _slurm_wait_tools_available():
                        slurm_text = ", ".join(pending_slurm_jobs)
                        cli._print_tagged(
                            "loop",
                            (
                                "cannot poll slurm job(s) without `sacct` or `squeue`; "
                                f"continuing without slurm wait (jobs: {slurm_text})"
                            ),
                            stderr=True,
                        )
                        pending_slurm_jobs = []
                    if initially_dead_pids:
                        dead_text = ", ".join(str(pid) for pid in initially_dead_pids)
                        cli._print_tagged(
                            "loop",
                            (
                                "detected non-running pid(s) before wait; "
                                "continuing next iteration for debug/resubmit "
                                f"(pid(s): {dead_text})"
                            ),
                            stderr=True,
                        )
                        continue
                    if pending_slurm_jobs:
                        (
                            pending_slurm_jobs,
                            failed_slurm_jobs,
                            slurm_issues,
                            slurm_monitors,
                        ) = _refresh_slurm_monitors(
                            pending_slurm_jobs,
                            slurm_monitors,
                            now_monotonic=poll_started,
                            unknown_poll_limit=SLURM_UNKNOWN_CONSECUTIVE_LIMIT,
                        )
                        if failed_slurm_jobs:
                            failed_text = ", ".join(
                                f"{job_id}:{state}"
                                for job_id, state in failed_slurm_jobs
                            )
                            cli._print_tagged(
                                "loop",
                                (
                                    "slurm job(s) reached non-success terminal state; "
                                    f"continuing (jobs: {failed_text})"
                                ),
                                stderr=True,
                            )
                        if slurm_issues:
                            issue_text = _format_slurm_issues(slurm_issues)
                            cli._print_tagged(
                                "loop",
                                (
                                    "detected slurm polling issue; "
                                    "continuing next iteration for debug/resubmit "
                                    f"({issue_text})"
                                ),
                                stderr=True,
                            )
                            continue
                    if alive or pending_slurm_jobs:
                        wait_targets = _format_waiting_targets(
                            alive=alive, pending_slurm_jobs=pending_slurm_jobs
                        )
                        stall_text = (
                            f"{effective_pid_stall_seconds:.1f}s"
                            if effective_pid_stall_seconds > 0
                            else "disabled"
                        )
                        cli._print_tagged(
                            "loop",
                            (
                                "polling jobs until completion "
                                f"({wait_targets}, poll: {poll_interval:.1f}s, "
                                f"max wait: {max_wait_seconds:.1f}s, pid stall: {stall_text})"
                            ),
                        )
                        started = poll_started
                        next_status_log = started + POLL_STATUS_HEARTBEAT_SECONDS
                        pid_issue_caused_early_continue = False
                        slurm_issue_caused_early_continue = False
                        while alive or pending_slurm_jobs:
                            now_monotonic = time.monotonic()
                            elapsed = now_monotonic - started
                            remaining = max_wait_seconds - elapsed
                            if now_monotonic >= next_status_log:
                                remaining_text = max(0.0, remaining)
                                cli._print_tagged(
                                    "loop",
                                    (
                                        "polling status @ "
                                        f"{_utc_now_timestamp()} "
                                        f"(elapsed: {elapsed:.1f}s, remaining: {remaining_text:.1f}s, "
                                        "waiting on: "
                                        + _format_waiting_targets(
                                            alive=alive,
                                            pending_slurm_jobs=pending_slurm_jobs,
                                        )
                                        + ")"
                                    ),
                                )
                                next_status_log = (
                                    now_monotonic + POLL_STATUS_HEARTBEAT_SECONDS
                                )
                            if remaining <= 0:
                                cli._print_tagged(
                                    "loop",
                                    (
                                        "job polling reached max wait "
                                        f"({max_wait_seconds:.1f}s); continuing "
                                        "with still-running targets: "
                                        + _format_waiting_targets(
                                            alive=alive,
                                            pending_slurm_jobs=pending_slurm_jobs,
                                        )
                                    ),
                                    stderr=True,
                                )
                                break
                            sleep_seconds = min(poll_interval, remaining)
                            if sleep_seconds > 0:
                                time.sleep(sleep_seconds)
                            now_monotonic = time.monotonic()
                            alive, pid_monitors, pid_issues = _refresh_pid_monitors(
                                pid_numbers,
                                pid_monitors,
                                now_monotonic=now_monotonic,
                                stall_seconds=effective_pid_stall_seconds,
                            )
                            if pid_issues:
                                issue_text = _format_pid_issues(pid_issues)
                                still_waiting_on: list[str] = []
                                if alive:
                                    still_waiting_on.append(
                                        "still-running pid(s): "
                                        + ", ".join(str(pid) for pid in alive)
                                    )
                                if pending_slurm_jobs:
                                    still_waiting_on.append(
                                        "pending slurm job(s): "
                                        + ", ".join(pending_slurm_jobs)
                                    )
                                suffix = (
                                    f"; {'; '.join(still_waiting_on)}"
                                    if still_waiting_on
                                    else ""
                                )
                                cli._print_tagged(
                                    "loop",
                                    (
                                        "detected pid issue during polling; "
                                        "continuing next iteration for debug/resubmit "
                                        f"({issue_text}{suffix})"
                                    ),
                                    stderr=True,
                                )
                                pid_issue_caused_early_continue = True
                                break
                            if pending_slurm_jobs:
                                (
                                    pending_slurm_jobs,
                                    failed_slurm_jobs,
                                    slurm_issues,
                                    slurm_monitors,
                                ) = _refresh_slurm_monitors(
                                    pending_slurm_jobs,
                                    slurm_monitors,
                                    now_monotonic=now_monotonic,
                                    unknown_poll_limit=SLURM_UNKNOWN_CONSECUTIVE_LIMIT,
                                )
                                if failed_slurm_jobs:
                                    failed_text = ", ".join(
                                        f"{job_id}:{state}"
                                        for job_id, state in failed_slurm_jobs
                                    )
                                    cli._print_tagged(
                                        "loop",
                                        (
                                            "slurm job(s) reached non-success terminal state; "
                                            f"continuing (jobs: {failed_text})"
                                        ),
                                        stderr=True,
                                    )
                                if slurm_issues:
                                    issue_text = _format_slurm_issues(slurm_issues)
                                    waiting_on: list[str] = []
                                    if alive:
                                        waiting_on.append(
                                            "still-running pid(s): "
                                            + ", ".join(str(pid) for pid in alive)
                                        )
                                    suffix = (
                                        f"; {'; '.join(waiting_on)}"
                                        if waiting_on
                                        else ""
                                    )
                                    cli._print_tagged(
                                        "loop",
                                        (
                                            "detected slurm polling issue; "
                                            "continuing next iteration for debug/resubmit "
                                            f"({issue_text}{suffix})"
                                        ),
                                        stderr=True,
                                    )
                                    slurm_issue_caused_early_continue = True
                                    break
                        if pid_issue_caused_early_continue:
                            continue
                        if slurm_issue_caused_early_continue:
                            continue
                        if not alive and not pending_slurm_jobs:
                            waited = time.monotonic() - started
                            cli._print_tagged(
                                "loop",
                                f"job polling complete after {waited:.1f}s.",
                            )
                    continue
                suggested_wait = cli._extract_loop_wait_seconds(assistant_text)
                wait_source = "agent" if suggested_wait is not None else "default"
                requested_wait = (
                    suggested_wait if suggested_wait is not None else wait_seconds
                )
                effective_wait = min(requested_wait, max_wait_seconds)
                if effective_wait > 0:
                    if requested_wait > max_wait_seconds:
                        cli._print_tagged(
                            "loop",
                            (
                                "sleeping "
                                f"{effective_wait:.1f}s before next iteration "
                                f"(source: {wait_source}, capped by --max-wait-seconds)"
                            ),
                        )
                    else:
                        cli._print_tagged(
                            "loop",
                            (
                                "sleeping "
                                f"{effective_wait:.1f}s before next iteration "
                                f"(source: {wait_source})"
                            ),
                        )
                    time.sleep(effective_wait)
    finally:
        cli._cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)

    cli._print_tagged(
        "loop",
        f"max iterations reached ({max_iterations}) without {cli.LOOP_DONE_TOKEN}.",
        stderr=True,
    )
    _record_loop_outcome(
        status="incomplete_max_iterations",
        reason="max_iterations_reached",
    )
    return 1


def cmd_exec(args: argparse.Namespace) -> int:
    """Run single-turn execution mode.

    Dependency note:
    - This is the base execution primitive reused by `chat` and `loop`.
    """

    cli = _cli()
    user_prompt, prompt_file = cli._resolve_exec_like_user_prompt(args)

    repo_dir = Path.cwd().resolve()
    cli._ensure_exec_repo_ready(repo_dir, args)
    cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt=user_prompt,
        prompt_file=prompt_file,
        overwrite=False,
    )
    prompt = f"{cli.UNIFIED_MEMORY_PROMPT_PREFIX}{user_prompt.strip()}\n"

    scipkg_root = cli.resolve_scipkg_root()
    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(args.sandbox, str) and args.sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = args.sandbox.strip()

    provider_bin = args.codex_bin if provider == "codex" else None
    selection = cli._resolve_exec_package_selection(
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
        raise cli.PackageError("No package selected for execution.")

    source = str(selection.get("source") or "default")
    note = str(selection.get("note") or "").strip()
    cli._print_tagged("package", f"Using {package_id} (selection: {source})")
    if note and note not in {"manual_pin", "default_fallback", "matched"}:
        cli._print_tagged("router", note)
    sandbox_text = (
        f"enforce({sandbox_mode})" if sandbox_policy == "enforce" else "bypass"
    )
    cli._print_tagged("agent", f"provider: {provider}, sandbox: {sandbox_text}")

    overlay = cli._overlay_exec_package(
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
    cli._print_tagged(
        "overlay",
        (
            "linked entries: "
            f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
        ),
    )

    try:
        return cli._run_exec_codex_prompt(
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
            codex_bin=provider_bin,
            provider=provider,
            sandbox_policy=sandbox_policy,
        )
    finally:
        cli._cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)
