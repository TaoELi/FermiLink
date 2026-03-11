from __future__ import annotations

import argparse
from collections.abc import Callable
import fnmatch
import json
import math
import os
import shlex
import statistics
import subprocess
from pathlib import Path
from typing import Any

import yaml

from fermilink.cli import optimize_git, optimize_prompts, optimize_state


def _cli():
    from fermilink import cli

    return cli


def _load_benchmark(path: Path) -> dict[str, Any]:
    cli = _cli()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise cli.PackageError(f"Failed to read benchmark file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise cli.PackageError(f"Invalid YAML in benchmark file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise cli.PackageError(f"Benchmark file must contain a YAML object: {path}")

    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        raise cli.PackageError(f"Benchmark file missing runtime block: {path}")
    command = runtime.get("command")
    if not isinstance(command, list) or not all(
        isinstance(item, str) and item.strip() for item in command
    ):
        raise cli.PackageError(
            "Benchmark runtime.command must be a non-empty string list."
        )
    repo = payload.get("repo")
    if not isinstance(repo, dict):
        raise cli.PackageError("Benchmark file missing repo block.")
    editable = repo.get("editable_paths")
    if not isinstance(editable, list) or not editable:
        raise cli.PackageError(
            "Benchmark repo.editable_paths must be a non-empty list."
        )
    controller = payload.get("controller")
    if not isinstance(controller, dict):
        raise cli.PackageError("Benchmark file missing controller block.")
    objective = controller.get("objective")
    if not isinstance(objective, dict):
        raise cli.PackageError("Benchmark controller.objective must be an object.")
    primary_metric = str(objective.get("primary_metric") or "").strip()
    if not primary_metric:
        raise cli.PackageError("Benchmark objective.primary_metric is required.")
    return payload


def _str_list(payload: object) -> list[str]:
    if not isinstance(payload, list):
        return []
    return [
        str(item).strip()
        for item in payload
        if isinstance(item, str) and str(item).strip()
    ]


def _benchmark_editable_paths(benchmark: dict[str, Any]) -> list[str]:
    repo = benchmark.get("repo")
    if not isinstance(repo, dict):
        return []
    return _str_list(repo.get("editable_paths"))


def _benchmark_immutable_paths(benchmark: dict[str, Any]) -> list[str]:
    repo = benchmark.get("repo")
    if not isinstance(repo, dict):
        return []
    return _str_list(repo.get("immutable_paths"))


def _matches_any(path_text: str, patterns: list[str]) -> bool:
    normalized = str(path_text or "").replace("\\", "/").strip()
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns)


def _objective_config(benchmark: dict[str, Any]) -> dict[str, Any]:
    controller = benchmark.get("controller")
    if not isinstance(controller, dict):
        return {}
    objective = controller.get("objective")
    return objective if isinstance(objective, dict) else {}


def _campaign_config(benchmark: dict[str, Any]) -> dict[str, Any]:
    campaign = benchmark.get("campaign")
    return campaign if isinstance(campaign, dict) else {}


def _correctness_config(benchmark: dict[str, Any]) -> dict[str, Any]:
    correctness = benchmark.get("correctness")
    return correctness if isinstance(correctness, dict) else {}


def _controller_config(benchmark: dict[str, Any]) -> dict[str, Any]:
    controller = benchmark.get("controller")
    return controller if isinstance(controller, dict) else {}


