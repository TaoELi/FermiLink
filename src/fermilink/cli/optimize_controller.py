from __future__ import annotations

import argparse
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


def _evaluate_candidate(
    benchmark_payload: dict[str, Any],
    *,
    baseline_metrics: dict[str, Any],
    incumbent_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    objective = _objective_config(benchmark_payload)
    primary_metric = str(objective.get("primary_metric") or "").strip()
    direction = str(objective.get("direction") or "minimize").strip().lower()
    min_relative_improvement = float(objective.get("min_relative_improvement") or 0.0)

    if not primary_metric:
        return {
            "accepted": False,
            "status": "missing_metrics",
            "reason": "missing primary metric",
        }
    if not bool(candidate_metrics.get("correctness_ok")):
        return {
            "accepted": False,
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
            "accepted": False,
            "status": "correctness_failure",
            "reason": "; ".join(correctness.get("errors") or []),
            "correctness": correctness,
        }

    incumbent_summary = incumbent_metrics.get("summary_metrics")
    candidate_summary = candidate_metrics.get("summary_metrics")
    baseline_summary = baseline_metrics.get("summary_metrics")
    if not isinstance(incumbent_summary, dict) or not isinstance(
        candidate_summary, dict
    ):
        return {
            "accepted": False,
            "status": "missing_metrics",
            "reason": "missing summary_metrics",
        }
    incumbent_primary = incumbent_summary.get(primary_metric)
    candidate_primary = candidate_summary.get(primary_metric)
    if not isinstance(incumbent_primary, (int, float)) or not isinstance(
        candidate_primary, (int, float)
    ):
        return {
            "accepted": False,
            "status": "missing_metrics",
            "reason": primary_metric,
        }

    incumbent_primary = float(incumbent_primary)
    candidate_primary = float(candidate_primary)
    if direction == "maximize":
        improvement = candidate_primary - incumbent_primary
    else:
        improvement = incumbent_primary - candidate_primary
    scale = abs(incumbent_primary) if incumbent_primary != 0 else 1.0
    relative_improvement = improvement / scale
    if relative_improvement < min_relative_improvement:
        return {
            "accepted": False,
            "status": "rejected",
            "reason": (
                f"{primary_metric} did not improve enough "
                f"(relative improvement {relative_improvement:.6f})"
            ),
            "correctness": correctness,
        }

    secondary = _controller_config(benchmark_payload).get("secondary_objectives")
    if isinstance(secondary, list) and isinstance(baseline_summary, dict):
        for item in secondary:
            if not isinstance(item, dict):
                continue
            metric_name = str(item.get("metric") or "").strip()
            soft_limit = item.get("soft_limit_relative_to_baseline")
            if not metric_name or not isinstance(soft_limit, (int, float)):
                continue
            baseline_value = baseline_summary.get(metric_name)
            candidate_value = candidate_summary.get(metric_name)
            if not isinstance(baseline_value, (int, float)) or not isinstance(
                candidate_value, (int, float)
            ):
                continue
            if float(candidate_value) > float(baseline_value) * float(soft_limit):
                return {
                    "accepted": False,
                    "status": "rejected",
                    "reason": (
                        f"{metric_name} exceeded soft limit "
                        f"relative to baseline ({candidate_value} > "
                        f"{float(baseline_value) * float(soft_limit):.6g})"
                    ),
                    "correctness": correctness,
                }

    return {
        "accepted": True,
        "status": "accepted",
        "reason": (
            f"{primary_metric} improved from {incumbent_primary:.12g} to "
            f"{candidate_primary:.12g}"
        ),
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
    benchmark_rel = optimize_state.safe_relative(benchmark_path, project_root)
    program_rel = optimize_state.safe_relative(program_path, project_root)
    memory_rel = optimize_state.safe_relative(memory_path, project_root)
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
    agents_md = optimize_prompts.build_optimize_agents_md(
        benchmark_rel=benchmark_rel,
        program_rel=program_rel,
        memory_rel=memory_rel,
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
        recent_results = optimize_state.recent_results_text(results_path)
        prompt = optimize_prompts.build_optimize_prompt(
            benchmark_payload=benchmark_payload,
            benchmark_rel=benchmark_rel,
            program_rel=program_rel,
            memory_rel=memory_rel,
            results_rel=results_rel,
            recent_results_text=recent_results,
            state_payload=state_payload,
            editable_paths=editable_paths,
        )
        cli._print_tagged("optimize", f"iteration {iteration}")
        with optimize_git.temporary_optimize_agents(
            project_root,
            provider=provider,
            content=agents_md,
        ):
            run_result = cli._run_exec_chat_turn(
                repo_dir=project_root,
                prompt=prompt,
                sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
                provider_bin_override=provider_bin_override,
                provider=provider,
                sandbox_policy=sandbox_policy,
                model=model,
                reasoning_effort=reasoning_effort,
            )

        assistant_text = str(run_result.get("assistant_text") or "")
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

        if forbidden_changed:
            optimize_git.reset_to_commit(
                project_root,
                commit_sha=start_sha,
                cleanup_paths=cleanup_untracked,
            )
            rejected_count += 1
            consecutive_rejections += 1
            optimize_state.append_result(
                results_path,
                iteration=iteration,
                commit=start_sha[:12],
                status="rejected",
                primary_metric_name=primary_metric_name,
                primary_metric_value="nan",
                description=f"{description} [forbidden paths: {', '.join(forbidden_changed[:4])}]",
            )
            optimize_state.record_campaign_event(
                memory_path,
                commit=start_sha[:12],
                primary_metric_name=primary_metric_name,
                primary_metric_value="nan",
                status="rejected",
                description=f"{description} [forbidden paths]",
            )
            state_payload["iteration"] = iteration
            state_payload["rejected_count"] = rejected_count
            state_payload["consecutive_rejections"] = consecutive_rejections
            optimize_state.write_state(state_path, state_payload)
            continue

        if int(run_result.get("return_code") or 0) != 0 or not editable_changed:
            optimize_git.reset_to_commit(
                project_root,
                commit_sha=start_sha,
                cleanup_paths=cleanup_untracked,
            )
            rejected_count += 1
            consecutive_rejections += 1
            optimize_state.append_result(
                results_path,
                iteration=iteration,
                commit=start_sha[:12],
                status="rejected",
                primary_metric_name=primary_metric_name,
                primary_metric_value="nan",
                description=description,
            )
            optimize_state.record_campaign_event(
                memory_path,
                commit=start_sha[:12],
                primary_metric_name=primary_metric_name,
                primary_metric_value="nan",
                status="rejected",
                description=description,
            )
            state_payload["iteration"] = iteration
            state_payload["rejected_count"] = rejected_count
            state_payload["consecutive_rejections"] = consecutive_rejections
            optimize_state.write_state(state_path, state_payload)
            continue

        commit_sha = optimize_git.commit_paths(
            project_root,
            paths=editable_changed,
            message=f"fermilink optimize iter {iteration}: {description}",
        )
        run_dir = optimize_state.runs_root(project_root) / f"iter_{iteration:04d}"
        candidate_metrics = _run_benchmark_suite(
            project_root,
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
            run_dir=run_dir,
            timeout_seconds=timeout_seconds,
        )
        if candidate_metrics.get("status") in {"timeout", "crash"}:
            optimize_git.reset_to_commit(
                project_root, commit_sha=start_sha, cleanup_paths=[]
            )
            rejected_count += 1
            consecutive_rejections += 1
            optimize_state.append_result(
                results_path,
                iteration=iteration,
                commit=commit_sha[:12],
                status=str(candidate_metrics.get("status") or "rejected"),
                primary_metric_name=primary_metric_name,
                primary_metric_value="nan",
                description=description,
            )
            optimize_state.record_campaign_event(
                memory_path,
                commit=commit_sha[:12],
                primary_metric_name=primary_metric_name,
                primary_metric_value="nan",
                status=str(candidate_metrics.get("status") or "rejected"),
                description=description,
            )
            state_payload["iteration"] = iteration
            state_payload["rejected_count"] = rejected_count
            state_payload["consecutive_rejections"] = consecutive_rejections
            optimize_state.write_state(state_path, state_payload)
            continue

        verdict = _evaluate_candidate(
            benchmark_payload,
            baseline_metrics=(
                state_payload.get("baseline_metrics")
                if isinstance(state_payload.get("baseline_metrics"), dict)
                else {}
            ),
            incumbent_metrics=(
                state_payload.get("incumbent_metrics")
                if isinstance(state_payload.get("incumbent_metrics"), dict)
                else {}
            ),
            candidate_metrics=candidate_metrics,
        )
        candidate_primary = (
            candidate_metrics.get("summary_metrics", {}).get(primary_metric_name)
            if isinstance(candidate_metrics.get("summary_metrics"), dict)
            else None
        )
        if verdict.get("accepted"):
            accepted_count += 1
            consecutive_rejections = 0
            state_payload["incumbent_commit"] = commit_sha
            state_payload["incumbent_metrics"] = candidate_metrics
            optimize_state.append_result(
                results_path,
                iteration=iteration,
                commit=commit_sha[:12],
                status="accepted",
                primary_metric_name=primary_metric_name,
                primary_metric_value=(
                    candidate_primary if candidate_primary is not None else "nan"
                ),
                description=description,
            )
            optimize_state.record_campaign_event(
                memory_path,
                commit=commit_sha[:12],
                primary_metric_name=primary_metric_name,
                primary_metric_value=(
                    candidate_primary if candidate_primary is not None else "nan"
                ),
                status="accepted",
                description=description,
            )
        else:
            optimize_git.reset_to_commit(
                project_root, commit_sha=start_sha, cleanup_paths=[]
            )
            rejected_count += 1
            consecutive_rejections += 1
            optimize_state.append_result(
                results_path,
                iteration=iteration,
                commit=commit_sha[:12],
                status=str(verdict.get("status") or "rejected"),
                primary_metric_name=primary_metric_name,
                primary_metric_value=(
                    candidate_primary if candidate_primary is not None else "nan"
                ),
                description=description,
            )
            optimize_state.record_campaign_event(
                memory_path,
                commit=commit_sha[:12],
                primary_metric_name=primary_metric_name,
                primary_metric_value=(
                    candidate_primary if candidate_primary is not None else "nan"
                ),
                status=str(verdict.get("status") or "rejected"),
                description=f"{description} [{verdict.get('reason')}]",
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