def _normalize_worker_loop_timing_options(
    *,
    wait_seconds_raw: object,
    max_wait_seconds_raw: object,
    pid_stall_seconds_raw: object,
) -> tuple[float, float, float]:
    cli = _cli()
    try:
        wait_seconds = float(wait_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--worker-wait-seconds must be a number.") from exc
    if wait_seconds < 0:
        raise cli.PackageError("--worker-wait-seconds must be >= 0.")

    try:
        max_wait_seconds = float(max_wait_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--worker-max-wait-seconds must be a number.") from exc
    if max_wait_seconds < 0:
        raise cli.PackageError("--worker-max-wait-seconds must be >= 0.")

    try:
        pid_stall_seconds = float(pid_stall_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--worker-pid-stall-seconds must be a number.") from exc
    if pid_stall_seconds < 0:
        raise cli.PackageError("--worker-pid-stall-seconds must be >= 0.")

    effective_pid_stall_seconds = pid_stall_seconds
    if max_wait_seconds > 0 and effective_pid_stall_seconds > max_wait_seconds:
        effective_pid_stall_seconds = max_wait_seconds
    return wait_seconds, max_wait_seconds, effective_pid_stall_seconds


def _run_optimize_worker_loop(
    *,
    prompt: str,
    max_iterations: int,
    wait_seconds: float,
    max_wait_seconds: float,
    pid_stall_seconds: float,
    run_turn: Callable[[int, int, str], dict[str, object]],
) -> dict[str, object]:
    cli = _cli()
    from fermilink.cli.commands import sessions as session_commands

    last_run_result: dict[str, object] = {}
    last_assistant_text = ""
    last_provider_return_code = 0

    for iteration in range(1, max_iterations + 1):
        cli._print_tagged("optimize", f"iteration {iteration}/{max_iterations}")
        run_result = run_turn(iteration, max_iterations, prompt)
        last_run_result = run_result

        assistant_text = str(run_result.get("assistant_text") or "")
        last_assistant_text = assistant_text
        if any(line.strip() == cli.LOOP_DONE_TOKEN for line in assistant_text.splitlines()):
            return {
                "status": "done",
                "reason": "done_token",
                "exit_code": 0,
                "iteration": iteration,
                "assistant_text": assistant_text,
                "provider_return_code": int(run_result.get("return_code") or 0),
                "run_result": run_result,
            }

        return_code = int(run_result.get("return_code") or 0)
        last_provider_return_code = return_code
        if return_code != 0:
            return {
                "status": "provider_failure",
                "reason": f"provider_exit_code_{return_code}",
                "exit_code": return_code,
                "iteration": iteration,
                "assistant_text": assistant_text,
                "provider_return_code": return_code,
                "run_result": run_result,
            }

        if iteration >= max_iterations:
            continue

        pid_numbers = cli._extract_loop_pid_numbers(assistant_text)
        slurm_job_numbers = cli._extract_loop_slurm_job_numbers(assistant_text)
        if pid_numbers or slurm_job_numbers:
            poll_interval = wait_seconds if wait_seconds > 0 else 1.0
            poll_started = session_commands.time.monotonic()
            alive, pid_monitors, initially_dead_pids = (
                session_commands._initialize_pid_monitors(
                    pid_numbers,
                    now_monotonic=poll_started,
                )
            )
            pending_slurm_jobs = list(slurm_job_numbers)
            slurm_monitors: dict[str, object] = {}
            if pending_slurm_jobs and not session_commands._slurm_wait_tools_available():
                slurm_text = ", ".join(pending_slurm_jobs)
                cli._print_tagged(
                    "optimize",
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
                    "optimize",
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
                ) = session_commands._refresh_slurm_monitors(
                    pending_slurm_jobs,
                    slurm_monitors,
                    now_monotonic=poll_started,
                    unknown_poll_limit=session_commands.SLURM_UNKNOWN_CONSECUTIVE_LIMIT,
                )
                if failed_slurm_jobs:
                    failed_text = ", ".join(
                        f"{job_id}:{state}" for job_id, state in failed_slurm_jobs
                    )
                    cli._print_tagged(
                        "optimize",
                        (
                            "slurm job(s) reached non-success terminal state; "
                            f"continuing (jobs: {failed_text})"
                        ),
                        stderr=True,
                    )
                if slurm_issues:
                    issue_text = session_commands._format_slurm_issues(slurm_issues)
                    cli._print_tagged(
                        "optimize",
                        (
                            "detected slurm polling issue; "
                            "continuing next iteration for debug/resubmit "
                            f"({issue_text})"
                        ),
                        stderr=True,
                    )
                    continue
            if alive or pending_slurm_jobs:
                wait_targets = session_commands._format_waiting_targets(
                    alive=alive,
                    pending_slurm_jobs=pending_slurm_jobs,
                )
                stall_text = (
                    f"{pid_stall_seconds:.1f}s"
                    if pid_stall_seconds > 0
                    else "disabled"
                )
                cli._print_tagged(
                    "optimize",
                    (
                        "polling jobs until completion "
                        f"({wait_targets}, poll: {poll_interval:.1f}s, "
                        f"max wait: {max_wait_seconds:.1f}s, pid stall: {stall_text})"
                    ),
                )
                started = poll_started
                next_status_log = started + session_commands.POLL_STATUS_HEARTBEAT_SECONDS
                pid_issue_caused_early_continue = False
                slurm_issue_caused_early_continue = False
                while alive or pending_slurm_jobs:
                    now_monotonic = session_commands.time.monotonic()
                    elapsed = now_monotonic - started
                    remaining = max_wait_seconds - elapsed
                    if now_monotonic >= next_status_log:
                        remaining_text = max(0.0, remaining)
                        cli._print_tagged(
                            "optimize",
                            (
                                "polling status @ "
                                f"{session_commands._utc_now_timestamp()} "
                                f"(elapsed: {elapsed:.1f}s, remaining: {remaining_text:.1f}s, "
                                "waiting on: "
                                + session_commands._format_waiting_targets(
                                    alive=alive,
                                    pending_slurm_jobs=pending_slurm_jobs,
                                )
                                + ")"
                            ),
                        )
                        next_status_log = (
                            now_monotonic
                            + session_commands.POLL_STATUS_HEARTBEAT_SECONDS
                        )
                    if remaining <= 0:
                        cli._print_tagged(
                            "optimize",
                            (
                                "job polling reached max wait "
                                f"({max_wait_seconds:.1f}s); continuing "
                                "with still-running targets: "
                                + session_commands._format_waiting_targets(
                                    alive=alive,
                                    pending_slurm_jobs=pending_slurm_jobs,
                                )
                            ),
                            stderr=True,
                        )
                        break
                    sleep_seconds = min(poll_interval, remaining)
                    if sleep_seconds > 0:
                        session_commands.time.sleep(sleep_seconds)
                    now_monotonic = session_commands.time.monotonic()
                    alive, pid_monitors, pid_issues = (
                        session_commands._refresh_pid_monitors(
                            pid_numbers,
                            pid_monitors,
                            now_monotonic=now_monotonic,
                            stall_seconds=pid_stall_seconds,
                        )
                    )
                    if pid_issues:
                        issue_text = session_commands._format_pid_issues(pid_issues)
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
                            "optimize",
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
                        ) = session_commands._refresh_slurm_monitors(
                            pending_slurm_jobs,
                            slurm_monitors,
                            now_monotonic=now_monotonic,
                            unknown_poll_limit=session_commands.SLURM_UNKNOWN_CONSECUTIVE_LIMIT,
                        )
                        if failed_slurm_jobs:
                            failed_text = ", ".join(
                                f"{job_id}:{state}"
                                for job_id, state in failed_slurm_jobs
                            )
                            cli._print_tagged(
                                "optimize",
                                (
                                    "slurm job(s) reached non-success terminal state; "
                                    f"continuing (jobs: {failed_text})"
                                ),
                                stderr=True,
                            )
                        if slurm_issues:
                            issue_text = session_commands._format_slurm_issues(slurm_issues)
                            waiting_on: list[str] = []
                            if alive:
                                waiting_on.append(
                                    "still-running pid(s): "
                                    + ", ".join(str(pid) for pid in alive)
                                )
                            suffix = (
                                f"; {'; '.join(waiting_on)}" if waiting_on else ""
                            )
                            cli._print_tagged(
                                "optimize",
                                (
                                    "detected slurm polling issue; "
                                    "continuing next iteration for debug/resubmit "
                                    f"({issue_text}{suffix})"
                                ),
                                stderr=True,
                            )
                            slurm_issue_caused_early_continue = True
                            break
                if pid_issue_caused_early_continue or slurm_issue_caused_early_continue:
                    continue
                if not alive and not pending_slurm_jobs:
                    waited = session_commands.time.monotonic() - started
                    cli._print_tagged(
                        "optimize",
                        f"job polling complete after {waited:.1f}s.",
                    )
            continue

        suggested_wait = cli._extract_loop_wait_seconds(assistant_text)
        wait_source = "agent" if suggested_wait is not None else "default"
        requested_wait = suggested_wait if suggested_wait is not None else wait_seconds
        effective_wait = min(requested_wait, max_wait_seconds)
        if effective_wait > 0:
            if requested_wait > max_wait_seconds:
                cli._print_tagged(
                    "optimize",
                    (
                        "sleeping "
                        f"{effective_wait:.1f}s before next iteration "
                        f"(source: {wait_source}, capped by --worker-max-wait-seconds)"
                    ),
                )
            else:
                cli._print_tagged(
                    "optimize",
                    (
                        "sleeping "
                        f"{effective_wait:.1f}s before next iteration "
                        f"(source: {wait_source})"
                    ),
                )
            session_commands.time.sleep(effective_wait)

    cli._print_tagged(
        "optimize",
        f"max iterations reached ({max_iterations}) without {cli.LOOP_DONE_TOKEN}.",
        stderr=True,
    )
    return {
        "status": "incomplete_max_iterations",
        "reason": "max_iterations_reached",
        "exit_code": 1,
        "iteration": max_iterations,
        "assistant_text": last_assistant_text,
        "provider_return_code": last_provider_return_code,
        "run_result": last_run_result,
    }


def _worker_config(benchmark: dict[str, Any]) -> dict[str, Any]:
    worker = benchmark.get("worker")
    if worker is None:
        return {}
    if not isinstance(worker, dict):
        raise _cli().PackageError("Benchmark worker block must be an object.")
    return worker


def _resolve_worker_loop_config(
    args: argparse.Namespace,
    benchmark_payload: dict[str, Any],
) -> dict[str, float | int]:
    cli = _cli()
    worker = _worker_config(benchmark_payload)
    max_iterations_raw = (
        getattr(args, "worker_max_iterations", None)
        if getattr(args, "worker_max_iterations", None) is not None
        else worker.get("max_iterations", 8)
    )
    try:
        max_iterations = int(max_iterations_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--worker-max-iterations must be an integer.") from exc
    if max_iterations < 1:
        raise cli.PackageError("--worker-max-iterations must be >= 1.")

    wait_seconds, max_wait_seconds, pid_stall_seconds = (
        _normalize_worker_loop_timing_options(
            wait_seconds_raw=(
                getattr(args, "worker_wait_seconds", None)
                if getattr(args, "worker_wait_seconds", None) is not None
                else worker.get("wait_seconds", 1.0)
            ),
            max_wait_seconds_raw=(
                getattr(args, "worker_max_wait_seconds", None)
                if getattr(args, "worker_max_wait_seconds", None) is not None
                else worker.get("max_wait_seconds", 6000.0)
            ),
            pid_stall_seconds_raw=(
                getattr(args, "worker_pid_stall_seconds", None)
                if getattr(args, "worker_pid_stall_seconds", None) is not None
                else worker.get("pid_stall_seconds", 900.0)
            ),
        )
    )
    return {
        "max_iterations": max_iterations,
        "wait_seconds": wait_seconds,
        "max_wait_seconds": max_wait_seconds,
        "pid_stall_seconds": pid_stall_seconds,
    }


def _build_optimize_hpc_constraints_block(
    project_root: Path,
    *,
    args: argparse.Namespace,
) -> str:
    cli = _cli()
    hpc_context = cli._resolve_invocation_hpc_context(repo_dir=project_root, args=args)
    if not isinstance(hpc_context, dict) or not bool(hpc_context.get("enabled")):
        return ""
    prompt_lines = cli._build_hpc_prompt_lines(hpc_context)
    if not isinstance(prompt_lines, list) or not prompt_lines:
        return ""
    return "Execution target constraints:\n" + "\n".join(prompt_lines)


def _aggregation_for_metric(name: str, aggregation: dict[str, str]) -> str:
    lowered = name.lower()
    if any(token in lowered for token in ("memory", "rss", "mb")):
        return str(aggregation.get("memory") or "max").strip().lower()
    if any(token in lowered for token in ("iteration", "cycle", "step")):
        return str(aggregation.get("iterations") or "median").strip().lower()
    if any(token in lowered for token in ("time", "wall", "second")):
        return str(aggregation.get("timing") or "median").strip().lower()
    return "median"


def _aggregate_values(values: list[float], mode: str) -> float:
    if not values:
        return float("nan")
    if mode == "max":
        return max(values)
    if mode == "min":
        return min(values)
    if mode == "mean":
        return statistics.fmean(values)
    return statistics.median(values)


def _flatten_numbers(value: object) -> list[float]:
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, list):
        flattened: list[float] = []
        for item in value:
            flattened.extend(_flatten_numbers(item))
        return flattened
    if isinstance(value, dict):
        flattened = []
        for key in sorted(value):
            flattened.extend(_flatten_numbers(value[key]))
        return flattened
    return []


def _rms_difference(left: object, right: object) -> float:
    left_values = _flatten_numbers(left)
    right_values = _flatten_numbers(right)
    if not left_values or len(left_values) != len(right_values):
        return float("inf")
    sq_sum = 0.0
    for left_value, right_value in zip(left_values, right_values):
        sq_sum += (left_value - right_value) ** 2
    return math.sqrt(sq_sum / len(left_values))


def _resolve_optimize_branch(
    benchmark: dict[str, Any],
    *,
    package_id: str,
    override: str | None,
) -> str:
    if isinstance(override, str) and override.strip():
        return override.strip()
    campaign = _campaign_config(benchmark)
    preferred = str(campaign.get("incumbent_branch") or "").strip()
    if preferred:
        return preferred
    return f"fermilink-optimize/{package_id}"


def _ensure_channel_skills(
    project_root: Path,
    *,
    package_id: str,
    channel: str,
    version_id: str | None,
    require_verified: bool,
) -> dict[str, object]:
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    managed_root = scipkg_root / "packages" / package_id
    if not managed_root.is_dir():
        curated = cli.resolve_curated_package(
            package_id, channel=cli.normalize_channel_id(channel)
        )
        selected_version = cli.select_package_version(curated, version_id=version_id)
        if require_verified and not selected_version.verified:
            raise cli.PackageError(
                f"Selected curated version '{selected_version.version_id}' for package "
                f"'{package_id}' is not verified."
            )
        cli.install_from_zip(
            scipkg_root,
            package_id,
            zip_url=selected_version.source_archive_url,
            title=curated.title,
            activate=False,
            force=False,
            max_zip_bytes=cli.DEFAULT_MAX_ZIP_BYTES,
        )
    source_skills = managed_root / "skills"
    if not source_skills.is_dir():
        raise cli.PackageError(
            f"Managed package does not contain skills/: {source_skills}"
        )
    target_skills = project_root / "skills"
    if target_skills.exists():
        raise cli.PackageError(f"Target skills/ already exists: {target_skills}")
    cli.shutil.copytree(source_skills, target_skills)
    optimize_git.ensure_local_excludes(project_root, ["skills/"])
    return {
        "source": "channel",
        "skills_path": str(target_skills),
        "created": True,
    }


def _ensure_compile_skills(project_root: Path, *, package_id: str) -> dict[str, object]:
    cli = _cli()
    compile_args = argparse.Namespace(
        package_id=package_id,
        project_path=str(project_root),
        title=None,
        max_skills=30,
        core_skill_count=6,
        docs_only=False,
        keep_compile_artifacts=False,
        strict_compile_validation=False,
        install_off=True,
        activate=False,
        no_router_sync=True,
        json=False,
    )
    code = cli._cmd_compile(compile_args)
    if code != 0:
        raise cli.PackageError(
            f"Failed to compile skills for optimize mode at {project_root}."
        )
    target_skills = project_root / "skills"
    if not target_skills.is_dir():
        raise cli.PackageError("Compile completed without creating skills/.")
    optimize_git.ensure_local_excludes(project_root, ["skills/"])
    return {
        "source": "compile",
        "skills_path": str(target_skills),
        "created": True,
    }


def _ensure_skills(
    project_root: Path,
    *,
    package_id: str,
    skills_source: str,
    channel: str,
    version_id: str | None,
    require_verified: bool,
) -> dict[str, object]:
    target_skills = project_root / "skills"
    if target_skills.is_dir():
        return {
            "source": "existing",
            "skills_path": str(target_skills),
            "created": False,
        }

    source_mode = str(skills_source or "auto").strip().lower()
    if source_mode == "existing":
        raise _cli().PackageError(
            f"skills/ is required but missing in optimize target: {target_skills}"
        )
    if source_mode == "channel":
        return _ensure_channel_skills(
            project_root,
            package_id=package_id,
            channel=channel,
            version_id=version_id,
            require_verified=require_verified,
        )
    if source_mode == "compile":
        return _ensure_compile_skills(project_root, package_id=package_id)

    for candidate in ("channel", "compile"):
        try:
            if candidate == "channel":
                return _ensure_channel_skills(
                    project_root,
                    package_id=package_id,
                    channel=channel,
                    version_id=version_id,
                    require_verified=require_verified,
                )
            return _ensure_compile_skills(project_root, package_id=package_id)
        except Exception:
            continue
    raise _cli().PackageError(
        "Unable to create skills/ for optimize mode using either channel or compile."
    )


def _expand_runtime_command(
    command: list[str],
    *,
    benchmark_path: Path,
    project_root: Path,
) -> list[str]:
    replacements = {
        "{benchmark}": str(benchmark_path),
        "{project_root}": str(project_root),
    }
    expanded: list[str] = []
    for token in command:
        rendered = str(token)
        for key, value in replacements.items():
            rendered = rendered.replace(key, value)
        expanded.append(rendered)
    return expanded


def _parse_benchmark_stdout(stdout_text: str) -> dict[str, Any]:
    cli = _cli()
    normalized = str(stdout_text or "").strip()
    if not normalized:
        raise cli.PackageError("Benchmark command returned empty stdout.")
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            raise cli.PackageError("Benchmark command did not emit JSON.")
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise cli.PackageError(
                "Benchmark command stdout must be valid JSON."
            ) from exc
    if not isinstance(payload, dict):
        raise cli.PackageError("Benchmark command JSON payload must be an object.")
    return payload


def _run_benchmark_once(
    project_root: Path,
    *,
    benchmark_path: Path,
    benchmark_payload: dict[str, Any],
    timeout_seconds: int,
    run_dir: Path,
    run_label: str,
) -> dict[str, Any]:
    cli = _cli()
    runtime = benchmark_payload.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    command = _expand_runtime_command(
        list(runtime.get("command") or []),
        benchmark_path=benchmark_path,
        project_root=project_root,
    )
    env = os.environ.copy()
    runtime_env = runtime.get("env")
    if isinstance(runtime_env, dict):
        for key, value in runtime_env.items():
            if not isinstance(key, str):
                continue
            env[key] = str(value)
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / f"{run_label}.stdout.log"
    stderr_path = run_dir / f"{run_label}.stderr.log"
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(str(exc.stdout or ""), encoding="utf-8")
        stderr_path.write_text(str(exc.stderr or ""), encoding="utf-8")
        return {
            "ok": False,
            "status": "timeout",
            "summary_metrics": {},
            "cases": [],
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
        }

    stdout_text = str(completed.stdout or "")
    stderr_text = str(completed.stderr or "")
    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")
    if completed.returncode != 0:
        return {
            "ok": False,
            "status": "crash",
            "return_code": int(completed.returncode),
            "summary_metrics": {},
            "cases": [],
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
        }
    payload = _parse_benchmark_stdout(stdout_text)
    payload["ok"] = True
    payload["status"] = "ok"
    payload["stdout_log"] = str(stdout_path)
    payload["stderr_log"] = str(stderr_path)
    return payload


def _aggregate_benchmark_runs(
    benchmark_payload: dict[str, Any],
    measured_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    cli = _cli()
    if not measured_runs:
        raise cli.PackageError("No measured benchmark runs were collected.")
    aggregation = (
        _controller_config(benchmark_payload).get("aggregation")
        if isinstance(_controller_config(benchmark_payload).get("aggregation"), dict)
        else {}
    )
    summary_keys: set[str] = set()
    for run in measured_runs:
        metrics = run.get("summary_metrics")
        if isinstance(metrics, dict):
            summary_keys.update(
                key for key, value in metrics.items() if isinstance(value, (int, float))
            )

    summary_metrics: dict[str, float] = {}
    for key in sorted(summary_keys):
        values: list[float] = []
        for run in measured_runs:
            metrics = run.get("summary_metrics")
            if not isinstance(metrics, dict):
                continue
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                values.append(float(value))
        if not values:
            continue
        mode = _aggregation_for_metric(key, aggregation)
        summary_metrics[key] = _aggregate_values(values, mode)

    representative_cases = measured_runs[0].get("cases")
    if not isinstance(representative_cases, list):
        representative_cases = []
    correctness_ok = all(bool(run.get("correctness_ok")) for run in measured_runs)
    return {
        "summary_metrics": summary_metrics,
        "cases": representative_cases,
        "raw_runs": measured_runs,
        "correctness_ok": correctness_ok,
        "status": "ok",
    }


def _run_benchmark_suite(
    project_root: Path,
    *,
    benchmark_path: Path,
    benchmark_payload: dict[str, Any],
    run_dir: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    controller = _controller_config(benchmark_payload)
    warmup_runs = int(controller.get("warmup_runs") or 0)
    measured_runs = int(controller.get("measured_runs") or 1)

    for index in range(warmup_runs):
        warmup = _run_benchmark_once(
            project_root,
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
            timeout_seconds=timeout_seconds,
            run_dir=run_dir,
            run_label=f"warmup_{index + 1}",
        )
        if not warmup.get("ok"):
            return warmup

    runs: list[dict[str, Any]] = []
    for index in range(measured_runs):
        measured = _run_benchmark_once(
            project_root,
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
            timeout_seconds=timeout_seconds,
            run_dir=run_dir,
            run_label=f"measured_{index + 1}",
        )
        if not measured.get("ok"):
            return measured
        runs.append(measured)
    aggregated = _aggregate_benchmark_runs(benchmark_payload, runs)
    (run_dir / "metrics.json").write_text(
        json.dumps(aggregated, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return aggregated


def _case_map(cases: object) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    if not isinstance(cases, list):
        return mapped
    for item in cases:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id") or "").strip()
        if not case_id:
            continue
        mapped[case_id] = item
    return mapped


def _compare_correctness(
    benchmark_payload: dict[str, Any],
    *,
    incumbent_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    correctness = _correctness_config(benchmark_payload)
    require_all_cases_converged = bool(
        correctness.get("require_all_cases_converged", True)
    )
    max_abs_energy_delta = float(correctness.get("max_abs_energy_delta_hartree") or 0.0)
    max_abs_dm_rms_delta = float(correctness.get("max_abs_dm_rms_delta") or 0.0)
    max_abs_mo_energy_rms_delta = float(
        correctness.get("max_abs_mo_energy_rms_delta") or 0.0
    )

    incumbent_cases = _case_map(incumbent_metrics.get("cases"))
    candidate_cases = _case_map(candidate_metrics.get("cases"))
    errors: list[str] = []
    for case_id, incumbent_case in incumbent_cases.items():
        candidate_case = candidate_cases.get(case_id)
        if candidate_case is None:
            errors.append(f"missing benchmark case: {case_id}")
            continue
        if require_all_cases_converged and not bool(candidate_case.get("converged")):
            errors.append(f"case {case_id} did not converge")
            continue
        incumbent_energy = incumbent_case.get("total_energy_hartree")
        candidate_energy = candidate_case.get("total_energy_hartree")
        if isinstance(incumbent_energy, (int, float)) and isinstance(
            candidate_energy, (int, float)
        ):
            if (
                abs(float(candidate_energy) - float(incumbent_energy))
                > max_abs_energy_delta
            ):
                errors.append(f"case {case_id} energy drift exceeds threshold")
        dm_diff = _rms_difference(
            incumbent_case.get("density_matrix"),
            candidate_case.get("density_matrix"),
        )
        if dm_diff > max_abs_dm_rms_delta:
            errors.append(f"case {case_id} density-matrix drift exceeds threshold")
        mo_diff = _rms_difference(
            incumbent_case.get("mo_energies"),
            candidate_case.get("mo_energies"),
        )
        if mo_diff > max_abs_mo_energy_rms_delta:
            errors.append(f"case {case_id} MO-energy drift exceeds threshold")

    return {
        "ok": not errors,
        "errors": errors,
    }


def _metric_value(metrics: dict[str, Any], metric_name: str) -> float | None:
    summary = metrics.get("summary_metrics")
    if not isinstance(summary, dict):
        return None
    value = summary.get(metric_name)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _relative_change(
    *,
    previous: float | None,
    current: float | None,
    direction: str,
) -> float | None:
    if previous is None or current is None:
        return None
    if direction == "maximize":
        delta = current - previous
    else:
        delta = previous - current
    scale = abs(previous) if previous != 0 else 1.0
    return delta / scale


def _condense_metrics_for_controller(metrics: dict[str, Any]) -> dict[str, Any]:
    summary_metrics = metrics.get("summary_metrics")
    rendered_summary = summary_metrics if isinstance(summary_metrics, dict) else {}
    cases = metrics.get("cases")
    rendered_cases: list[dict[str, Any]] = []
    if isinstance(cases, list):
        for item in cases:
            if not isinstance(item, dict):
                continue
            rendered_cases.append(
                {
                    "id": str(item.get("id") or ""),
                    "converged": bool(item.get("converged")),
                    "wall_seconds": item.get("wall_seconds"),
                    "scf_iterations": item.get("scf_iterations"),
                    "total_energy_hartree": item.get("total_energy_hartree"),
                    "error": str(item.get("error") or ""),
                }
            )
    return {
        "status": metrics.get("status"),
        "correctness_ok": bool(metrics.get("correctness_ok")),
        "summary_metrics": rendered_summary,
        "cases": rendered_cases,
    }


def _hard_validate_candidate(
    benchmark_payload: dict[str, Any],
    *,
    incumbent_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    objective = _objective_config(benchmark_payload)
    primary_metric = str(objective.get("primary_metric") or "").strip()

    if not primary_metric:
        return {
            "hard_reject": True,
            "status": "missing_metrics",
            "reason": "missing primary metric",
        }
    if not bool(candidate_metrics.get("correctness_ok")):
        return {
            "hard_reject": True,
            "status": "correctness_failure",
            "reason": "candidate benchmark reported correctness failure",
        }

    correctness = _compare_correctness(
        benchmark_payload,
        incumbent_metrics=incumbent_metrics,
        candidate_metrics=candidate_metrics,
    )
    if not correctness.get("ok"):
        return {
            "hard_reject": True,
            "status": "correctness_failure",
            "reason": "; ".join(correctness.get("errors") or []),
            "correctness": correctness,
        }

    incumbent_summary = incumbent_metrics.get("summary_metrics")
    candidate_summary = candidate_metrics.get("summary_metrics")
    if not isinstance(incumbent_summary, dict) or not isinstance(
        candidate_summary, dict
    ):
        return {
            "hard_reject": True,
            "status": "missing_metrics",
            "reason": "missing summary_metrics",
            "correctness": correctness,
        }

    incumbent_primary = incumbent_summary.get(primary_metric)
    candidate_primary = candidate_summary.get(primary_metric)
    if not isinstance(incumbent_primary, (int, float)) or not isinstance(
        candidate_primary, (int, float)
    ):
        return {
            "hard_reject": True,
            "status": "missing_metrics",
            "reason": primary_metric,
            "correctness": correctness,
        }

    return {
        "hard_reject": False,
        "status": "ok",
        "reason": "",
        "correctness": correctness,
    }


def _initial_state(
    *,
    package_id: str,
    benchmark_payload: dict[str, Any],
    benchmark_rel: str,
    program_rel: str,
    memory_rel: str,
    results_rel: str,
    branch_name: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "package_id": package_id,
        "benchmark_id": str(benchmark_payload.get("benchmark_id") or "benchmark"),
        "benchmark_path": benchmark_rel,
        "program_path": program_rel,
        "memory_path": memory_rel,
        "results_path": results_rel,
        "branch": branch_name,
        "started_at_utc": optimize_state.utc_now_z(),
        "iteration": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "consecutive_rejections": 0,
        "baseline_commit": "",
        "baseline_metrics": {},
        "incumbent_commit": "",
        "incumbent_metrics": {},
    }


def _description_or_default(assistant_text: str, *, iteration: int) -> str:
    value = optimize_prompts.extract_experiment_description(assistant_text)
    if value:
        return value
    return f"optimize iteration {iteration}"


def _write_run_text(run_dir: Path, filename: str, text: str) -> None:
    target_path = run_dir / filename
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(str(text or ""), encoding="utf-8")


def _write_run_json(run_dir: Path, filename: str, payload: dict[str, Any]) -> None:
    target_path = run_dir / filename
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_campaign(args: argparse.Namespace) -> dict[str, Any]:
    cli = _cli()
    package_id = cli.normalize_package_id(args.package_id)
    project_root = cli._resolve_project_path(args.project_path)
    if not project_root.is_dir():
        raise cli.PackageError(f"Optimize path is not a directory: {project_root}")
    git_repo_initialized = cli._ensure_compile_repo_ready(project_root)
    benchmark_path = cli._resolve_project_path(args.benchmark)
    if not benchmark_path.is_file():
        raise cli.PackageError(f"Benchmark file does not exist: {benchmark_path}")
    benchmark_payload = _load_benchmark(benchmark_path)

    optimize_git.ensure_local_excludes(project_root, [".fermilink-optimize/"])
    if (project_root / "skills").exists():
        optimize_git.ensure_local_excludes(project_root, ["skills/"])
    optimize_git.ensure_clean_repo(project_root, allow_dirty=bool(args.allow_dirty))
    branch_name = _resolve_optimize_branch(
        benchmark_payload,
        package_id=package_id,
        override=getattr(args, "branch", None),
    )
    branch_info = optimize_git.checkout_optimize_branch(
        project_root,
        branch_name=branch_name,
    )

    optimize_state.ensure_optimize_root(project_root)
    skills_bootstrap = _ensure_skills(
        project_root,
        package_id=package_id,
        skills_source=str(getattr(args, "skills_source", "auto") or "auto"),
        channel=str(getattr(args, "channel", "skilled-scipkg") or "skilled-scipkg"),
        version_id=getattr(args, "version_id", None),
        require_verified=bool(getattr(args, "require_verified", False)),
    )

    program_path_arg = getattr(args, "program", None)
    if isinstance(program_path_arg, str) and program_path_arg.strip():
        program_path = cli._resolve_project_path(program_path_arg)
    else:
        program_path = optimize_state.default_program_path(project_root)
    optimize_state.ensure_program_file(
        program_path,
        content=optimize_prompts.default_program_markdown(
            package_id=package_id,
            benchmark_id=str(benchmark_payload.get("benchmark_id") or "benchmark"),
        ),
    )
    results_path = optimize_state.results_path(project_root)
    optimize_state.ensure_results_file(results_path)
    memory_path = optimize_state.memory_path(project_root)
    worker_memory_path = optimize_state.worker_memory_path(project_root)
    benchmark_rel = optimize_state.safe_relative(benchmark_path, project_root)
    program_rel = optimize_state.safe_relative(program_path, project_root)
    memory_rel = optimize_state.safe_relative(memory_path, project_root)
    worker_memory_rel = optimize_state.safe_relative(worker_memory_path, project_root)
    results_rel = optimize_state.safe_relative(results_path, project_root)
    optimize_state.ensure_memory_file(
        memory_path,
        package_id=package_id,
        benchmark_id=str(benchmark_payload.get("benchmark_id") or "benchmark"),
        benchmark_rel=benchmark_rel,
        optimize_branch=branch_name,
    )

    state_path = optimize_state.state_path(project_root)
    state_payload = optimize_state.load_state(state_path)
    if state_payload is None:
        state_payload = _initial_state(
            package_id=package_id,
            benchmark_payload=benchmark_payload,
            benchmark_rel=benchmark_rel,
            program_rel=program_rel,
            memory_rel=memory_rel,
            results_rel=results_rel,
            branch_name=branch_name,
        )
    state_payload["branch"] = branch_name
    optimize_state.write_state(state_path, state_payload)

    if bool(getattr(args, "plan_only", False)):
        return {
            "package_id": package_id,
            "branch": branch_name,
            "git_repo_initialized": git_repo_initialized,
            "skills_bootstrap": skills_bootstrap,
            "state_path": str(state_path),
            "program_path": str(program_path),
            "memory_path": str(memory_path),
            "results_path": str(results_path),
            "incumbent_commit": str(state_payload.get("incumbent_commit") or ""),
            "incumbent_primary_metric": None,
            "primary_metric_name": str(
                _objective_config(benchmark_payload).get("primary_metric")
                or "primary_metric"
            ),
            "accepted_count": int(state_payload.get("accepted_count") or 0),
            "rejected_count": int(state_payload.get("rejected_count") or 0),
            "status": "planned",
        }

    controller = _controller_config(benchmark_payload)
    timeout_seconds = int(
        getattr(args, "timeout_seconds", None)
        or controller.get("timeout_seconds")
        or 900
    )
    primary_metric_name = str(
        _objective_config(benchmark_payload).get("primary_metric") or "primary_metric"
    )

    if not str(state_payload.get("baseline_commit") or "").strip():
        cli._print_tagged("optimize", "running baseline benchmark")
        baseline_commit = optimize_git.head_sha(project_root)
        baseline_dir = optimize_state.runs_root(project_root) / "baseline"
        baseline_metrics = _run_benchmark_suite(
            project_root,
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
            run_dir=baseline_dir,
            timeout_seconds=timeout_seconds,
        )
        if (
            not baseline_metrics.get("ok", True)
            and baseline_metrics.get("status") != "ok"
        ):
            raise cli.PackageError(
                f"Baseline benchmark failed: {baseline_metrics.get('status')}"
            )
        if not bool(baseline_metrics.get("correctness_ok", True)):
            raise cli.PackageError(
                "Baseline benchmark completed but did not satisfy correctness gates."
            )
        state_payload["baseline_commit"] = baseline_commit
        state_payload["baseline_metrics"] = baseline_metrics
        state_payload["incumbent_commit"] = baseline_commit
        state_payload["incumbent_metrics"] = baseline_metrics
        baseline_primary = (
            baseline_metrics.get("summary_metrics", {}).get(primary_metric_name)
            if isinstance(baseline_metrics.get("summary_metrics"), dict)
            else None
        )
        optimize_state.append_result(
            results_path,
            iteration=0,
            commit=baseline_commit[:12],
            status="baseline",
            primary_metric_name=primary_metric_name,
            primary_metric_value=(
                baseline_primary if baseline_primary is not None else "nan"
            ),
            description="baseline",
        )
        optimize_state.record_campaign_event(
            memory_path,
            commit=baseline_commit[:12],
            primary_metric_name=primary_metric_name,
            primary_metric_value=(
                baseline_primary if baseline_primary is not None else "nan"
            ),
            status="baseline",
            description="baseline",
        )
        optimize_state.write_state(state_path, state_payload)

    if bool(getattr(args, "baseline_only", False)):
        incumbent_metrics = state_payload.get("incumbent_metrics")
        incumbent_summary = (
            incumbent_metrics.get("summary_metrics")
            if isinstance(incumbent_metrics, dict)
            else {}
        )
        return {
            "package_id": package_id,
            "branch": branch_name,
            "git_repo_initialized": git_repo_initialized,
            "skills_bootstrap": skills_bootstrap,
            "state_path": str(state_path),
            "program_path": str(program_path),
            "memory_path": str(memory_path),
            "results_path": str(results_path),
            "incumbent_commit": str(state_payload.get("incumbent_commit") or ""),
            "incumbent_primary_metric": (
                incumbent_summary.get(primary_metric_name)
                if isinstance(incumbent_summary, dict)
                else None
            ),
            "primary_metric_name": primary_metric_name,
            "accepted_count": int(state_payload.get("accepted_count") or 0),
            "rejected_count": int(state_payload.get("rejected_count") or 0),
            "status": "baseline_only",
        }

    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(args.sandbox, str) and args.sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = args.sandbox.strip()
    model = runtime_policy.model
    reasoning_effort = runtime_policy.reasoning_effort
    provider_bin_override = cli.resolve_provider_binary_override(
        provider,
        raw_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
    )
    editable_paths = _benchmark_editable_paths(benchmark_payload)
    immutable_paths = _benchmark_immutable_paths(benchmark_payload)
    worker_loop_config = _resolve_worker_loop_config(args, benchmark_payload)
    hpc_constraints_block = _build_optimize_hpc_constraints_block(
        project_root,
        args=args,
    )
    agents_md = optimize_prompts.build_optimize_agents_md(
        benchmark_rel=benchmark_rel,
        program_rel=program_rel,
        controller_memory_rel=memory_rel,
        worker_memory_rel=worker_memory_rel,
        results_rel=results_rel,
        editable_paths=editable_paths,
        immutable_paths=immutable_paths,
    )

    max_iterations = int(
        getattr(args, "max_iterations", None)
        or _campaign_config(benchmark_payload).get("max_iterations")
        or 20
    )
    stop_on_consecutive_rejections = int(
        getattr(args, "stop_on_consecutive_rejections", None)
        or _campaign_config(benchmark_payload).get("stop_on_consecutive_rejections")
        or max_iterations
    )

    iteration = int(state_payload.get("iteration") or 0)
    accepted_count = int(state_payload.get("accepted_count") or 0)
    rejected_count = int(state_payload.get("rejected_count") or 0)
    consecutive_rejections = int(state_payload.get("consecutive_rejections") or 0)

    while True:
        if not bool(getattr(args, "forever", False)) and iteration >= max_iterations:
            break
        if consecutive_rejections >= stop_on_consecutive_rejections:
            cli._print_tagged(
                "optimize",
                (
                    "stopping after consecutive rejection limit "
                    f"({consecutive_rejections}/{stop_on_consecutive_rejections})."
                ),
            )
            break

        iteration += 1
        start_sha = optimize_git.head_sha(project_root)
        run_dir = optimize_state.runs_root(project_root) / f"iter_{iteration:04d}"
        run_rel = optimize_state.safe_relative(run_dir, project_root)
        recent_results = optimize_state.recent_results_text(results_path)
        optimize_state.reset_worker_memory_file(
            worker_memory_path,
            package_id=package_id,
            benchmark_id=str(benchmark_payload.get("benchmark_id") or "benchmark"),
            benchmark_rel=benchmark_rel,
            program_rel=program_rel,
            controller_memory_rel=memory_rel,
            results_rel=results_rel,
            worker_iteration=iteration,
        )
        prompt = optimize_prompts.build_optimize_prompt(
            benchmark_payload=benchmark_payload,
            benchmark_rel=benchmark_rel,
            program_rel=program_rel,
            controller_memory_rel=memory_rel,
            worker_memory_rel=worker_memory_rel,
            results_rel=results_rel,
            recent_results_text=recent_results,
            state_payload=state_payload,
            editable_paths=editable_paths,
            hpc_constraints_block=hpc_constraints_block,
        )
        _write_run_text(run_dir, "worker_prompt.txt", prompt)
        cli._print_tagged("optimize", f"iteration {iteration}")

        def _run_worker_turn(
            loop_iteration: int,
            _loop_max_iterations: int,
            prompt_text: str,
        ) -> dict[str, object]:
            result = cli._run_exec_chat_turn(
                repo_dir=project_root,
                prompt=prompt_text,
                sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
                provider_bin_override=provider_bin_override,
                provider=provider,
                sandbox_policy=sandbox_policy,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            _write_run_json(
                run_dir,
                f"worker_turns/turn_{loop_iteration:04d}.json",
                {
                    "assistant_text": str(result.get("assistant_text") or ""),
                    "return_code": int(result.get("return_code") or 0),
                    "stderr": str(result.get("stderr") or ""),
                },
            )
            return result

        with optimize_git.temporary_optimize_agents(
            project_root,
            provider=provider,
            content=agents_md,
        ):
            worker_loop_result = _run_optimize_worker_loop(
                prompt=prompt,
                max_iterations=int(worker_loop_config["max_iterations"]),
                wait_seconds=float(worker_loop_config["wait_seconds"]),
                max_wait_seconds=float(worker_loop_config["max_wait_seconds"]),
                pid_stall_seconds=float(worker_loop_config["pid_stall_seconds"]),
                run_turn=_run_worker_turn,
            )

        archived_worker_memory = optimize_state.archive_worker_memory(
            worker_memory_path,
            run_dir,
        )
        assistant_text = str(worker_loop_result.get("assistant_text") or "")
        final_worker_turn = worker_loop_result.get("run_result")
        final_worker_turn = final_worker_turn if isinstance(final_worker_turn, dict) else {}
        _write_run_json(
            run_dir,
            "worker_loop_result.json",
            {
                "status": str(worker_loop_result.get("status") or ""),
                "reason": str(worker_loop_result.get("reason") or ""),
                "iteration_count": int(worker_loop_result.get("iteration") or 0),
                "exit_code": int(worker_loop_result.get("exit_code") or 0),
                "provider_return_code": int(
                    worker_loop_result.get("provider_return_code") or 0
                ),
                "assistant_text": assistant_text,
                "stderr": str(final_worker_turn.get("stderr") or ""),
                "archived_worker_memory": (
                    str(archived_worker_memory) if archived_worker_memory else ""
                ),
                "worker_loop_config": worker_loop_config,
            },
        )
        description = _description_or_default(assistant_text, iteration=iteration)
        changed_entries = optimize_git.list_changed_paths(project_root)
        cleanup_untracked = [
            entry["path"] for entry in changed_entries if entry.get("status") == "??"
        ]
        editable_changed = [
            entry["path"]
            for entry in changed_entries
            if _matches_any(entry.get("path", ""), editable_paths)
        ]
        forbidden_changed = [
            entry["path"]
            for entry in changed_entries
            if not _matches_any(entry.get("path", ""), editable_paths)
        ]

        incumbent_metrics = (
            state_payload.get("incumbent_metrics")
            if isinstance(state_payload.get("incumbent_metrics"), dict)
            else {}
        )
        baseline_metrics = (
            state_payload.get("baseline_metrics")
            if isinstance(state_payload.get("baseline_metrics"), dict)
            else {}
        )
        objective = _objective_config(benchmark_payload)
        objective_direction = (
            str(objective.get("direction") or "minimize").strip().lower()
        )
        incumbent_primary = _metric_value(incumbent_metrics, primary_metric_name)
        baseline_primary = _metric_value(baseline_metrics, primary_metric_name)

        candidate_commit: str | None = None
        candidate_metrics: dict[str, Any] = {}
        controller_summary: str | None = None
        controller_decision: str | None = None
        controller_result: dict[str, object] = {}
        hard_reject = False
        hard_status = "rejected"
        hard_reason = ""
        candidate_primary: float | None = None
        benchmark_ran = False

        evaluation_context: dict[str, Any] = {
            "worker_loop_status": str(worker_loop_result.get("status") or ""),
            "worker_loop_reason": str(worker_loop_result.get("reason") or ""),
            "worker_loop_iteration_count": int(worker_loop_result.get("iteration") or 0),
            "worker_return_code": int(
                worker_loop_result.get("provider_return_code") or 0
            ),
            "candidate_description": description,
            "changed_paths": [entry.get("path", "") for entry in changed_entries],
            "editable_changed_paths": editable_changed,
            "forbidden_changed_paths": forbidden_changed,
            "primary_metric_name": primary_metric_name,
            "objective_direction": objective_direction,
            "baseline_commit": str(state_payload.get("baseline_commit") or ""),
            "baseline_primary_metric": baseline_primary,
            "incumbent_commit": str(state_payload.get("incumbent_commit") or ""),
            "incumbent_primary_metric": incumbent_primary,
            "benchmark_status": "not_run",
            "candidate_commit": None,
            "hard_reject": False,
            "hard_reject_reason": "",
            "hard_reject_status": "",
        }

        if str(worker_loop_result.get("status") or "") != "done":
            hard_reject = True
            hard_status = "worker_incomplete"
            hard_reason = (
                "worker loop did not finish cleanly: "
                f"{worker_loop_result.get('status') or 'unknown'}"
            )
        elif forbidden_changed:
            hard_reject = True
            hard_status = "invalid_scope"
            hard_reason = (
                f"modified forbidden paths: {', '.join(forbidden_changed[:4])}"
            )
        elif not editable_changed:
            hard_reject = True
            hard_status = "rejected"
            hard_reason = "worker loop finished without an editable code change"
        else:
            candidate_commit = optimize_git.commit_paths(
                project_root,
                paths=editable_changed,
                message=f"fermilink optimize iter {iteration}: {description}",
            )
            evaluation_context["candidate_commit"] = candidate_commit
            diff_stat = optimize_git.run_git(
                project_root,
                ["diff", "--stat", f"{start_sha}..{candidate_commit}"],
            )
            _write_run_text(run_dir, "candidate_diff_stat.txt", diff_stat.stdout or "")
            diff_full = optimize_git.run_git(
                project_root,
                ["diff", f"{start_sha}..{candidate_commit}"],
            )
            _write_run_text(run_dir, "candidate.diff", diff_full.stdout or "")
            benchmark_ran = True
            candidate_metrics = _run_benchmark_suite(
                project_root,
                benchmark_path=benchmark_path,
                benchmark_payload=benchmark_payload,
                run_dir=run_dir,
                timeout_seconds=timeout_seconds,
            )
            evaluation_context["benchmark_status"] = str(
                candidate_metrics.get("status") or "unknown"
            )
            evaluation_context["candidate_metrics"] = _condense_metrics_for_controller(
                candidate_metrics
            )
            candidate_primary = _metric_value(candidate_metrics, primary_metric_name)
            evaluation_context["candidate_primary_metric"] = candidate_primary
            evaluation_context["relative_change_vs_incumbent"] = _relative_change(
                previous=incumbent_primary,
                current=candidate_primary,
                direction=objective_direction,
            )
            evaluation_context["relative_change_vs_baseline"] = _relative_change(
                previous=baseline_primary,
                current=candidate_primary,
                direction=objective_direction,
            )

            if candidate_metrics.get("status") in {"timeout", "crash"}:
                hard_reject = True
                hard_status = str(candidate_metrics.get("status") or "rejected")
                hard_reason = f"benchmark {hard_status}"
            else:
                hard_validation = _hard_validate_candidate(
                    benchmark_payload,
                    incumbent_metrics=incumbent_metrics,
                    candidate_metrics=candidate_metrics,
                )
                if hard_validation.get("correctness"):
                    evaluation_context["correctness"] = hard_validation.get(
                        "correctness"
                    )
                if bool(hard_validation.get("hard_reject")):
                    hard_reject = True
                    hard_status = str(hard_validation.get("status") or "rejected")
                    hard_reason = str(hard_validation.get("reason") or hard_status)

        evaluation_context["hard_reject"] = hard_reject
        evaluation_context["hard_reject_reason"] = hard_reason
        evaluation_context["hard_reject_status"] = hard_status if hard_reject else ""
        _write_run_json(run_dir, "review_context.json", evaluation_context)

        if benchmark_ran and candidate_commit is not None:
            controller_agents_md = optimize_prompts.build_controller_agents_md(
                benchmark_rel=benchmark_rel,
                program_rel=program_rel,
                memory_rel=memory_rel,
                results_rel=results_rel,
                run_rel=run_rel,
            )
            controller_prompt = optimize_prompts.build_controller_prompt(
                benchmark_payload=benchmark_payload,
                benchmark_rel=benchmark_rel,
                program_rel=program_rel,
                memory_rel=memory_rel,
                results_rel=results_rel,
                run_rel=run_rel,
                recent_results_text=recent_results,
                iteration=iteration,
                incumbent_commit=str(state_payload.get("incumbent_commit") or ""),
                candidate_commit=candidate_commit,
                worker_description=description,
                changed_paths=editable_changed
                or [entry.get("path", "") for entry in changed_entries],
                evaluation_context=evaluation_context,
            )
            _write_run_text(run_dir, "controller_prompt.txt", controller_prompt)
            with optimize_git.temporary_optimize_agents(
                project_root,
                provider=provider,
                content=controller_agents_md,
            ):
                controller_result = cli._run_exec_chat_turn(
                    repo_dir=project_root,
                    prompt=controller_prompt,
                    sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
                    provider_bin_override=provider_bin_override,
                    provider=provider,
                    sandbox_policy=sandbox_policy,
                    model=model,
                    reasoning_effort=reasoning_effort,
                )

            controller_text = str(controller_result.get("assistant_text") or "")
            controller_decision = optimize_prompts.extract_decision(controller_text)
            controller_summary = optimize_prompts.extract_controller_summary(
                controller_text
            )
            _write_run_json(
                run_dir,
                "controller_result.json",
                {
                    "assistant_text": controller_text,
                    "decision": controller_decision,
                    "controller_summary": controller_summary,
                    "return_code": int(controller_result.get("return_code") or 0),
                    "stderr": str(controller_result.get("stderr") or ""),
                },
            )
            post_controller_changes = optimize_git.list_changed_paths(project_root)
            if post_controller_changes:
                hard_reject = True
                hard_status = "rejected"
                hard_reason = "controller review left tracked repository changes"
                evaluation_context["hard_reject"] = True
                evaluation_context["hard_reject_reason"] = hard_reason
                evaluation_context["hard_reject_status"] = hard_status
                evaluation_context["post_controller_changes"] = post_controller_changes
                _write_run_json(run_dir, "review_context.json", evaluation_context)
        elif hard_reason and not controller_summary:
            controller_summary = hard_reason

        final_status = "rejected"
        if benchmark_ran and candidate_commit is not None:
            if int(controller_result.get("return_code") or 0) != 0:
                controller_decision = "REJECTED"
                if not controller_summary:
                    controller_summary = "controller agent exited non-zero"
            elif controller_decision not in {"ACCEPTED", "REJECTED"}:
                controller_decision = "REJECTED"
                if not controller_summary:
                    controller_summary = (
                        "controller agent did not emit a valid decision tag"
                    )
        elif controller_summary is None and hard_reject:
            controller_summary = hard_reason

        if hard_reject:
            final_status = hard_status or "rejected"
            if (
                benchmark_ran
                and controller_decision == "ACCEPTED"
                and not controller_summary
            ):
                controller_summary = "controller acceptance overridden by hard guard"
        elif benchmark_ran and controller_decision == "ACCEPTED" and candidate_commit:
            final_status = "accepted"
        else:
            controller_decision = "REJECTED"
            final_status = "rejected"

        event_description = description
        if controller_summary:
            event_description = f"{description} [{controller_summary}]"

        if final_status == "accepted" and candidate_commit:
            accepted_count += 1
            consecutive_rejections = 0
            state_payload["incumbent_commit"] = candidate_commit
            state_payload["incumbent_metrics"] = candidate_metrics
            optimize_state.append_result(
                results_path,
                iteration=iteration,
                commit=candidate_commit[:12],
                status="accepted",
                primary_metric_name=primary_metric_name,
                primary_metric_value=(
                    candidate_primary if candidate_primary is not None else "nan"
                ),
                description=event_description,
            )
            optimize_state.record_campaign_event(
                memory_path,
                commit=candidate_commit[:12],
                primary_metric_name=primary_metric_name,
                primary_metric_value=(
                    candidate_primary if candidate_primary is not None else "nan"
                ),
                status="accepted",
                description=event_description,
            )
        else:
            if candidate_commit is not None:
                optimize_git.reset_to_commit(
                    project_root,
                    commit_sha=start_sha,
                    cleanup_paths=[],
                )
            else:
                optimize_git.reset_to_commit(
                    project_root,
                    commit_sha=start_sha,
                    cleanup_paths=cleanup_untracked,
                )
            rejected_count += 1
            consecutive_rejections += 1
            recorded_commit = (
                candidate_commit[:12] if candidate_commit else start_sha[:12]
            )
            optimize_state.append_result(
                results_path,
                iteration=iteration,
                commit=recorded_commit,
                status=final_status,
                primary_metric_name=primary_metric_name,
                primary_metric_value=(
                    candidate_primary if candidate_primary is not None else "nan"
                ),
                description=event_description,
            )
            optimize_state.record_campaign_event(
                memory_path,
                commit=recorded_commit,
                primary_metric_name=primary_metric_name,
                primary_metric_value=(
                    candidate_primary if candidate_primary is not None else "nan"
                ),
                status=final_status,
                description=event_description,
            )

        state_payload["iteration"] = iteration
        state_payload["accepted_count"] = accepted_count
        state_payload["rejected_count"] = rejected_count
        state_payload["consecutive_rejections"] = consecutive_rejections
        optimize_state.write_state(state_path, state_payload)

    incumbent_metrics = state_payload.get("incumbent_metrics")
    incumbent_summary = (
        incumbent_metrics.get("summary_metrics")
        if isinstance(incumbent_metrics, dict)
        else {}
    )
    return {
        "package_id": package_id,
        "branch": branch_name,
        "branch_info": branch_info,
        "git_repo_initialized": git_repo_initialized,
        "skills_bootstrap": skills_bootstrap,
        "state_path": str(state_path),
        "program_path": str(program_path),
        "memory_path": str(memory_path),
        "results_path": str(results_path),
        "incumbent_commit": str(state_payload.get("incumbent_commit") or ""),
        "incumbent_primary_metric": (
            incumbent_summary.get(primary_metric_name)
            if isinstance(incumbent_summary, dict)
            else None
        ),
        "primary_metric_name": primary_metric_name,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "status": "completed",
    }
