from __future__ import annotations

import argparse
import copy
from collections.abc import Callable
import fnmatch
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from fermilink.cli import optimize_git, optimize_prompts, optimize_state


def _cli():
    from fermilink import cli

    return cli


BENCHMARK_LAUNCHER_TAG = "benchmark_launcher"
BENCHMARK_LAUNCHER_TOKEN_RE = re.compile(
    rf"<{BENCHMARK_LAUNCHER_TAG}>\s*(.*?)\s*</{BENCHMARK_LAUNCHER_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
BENCHMARK_INFRA_FAILURE_STATUSES = {
    "timeout",
    "crash",
    "pid_issue",
    "slurm_failure",
    "slurm_poll_error",
    "slurm_poll_unavailable",
    "missing_result_source",
    "missing_result_json",
    "result_collect_timeout",
    "result_collect_crash",
}
DEFAULT_BENCHMARK_LAUNCHER_MAX_ATTEMPTS = 3
QUICK_DEFAULT_MAX_ITERATIONS = 30
QUICK_DEFAULT_STOP_ON_CONSECUTIVE_REJECTIONS = 8
QUICK_DEFAULT_WORKER_MAX_ITERATIONS = 8
QUICK_DEFAULT_WORKER_WAIT_SECONDS = 1
QUICK_DEFAULT_WORKER_MAX_WAIT_SECONDS = 900
QUICK_DEFAULT_WORKER_PID_STALL_SECONDS = 300
QUICK_DEFAULT_TIMEOUT_SECONDS = 900
QUICK_DEFAULT_MIN_RELATIVE_IMPROVEMENT = 0.01
BENCHMARK_SPLIT_KEY = "split"
BENCHMARK_SPLIT_TRAIN_CASE_IDS_KEY = "train_case_ids"
CORRECTNESS_MODE_RUNNER_ONLY = "runner_only"
CORRECTNESS_MODE_FIELD_TOLERANCES = "field_tolerances"
CORRECTNESS_MODE_VALUES = {
    CORRECTNESS_MODE_RUNNER_ONLY,
    CORRECTNESS_MODE_FIELD_TOLERANCES,
}
LEGACY_SCF_CORRECTNESS_KEYS = {
    "max_abs_energy_delta_hartree",
    "max_abs_dm_rms_delta",
    "max_abs_mo_energy_rms_delta",
}
FIELD_TOLERANCE_DELTA_KEY_MAP = {
    "abs_delta": ("abs_delta", "max_abs_delta"),
    "rms_delta": ("rms_delta", "max_rms_delta"),
    "relative_delta": ("relative_delta", "max_relative_delta"),
}
FIELD_TOLERANCE_COMPARISONS = set(FIELD_TOLERANCE_DELTA_KEY_MAP)
FIELD_PATH_MISSING = object()
QUICK_SOURCE_CODE_EXTENSIONS = (
    ".py",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".f",
    ".f90",
    ".f95",
    ".f03",
    ".f08",
    ".go",
    ".rs",
    ".java",
    ".jl",
    ".m",
    ".r",
    ".lua",
)
QUICK_REFERENCE_EXAMPLES = {
    "python": (
        "python-pyscf-scf-benchmark.yaml",
        "python-pyscf-scf-bench.py",
    ),
    "cpp": (
        "cpp-lammps-tip4p-force-eval-benchmark.yaml",
        "cpp-lammps-tip4p-force-eval-bench.sh",
    ),
    "cmake": (
        "cpp-lammps-tip4p-force-eval-benchmark.yaml",
        "cpp-lammps-tip4p-force-eval-bench.sh",
    ),
    "fortran": (
        "fortran-quantum-espresso-scf-benchmark.yaml",
        "fortran-quantum-espresso-scf-bench.sh",
    ),
}
GOAL_VALIDATION_CACHE_STATE_KEY = "goal_validation_cache"
GOAL_VALIDATION_CACHE_MAX_ENTRIES = 32
GOAL_ALLOW_RUNNER_ONLY_KEY = "allow_runner_only"
AUTO_CORRECTNESS_SKIP_CASE_FIELDS = {
    "id",
    "converged",
    "wall_seconds",
    "total_seconds",
    "error",
}
AUTO_FIELD_TOLERANCE_RELATIVE_DELTA = 1.0e-4
AUTO_FIELD_TOLERANCE_ABS_DELTA_FLOOR = 1.0e-8
AUTO_FIELD_TOLERANCE_MAX_FIELDS = 16


def _dict_clone(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return copy.deepcopy(payload)


def _normalize_runtime_env(raw_env: object) -> dict[str, str]:
    if not isinstance(raw_env, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in raw_env.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        normalized[key] = str(raw_value if raw_value is not None else "")
    return normalized


def _safe_positive_int(raw: object, *, default: int, allow_zero: bool = False) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if allow_zero:
        return value if value >= 0 else default
    return value if value > 0 else default


def _safe_non_negative_float(raw: object, *, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value < 0:
        return default
    return value


def _quick_reference_search_roots(project_root: Path) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    candidates = [
        project_root / "scripts",
        Path(__file__).resolve().parents[3] / "scripts",
    ]
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        roots.append(candidate)
    return roots


def _load_quick_reference_template(
    project_root: Path,
    *,
    language: str,
) -> dict[str, Any]:
    reference = QUICK_REFERENCE_EXAMPLES.get(language)
    if not reference:
        return {}
    benchmark_name, runner_name = reference
    project_scripts_root = project_root / "scripts"
    for scripts_root in _quick_reference_search_roots(project_root):
        benchmark_path = scripts_root / benchmark_name
        runner_path = scripts_root / runner_name
        if not benchmark_path.is_file():
            continue
        try:
            benchmark_payload = yaml.safe_load(
                benchmark_path.read_text(encoding="utf-8")
            )
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(benchmark_payload, dict):
            continue
        source = "project"
        try:
            if scripts_root.resolve() != project_scripts_root.resolve():
                source = "builtin"
        except OSError:
            if str(scripts_root) != str(project_scripts_root):
                source = "builtin"
        return {
            "source": source,
            "language": language,
            "benchmark_path": str(benchmark_path),
            "runner_path": str(runner_path),
            "benchmark_rel": optimize_state.safe_relative(benchmark_path, project_root),
            "runner_rel": optimize_state.safe_relative(runner_path, project_root),
            "benchmark_name": benchmark_name,
            "runner_name": runner_name,
            "benchmark": benchmark_payload,
        }
    return {}


def _template_case_hints(template_benchmark: dict[str, Any]) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    cases = template_benchmark.get("cases")
    if not isinstance(cases, list):
        return hints
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            continue
        case_id = str(raw_case.get("id") or "").strip()
        preview = str(raw_case.get("command_preview") or "").strip()
        if not preview:
            command = _normalize_string_command_list(raw_case.get("command"))
            if command:
                preview = shlex.join(command)
        if not preview:
            continue
        hints.append({"id": case_id, "command_preview": preview})
        if len(hints) >= 8:
            break
    return hints


def _quick_objective_from_template(
    template_benchmark: dict[str, Any],
) -> dict[str, Any]:
    objective: dict[str, Any] = {
        "primary_metric": "weighted_median_wall_seconds",
        "direction": "minimize",
        "min_relative_improvement": QUICK_DEFAULT_MIN_RELATIVE_IMPROVEMENT,
    }
    controller = _dict_clone(template_benchmark.get("controller"))
    template_objective = _dict_clone(controller.get("objective"))
    if template_objective:
        for key in ("min_relative_improvement", "tie_relative_tolerance"):
            if key in template_objective:
                objective[key] = copy.deepcopy(template_objective[key])
    direction = str(objective.get("direction") or "minimize").strip().lower()
    if direction not in {"minimize", "maximize"}:
        direction = "minimize"
    min_relative_improvement = _safe_non_negative_float(
        objective.get("min_relative_improvement"),
        default=QUICK_DEFAULT_MIN_RELATIVE_IMPROVEMENT,
    )
    tie_relative_tolerance = _safe_non_negative_float(
        objective.get("tie_relative_tolerance"),
        default=0.0,
    )
    objective["primary_metric"] = "weighted_median_wall_seconds"
    objective["direction"] = direction
    objective["min_relative_improvement"] = min_relative_improvement
    if tie_relative_tolerance > 0:
        objective["tie_relative_tolerance"] = tie_relative_tolerance
    else:
        objective.pop("tie_relative_tolerance", None)
    return objective


def _normalize_correctness_mode(raw_mode: object) -> str:
    mode = str(raw_mode or "").strip().lower()
    if mode in CORRECTNESS_MODE_VALUES:
        return mode
    return ""


def _resolve_correctness_mode(correctness: dict[str, Any]) -> str:
    explicit = _normalize_correctness_mode(correctness.get("mode"))
    if explicit:
        return explicit
    field_tolerances = correctness.get("field_tolerances")
    if isinstance(field_tolerances, list) and field_tolerances:
        return CORRECTNESS_MODE_FIELD_TOLERANCES
    return CORRECTNESS_MODE_RUNNER_ONLY


def _quick_correctness_mode_from_template(
    template_correctness: dict[str, Any],
    *,
    allow_template_mode: bool,
) -> str:
    explicit = _normalize_correctness_mode(template_correctness.get("mode"))
    if allow_template_mode and explicit:
        return explicit
    # Quick-mode autogen should remain generic unless an explicit project-local
    # template requests otherwise.
    return CORRECTNESS_MODE_RUNNER_ONLY


def _quick_default_field_tolerances(
    payload: object,
) -> list[dict[str, Any]]:
    tolerances: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return tolerances
    for item in payload:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        if not field:
            continue
        spec: dict[str, Any] = {"field": field}
        for key in (
            "abs_delta",
            "rms_delta",
            "relative_delta",
            "max_abs_delta",
            "max_rms_delta",
            "max_relative_delta",
            "comparison",
            "label",
        ):
            if key in item:
                spec[key] = copy.deepcopy(item[key])
        tolerances.append(spec)
    return tolerances


def _quick_correctness_from_template(
    template_benchmark: dict[str, Any],
    *,
    allow_template_mode: bool,
) -> dict[str, Any]:
    template_correctness = _dict_clone(template_benchmark.get("correctness"))
    mode = _quick_correctness_mode_from_template(
        template_correctness,
        allow_template_mode=allow_template_mode,
    )
    correctness: dict[str, Any] = {
        "mode": mode,
        "require_all_cases_converged": bool(
            template_correctness.get("require_all_cases_converged", True)
        ),
    }
    if mode == CORRECTNESS_MODE_FIELD_TOLERANCES:
        tolerances = _quick_default_field_tolerances(
            template_correctness.get("field_tolerances")
        )
        if tolerances:
            correctness["field_tolerances"] = tolerances
    return correctness


def _quick_controller_defaults(template_benchmark: dict[str, Any]) -> dict[str, Any]:
    controller = _dict_clone(template_benchmark.get("controller"))
    defaults: dict[str, Any] = {
        "timeout_seconds": QUICK_DEFAULT_TIMEOUT_SECONDS,
        "warmup_runs": 0,
        "measured_runs": 1,
    }
    defaults["timeout_seconds"] = _safe_positive_int(
        controller.get("timeout_seconds"),
        default=QUICK_DEFAULT_TIMEOUT_SECONDS,
    )
    defaults["warmup_runs"] = _safe_positive_int(
        controller.get("warmup_runs"),
        default=0,
        allow_zero=True,
    )
    defaults["measured_runs"] = _safe_positive_int(
        controller.get("measured_runs"),
        default=1,
    )
    for key in ("aggregation", "secondary_objectives", "reject_on"):
        if key in controller:
            defaults[key] = copy.deepcopy(controller[key])
    return defaults


def _runtime_config(benchmark: dict[str, Any]) -> dict[str, Any]:
    runtime = benchmark.get("runtime")
    return runtime if isinstance(runtime, dict) else {}


def _runtime_mode(runtime: dict[str, Any]) -> str:
    mode = str(runtime.get("mode") or "direct").strip().lower()
    if mode in {"", "direct", "sync", "local"}:
        return "direct"
    if mode in {"submit_poll", "async", "async_poll"}:
        return "submit_poll"
    return mode


def _validate_optional_number(
    *,
    raw: object,
    label: str,
    allow_zero: bool,
) -> None:
    cli = _cli()
    if raw is None:
        return
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError(f"{label} must be a number.") from exc
    if allow_zero:
        if value < 0:
            raise cli.PackageError(f"{label} must be >= 0.")
    elif value <= 0:
        raise cli.PackageError(f"{label} must be > 0.")


def _normalize_string_command_list(payload: object) -> list[str]:
    if isinstance(payload, str):
        try:
            parsed = shlex.split(payload)
        except ValueError:
            return []
        return [item for item in parsed if isinstance(item, str) and item.strip()]
    if not isinstance(payload, list):
        return []
    return [
        str(item).strip()
        for item in payload
        if isinstance(item, str) and str(item).strip()
    ]


def _validate_field_tolerances_config(
    payload: object,
    *,
    context_label: str,
) -> None:
    cli = _cli()
    if not isinstance(payload, list) or not payload:
        raise cli.PackageError(
            f"{context_label}.field_tolerances must be a non-empty list."
        )
    for index, spec in enumerate(payload, start=1):
        spec_label = f"{context_label}.field_tolerances[{index}]"
        if not isinstance(spec, dict):
            raise cli.PackageError(f"{spec_label} must be an object.")
        field = str(spec.get("field") or "").strip()
        if not field:
            raise cli.PackageError(f"{spec_label}.field is required.")
        threshold_count = 0
        for aliases in FIELD_TOLERANCE_DELTA_KEY_MAP.values():
            metric_has_threshold = False
            for key in aliases:
                if key not in spec:
                    continue
                raw_value = spec.get(key)
                if raw_value is None:
                    continue
                if isinstance(raw_value, bool):
                    raise cli.PackageError(f"{spec_label}.{key} must be a number.")
                _validate_optional_number(
                    raw=raw_value,
                    label=f"{spec_label}.{key}",
                    allow_zero=True,
                )
                metric_has_threshold = True
            if metric_has_threshold:
                threshold_count += 1
        if threshold_count == 0:
            allowed = ", ".join(sorted(FIELD_TOLERANCE_COMPARISONS))
            raise cli.PackageError(
                f"{spec_label} must set at least one threshold ({allowed})."
            )
        comparison = str(spec.get("comparison") or "").strip().lower()
        if comparison and comparison not in FIELD_TOLERANCE_COMPARISONS:
            allowed = ", ".join(sorted(FIELD_TOLERANCE_COMPARISONS))
            raise cli.PackageError(
                f"{spec_label}.comparison must be one of: {allowed}."
            )


def _validate_correctness_schema(payload: dict[str, Any]) -> None:
    cli = _cli()
    correctness = payload.get("correctness")
    if correctness is None:
        return
    if not isinstance(correctness, dict):
        raise cli.PackageError("Benchmark correctness block must be an object.")

    mode_raw = correctness.get("mode")
    mode_explicit = _normalize_correctness_mode(mode_raw)
    if mode_raw is not None and not mode_explicit:
        allowed = ", ".join(sorted(CORRECTNESS_MODE_VALUES))
        raise cli.PackageError(
            "Benchmark correctness.mode must be one of: " f"{allowed}."
        )
    legacy_scf_keys = sorted(
        key for key in LEGACY_SCF_CORRECTNESS_KEYS if key in correctness
    )
    if legacy_scf_keys:
        keys_text = ", ".join(legacy_scf_keys)
        raise cli.PackageError(
            "Legacy SCF correctness keys are no longer supported "
            f"({keys_text}). Use correctness.mode=field_tolerances instead."
        )
    mode = mode_explicit or _resolve_correctness_mode(correctness)

    require_all = correctness.get("require_all_cases_converged")
    if require_all is not None and not isinstance(require_all, bool):
        raise cli.PackageError(
            "Benchmark correctness.require_all_cases_converged must be true/false."
        )
    allow_runner_only = correctness.get(GOAL_ALLOW_RUNNER_ONLY_KEY)
    if allow_runner_only is not None and not isinstance(allow_runner_only, bool):
        raise cli.PackageError(
            f"Benchmark correctness.{GOAL_ALLOW_RUNNER_ONLY_KEY} must be true/false."
        )

    if mode == CORRECTNESS_MODE_FIELD_TOLERANCES:
        _validate_field_tolerances_config(
            correctness.get("field_tolerances"),
            context_label="Benchmark correctness",
        )


def _benchmark_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        return []
    return [item for item in raw_cases if isinstance(item, dict)]


def _benchmark_case_id(case: dict[str, Any]) -> str:
    return str(case.get("id") or "").strip()


def _benchmark_case_ids(cases: list[dict[str, Any]]) -> list[str]:
    return [_benchmark_case_id(case) for case in cases if _benchmark_case_id(case)]


def _split_train_case_ids(payload: dict[str, Any]) -> list[str]:
    split = payload.get(BENCHMARK_SPLIT_KEY)
    if split is None:
        return []
    if not isinstance(split, dict):
        return []
    train_case_ids = split.get(BENCHMARK_SPLIT_TRAIN_CASE_IDS_KEY)
    if not isinstance(train_case_ids, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_case_id in train_case_ids:
        case_id = str(raw_case_id or "").strip()
        if not case_id or case_id in seen:
            continue
        seen.add(case_id)
        normalized.append(case_id)
    return normalized


def _validate_case_split(payload: dict[str, Any]) -> None:
    cli = _cli()
    split = payload.get(BENCHMARK_SPLIT_KEY)
    if split is None:
        return
    if not isinstance(split, dict):
        raise cli.PackageError("Benchmark split block must be an object when provided.")
    raw_train = split.get(BENCHMARK_SPLIT_TRAIN_CASE_IDS_KEY)
    if not isinstance(raw_train, list) or not raw_train:
        raise cli.PackageError(
            "Benchmark split.train_case_ids must be a non-empty list."
        )
    train_case_ids = _split_train_case_ids(payload)
    if not train_case_ids:
        raise cli.PackageError(
            "Benchmark split.train_case_ids must include at least one non-empty case id."
        )
    train_case_id_set = set(train_case_ids)
    cases = _benchmark_cases(payload)
    if not cases:
        raise cli.PackageError(
            "Benchmark split requires a non-empty top-level cases list."
        )
    case_ids: list[str] = []
    seen_case_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        case_id = _benchmark_case_id(case)
        if not case_id:
            raise cli.PackageError(
                "Benchmark split requires every case to define a non-empty id "
                f"(missing at cases[{index}])."
            )
        if case_id in seen_case_ids:
            raise cli.PackageError(
                f"Benchmark split found duplicate case id: {case_id}."
            )
        seen_case_ids.add(case_id)
        case_ids.append(case_id)
    missing_train = [
        case_id for case_id in train_case_ids if case_id not in seen_case_ids
    ]
    if missing_train:
        missing_text = ", ".join(missing_train)
        raise cli.PackageError(
            "Benchmark split.train_case_ids references unknown cases: "
            f"{missing_text}."
        )
    test_case_ids = [
        case_id for case_id in case_ids if case_id not in train_case_id_set
    ]
    if not test_case_ids:
        raise cli.PackageError(
            "Benchmark split must leave at least one controller-only test case."
        )


def _partition_benchmark_payload_by_split(
    benchmark_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    train_case_ids = _split_train_case_ids(benchmark_payload)
    if not train_case_ids:
        return (
            copy.deepcopy(benchmark_payload),
            copy.deepcopy(benchmark_payload),
            {
                "enabled": False,
                "train_case_ids": [],
                "test_case_ids": [],
            },
        )

    train_case_id_set = set(train_case_ids)
    cases = _benchmark_cases(benchmark_payload)
    train_cases: list[dict[str, Any]] = []
    test_cases: list[dict[str, Any]] = []
    for case in cases:
        case_id = _benchmark_case_id(case)
        if case_id in train_case_id_set:
            train_cases.append(copy.deepcopy(case))
        else:
            test_cases.append(copy.deepcopy(case))

    worker_payload = copy.deepcopy(benchmark_payload)
    worker_payload["cases"] = train_cases
    worker_payload.pop(BENCHMARK_SPLIT_KEY, None)

    controller_payload = copy.deepcopy(benchmark_payload)
    controller_payload["cases"] = test_cases
    controller_payload.pop(BENCHMARK_SPLIT_KEY, None)

    return (
        worker_payload,
        controller_payload,
        {
            "enabled": True,
            "train_case_ids": train_case_ids,
            "test_case_ids": _benchmark_case_ids(test_cases),
        },
    )


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
    mode = _runtime_mode(runtime)
    if mode not in {"direct", "submit_poll"}:
        raise cli.PackageError(
            "Benchmark runtime.mode must be `direct` or `submit_poll`."
        )
    result_command = runtime.get("result_command")
    if result_command is not None and (
        not isinstance(result_command, list)
        or not all(isinstance(item, str) and item.strip() for item in result_command)
    ):
        raise cli.PackageError(
            "Benchmark runtime.result_command must be a non-empty string list."
        )
    _validate_optional_number(
        raw=runtime.get("submission_timeout_seconds"),
        label="Benchmark runtime.submission_timeout_seconds",
        allow_zero=False,
    )
    _validate_optional_number(
        raw=runtime.get("poll_interval_seconds"),
        label="Benchmark runtime.poll_interval_seconds",
        allow_zero=False,
    )
    _validate_optional_number(
        raw=runtime.get("max_poll_seconds"),
        label="Benchmark runtime.max_poll_seconds",
        allow_zero=False,
    )
    _validate_optional_number(
        raw=runtime.get("pid_stall_seconds"),
        label="Benchmark runtime.pid_stall_seconds",
        allow_zero=True,
    )
    launcher_attempts_raw = runtime.get("launcher_max_attempts")
    if launcher_attempts_raw is not None:
        try:
            launcher_attempts = int(launcher_attempts_raw)
        except (TypeError, ValueError) as exc:
            raise cli.PackageError(
                "Benchmark runtime.launcher_max_attempts must be an integer."
            ) from exc
        if launcher_attempts < 1:
            raise cli.PackageError(
                "Benchmark runtime.launcher_max_attempts must be >= 1."
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
    _validate_correctness_schema(payload)
    _validate_case_split(payload)
    if mode == "submit_poll":
        result_json_path = str(runtime.get("result_json_path") or "").strip()
        artifacts = payload.get("artifacts")
        artifact_json_path = ""
        if isinstance(artifacts, dict):
            artifact_json_path = str(artifacts.get("latest_metrics_json") or "").strip()
        has_result_json = bool(result_json_path or artifact_json_path)
        has_result_command = isinstance(result_command, list) and bool(result_command)
        if not has_result_json and not has_result_command:
            raise cli.PackageError(
                "Benchmark runtime.mode=submit_poll requires either "
                "`runtime.result_json_path`, `artifacts.latest_metrics_json`, or "
                "`runtime.result_command`."
            )
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


def _objective_incumbent_relative_primary(benchmark: dict[str, Any]) -> bool:
    objective = _objective_config(benchmark)
    return bool(objective.get("incumbent_relative_primary"))


def _objective_primary_for_context(
    benchmark: dict[str, Any],
    *,
    incumbent_metrics: dict[str, Any],
    primary_metric_name: str,
) -> float | None:
    if not _objective_incumbent_relative_primary(benchmark):
        return _metric_value(incumbent_metrics, primary_metric_name)
    return 1.0 if incumbent_metrics else None


def _normalize_incumbent_metrics_for_state(
    benchmark: dict[str, Any],
    *,
    primary_metric_name: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    if not _objective_incumbent_relative_primary(benchmark):
        return metrics
    normalized = copy.deepcopy(metrics)
    summary = normalized.get("summary_metrics")
    if not isinstance(summary, dict):
        summary = {}
        normalized["summary_metrics"] = summary
    summary[primary_metric_name] = 1.0
    return normalized


def _campaign_config(benchmark: dict[str, Any]) -> dict[str, Any]:
    campaign = benchmark.get("campaign")
    return campaign if isinstance(campaign, dict) else {}


def _correctness_config(benchmark: dict[str, Any]) -> dict[str, Any]:
    correctness = benchmark.get("correctness")
    return correctness if isinstance(correctness, dict) else {}


def _correctness_allows_runner_only(correctness: dict[str, Any]) -> bool:
    return bool(correctness.get(GOAL_ALLOW_RUNNER_ONLY_KEY))


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
        if any(
            line.strip() == cli.LOOP_DONE_TOKEN for line in assistant_text.splitlines()
        ):
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
            if (
                pending_slurm_jobs
                and not session_commands._slurm_wait_tools_available()
            ):
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
                    f"{pid_stall_seconds:.1f}s" if pid_stall_seconds > 0 else "disabled"
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
                next_status_log = (
                    started + session_commands.POLL_STATUS_HEARTBEAT_SECONDS
                )
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
                                "pending slurm job(s): " + ", ".join(pending_slurm_jobs)
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
                            issue_text = session_commands._format_slurm_issues(
                                slurm_issues
                            )
                            waiting_on: list[str] = []
                            if alive:
                                waiting_on.append(
                                    "still-running pid(s): "
                                    + ", ".join(str(pid) for pid in alive)
                                )
                            suffix = f"; {'; '.join(waiting_on)}" if waiting_on else ""
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


def _max_abs_difference(left: object, right: object) -> float:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right))
    left_values = _flatten_numbers(left)
    right_values = _flatten_numbers(right)
    if not left_values or len(left_values) != len(right_values):
        return float("inf")
    return max(
        abs(left_value - right_value)
        for left_value, right_value in zip(left_values, right_values)
    )


def _relative_difference(left: object, right: object) -> float:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        baseline = abs(float(left))
        delta = abs(float(left) - float(right))
        if baseline <= 1.0e-12:
            return 0.0 if delta <= 1.0e-12 else float("inf")
        return delta / baseline
    left_values = _flatten_numbers(left)
    right_values = _flatten_numbers(right)
    if not left_values or len(left_values) != len(right_values):
        return float("inf")
    rms_delta = _rms_difference(left, right)
    baseline_rms = math.sqrt(
        sum(value * value for value in left_values) / len(left_values)
    )
    if baseline_rms <= 1.0e-12:
        return 0.0 if rms_delta <= 1.0e-12 else float("inf")
    return rms_delta / baseline_rms


def _value_at_field_path(payload: object, field_path: str) -> object:
    current = payload
    tokens = [
        token.strip() for token in str(field_path or "").split(".") if token.strip()
    ]
    if not tokens:
        return FIELD_PATH_MISSING
    for token in tokens:
        if isinstance(current, dict):
            if token not in current:
                return FIELD_PATH_MISSING
            current = current[token]
            continue
        if isinstance(current, list):
            try:
                index = int(token)
            except (TypeError, ValueError):
                return FIELD_PATH_MISSING
            if index < 0 or index >= len(current):
                return FIELD_PATH_MISSING
            current = current[index]
            continue
        return FIELD_PATH_MISSING
    return current


def _field_tolerance_threshold(spec: dict[str, Any], metric_name: str) -> float | None:
    aliases = FIELD_TOLERANCE_DELTA_KEY_MAP.get(metric_name, ())
    for key in aliases:
        value = spec.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
    return None


def _field_tolerance_case_errors(
    *,
    case_id: str,
    incumbent_case: dict[str, Any],
    candidate_case: dict[str, Any],
    tolerance_specs: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    for spec in tolerance_specs:
        field_path = str(spec.get("field") or "").strip()
        if not field_path:
            continue
        label = str(spec.get("label") or "").strip() or field_path
        incumbent_value = _value_at_field_path(incumbent_case, field_path)
        candidate_value = _value_at_field_path(candidate_case, field_path)
        if (
            incumbent_value is FIELD_PATH_MISSING
            or candidate_value is FIELD_PATH_MISSING
        ):
            errors.append(
                f"case {case_id} missing field `{field_path}` for correctness check"
            )
            continue
        requested_comparison = str(spec.get("comparison") or "").strip().lower()
        if requested_comparison and requested_comparison in FIELD_TOLERANCE_COMPARISONS:
            comparison_names = [requested_comparison]
        else:
            comparison_names = [
                name
                for name in FIELD_TOLERANCE_DELTA_KEY_MAP
                if _field_tolerance_threshold(spec, name) is not None
            ]
        for comparison_name in comparison_names:
            threshold = _field_tolerance_threshold(spec, comparison_name)
            if threshold is None:
                continue
            if comparison_name == "abs_delta":
                diff = _max_abs_difference(incumbent_value, candidate_value)
            elif comparison_name == "rms_delta":
                diff = _rms_difference(incumbent_value, candidate_value)
            else:
                diff = _relative_difference(incumbent_value, candidate_value)
            if diff > threshold:
                errors.append(
                    (
                        f"case {case_id} {label} {comparison_name} "
                        f"exceeds threshold ({diff:.6g} > {threshold:.6g})"
                    )
                )
    return errors


def _numeric_case_field_scale(value: object) -> float | None:
    flattened = _flatten_numbers(value)
    if not flattened:
        return None
    return max(abs(item) for item in flattened)


def _infer_field_tolerances_from_baseline_metrics(
    baseline_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    case_rows = baseline_metrics.get("cases")
    if not isinstance(case_rows, list):
        return []
    scales_by_field: dict[str, float] = {}
    for case in case_rows:
        if not isinstance(case, dict):
            continue
        for field_name, value in case.items():
            normalized_field = str(field_name or "").strip()
            if not normalized_field or normalized_field in AUTO_CORRECTNESS_SKIP_CASE_FIELDS:
                continue
            scale = _numeric_case_field_scale(value)
            if scale is None:
                continue
            previous = scales_by_field.get(normalized_field)
            if previous is None or scale > previous:
                scales_by_field[normalized_field] = scale
    inferred: list[dict[str, Any]] = []
    for field_name in sorted(scales_by_field)[:AUTO_FIELD_TOLERANCE_MAX_FIELDS]:
        scale = scales_by_field[field_name]
        abs_delta = max(
            AUTO_FIELD_TOLERANCE_ABS_DELTA_FLOOR,
            scale * AUTO_FIELD_TOLERANCE_RELATIVE_DELTA,
        )
        inferred.append(
            {
                "field": field_name,
                "max_abs_delta": abs_delta,
                "max_relative_delta": AUTO_FIELD_TOLERANCE_RELATIVE_DELTA,
                "label": field_name,
            }
        )
    return inferred


def _effective_correctness_benchmark_payload(
    benchmark_payload: dict[str, Any],
    *,
    baseline_metrics: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    correctness = _correctness_config(benchmark_payload)
    mode = _resolve_correctness_mode(correctness)
    if mode != CORRECTNESS_MODE_RUNNER_ONLY:
        return benchmark_payload, {"upgraded": False, "reason": "mode_not_runner_only"}
    if _correctness_allows_runner_only(correctness):
        return benchmark_payload, {"upgraded": False, "reason": "runner_only_allowed"}
    inferred_tolerances = _infer_field_tolerances_from_baseline_metrics(baseline_metrics)
    if not inferred_tolerances:
        return benchmark_payload, {"upgraded": False, "reason": "no_numeric_fields"}
    effective_payload = copy.deepcopy(benchmark_payload)
    effective_correctness = _correctness_config(effective_payload)
    if not isinstance(effective_correctness, dict):
        effective_correctness = {}
        effective_payload["correctness"] = effective_correctness
    effective_correctness["mode"] = CORRECTNESS_MODE_FIELD_TOLERANCES
    effective_correctness["field_tolerances"] = inferred_tolerances
    effective_correctness["auto_inferred_from_baseline"] = True
    return effective_payload, {
        "upgraded": True,
        "reason": "auto_field_tolerances",
        "field_tolerance_count": len(inferred_tolerances),
    }


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
    run_dir: Path | None = None,
) -> list[str]:
    replacements = {
        "{benchmark}": str(benchmark_path),
        "{project_root}": str(project_root),
    }
    if run_dir is not None:
        replacements["{run_dir}"] = str(run_dir)
    expanded: list[str] = []
    for token in command:
        rendered = str(token)
        for key, value in replacements.items():
            rendered = rendered.replace(key, value)
        expanded.append(rendered)
    return expanded


def _resolve_runtime_result_json_path(
    *,
    benchmark_payload: dict[str, Any],
    benchmark_path: Path,
    project_root: Path,
    run_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> Path | None:
    runtime_effective = (
        runtime if isinstance(runtime, dict) else _runtime_config(benchmark_payload)
    )
    artifacts = benchmark_payload.get("artifacts")
    raw_path = str(runtime_effective.get("result_json_path") or "").strip()
    if not raw_path and isinstance(artifacts, dict):
        raw_path = str(artifacts.get("latest_metrics_json") or "").strip()
    if not raw_path:
        return None
    expanded = _expand_runtime_command(
        [raw_path],
        benchmark_path=benchmark_path,
        project_root=project_root,
        run_dir=run_dir,
    )[0]
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path


def _resolve_runtime_result_command(
    *,
    benchmark_payload: dict[str, Any],
    benchmark_path: Path,
    project_root: Path,
    run_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> list[str]:
    runtime_effective = (
        runtime if isinstance(runtime, dict) else _runtime_config(benchmark_payload)
    )
    raw = runtime_effective.get("result_command")
    if not isinstance(raw, list):
        return []
    command = [item for item in raw if isinstance(item, str) and item.strip()]
    if not command:
        return []
    return _expand_runtime_command(
        command,
        benchmark_path=benchmark_path,
        project_root=project_root,
        run_dir=run_dir,
    )


def _benchmark_failure_payload(
    *,
    status: str,
    stdout_path: Path,
    stderr_path: Path,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "status": status,
        "summary_metrics": {},
        "cases": [],
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _validate_benchmark_output_payload(
    payload: dict[str, Any],
    *,
    benchmark_payload: dict[str, Any],
) -> None:
    cli = _cli()
    benchmark_id = payload.get("benchmark_id")
    if not isinstance(benchmark_id, str) or not benchmark_id.strip():
        raise cli.PackageError(
            "Benchmark command JSON payload must include a non-empty `benchmark_id`."
        )
    expected_benchmark_id = str(benchmark_payload.get("benchmark_id") or "").strip()
    if expected_benchmark_id and benchmark_id.strip() != expected_benchmark_id:
        raise cli.PackageError(
            "Benchmark command JSON payload benchmark_id does not match benchmark file "
            f"({benchmark_id.strip()} != {expected_benchmark_id})."
        )
    correctness_ok = payload.get("correctness_ok")
    if not isinstance(correctness_ok, bool):
        raise cli.PackageError(
            "Benchmark command JSON payload field `correctness_ok` must be true/false."
        )
    summary_metrics = payload.get("summary_metrics")
    if not isinstance(summary_metrics, dict):
        raise cli.PackageError(
            "Benchmark command JSON payload field `summary_metrics` must be an object."
        )
    primary_metric = str(
        _objective_config(benchmark_payload).get("primary_metric") or ""
    ).strip()
    if primary_metric:
        primary_value = summary_metrics.get(primary_metric)
        if isinstance(primary_value, bool) or not isinstance(primary_value, (int, float)):
            raise cli.PackageError(
                "Benchmark command JSON payload summary_metrics is missing numeric "
                f"primary metric `{primary_metric}`."
            )
    peak_rss_mb = summary_metrics.get("peak_rss_mb")
    if peak_rss_mb is not None and (
        isinstance(peak_rss_mb, bool) or not isinstance(peak_rss_mb, (int, float))
    ):
        raise cli.PackageError(
            "Benchmark command JSON payload summary_metrics.peak_rss_mb must be numeric."
        )

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise cli.PackageError(
            "Benchmark command JSON payload field `cases` must be a list."
        )
    expected_case_ids = set(_benchmark_case_ids(_benchmark_cases(benchmark_payload)))
    observed_case_ids: set[str] = set()
    for index, case in enumerate(raw_cases, start=1):
        if not isinstance(case, dict):
            raise cli.PackageError(
                f"Benchmark command JSON payload cases[{index}] must be an object."
            )
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            raise cli.PackageError(
                f"Benchmark command JSON payload cases[{index}] requires non-empty `id`."
            )
        if case_id in observed_case_ids:
            raise cli.PackageError(
                "Benchmark command JSON payload contains duplicate case id: "
                f"{case_id}."
            )
        observed_case_ids.add(case_id)
        converged = case.get("converged")
        if converged is not None and not isinstance(converged, bool):
            raise cli.PackageError(
                "Benchmark command JSON payload case "
                f"`{case_id}` field `converged` must be true/false."
            )
        for field_name in ("wall_seconds", "total_seconds"):
            field_value = case.get(field_name)
            if field_value is None:
                continue
            if isinstance(field_value, bool) or not isinstance(field_value, (int, float)):
                raise cli.PackageError(
                    "Benchmark command JSON payload case "
                    f"`{case_id}` field `{field_name}` must be numeric."
                )
        error_value = case.get("error")
        if error_value is not None and not isinstance(error_value, str):
            raise cli.PackageError(
                f"Benchmark command JSON payload case `{case_id}` field `error` must be a string."
            )
    if expected_case_ids:
        missing_ids = sorted(expected_case_ids - observed_case_ids)
        if missing_ids:
            raise cli.PackageError(
                "Benchmark command JSON payload missing cases from benchmark: "
                + ", ".join(missing_ids)
            )


def _parse_benchmark_stdout(
    stdout_text: str,
    *,
    benchmark_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    if isinstance(benchmark_payload, dict):
        _validate_benchmark_output_payload(payload, benchmark_payload=benchmark_payload)
    return payload


def _resolve_hpc_profile_key(args: argparse.Namespace) -> str:
    raw = getattr(args, "hpc_profile", None)
    if not isinstance(raw, str) or not raw.strip():
        return ""
    return str(Path(raw).expanduser().resolve())


def _runtime_launcher_max_attempts(runtime: dict[str, Any]) -> int:
    raw = runtime.get("launcher_max_attempts")
    if raw is None:
        return DEFAULT_BENCHMARK_LAUNCHER_MAX_ATTEMPTS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_BENCHMARK_LAUNCHER_MAX_ATTEMPTS
    return max(1, value)


def _extract_benchmark_launcher_cache(
    state_payload: dict[str, Any],
    *,
    hpc_profile_key: str,
) -> dict[str, Any] | None:
    launcher = state_payload.get("benchmark_launcher")
    if not isinstance(launcher, dict):
        return None
    command = _normalize_string_command_list(launcher.get("command_template"))
    if not command:
        return None
    cached_hpc_profile_key = str(launcher.get("hpc_profile_key") or "").strip()
    if cached_hpc_profile_key != hpc_profile_key:
        return None
    result_command = _normalize_string_command_list(
        launcher.get("result_command_template")
    )
    result_json_path = str(launcher.get("result_json_path_template") or "").strip()
    return {
        "command_template": command,
        "result_command_template": result_command,
        "result_json_path_template": result_json_path,
        "source": str(launcher.get("source") or "controller_agent"),
    }


def _write_benchmark_launcher_cache(
    state_payload: dict[str, Any],
    *,
    launcher: dict[str, Any],
    hpc_profile_key: str,
    source: str,
) -> None:
    state_payload["benchmark_launcher"] = {
        "command_template": list(launcher.get("command_template") or []),
        "result_command_template": list(launcher.get("result_command_template") or []),
        "result_json_path_template": str(
            launcher.get("result_json_path_template") or ""
        ).strip(),
        "hpc_profile_key": hpc_profile_key,
        "source": source,
        "updated_at_utc": optimize_state.utc_now_z(),
    }


def _clear_benchmark_launcher_cache(
    state_payload: dict[str, Any], *, reason: str
) -> None:
    state_payload.pop("benchmark_launcher", None)
    state_payload["benchmark_launcher_last_error"] = {
        "reason": reason,
        "updated_at_utc": optimize_state.utc_now_z(),
    }


def _runtime_override_from_launcher(launcher: dict[str, Any]) -> dict[str, Any]:
    override: dict[str, Any] = {
        "command": list(launcher.get("command_template") or []),
    }
    result_command = _normalize_string_command_list(
        launcher.get("result_command_template")
    )
    if result_command:
        override["result_command"] = result_command
    result_json_path = str(launcher.get("result_json_path_template") or "").strip()
    if result_json_path:
        override["result_json_path"] = result_json_path
    return override


def _append_benchmark_launcher_memory_note(
    memory_path: Path,
    *,
    event: str,
    launcher: dict[str, Any] | None,
    reason: str = "",
) -> None:
    try:
        content = memory_path.read_text(encoding="utf-8")
    except OSError:
        return
    timestamp = optimize_state.utc_now_z()
    if isinstance(launcher, dict):
        command = _normalize_string_command_list(launcher.get("command_template"))
        command_text = shlex.join(command) if command else "(missing command)"
        result_json_path = str(launcher.get("result_json_path_template") or "").strip()
        result_command = _normalize_string_command_list(
            launcher.get("result_command_template")
        )
        result_parts: list[str] = []
        if result_json_path:
            result_parts.append(f"result_json_path=`{result_json_path}`")
        if result_command:
            result_parts.append(f"result_command=`{shlex.join(result_command)}`")
        result_text = (
            "; ".join(result_parts) if result_parts else "result_source=runtime"
        )
        suffix = f"; reason={reason}" if reason else ""
        entry = (
            f"- [{timestamp}] benchmark launcher {event}: command=`{command_text}`; "
            f"{result_text}{suffix}"
        )
    else:
        suffix = f" ({reason})" if reason else ""
        entry = f"- [{timestamp}] benchmark launcher {event}{suffix}"
    updated = optimize_state._append_section_line(content, "### Progress log", entry)
    try:
        memory_path.write_text(updated, encoding="utf-8")
    except OSError:
        return


def _build_benchmark_launcher_prompt(
    *,
    benchmark_payload: dict[str, Any],
    benchmark_rel: str,
    memory_rel: str,
    run_rel: str,
    hpc_constraints_block: str,
) -> str:
    runtime = _runtime_config(benchmark_payload)
    benchmark_command = _normalize_string_command_list(runtime.get("command"))
    benchmark_command_json = json.dumps(benchmark_command, indent=2)
    runtime_result_path = str(runtime.get("result_json_path") or "").strip()
    result_path_note = (
        runtime_result_path
        if runtime_result_path
        else "use `artifacts.latest_metrics_json` or include `result_command`"
    )
    constraints_block = (
        f"{hpc_constraints_block}\n\n" if hpc_constraints_block.strip() else ""
    )
    return (
        "You are planning the authoritative benchmark submission launcher for FermiLink optimize.\n"
        "\n"
        f"{constraints_block}"
        f"Benchmark contract: `{benchmark_rel}`\n"
        f"Controller memory: `{memory_rel}`\n"
        f"Current run directory: `{run_rel}`\n"
        "\n"
        "Goal:\n"
        "- Produce a robust submission command for `runtime.mode=submit_poll`.\n"
        "- The submission command must submit benchmark execution and print either `<slurm_job_number>` or `<pid_number>`.\n"
        "- The submitted job must execute the authoritative benchmark payload command below.\n"
        "\n"
        "Authoritative benchmark payload command:\n"
        f"{benchmark_command_json}\n"
        "\n"
        "Allowed placeholders in launcher outputs:\n"
        "- `{benchmark}`\n"
        "- `{project_root}`\n"
        "- `{run_dir}`\n"
        "\n"
        "Respond with exactly one JSON object inside tags:\n"
        f'<{BENCHMARK_LAUNCHER_TAG}>{{"command": ["..."], "result_json_path": "... optional ...", "result_command": ["... optional ..."]}}</{BENCHMARK_LAUNCHER_TAG}>\n'
        "\n"
        "Rules:\n"
        "- `command` is required and must be a list of command tokens (no shell prose).\n"
        f"- If unsure about result retrieval, keep result source as runtime default ({result_path_note}).\n"
        "- Do not include extra text outside the tag.\n"
    )


def _extract_launcher_from_assistant_text(assistant_text: str) -> dict[str, Any] | None:
    match = BENCHMARK_LAUNCHER_TOKEN_RE.search(str(assistant_text or ""))
    if not match:
        return None
    raw_payload = str(match.group(1) or "").strip()
    if not raw_payload:
        return None
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    command = _normalize_string_command_list(payload.get("command"))
    if not command:
        return None
    result_command = _normalize_string_command_list(payload.get("result_command"))
    result_json_path = str(payload.get("result_json_path") or "").strip()
    return {
        "command_template": command,
        "result_command_template": result_command,
        "result_json_path_template": result_json_path,
    }


def _ensure_benchmark_launcher(
    project_root: Path,
    *,
    benchmark_path: Path,
    benchmark_payload: dict[str, Any],
    run_dir: Path,
    run_rel: str,
    benchmark_rel: str,
    memory_rel: str,
    hpc_constraints_block: str,
    memory_path: Path,
    state_path: Path,
    state_payload: dict[str, Any],
    hpc_profile_key: str,
    provider: str,
    provider_bin_override: str | None,
    sandbox_mode: str,
    sandbox_policy: str,
    model: str | None,
    reasoning_effort: str | None,
) -> tuple[dict[str, Any] | None, str]:
    cli = _cli()
    cached = _extract_benchmark_launcher_cache(
        state_payload,
        hpc_profile_key=hpc_profile_key,
    )
    if cached is not None:
        return cached, "cached"
    if isinstance(state_payload.get("benchmark_launcher"), dict):
        _clear_benchmark_launcher_cache(
            state_payload,
            reason="cached launcher invalid for current context",
        )
        optimize_state.write_state(state_path, state_payload)

    prompt = _build_benchmark_launcher_prompt(
        benchmark_payload=benchmark_payload,
        benchmark_rel=benchmark_rel,
        memory_rel=memory_rel,
        run_rel=run_rel,
        hpc_constraints_block=hpc_constraints_block,
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_run_text(run_dir, "benchmark_launcher_prompt.txt", prompt)
    planner_agents_md = (
        "# FermiLink Optimize Benchmark Launcher Planning Mode\n"
        "\n"
        "You are planning benchmark launcher commands for authoritative optimize benchmarking.\n"
        "\n"
        "Rules:\n"
        "- Do not edit any files.\n"
        "- Return only the requested launcher tag payload.\n"
    )
    with optimize_git.temporary_optimize_agents(
        project_root,
        provider=provider,
        content=planner_agents_md,
    ):
        planner_result = cli._run_exec_chat_turn(
            repo_dir=project_root,
            prompt=prompt,
            sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
            provider_bin_override=provider_bin_override,
            provider=provider,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    assistant_text = str(planner_result.get("assistant_text") or "")
    launcher = _extract_launcher_from_assistant_text(assistant_text)
    _write_run_json(
        run_dir,
        "benchmark_launcher_plan.json",
        {
            "return_code": int(planner_result.get("return_code") or 0),
            "assistant_text": assistant_text,
            "stderr": str(planner_result.get("stderr") or ""),
            "parsed_launcher": launcher if isinstance(launcher, dict) else {},
        },
    )
    if int(planner_result.get("return_code") or 0) != 0:
        return None, "launcher planner agent exited non-zero"
    if launcher is None:
        return None, "launcher planner output did not include a valid launcher payload"

    _write_benchmark_launcher_cache(
        state_payload,
        launcher=launcher,
        hpc_profile_key=hpc_profile_key,
        source="controller_agent",
    )
    optimize_state.write_state(state_path, state_payload)
    _append_benchmark_launcher_memory_note(
        memory_path,
        event="locked",
        launcher=launcher,
    )
    return launcher, "planned"


def _resolve_submit_poll_timing(
    runtime: dict[str, Any],
    *,
    wait_hint_seconds: float | None,
    timeout_seconds: float,
) -> tuple[float, float, float]:
    cli = _cli()
    poll_interval_raw = runtime.get("poll_interval_seconds")
    if poll_interval_raw is None:
        poll_interval_raw = (
            wait_hint_seconds
            if isinstance(wait_hint_seconds, (int, float)) and wait_hint_seconds > 0
            else 1.0
        )
    try:
        poll_interval = float(poll_interval_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError(
            "Benchmark runtime.poll_interval_seconds must be a number."
        ) from exc
    if poll_interval <= 0:
        raise cli.PackageError("Benchmark runtime.poll_interval_seconds must be > 0.")

    max_poll_raw = runtime.get("max_poll_seconds")
    if max_poll_raw is None:
        max_poll_seconds = float(timeout_seconds)
    else:
        try:
            max_poll_seconds = float(max_poll_raw)
        except (TypeError, ValueError) as exc:
            raise cli.PackageError(
                "Benchmark runtime.max_poll_seconds must be a number."
            ) from exc
    if max_poll_seconds <= 0:
        raise cli.PackageError("Benchmark runtime.max_poll_seconds must be > 0.")
    max_poll_seconds = min(max_poll_seconds, float(timeout_seconds))

    pid_stall_raw = runtime.get("pid_stall_seconds", 900.0)
    try:
        pid_stall_seconds = float(pid_stall_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError(
            "Benchmark runtime.pid_stall_seconds must be a number."
        ) from exc
    if pid_stall_seconds < 0:
        raise cli.PackageError("Benchmark runtime.pid_stall_seconds must be >= 0.")
    if max_poll_seconds > 0 and pid_stall_seconds > max_poll_seconds:
        pid_stall_seconds = max_poll_seconds
    return poll_interval, max_poll_seconds, pid_stall_seconds


def _poll_submitted_benchmark_targets(
    *,
    pid_numbers: list[int],
    slurm_job_numbers: list[str],
    poll_interval_seconds: float,
    max_wait_seconds: float,
    pid_stall_seconds: float,
) -> dict[str, Any]:
    cli = _cli()
    from fermilink.cli.commands import sessions as session_commands

    poll_started = session_commands.time.monotonic()
    alive, pid_monitors, initially_dead_pids = (
        session_commands._initialize_pid_monitors(
            pid_numbers,
            now_monotonic=poll_started,
        )
    )
    pending_slurm_jobs = list(slurm_job_numbers)
    slurm_monitors: dict[str, object] = {}

    if initially_dead_pids:
        dead_text = ", ".join(str(pid) for pid in initially_dead_pids)
        cli._print_tagged(
            "optimize",
            ("benchmark submission included already-finished pid(s): " f"{dead_text}"),
        )

    if pending_slurm_jobs and not session_commands._slurm_wait_tools_available():
        slurm_text = ", ".join(pending_slurm_jobs)
        return {
            "ok": False,
            "status": "slurm_poll_unavailable",
            "reason": f"missing sacct/squeue for slurm job(s): {slurm_text}",
        }

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
            return {
                "ok": False,
                "status": "slurm_failure",
                "reason": f"slurm job(s) failed: {failed_text}",
            }
        if slurm_issues:
            issue_text = session_commands._format_slurm_issues(slurm_issues)
            return {
                "ok": False,
                "status": "slurm_poll_error",
                "reason": issue_text,
            }

    if not alive and not pending_slurm_jobs:
        return {
            "ok": True,
            "status": "ok",
            "waited_seconds": 0.0,
        }

    wait_targets = session_commands._format_waiting_targets(
        alive=alive,
        pending_slurm_jobs=pending_slurm_jobs,
    )
    stall_text = f"{pid_stall_seconds:.1f}s" if pid_stall_seconds > 0 else "disabled"
    cli._print_tagged(
        "optimize",
        (
            "polling benchmark submission "
            f"({wait_targets}, poll: {poll_interval_seconds:.1f}s, "
            f"max wait: {max_wait_seconds:.1f}s, pid stall: {stall_text})"
        ),
    )
    next_status_log = poll_started + session_commands.POLL_STATUS_HEARTBEAT_SECONDS

    while alive or pending_slurm_jobs:
        now_monotonic = session_commands.time.monotonic()
        elapsed = now_monotonic - poll_started
        remaining = max_wait_seconds - elapsed
        if now_monotonic >= next_status_log:
            remaining_text = max(0.0, remaining)
            cli._print_tagged(
                "optimize",
                (
                    "benchmark polling status @ "
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
                now_monotonic + session_commands.POLL_STATUS_HEARTBEAT_SECONDS
            )
        if remaining <= 0:
            return {
                "ok": False,
                "status": "timeout",
                "reason": (
                    "benchmark submission polling exceeded max wait; "
                    "still waiting on: "
                    + session_commands._format_waiting_targets(
                        alive=alive,
                        pending_slurm_jobs=pending_slurm_jobs,
                    )
                ),
            }
        sleep_seconds = min(poll_interval_seconds, remaining)
        if sleep_seconds > 0:
            session_commands.time.sleep(sleep_seconds)

        now_monotonic = session_commands.time.monotonic()
        if alive:
            alive, pid_monitors, pid_issues = session_commands._refresh_pid_monitors(
                alive,
                pid_monitors,
                now_monotonic=now_monotonic,
                stall_seconds=pid_stall_seconds,
            )
            blocking_pid_issues = [
                (status, pid)
                for status, pid in pid_issues
                if status in {"reused", "stalled"}
            ]
            if blocking_pid_issues:
                issue_text = session_commands._format_pid_issues(blocking_pid_issues)
                return {
                    "ok": False,
                    "status": "pid_issue",
                    "reason": issue_text,
                }

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
                    f"{job_id}:{state}" for job_id, state in failed_slurm_jobs
                )
                return {
                    "ok": False,
                    "status": "slurm_failure",
                    "reason": f"slurm job(s) failed: {failed_text}",
                }
            if slurm_issues:
                issue_text = session_commands._format_slurm_issues(slurm_issues)
                return {
                    "ok": False,
                    "status": "slurm_poll_error",
                    "reason": issue_text,
                }

    waited_seconds = session_commands.time.monotonic() - poll_started
    cli._print_tagged(
        "optimize",
        f"benchmark submission polling complete after {waited_seconds:.1f}s.",
    )
    return {
        "ok": True,
        "status": "ok",
        "waited_seconds": waited_seconds,
    }


def _collect_submitted_benchmark_payload(
    project_root: Path,
    *,
    benchmark_path: Path,
    benchmark_payload: dict[str, Any],
    runtime: dict[str, Any],
    env: dict[str, str],
    run_dir: Path,
    run_label: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    result_command = _resolve_runtime_result_command(
        benchmark_payload=benchmark_payload,
        benchmark_path=benchmark_path,
        project_root=project_root,
        run_dir=run_dir,
        runtime=runtime,
    )
    if result_command:
        stdout_path = run_dir / f"{run_label}.result.stdout.log"
        stderr_path = run_dir / f"{run_label}.result.stderr.log"
        try:
            completed = subprocess.run(
                result_command,
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
                "status": "result_collect_timeout",
                "result_stdout_log": str(stdout_path),
                "result_stderr_log": str(stderr_path),
            }
        except (OSError, ValueError) as exc:
            stderr_path.write_text(str(exc), encoding="utf-8")
            return {
                "ok": False,
                "status": "result_collect_crash",
                "result_stdout_log": str(stdout_path),
                "result_stderr_log": str(stderr_path),
            }
        stdout_text = str(completed.stdout or "")
        stderr_text = str(completed.stderr or "")
        stdout_path.write_text(stdout_text, encoding="utf-8")
        stderr_path.write_text(stderr_text, encoding="utf-8")
        if completed.returncode != 0:
            return {
                "ok": False,
                "status": "result_collect_crash",
                "return_code": int(completed.returncode),
                "result_stdout_log": str(stdout_path),
                "result_stderr_log": str(stderr_path),
            }
        try:
            payload = _parse_benchmark_stdout(
                stdout_text,
                benchmark_payload=benchmark_payload,
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "invalid_output_schema",
                "reason": str(exc),
                "result_stdout_log": str(stdout_path),
                "result_stderr_log": str(stderr_path),
            }
        return {
            "ok": True,
            "payload": payload,
            "result_stdout_log": str(stdout_path),
            "result_stderr_log": str(stderr_path),
        }

    result_json_path = _resolve_runtime_result_json_path(
        benchmark_payload=benchmark_payload,
        benchmark_path=benchmark_path,
        project_root=project_root,
        run_dir=run_dir,
        runtime=runtime,
    )
    if result_json_path is None:
        return {
            "ok": False,
            "status": "missing_result_source",
        }
    try:
        metrics_text = result_json_path.read_text(encoding="utf-8")
    except OSError:
        return {
            "ok": False,
            "status": "missing_result_json",
            "result_json_path": str(result_json_path),
        }
    try:
        payload = _parse_benchmark_stdout(
            metrics_text,
            benchmark_payload=benchmark_payload,
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "invalid_output_schema",
            "reason": str(exc),
            "result_json_path": str(result_json_path),
        }
    snapshot_path = run_dir / f"{run_label}.result.metrics.json"
    snapshot_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "payload": payload,
        "result_json_path": str(result_json_path),
        "result_metrics_snapshot": str(snapshot_path),
    }


def _run_benchmark_once(
    project_root: Path,
    *,
    benchmark_path: Path,
    benchmark_payload: dict[str, Any],
    timeout_seconds: int,
    run_dir: Path,
    run_label: str,
    runtime_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cli = _cli()
    runtime = dict(_runtime_config(benchmark_payload))
    if isinstance(runtime_override, dict):
        for key in ("command", "result_command", "result_json_path"):
            if key in runtime_override:
                runtime[key] = runtime_override.get(key)
    runtime_mode = _runtime_mode(runtime)
    command = _expand_runtime_command(
        list(runtime.get("command") or []),
        benchmark_path=benchmark_path,
        project_root=project_root,
        run_dir=run_dir,
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
    started_monotonic = time.monotonic()
    if runtime_mode == "submit_poll":
        submission_timeout_raw = runtime.get("submission_timeout_seconds")
        if submission_timeout_raw is None:
            submission_timeout = min(float(timeout_seconds), 120.0)
        else:
            submission_timeout = float(submission_timeout_raw)
        submission_timeout = min(submission_timeout, float(timeout_seconds))
        try:
            completed = subprocess.run(
                command,
                cwd=str(project_root),
                text=True,
                capture_output=True,
                env=env,
                timeout=submission_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(str(exc.stdout or ""), encoding="utf-8")
            stderr_path.write_text(str(exc.stderr or ""), encoding="utf-8")
            return _benchmark_failure_payload(
                status="timeout",
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
        except (OSError, ValueError) as exc:
            stderr_path.write_text(str(exc), encoding="utf-8")
            return _benchmark_failure_payload(
                status="crash",
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                extra={"reason": str(exc)},
            )

        stdout_text = str(completed.stdout or "")
        stderr_text = str(completed.stderr or "")
        stdout_path.write_text(stdout_text, encoding="utf-8")
        stderr_path.write_text(stderr_text, encoding="utf-8")
        if completed.returncode != 0:
            return _benchmark_failure_payload(
                status="crash",
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                extra={"return_code": int(completed.returncode)},
            )

        submission_text = "\n".join(
            part for part in (stdout_text, stderr_text) if part.strip()
        )
        pid_numbers = cli._extract_loop_pid_numbers(submission_text)
        slurm_job_numbers = cli._extract_loop_slurm_job_numbers(submission_text)
        if not pid_numbers and not slurm_job_numbers:
            try:
                payload = _parse_benchmark_stdout(
                    stdout_text,
                    benchmark_payload=benchmark_payload,
                )
            except Exception as exc:
                return _benchmark_failure_payload(
                    status="invalid_output_schema",
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    extra={"reason": str(exc)},
                )
            payload["ok"] = True
            payload["status"] = "ok"
            payload["stdout_log"] = str(stdout_path)
            payload["stderr_log"] = str(stderr_path)
            payload["runtime_mode"] = "submit_poll_fallback_direct"
            return payload

        elapsed = time.monotonic() - started_monotonic
        remaining_timeout = float(timeout_seconds) - elapsed
        if remaining_timeout <= 0:
            return _benchmark_failure_payload(
                status="timeout",
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
        wait_hint = cli._extract_loop_wait_seconds(submission_text)
        poll_interval, max_poll_seconds, pid_stall_seconds = (
            _resolve_submit_poll_timing(
                runtime,
                wait_hint_seconds=wait_hint,
                timeout_seconds=remaining_timeout,
            )
        )
        if max_poll_seconds <= 0:
            return _benchmark_failure_payload(
                status="timeout",
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
        poll_result = _poll_submitted_benchmark_targets(
            pid_numbers=pid_numbers,
            slurm_job_numbers=slurm_job_numbers,
            poll_interval_seconds=poll_interval,
            max_wait_seconds=max_poll_seconds,
            pid_stall_seconds=pid_stall_seconds,
        )
        if not bool(poll_result.get("ok")):
            return _benchmark_failure_payload(
                status=str(poll_result.get("status") or "poll_failed"),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                extra={"reason": str(poll_result.get("reason") or "").strip()},
            )

        elapsed = time.monotonic() - started_monotonic
        remaining_timeout = float(timeout_seconds) - elapsed
        if remaining_timeout <= 0:
            return _benchmark_failure_payload(
                status="timeout",
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
        collect_result = _collect_submitted_benchmark_payload(
            project_root,
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
            runtime=runtime,
            env=env,
            run_dir=run_dir,
            run_label=run_label,
            timeout_seconds=remaining_timeout,
        )
        if not bool(collect_result.get("ok")):
            return _benchmark_failure_payload(
                status=str(collect_result.get("status") or "missing_result_source"),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                extra={
                    key: value
                    for key, value in collect_result.items()
                    if key not in {"ok", "status"}
                },
            )

        payload = collect_result.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        payload["ok"] = True
        payload["status"] = "ok"
        payload["stdout_log"] = str(stdout_path)
        payload["stderr_log"] = str(stderr_path)
        payload["runtime_mode"] = "submit_poll"
        if "result_json_path" in collect_result:
            payload["result_json_path"] = str(collect_result.get("result_json_path"))
        if "result_metrics_snapshot" in collect_result:
            payload["result_metrics_snapshot"] = str(
                collect_result.get("result_metrics_snapshot")
            )
        if "result_stdout_log" in collect_result:
            payload["result_stdout_log"] = str(collect_result.get("result_stdout_log"))
        if "result_stderr_log" in collect_result:
            payload["result_stderr_log"] = str(collect_result.get("result_stderr_log"))
        return payload

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
        return _benchmark_failure_payload(
            status="timeout",
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
    except (OSError, ValueError) as exc:
        stderr_path.write_text(str(exc), encoding="utf-8")
        return _benchmark_failure_payload(
            status="crash",
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            extra={"reason": str(exc)},
        )

    stdout_text = str(completed.stdout or "")
    stderr_text = str(completed.stderr or "")
    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")
    if completed.returncode != 0:
        return _benchmark_failure_payload(
            status="crash",
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            extra={"return_code": int(completed.returncode)},
        )
    try:
        payload = _parse_benchmark_stdout(
            stdout_text,
            benchmark_payload=benchmark_payload,
        )
    except Exception as exc:
        return _benchmark_failure_payload(
            status="invalid_output_schema",
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            extra={"reason": str(exc)},
        )
    payload["ok"] = True
    payload["status"] = "ok"
    payload["stdout_log"] = str(stdout_path)
    payload["stderr_log"] = str(stderr_path)
    payload["runtime_mode"] = "direct"
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
    guardrail_errors: list[str] = []
    seen_guardrail_errors: set[str] = set()
    include_run_prefix = len(measured_runs) > 1
    for run_index, run in enumerate(measured_runs, start=1):
        run_errors = run.get("guardrail_errors")
        if not isinstance(run_errors, list):
            continue
        for raw_error in run_errors:
            message = str(raw_error or "").strip()
            if not message:
                continue
            rendered = (
                f"measured_{run_index}: {message}" if include_run_prefix else message
            )
            if rendered in seen_guardrail_errors:
                continue
            seen_guardrail_errors.add(rendered)
            guardrail_errors.append(rendered)

    payload: dict[str, Any] = {
        "summary_metrics": summary_metrics,
        "cases": representative_cases,
        "raw_runs": measured_runs,
        "correctness_ok": correctness_ok,
        "status": "ok",
    }
    if guardrail_errors:
        payload["guardrail_errors"] = guardrail_errors
    return payload


def _run_benchmark_suite(
    project_root: Path,
    *,
    benchmark_path: Path,
    benchmark_payload: dict[str, Any],
    run_dir: Path,
    timeout_seconds: int,
    runtime_override: dict[str, Any] | None = None,
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
            runtime_override=runtime_override,
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
            runtime_override=runtime_override,
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


def _run_authoritative_benchmark_suite(
    project_root: Path,
    *,
    benchmark_path: Path,
    benchmark_payload: dict[str, Any],
    run_dir: Path,
    run_rel: str,
    timeout_seconds: int,
    state_payload: dict[str, Any],
    state_path: Path,
    memory_path: Path,
    benchmark_rel: str,
    memory_rel: str,
    hpc_constraints_block: str,
    hpc_profile_key: str,
    use_dynamic_submit_launcher: bool,
    provider: str,
    provider_bin_override: str | None,
    sandbox_mode: str,
    sandbox_policy: str,
    model: str | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    cli = _cli()
    runtime = _runtime_config(benchmark_payload)
    if not use_dynamic_submit_launcher or _runtime_mode(runtime) != "submit_poll":
        return _run_benchmark_suite(
            project_root,
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
            run_dir=run_dir,
            timeout_seconds=timeout_seconds,
        )

    max_attempts = _runtime_launcher_max_attempts(runtime)
    latest_result: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        launcher, launcher_source = _ensure_benchmark_launcher(
            project_root,
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
            run_dir=run_dir,
            run_rel=run_rel,
            benchmark_rel=benchmark_rel,
            memory_rel=memory_rel,
            hpc_constraints_block=hpc_constraints_block,
            memory_path=memory_path,
            state_path=state_path,
            state_payload=state_payload,
            hpc_profile_key=hpc_profile_key,
            provider=provider,
            provider_bin_override=provider_bin_override,
            sandbox_mode=sandbox_mode,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        if launcher is None:
            return {
                "ok": False,
                "status": "launcher_planning_failed",
                "summary_metrics": {},
                "cases": [],
                "reason": launcher_source,
            }
        runtime_override = _runtime_override_from_launcher(launcher)
        latest_result = _run_benchmark_suite(
            project_root,
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
            run_dir=run_dir,
            timeout_seconds=timeout_seconds,
            runtime_override=runtime_override,
        )
        latest_result["launcher_source"] = launcher_source
        status = str(latest_result.get("status") or "unknown")
        if status == "ok":
            return latest_result
        if status not in BENCHMARK_INFRA_FAILURE_STATUSES:
            return latest_result
        if attempt >= max_attempts:
            return latest_result
        cli._print_tagged(
            "optimize",
            (
                "benchmark launcher failed with infra status "
                f"`{status}`; replanning launcher "
                f"(attempt {attempt + 1}/{max_attempts})"
            ),
            stderr=True,
        )
        _append_benchmark_launcher_memory_note(
            memory_path,
            event="invalidated",
            launcher=launcher,
            reason=status,
        )
        _clear_benchmark_launcher_cache(state_payload, reason=f"{status}")
        optimize_state.write_state(state_path, state_payload)
    return latest_result


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
    mode = _resolve_correctness_mode(correctness)
    require_all_cases_converged = bool(
        correctness.get("require_all_cases_converged", True)
    )
    incumbent_cases = _case_map(incumbent_metrics.get("cases"))
    candidate_cases = _case_map(candidate_metrics.get("cases"))
    field_tolerance_specs = (
        list(correctness.get("field_tolerances"))
        if mode == CORRECTNESS_MODE_FIELD_TOLERANCES
        and isinstance(correctness.get("field_tolerances"), list)
        else []
    )
    errors: list[str] = []
    for case_id, incumbent_case in incumbent_cases.items():
        candidate_case = candidate_cases.get(case_id)
        if candidate_case is None:
            errors.append(f"missing benchmark case: {case_id}")
            continue
        if require_all_cases_converged and not bool(candidate_case.get("converged")):
            errors.append(f"case {case_id} did not converge")
            continue
        if mode == CORRECTNESS_MODE_FIELD_TOLERANCES:
            errors.extend(
                _field_tolerance_case_errors(
                    case_id=case_id,
                    incumbent_case=incumbent_case,
                    candidate_case=candidate_case,
                    tolerance_specs=field_tolerance_specs,
                )
            )

    return {
        "ok": not errors,
        "errors": errors,
        "mode": mode,
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
            case_payload: dict[str, Any] = {
                "id": str(item.get("id") or ""),
                "converged": bool(item.get("converged")),
                "wall_seconds": item.get("wall_seconds"),
                "error": str(item.get("error") or ""),
            }
            scalar_metrics: dict[str, float] = {}
            for key, value in item.items():
                if key in {
                    "id",
                    "converged",
                    "wall_seconds",
                    "error",
                    "command",
                    "command_preview",
                }:
                    continue
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    scalar_metrics[str(key)] = float(value)
            if scalar_metrics:
                case_payload["scalar_metrics"] = scalar_metrics
            rendered_cases.append(case_payload)
    return {
        "status": metrics.get("status"),
        "correctness_ok": bool(metrics.get("correctness_ok")),
        "summary_metrics": rendered_summary,
        "cases": rendered_cases,
        "guardrail_errors": (
            list(metrics.get("guardrail_errors"))
            if isinstance(metrics.get("guardrail_errors"), list)
            else []
        ),
    }


def _collect_guardrail_errors(metrics: dict[str, Any]) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()

    direct_errors = metrics.get("guardrail_errors")
    if isinstance(direct_errors, list):
        for raw in direct_errors:
            message = str(raw or "").strip()
            if not message or message in seen:
                continue
            seen.add(message)
            collected.append(message)

    raw_runs = metrics.get("raw_runs")
    if isinstance(raw_runs, list):
        for run_index, run in enumerate(raw_runs, start=1):
            if not isinstance(run, dict):
                continue
            run_errors = run.get("guardrail_errors")
            if not isinstance(run_errors, list):
                continue
            for raw in run_errors:
                message = str(raw or "").strip()
                if not message:
                    continue
                if len(raw_runs) > 1:
                    message = f"measured_{run_index}: {message}"
                if message in seen:
                    continue
                seen.add(message)
                collected.append(message)

    return collected


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

    guardrail_errors = _collect_guardrail_errors(candidate_metrics)
    if guardrail_errors:
        return {
            "hard_reject": True,
            "status": "rejected",
            "reason": "performance_regression: " + "; ".join(guardrail_errors),
            "correctness": correctness,
            "category": "performance_regression",
            "performance_regression": {
                "count": len(guardrail_errors),
                "errors": guardrail_errors,
            },
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


def _run_lock_payload(
    project_root: Path,
    *,
    mode: str,
    package_id: str,
    benchmark_path: Path,
    prompt_path: Path | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "pid": os.getpid(),
        "started_at_utc": optimize_state.utc_now_z(),
        "mode": str(mode or "expert"),
        "project_root": str(project_root),
        "package_id": package_id,
        "benchmark_path": str(benchmark_path),
    }
    if isinstance(prompt_path, Path):
        payload["prompt_path"] = str(prompt_path)
    return payload


def _write_campaign_run_lock(
    project_root: Path,
    *,
    mode: str,
    package_id: str,
    benchmark_path: Path,
    prompt_path: Path | None,
) -> None:
    cli = _cli()
    lock_path = optimize_state.run_lock_path(project_root)
    lock_payload = optimize_state.load_run_lock(lock_path)
    if isinstance(lock_payload, dict):
        try:
            lock_pid = int(lock_payload.get("pid") or 0)
        except (TypeError, ValueError):
            lock_pid = 0
        if (
            lock_pid > 0
            and lock_pid != os.getpid()
            and optimize_state.pid_is_running(lock_pid)
        ):
            started = str(lock_payload.get("started_at_utc") or "").strip()
            raise cli.PackageError(
                "Optimize campaign appears active in this repository "
                f"(pid={lock_pid}, started={started or 'unknown'}). "
                "Use `fermilink optimize status` to inspect it before launching "
                "another run."
            )
    optimize_state.write_run_lock(
        lock_path,
        _run_lock_payload(
            project_root,
            mode=mode,
            package_id=package_id,
            benchmark_path=benchmark_path,
            prompt_path=prompt_path,
        ),
    )


def _collect_tracked_files(project_root: Path) -> list[str]:
    completed = optimize_git.run_git(
        project_root,
        ["ls-files", "-z"],
    )
    files: list[str] = []
    for raw in str(completed.stdout or "").split("\0"):
        item = str(raw or "").strip().replace("\\", "/")
        if item:
            files.append(item)
    return files


def _infer_quick_language(project_root: Path, tracked_files: list[str]) -> str:
    if (project_root / "pyproject.toml").is_file() or (
        project_root / "setup.py"
    ).is_file():
        return "python"
    if any(path.endswith(".py") for path in tracked_files):
        return "python"
    if any(
        path.endswith((".f", ".f90", ".f95", ".f03", ".f08")) for path in tracked_files
    ):
        return "fortran"
    if any(path.endswith((".c", ".cc", ".cpp", ".cxx")) for path in tracked_files):
        return "cpp"
    if (project_root / "CMakeLists.txt").is_file():
        return "cmake"
    return "generic"


def _extract_prompt_paths(prompt_text: str) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    path_token_re = re.compile(
        r"(?<![A-Za-z0-9_])([A-Za-z0-9_./-]+\.[A-Za-z0-9_]+)(?![A-Za-z0-9_])"
    )
    for match in path_token_re.finditer(str(prompt_text or "")):
        candidate = str(match.group(1) or "").strip().lstrip("./")
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        hints.append(candidate.replace("\\", "/"))
    return hints


def _infer_editable_paths(
    project_root: Path,
    *,
    prompt_text: str,
    tracked_files: list[str],
    language: str,
) -> list[str]:
    tracked_set = set(tracked_files)
    explicit = [
        hint
        for hint in _extract_prompt_paths(prompt_text)
        if hint in tracked_set and not hint.startswith(".fermilink-optimize/")
    ]
    if explicit:
        return explicit

    if any(path.startswith("src/") for path in tracked_files):
        return ["src/**"]
    if any(path.startswith("lib/") for path in tracked_files):
        return ["lib/**"]
    if language == "python":
        return ["**/*.py"]
    source_files = [
        path
        for path in tracked_files
        if Path(path).suffix.lower() in QUICK_SOURCE_CODE_EXTENSIONS
    ]
    if source_files:
        return source_files[:20]
    return ["**/*"]


def _split_shell_tokens(raw: str) -> list[str]:
    try:
        tokens = shlex.split(str(raw or "").strip())
    except ValueError:
        return []
    return [token for token in tokens if str(token or "").strip()]


def _extract_prompt_commands(prompt_text: str) -> list[list[str]]:
    commands: list[list[str]] = []
    seen: set[str] = set()
    in_fence = False
    for raw_line in str(prompt_text or "").splitlines():
        line = str(raw_line or "").rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        candidate = ""
        if in_fence:
            candidate = stripped
        elif stripped.startswith("$"):
            candidate = stripped[1:].strip()
        else:
            for prefix in ("- `", "* `", "+ `", "`"):
                if stripped.startswith(prefix) and "`" in stripped[len(prefix) :]:
                    tail = stripped[len(prefix) :]
                    candidate = tail.split("`", 1)[0].strip()
                    break
        if not candidate:
            continue
        if candidate.lower().startswith("fermilink "):
            continue
        tokens = _split_shell_tokens(candidate)
        if not tokens:
            continue
        signature = shlex.join(tokens)
        if signature in seen:
            continue
        seen.add(signature)
        commands.append(tokens)
    return commands[:8]


def _python_executable_for_quick(project_root: Path) -> str:
    venv_python = project_root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    return sys.executable


def _default_quick_case_commands(
    project_root: Path,
    *,
    language: str,
    python_exec: str,
) -> list[list[str]]:
    tests_dir = project_root / "tests"
    if language == "python" and tests_dir.is_dir():
        return [[python_exec, "-m", "pytest", "-q"]]
    if language == "python":
        for candidate in (
            "scripts/benchmark.py",
            "scripts/bench.py",
            "scripts/run_benchmark.py",
        ):
            if (project_root / candidate).is_file():
                return [[python_exec, candidate]]
        return [[python_exec, "-c", "print('fermilink quick benchmark placeholder')"]]
    if (project_root / "Makefile").is_file():
        return [["make", "test"]]
    if (project_root / "CMakeLists.txt").is_file():
        return [["ctest", "--output-on-failure"]]
    return [["bash", "-lc", "echo 'fermilink quick benchmark placeholder'"]]


def _render_quick_runner_script() -> str:
    return (
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n"
        "\n"
        "import argparse\n"
        "import json\n"
        "import statistics\n"
        "import subprocess\n"
        "import time\n"
        "from pathlib import Path\n"
        "\n"
        "import yaml\n"
        "\n"
        "\n"
        "def _expand(tokens: list[str], replacements: dict[str, str]) -> list[str]:\n"
        "    expanded: list[str] = []\n"
        "    for token in tokens:\n"
        "        rendered = str(token)\n"
        "        for key, value in replacements.items():\n"
        "            rendered = rendered.replace(key, value)\n"
        "        expanded.append(rendered)\n"
        "    return expanded\n"
        "\n"
        "\n"
        "def _run_case(project_root: Path, case: dict[str, object], replacements: dict[str, str]) -> dict[str, object]:\n"
        "    case_id = str(case.get('id') or 'case')\n"
        "    raw_command = case.get('command')\n"
        "    command = [str(item) for item in raw_command if isinstance(item, str)] if isinstance(raw_command, list) else []\n"
        "    if not command:\n"
        "        return {\n"
        "            'id': case_id,\n"
        "            'converged': False,\n"
        "            'wall_seconds': 0.0,\n"
        "            'error': 'missing case.command',\n"
        "        }\n"
        "    expanded = _expand(command, replacements)\n"
        "    timeout_raw = case.get('timeout_seconds')\n"
        "    timeout_seconds = None\n"
        "    if isinstance(timeout_raw, (int, float)) and float(timeout_raw) > 0:\n"
        "        timeout_seconds = float(timeout_raw)\n"
        "    started = time.perf_counter()\n"
        "    try:\n"
        "        completed = subprocess.run(\n"
        "            expanded,\n"
        "            cwd=str(project_root),\n"
        "            text=True,\n"
        "            capture_output=True,\n"
        "            timeout=timeout_seconds,\n"
        "            check=False,\n"
        "        )\n"
        "        elapsed = max(0.0, time.perf_counter() - started)\n"
        "        stderr_text = str(completed.stderr or '').strip()\n"
        "        if not stderr_text:\n"
        "            stderr_text = str(completed.stdout or '').strip()\n"
        "        return {\n"
        "            'id': case_id,\n"
        "            'converged': completed.returncode == 0,\n"
        "            'wall_seconds': elapsed,\n"
        "            'error': '' if completed.returncode == 0 else stderr_text,\n"
        "            'return_code': int(completed.returncode),\n"
        "            'command': expanded,\n"
        "        }\n"
        "    except subprocess.TimeoutExpired:\n"
        "        elapsed = max(0.0, time.perf_counter() - started)\n"
        "        return {\n"
        "            'id': case_id,\n"
        "            'converged': False,\n"
        "            'wall_seconds': elapsed,\n"
        "            'error': 'timeout',\n"
        "            'return_code': 124,\n"
        "            'command': expanded,\n"
        "        }\n"
        "    except OSError as exc:\n"
        "        elapsed = max(0.0, time.perf_counter() - started)\n"
        "        return {\n"
        "            'id': case_id,\n"
        "            'converged': False,\n"
        "            'wall_seconds': elapsed,\n"
        "            'error': str(exc),\n"
        "            'return_code': 1,\n"
        "            'command': expanded,\n"
        "        }\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--benchmark', required=True)\n"
        "    parser.add_argument('--emit-json', action='store_true')\n"
        "    args = parser.parse_args()\n"
        "\n"
        "    benchmark_path = Path(args.benchmark).resolve()\n"
        "    project_root = benchmark_path.parent.parent.parent.resolve()\n"
        "    payload = yaml.safe_load(benchmark_path.read_text(encoding='utf-8'))\n"
        "    if not isinstance(payload, dict):\n"
        "        raise SystemExit('benchmark file must be a YAML object')\n"
        "    cases_raw = payload.get('cases')\n"
        "    if isinstance(cases_raw, list):\n"
        "        cases = [item for item in cases_raw if isinstance(item, dict)]\n"
        "    else:\n"
        "        cases = []\n"
        "    if not cases:\n"
        "        raise SystemExit('benchmark file requires a non-empty cases list')\n"
        "    replacements = {\n"
        "        '{benchmark}': str(benchmark_path),\n"
        "        '{project_root}': str(project_root),\n"
        "        '{run_dir}': str((project_root / '.fermilink-optimize' / 'runs' / 'autogen').resolve()),\n"
        "    }\n"
        "    case_results = [_run_case(project_root, case, replacements) for case in cases]\n"
        "    failures = sum(1 for item in case_results if not bool(item.get('converged')))\n"
        "    wall_values = [float(item.get('wall_seconds') or 0.0) for item in case_results]\n"
        "    median_wall = statistics.median(wall_values) if wall_values else 0.0\n"
        "    output = {\n"
        "        'benchmark_id': str(payload.get('benchmark_id') or 'quick-benchmark'),\n"
        "        'correctness_ok': failures == 0,\n"
        "        'summary_metrics': {\n"
        "            'weighted_median_wall_seconds': float(median_wall),\n"
        "            'peak_rss_mb': 0.0,\n"
        "            'total_failures': int(failures),\n"
        "        },\n"
        "        'cases': case_results,\n"
        "    }\n"
        "    print(json.dumps(output, sort_keys=True))\n"
        "    return 0\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )


def _render_quick_submit_launcher_script() -> str:
    return (
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n"
        "\n"
        "import argparse\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--benchmark', required=True)\n"
        "    parser.add_argument('--result-json', required=True)\n"
        "    parser.add_argument('--runner', required=True)\n"
        "    args = parser.parse_args()\n"
        "\n"
        "    benchmark_path = Path(args.benchmark).resolve()\n"
        "    project_root = benchmark_path.parent.parent.parent.resolve()\n"
        "    result_path = Path(args.result_json)\n"
        "    if not result_path.is_absolute():\n"
        "        result_path = (project_root / result_path).resolve()\n"
        "    result_path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    runner_path = Path(args.runner)\n"
        "    if not runner_path.is_absolute():\n"
        "        runner_path = (project_root / runner_path).resolve()\n"
        "    stderr_path = result_path.with_suffix('.submit.stderr.log')\n"
        "    with result_path.open('w', encoding='utf-8') as out_handle:\n"
        "        with stderr_path.open('w', encoding='utf-8') as err_handle:\n"
        "            proc = subprocess.Popen(\n"
        "                [sys.executable, str(runner_path), '--benchmark', str(benchmark_path), '--emit-json'],\n"
        "                cwd=str(project_root),\n"
        "                stdout=out_handle,\n"
        "                stderr=err_handle,\n"
        "                text=True,\n"
        "            )\n"
        "    print(f'<pid_number>{proc.pid}</pid_number>')\n"
        "    return 0\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )


def _render_quick_setup_script(project_root: Path, *, language: str) -> str:
    if language == "python":
        return (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"cd {shlex.quote(str(project_root))}\n"
            "if [ ! -d .venv ]; then\n"
            "  python -m venv .venv\n"
            "fi\n"
            ". .venv/bin/activate\n"
            "python -m pip install -U pip\n"
            "python -m pip install -e .\n"
        )
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(project_root))}\n"
        "echo 'No language-specific quick setup script was inferred for this repository.'\n"
    )


def _render_quick_run_script(
    *,
    package_id: str,
    project_root: Path,
    benchmark_path: Path,
    hpc_profile: str,
) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "fermilink optimize \\",
        f"  {shlex.quote(package_id)} \\",
        f"  {shlex.quote(str(project_root))} \\",
        f"  --benchmark {shlex.quote(str(benchmark_path))} \\",
        "  --skills-source existing \\",
        f"  --max-iterations {QUICK_DEFAULT_MAX_ITERATIONS} \\",
        f"  --stop-on-consecutive-rejections {QUICK_DEFAULT_STOP_ON_CONSECUTIVE_REJECTIONS} \\",
        f"  --worker-max-iterations {QUICK_DEFAULT_WORKER_MAX_ITERATIONS} \\",
        f"  --worker-wait-seconds {QUICK_DEFAULT_WORKER_WAIT_SECONDS} \\",
        f"  --worker-max-wait-seconds {QUICK_DEFAULT_WORKER_MAX_WAIT_SECONDS} \\",
        f"  --worker-pid-stall-seconds {QUICK_DEFAULT_WORKER_PID_STALL_SECONDS} \\",
        '  "$@"',
    ]
    profile_text = str(hpc_profile or "").strip()
    if profile_text:
        lines.insert(-1, f"  --hpc-profile {shlex.quote(profile_text)} \\")
    return "\n".join(lines) + "\n"


def _render_quick_benchmark_yaml(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(
        payload,
        sort_keys=False,
        default_flow_style=False,
    )


def _ensure_text_file(
    path: Path,
    *,
    content: str,
    executable: bool = False,
) -> bool:
    if path.exists():
        if executable:
            optimize_state.ensure_executable(path)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        optimize_state.ensure_executable(path)
    return True


def _resolve_quick_prompt_path(project_root: Path, raw_prompt: str) -> Path:
    prompt_path = Path(raw_prompt).expanduser()
    if not prompt_path.is_absolute():
        prompt_path = (project_root / prompt_path).resolve()
    else:
        prompt_path = prompt_path.resolve()
    return prompt_path


def _quick_scaffold(
    project_root: Path,
    *,
    package_id: str,
    prompt_path: Path,
    hpc_profile: str,
) -> dict[str, Any]:
    optimize_state.ensure_optimize_root(project_root)
    autogen_root = optimize_state.ensure_autogen_root(project_root)
    benchmark_path = optimize_state.quick_benchmark_path(project_root)
    runner_path = optimize_state.quick_runner_path(project_root)
    submit_path = optimize_state.quick_submit_launcher_path(project_root)
    setup_path = optimize_state.quick_setup_path(project_root)
    run_script_path = optimize_state.quick_run_script_path(project_root)
    manifest_path = optimize_state.quick_manifest_path(project_root)
    latest_metrics_rel = ".fermilink-optimize/autogen/latest_metrics.json"

    prompt_text = prompt_path.read_text(encoding="utf-8")
    tracked_files = _collect_tracked_files(project_root)
    language = _infer_quick_language(project_root, tracked_files)
    reference_template = _load_quick_reference_template(
        project_root,
        language=language,
    )
    reference_benchmark = _dict_clone(reference_template.get("benchmark"))
    template_case_hints = _template_case_hints(reference_benchmark)
    template_controller_defaults = _quick_controller_defaults(reference_benchmark)
    template_objective = _quick_objective_from_template(reference_benchmark)
    template_source = str(reference_template.get("source") or "").strip().lower()
    template_correctness = _quick_correctness_from_template(
        reference_benchmark,
        allow_template_mode=template_source == "project",
    )
    template_runtime = _dict_clone(reference_benchmark.get("runtime"))
    template_runtime_env = _normalize_runtime_env(template_runtime.get("env"))
    template_reporting = reference_benchmark.get("reporting")
    if not isinstance(template_reporting, dict):
        template_reporting = None
    reference_examples_payload: dict[str, str] = {}
    if reference_template:
        reference_examples_payload = {
            "source": str(reference_template.get("source") or ""),
            "benchmark": str(reference_template.get("benchmark_rel") or ""),
            "runner": str(reference_template.get("runner_rel") or ""),
        }
    python_exec = _python_executable_for_quick(project_root)
    case_commands = _extract_prompt_commands(prompt_text)
    case_command_source = "prompt"
    if not case_commands:
        case_command_source = "default"
        case_commands = _default_quick_case_commands(
            project_root,
            language=language,
            python_exec=python_exec,
        )
    editable_paths = _infer_editable_paths(
        project_root,
        prompt_text=prompt_text,
        tracked_files=tracked_files,
        language=language,
    )
    runtime_mode = "submit_poll" if str(hpc_profile or "").strip() else "direct"
    runner_rel = optimize_state.safe_relative(runner_path, project_root)
    submit_rel = optimize_state.safe_relative(submit_path, project_root)
    setup_rel = optimize_state.safe_relative(setup_path, project_root)
    run_script_rel = optimize_state.safe_relative(run_script_path, project_root)
    prompt_rel = optimize_state.safe_relative(prompt_path, project_root)
    benchmark_rel = optimize_state.safe_relative(benchmark_path, project_root)

    benchmark_payload: dict[str, Any] = {
        "schema_version": 1,
        "benchmark_id": f"autogen-{package_id}",
        "repo": {
            "editable_paths": editable_paths,
            "immutable_paths": [
                ".fermilink-optimize/**",
                "skills/**",
            ],
        },
        "controller": {
            "timeout_seconds": int(template_controller_defaults["timeout_seconds"]),
            "warmup_runs": int(template_controller_defaults["warmup_runs"]),
            "measured_runs": int(template_controller_defaults["measured_runs"]),
            "objective": template_objective,
        },
        "campaign": {
            "max_iterations": QUICK_DEFAULT_MAX_ITERATIONS,
            "stop_on_consecutive_rejections": QUICK_DEFAULT_STOP_ON_CONSECUTIVE_REJECTIONS,
        },
        "worker": {
            "max_iterations": QUICK_DEFAULT_WORKER_MAX_ITERATIONS,
            "wait_seconds": QUICK_DEFAULT_WORKER_WAIT_SECONDS,
            "max_wait_seconds": QUICK_DEFAULT_WORKER_MAX_WAIT_SECONDS,
            "pid_stall_seconds": QUICK_DEFAULT_WORKER_PID_STALL_SECONDS,
        },
        "correctness": template_correctness,
        "runtime": {},
        "cases": [],
        "autogen": {
            "prompt_path": prompt_rel,
            "language": language,
            "command_source": case_command_source,
            "generated_at_utc": optimize_state.utc_now_z(),
        },
    }
    for key in ("aggregation", "secondary_objectives", "reject_on"):
        if key in template_controller_defaults:
            benchmark_payload["controller"][key] = copy.deepcopy(
                template_controller_defaults[key]
            )
    if reference_examples_payload:
        benchmark_payload["autogen"]["reference_examples"] = reference_examples_payload
    if isinstance(template_reporting, dict):
        benchmark_payload["reporting"] = copy.deepcopy(template_reporting)

    cases_payload: list[dict[str, Any]] = []
    for index, command in enumerate(case_commands, start=1):
        case_id = f"case-{index}"
        case_payload: dict[str, Any] = {"id": case_id, "command": list(command)}
        if index <= len(template_case_hints):
            hint = template_case_hints[index - 1]
            hinted_id = str(hint.get("id") or "").strip()
            if hinted_id:
                case_payload["id"] = hinted_id
            preview = str(hint.get("command_preview") or "").strip()
            if preview:
                case_payload["command_preview"] = preview
        cases_payload.append(case_payload)
    benchmark_payload["cases"] = cases_payload

    runtime_payload = benchmark_payload["runtime"]
    if not isinstance(runtime_payload, dict):
        runtime_payload = {}
        benchmark_payload["runtime"] = runtime_payload
    if template_runtime_env:
        runtime_payload["env"] = template_runtime_env
    if runtime_mode == "submit_poll":
        runtime_payload.update(
            {
                "mode": "submit_poll",
                "command": [
                    python_exec,
                    submit_rel,
                    "--benchmark",
                    "{benchmark}",
                    "--result-json",
                    latest_metrics_rel,
                    "--runner",
                    runner_rel,
                ],
                "result_json_path": latest_metrics_rel,
                "poll_interval_seconds": QUICK_DEFAULT_WORKER_WAIT_SECONDS,
                "max_poll_seconds": QUICK_DEFAULT_WORKER_MAX_WAIT_SECONDS,
                "pid_stall_seconds": QUICK_DEFAULT_WORKER_PID_STALL_SECONDS,
            }
        )
    else:
        runtime_payload.update(
            {
                "mode": "direct",
                "command": [
                    python_exec,
                    runner_rel,
                    "--benchmark",
                    "{benchmark}",
                    "--emit-json",
                ],
            }
        )

    created_files: dict[str, bool] = {}
    created_files["runner"] = _ensure_text_file(
        runner_path,
        content=_render_quick_runner_script(),
        executable=True,
    )
    created_files["submit_launcher"] = _ensure_text_file(
        submit_path,
        content=_render_quick_submit_launcher_script(),
        executable=True,
    )
    created_files["setup"] = _ensure_text_file(
        setup_path,
        content=_render_quick_setup_script(project_root, language=language),
        executable=True,
    )
    created_files["benchmark"] = _ensure_text_file(
        benchmark_path,
        content=_render_quick_benchmark_yaml(benchmark_payload),
    )
    created_files["run_script"] = _ensure_text_file(
        run_script_path,
        content=_render_quick_run_script(
            package_id=package_id,
            project_root=project_root,
            benchmark_path=benchmark_path,
            hpc_profile=hpc_profile,
        ),
        executable=True,
    )
    benchmark_loaded = _load_benchmark(benchmark_path)

    manifest_payload = {
        "schema_version": 1,
        "mode": "quick",
        "created_at_utc": optimize_state.utc_now_z(),
        "package_id": package_id,
        "project_root": str(project_root),
        "prompt_path": str(prompt_path),
        "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
        "benchmark_path": str(benchmark_path),
        "runner_path": str(runner_path),
        "submit_launcher_path": str(submit_path),
        "setup_script_path": str(setup_path),
        "run_script_path": str(run_script_path),
        "runtime_mode": runtime_mode,
        "language": language,
        "editable_paths": editable_paths,
        "command_source": case_command_source,
        "inferred_case_commands": case_commands,
        "template_case_hints": template_case_hints,
        "reference_examples": reference_examples_payload,
        "created_files": created_files,
        "autogen_root": str(autogen_root),
        "prompt_rel": prompt_rel,
        "benchmark_rel": benchmark_rel,
        "runner_rel": runner_rel,
        "setup_rel": setup_rel,
        "run_script_rel": run_script_rel,
    }
    optimize_state.write_json_file(manifest_path, manifest_payload)

    return {
        "project_root": project_root,
        "prompt_path": prompt_path,
        "manifest_path": manifest_path,
        "benchmark_path": benchmark_path,
        "runner_path": runner_path,
        "submit_launcher_path": submit_path,
        "setup_script_path": setup_path,
        "run_script_path": run_script_path,
        "created_files": created_files,
        "runtime_mode": runtime_mode,
        "language": language,
        "editable_paths": editable_paths,
        "command_source": case_command_source,
        "reference_examples": reference_examples_payload,
        "benchmark_payload": benchmark_loaded,
        "benchmark_rel": benchmark_rel,
        "prompt_rel": prompt_rel,
        "runner_rel": runner_rel,
    }


def _resolve_status_project_root(args: argparse.Namespace) -> Path:
    cli = _cli()
    target = str(getattr(args, "project_path", None) or "").strip()
    if not target:
        target = "."
    return cli._resolve_project_path(target)


def read_campaign_status(args: argparse.Namespace) -> dict[str, Any]:
    cli = _cli()
    project_root = _resolve_status_project_root(args)
    if not project_root.is_dir():
        raise cli.PackageError(f"Optimize path is not a directory: {project_root}")
    state_path = optimize_state.state_path(project_root)
    results_path = optimize_state.results_path(project_root)
    lock_path = optimize_state.run_lock_path(project_root)
    state_payload = optimize_state.load_state(state_path) or {}
    lock_payload = optimize_state.load_run_lock(lock_path) or {}
    benchmark_rel = str(state_payload.get("benchmark_path") or "").strip()
    benchmark_path = (project_root / benchmark_rel).resolve() if benchmark_rel else None
    benchmark_payload: dict[str, Any] = {}
    if isinstance(benchmark_path, Path) and benchmark_path.is_file():
        try:
            benchmark_payload = _load_benchmark(benchmark_path)
        except Exception:
            benchmark_payload = {}
    primary_metric_name = str(
        _objective_config(benchmark_payload).get("primary_metric") or "primary_metric"
    )
    incumbent_metrics = (
        state_payload.get("incumbent_metrics")
        if isinstance(state_payload.get("incumbent_metrics"), dict)
        else {}
    )
    incumbent_primary = _objective_primary_for_context(
        benchmark_payload,
        incumbent_metrics=incumbent_metrics,
        primary_metric_name=primary_metric_name,
    )
    lock_pid = 0
    try:
        lock_pid = int(lock_payload.get("pid") or 0)
    except (TypeError, ValueError):
        lock_pid = 0
    run_lock_status = "inactive"
    if lock_pid > 0 and optimize_state.pid_is_running(lock_pid):
        run_lock_status = "active"
    elif lock_pid > 0:
        run_lock_status = "inactive_stale"
    launcher = state_payload.get("benchmark_launcher")
    launcher_status = "none"
    if isinstance(launcher, dict):
        source = str(launcher.get("source") or "unknown").strip() or "unknown"
        launcher_status = f"cached:{source}"
    runtime_mode = _runtime_mode(_runtime_config(benchmark_payload))
    tail = int(getattr(args, "tail", 30) or 30)
    if tail < 1:
        tail = 1
    recent_results = optimize_state.recent_results_text(results_path, limit=tail)
    return {
        "status": "ok" if state_payload else "missing",
        "project_root": str(project_root),
        "state_path": str(state_path),
        "results_path": str(results_path),
        "run_lock_path": str(lock_path),
        "run_lock_status": run_lock_status,
        "run_lock_pid": lock_pid if lock_pid > 0 else None,
        "run_lock_started_at": str(lock_payload.get("started_at_utc") or ""),
        "iteration": int(state_payload.get("iteration") or 0),
        "accepted_count": int(state_payload.get("accepted_count") or 0),
        "rejected_count": int(state_payload.get("rejected_count") or 0),
        "consecutive_rejections": int(state_payload.get("consecutive_rejections") or 0),
        "incumbent_commit": str(state_payload.get("incumbent_commit") or ""),
        "incumbent_primary_metric": incumbent_primary,
        "primary_metric_name": primary_metric_name,
        "runtime_mode": runtime_mode,
        "launcher_status": launcher_status,
        "recent_results": recent_results,
        "benchmark_path": (
            str(benchmark_path) if isinstance(benchmark_path, Path) else ""
        ),
    }


def run_quick_campaign(args: argparse.Namespace) -> dict[str, Any]:
    cli = _cli()
    project_root = Path.cwd().resolve()
    prompt_target = str(getattr(args, "package_id", None) or "").strip()
    if not prompt_target:
        raise cli.PackageError(
            "Quick optimize mode requires a prompt markdown path: "
            "`fermilink optimize prompt.md`."
        )
    prompt_path = _resolve_quick_prompt_path(project_root, prompt_target)
    if not prompt_path.is_file():
        raise cli.PackageError(
            f"Quick optimize prompt file does not exist: {prompt_path}"
        )
    cli._ensure_compile_repo_ready(project_root)
    package_id = cli.normalize_package_id(project_root.name or "package")
    hpc_profile = str(getattr(args, "hpc_profile", None) or "").strip()
    scaffold = _quick_scaffold(
        project_root,
        package_id=package_id,
        prompt_path=prompt_path,
        hpc_profile=hpc_profile,
    )
    campaign_args = argparse.Namespace(**vars(args))
    campaign_args.package_id = package_id
    campaign_args.project_path = str(project_root)
    campaign_args.benchmark = str(scaffold["benchmark_path"])
    campaign_args._optimize_mode = "quick"
    campaign_args._optimize_prompt_path = str(prompt_path)
    skills_source = str(
        getattr(campaign_args, "skills_source", "auto") or "auto"
    ).strip()
    if skills_source == "auto":
        campaign_args.skills_source = (
            "existing" if (project_root / "skills").is_dir() else "compile"
        )
    payload = run_campaign(campaign_args)
    payload["quick_mode"] = True
    payload["prompt_path"] = str(prompt_path)
    payload["scaffold_manifest_path"] = str(scaffold["manifest_path"])
    payload["scaffold_benchmark_path"] = str(scaffold["benchmark_path"])
    payload["scaffold_runner_path"] = str(scaffold["runner_path"])
    payload["generated_run_script_path"] = str(scaffold["run_script_path"])
    payload["generated_setup_script_path"] = str(scaffold["setup_script_path"])
    payload["scaffold_runtime_mode"] = str(scaffold["runtime_mode"])
    payload["scaffold_language"] = str(scaffold["language"])
    payload["scaffold_command_source"] = str(scaffold.get("command_source") or "")
    payload["scaffold_reference_examples"] = dict(
        scaffold.get("reference_examples") or {}
    )
    payload["scaffold_created_files"] = dict(scaffold["created_files"])
    return payload


# ---------------------------------------------------------------------------
# Goal mode
# ---------------------------------------------------------------------------

GOAL_MAX_ANALYSIS_TURNS = 3
GOAL_MAX_GENERATION_TURNS = 3
GOAL_MAX_REPAIR_ATTEMPTS = 2


def _goal_validation_cache_key(
    *,
    language: str,
    benchmark_text: str,
    runner_text: str,
) -> str:
    key_material = "\n".join(
        [
            "goal_validation_v1",
            str(language or "").strip().lower(),
            hashlib.sha256(benchmark_text.encode("utf-8")).hexdigest(),
            hashlib.sha256(runner_text.encode("utf-8")).hexdigest(),
        ]
    )
    return hashlib.sha256(key_material.encode("utf-8")).hexdigest()


def _goal_validation_cache_lookup(project_root: Path, cache_key: str) -> dict[str, Any] | None:
    state_path = optimize_state.state_path(project_root)
    state_payload = optimize_state.load_state(state_path)
    if not isinstance(state_payload, dict):
        return None
    cache_payload = state_payload.get(GOAL_VALIDATION_CACHE_STATE_KEY)
    if not isinstance(cache_payload, dict):
        return None
    entry = cache_payload.get(cache_key)
    if not isinstance(entry, dict):
        return None
    if not bool(entry.get("ok")):
        return None
    return entry


def _goal_validation_cache_store(
    project_root: Path,
    *,
    cache_key: str,
    language: str,
    benchmark_path: Path,
    runner_path: Path,
    benchmark_text: str,
    runner_text: str,
) -> None:
    state_path = optimize_state.state_path(project_root)
    state_payload = optimize_state.load_state(state_path)
    if not isinstance(state_payload, dict):
        return
    cache_payload = state_payload.get(GOAL_VALIDATION_CACHE_STATE_KEY)
    cache: dict[str, Any]
    if isinstance(cache_payload, dict):
        cache = copy.deepcopy(cache_payload)
    else:
        cache = {}
    cache[cache_key] = {
        "ok": True,
        "validated_at_utc": optimize_state.utc_now_z(),
        "language": str(language or "").strip().lower(),
        "benchmark_path": str(benchmark_path),
        "runner_path": str(runner_path),
        "benchmark_sha256": hashlib.sha256(benchmark_text.encode("utf-8")).hexdigest(),
        "runner_sha256": hashlib.sha256(runner_text.encode("utf-8")).hexdigest(),
    }
    if len(cache) > GOAL_VALIDATION_CACHE_MAX_ENTRIES:
        sortable_entries: list[tuple[str, str]] = []
        for key, value in cache.items():
            if not isinstance(value, dict):
                continue
            sortable_entries.append((key, str(value.get("validated_at_utc") or "")))
        for key, _stamp in sorted(sortable_entries, key=lambda item: item[1])[
            : len(cache) - GOAL_VALIDATION_CACHE_MAX_ENTRIES
        ]:
            cache.pop(key, None)
    state_payload[GOAL_VALIDATION_CACHE_STATE_KEY] = cache
    optimize_state.write_state(state_path, state_payload)


def _goal_runner_contract_errors(
    benchmark_payload: dict[str, Any],
    *,
    project_root: Path,
    benchmark_path: Path,
    runner_path: Path,
) -> list[str]:
    runtime = _runtime_config(benchmark_payload)
    mode = _runtime_mode(runtime)
    command = _normalize_string_command_list(runtime.get("command"))
    errors: list[str] = []
    if mode != "direct":
        errors.append("Goal benchmark runtime.mode must be `direct`.")
    if not command:
        errors.append("Goal benchmark runtime.command must be a non-empty string list.")
        return errors

    benchmark_flag_index = -1
    try:
        benchmark_flag_index = command.index("--benchmark")
    except ValueError:
        benchmark_flag_index = -1
    if benchmark_flag_index < 0:
        errors.append("Goal benchmark runtime.command must include `--benchmark`.")
    else:
        if benchmark_flag_index + 1 >= len(command):
            errors.append("Goal benchmark runtime.command `--benchmark` requires an argument.")
        else:
            benchmark_arg = str(command[benchmark_flag_index + 1] or "").strip()
            benchmark_rel = optimize_state.safe_relative(benchmark_path, project_root)
            benchmark_abs = str(benchmark_path)
            benchmark_matches = benchmark_arg in {benchmark_rel, benchmark_abs}
            if "{benchmark}" not in benchmark_arg and not benchmark_matches:
                errors.append(
                    "Goal benchmark runtime.command `--benchmark` argument must use "
                    "`{benchmark}` (or the generated benchmark path)."
                )
    if "--emit-json" not in command:
        errors.append("Goal benchmark runtime.command must include `--emit-json`.")

    runner_rel = optimize_state.safe_relative(runner_path, project_root).replace("\\", "/")
    runner_abs = str(runner_path).replace("\\", "/")
    runner_name = runner_path.name
    runner_referenced = False
    for token in command:
        normalized_token = str(token or "").strip().replace("\\", "/")
        if not normalized_token:
            continue
        token_name = Path(normalized_token).name if "/" in normalized_token else normalized_token
        if (
            normalized_token == runner_rel
            or normalized_token == runner_abs
            or token_name == runner_name
        ):
            runner_referenced = True
            break
    if not runner_referenced:
        errors.append(
            "Goal benchmark runtime.command must invoke the generated runner script "
            f"({runner_rel})."
        )
    return errors


def _tracked_file_summary(project_root: Path, tracked_files: list[str]) -> str:
    """Build a compact summary of the repo file tree for prompts."""

    if not tracked_files:
        return "(no tracked files)"
    dirs: dict[str, int] = {}
    for path in tracked_files:
        parts = path.split("/")
        if len(parts) > 1:
            top = parts[0]
        else:
            top = "."
        dirs[top] = dirs.get(top, 0) + 1
    lines: list[str] = []
    for dir_name in sorted(dirs):
        lines.append(f"  {dir_name}/ ({dirs[dir_name]} files)")
    if len(tracked_files) <= 80:
        lines.append("")
        lines.append("Full listing:")
        for path in sorted(tracked_files):
            lines.append(f"  {path}")
    else:
        lines.append(f"  (total: {len(tracked_files)} tracked files)")
    return "\n".join(lines)


def _goal_reference_template_text(
    project_root: Path,
    language: str,
) -> tuple[str, str]:
    """Load reference benchmark YAML and runner script as strings.

    Returns ``(benchmark_yaml_text, runner_script_text)`` for use as
    templates in the benchmark-generation prompt.
    """

    template = _load_quick_reference_template(project_root, language=language)
    benchmark_text = ""
    runner_text = ""
    if template:
        benchmark_path = str(template.get("benchmark_path") or "")
        runner_path = str(template.get("runner_path") or "")
        if benchmark_path:
            try:
                benchmark_text = Path(benchmark_path).read_text(encoding="utf-8")
            except OSError:
                pass
        if runner_path:
            try:
                runner_text = Path(runner_path).read_text(encoding="utf-8")
            except OSError:
                pass
    if not benchmark_text:
        benchmark_text = _render_quick_benchmark_yaml(
            {
                "schema_version": 1,
                "benchmark_id": "example",
                "controller": {
                    "timeout_seconds": 1800,
                    "warmup_runs": 1,
                    "measured_runs": 3,
                    "objective": {
                        "primary_metric": "weighted_median_wall_seconds",
                        "direction": "minimize",
                        "min_relative_improvement": 0.02,
                    },
                    "reject_on": [
                        "crash",
                        "timeout",
                        "missing_metrics",
                        "correctness_failure",
                    ],
                },
                "campaign": {
                    "max_iterations": 120,
                    "stop_on_consecutive_rejections": 30,
                },
                "worker": {"max_iterations": 8, "wait_seconds": 1},
                "correctness": {"mode": "runner_only"},
                "runtime": {
                    "mode": "direct",
                    "command": [
                        "python",
                        "runner.py",
                        "--benchmark",
                        "{benchmark}",
                        "--emit-json",
                    ],
                },
                "cases": [{"id": "example-case", "weight": 1.0}],
            }
        )
    if not runner_text:
        runner_text = _render_quick_runner_script()
    return benchmark_text, runner_text


def _validate_goal_benchmark(
    benchmark_path: Path,
) -> tuple[dict[str, Any] | None, str]:
    """Validate a generated benchmark YAML and return ``(payload, error)``.

    Returns the loaded payload on success or ``(None, error_message)`` on
    failure.
    """

    if not benchmark_path.is_file():
        return None, f"Benchmark file was not generated: {benchmark_path}"
    try:
        payload = _load_benchmark(benchmark_path)
    except Exception as exc:
        return None, f"Benchmark validation failed: {exc}"
    return payload, ""


def _validate_goal_runner(
    runner_path: Path,
    *,
    language: str,
    project_root: Path | None = None,
    benchmark_path: Path | None = None,
    benchmark_payload: dict[str, Any] | None = None,
) -> str:
    """Validate a generated benchmark runner and return an error string.

    Returns empty string on success.
    """

    if not runner_path.is_file():
        return f"Runner script was not generated: {runner_path}"
    try:
        source = runner_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Cannot read runner script: {exc}"
    if language == "python":
        try:
            compile(source, str(runner_path), "exec")
        except SyntaxError as exc:
            return f"Runner script has a syntax error: {exc}"
    if (
        isinstance(project_root, Path)
        and isinstance(benchmark_path, Path)
        and isinstance(benchmark_payload, dict)
    ):
        contract_errors = _goal_runner_contract_errors(
            benchmark_payload,
            project_root=project_root,
            benchmark_path=benchmark_path,
            runner_path=runner_path,
        )
        if contract_errors:
            return "; ".join(contract_errors)
    return ""


def _run_goal_analysis_turn(
    *,
    cli: Any,
    project_root: Path,
    goal_spec: dict[str, Any],
    goal_rel: str,
    language: str,
    tracked_files: list[str],
    autogen_rel: str,
    provider: str,
    provider_bin_override: str | None,
    sandbox_mode: str | None,
    sandbox_policy: str,
    model: str | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    """Run the source-analysis agent turn and return the result dict."""

    from fermilink.cli import optimize_source_analysis

    agents_md = optimize_source_analysis.build_source_analysis_agents_md(
        goal_rel=goal_rel,
        autogen_rel=autogen_rel,
    )
    prompt = optimize_source_analysis.build_source_analysis_prompt(
        goal_spec=goal_spec,
        goal_rel=goal_rel,
        language=language,
        tracked_file_summary=_tracked_file_summary(project_root, tracked_files),
    )
    with optimize_git.temporary_optimize_agents(
        project_root,
        provider=provider,
        content=agents_md,
    ):
        result = cli._run_exec_chat_turn(
            repo_dir=project_root,
            prompt=prompt,
            sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
            provider_bin_override=provider_bin_override,
            provider=provider,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    return result


def _run_goal_generation_turn(
    *,
    cli: Any,
    project_root: Path,
    goal_spec: dict[str, Any],
    goal_rel: str,
    analysis: dict[str, Any],
    analysis_rel: str,
    language: str,
    benchmark_template: str,
    runner_template: str,
    autogen_benchmark_rel: str,
    autogen_runner_rel: str,
    autogen_rel: str,
    provider: str,
    provider_bin_override: str | None,
    sandbox_mode: str | None,
    sandbox_policy: str,
    model: str | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    """Run the benchmark-generation agent turn and return the result dict."""

    from fermilink.cli import optimize_source_analysis

    agents_md = optimize_source_analysis.build_benchmark_generation_agents_md(
        goal_rel=goal_rel,
        analysis_rel=analysis_rel,
        autogen_rel=autogen_rel,
    )
    prompt = optimize_source_analysis.build_benchmark_generation_prompt(
        goal_spec=goal_spec,
        goal_rel=goal_rel,
        analysis=analysis,
        analysis_rel=analysis_rel,
        language=language,
        runner_template=runner_template,
        benchmark_template=benchmark_template,
        autogen_benchmark_rel=autogen_benchmark_rel,
        autogen_runner_rel=autogen_runner_rel,
    )
    with optimize_git.temporary_optimize_agents(
        project_root,
        provider=provider,
        content=agents_md,
    ):
        result = cli._run_exec_chat_turn(
            repo_dir=project_root,
            prompt=prompt,
            sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
            provider_bin_override=provider_bin_override,
            provider=provider,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    return result


def _goal_scaffold(
    project_root: Path,
    *,
    package_id: str,
    goal_spec: dict[str, Any],
    goal_path: Path,
    language: str,
    analysis: dict[str, Any],
    benchmark_yaml_text: str,
    runner_script_text: str,
    review_notes: str,
    hpc_profile: str,
) -> dict[str, Any]:
    """Write goal-generated benchmark and runner to the autogen directory.

    Returns a scaffold manifest similar to the quick-mode scaffold.
    """

    optimize_state.ensure_optimize_root(project_root)
    optimize_state.ensure_autogen_root(project_root)
    benchmark_path = optimize_state.goal_benchmark_path(project_root)
    runner_path = optimize_state.goal_runner_path(project_root)
    submit_path = optimize_state.goal_submit_launcher_path(project_root)
    setup_path = optimize_state.goal_setup_path(project_root)
    run_script_path = optimize_state.goal_run_script_path(project_root)
    manifest_path = optimize_state.goal_manifest_path(project_root)
    analysis_path = optimize_state.goal_analysis_path(project_root)

    goal_rel = optimize_state.safe_relative(goal_path, project_root)
    benchmark_rel = optimize_state.safe_relative(benchmark_path, project_root)
    runner_rel = optimize_state.safe_relative(runner_path, project_root)

    created_files: dict[str, bool] = {}

    # Write analysis
    optimize_state.write_json_file(analysis_path, analysis)
    created_files["analysis"] = True

    # Write benchmark YAML
    benchmark_path.parent.mkdir(parents=True, exist_ok=True)
    benchmark_path.write_text(benchmark_yaml_text, encoding="utf-8")
    created_files["benchmark"] = True

    # Write runner script
    runner_path.parent.mkdir(parents=True, exist_ok=True)
    runner_path.write_text(runner_script_text, encoding="utf-8")
    optimize_state.ensure_executable(runner_path)
    created_files["runner"] = True

    # Write support scripts (reuse quick-mode renderers)
    created_files["submit_launcher"] = _ensure_text_file(
        submit_path,
        content=_render_quick_submit_launcher_script(),
        executable=True,
    )
    created_files["setup"] = _ensure_text_file(
        setup_path,
        content=_render_quick_setup_script(project_root, language=language),
        executable=True,
    )
    created_files["run_script"] = _ensure_text_file(
        run_script_path,
        content=_render_quick_run_script(
            package_id=package_id,
            project_root=project_root,
            benchmark_path=benchmark_path,
            hpc_profile=hpc_profile,
        ),
        executable=True,
    )

    manifest_payload = {
        "schema_version": 1,
        "mode": "goal",
        "created_at_utc": optimize_state.utc_now_z(),
        "package_id": package_id,
        "project_root": str(project_root),
        "goal_path": str(goal_path),
        "goal_sha256": hashlib.sha256(
            str(goal_spec.get("raw_text") or "").encode("utf-8")
        ).hexdigest(),
        "benchmark_path": str(benchmark_path),
        "runner_path": str(runner_path),
        "analysis_path": str(analysis_path),
        "submit_launcher_path": str(submit_path),
        "setup_script_path": str(setup_path),
        "run_script_path": str(run_script_path),
        "language": language,
        "review_notes": review_notes,
        "created_files": created_files,
    }
    optimize_state.write_json_file(manifest_path, manifest_payload)

    return {
        "project_root": project_root,
        "goal_path": goal_path,
        "manifest_path": manifest_path,
        "benchmark_path": benchmark_path,
        "runner_path": runner_path,
        "analysis_path": analysis_path,
        "submit_launcher_path": submit_path,
        "setup_script_path": setup_path,
        "run_script_path": run_script_path,
        "created_files": created_files,
        "language": language,
        "review_notes": review_notes,
        "benchmark_rel": benchmark_rel,
        "goal_rel": goal_rel,
        "runner_rel": runner_rel,
    }


def _resolve_goal_resume_benchmark_path(project_root: Path) -> Path | None:
    """Resolve an existing goal-mode benchmark path for `--resume`."""

    goal_benchmark = optimize_state.goal_benchmark_path(project_root)
    if goal_benchmark.is_file():
        return goal_benchmark

    state_payload = optimize_state.load_state(optimize_state.state_path(project_root))
    if not isinstance(state_payload, dict):
        return None
    state_benchmark_raw = str(state_payload.get("benchmark_path") or "").strip()
    if not state_benchmark_raw:
        return None
    state_benchmark_path = Path(state_benchmark_raw).expanduser()
    if not state_benchmark_path.is_absolute():
        state_benchmark_path = (project_root / state_benchmark_path).resolve()
    if state_benchmark_path.is_file():
        return state_benchmark_path
    return None


def run_goal_campaign(args: argparse.Namespace) -> dict[str, Any]:
    """Goal mode: parse goal.md, analyse source, generate benchmark, then run campaign.

    This is the entry point for ``fermilink optimize goal.md`` when the
    markdown has goal-structured sections (or ``--goal`` is passed).

    Steps:
    1. Parse goal.md into a structured spec.
    2. Run source-analysis agent turn → structured JSON analysis.
    3. Run benchmark-generation agent turn → benchmark.yaml + runner.py.
    4. Validate generated files.
    5. Optionally retry generation if validation fails.
    6. Fall through to ``run_campaign()`` with generated benchmark.
    """

    from fermilink.cli import optimize_goal, optimize_source_analysis

    cli = _cli()
    project_root = Path.cwd().resolve()
    goal_target = str(getattr(args, "package_id", None) or "").strip()
    if not goal_target:
        raise cli.PackageError(
            "Goal optimize mode requires a goal markdown path: "
            "`fermilink optimize goal.md`."
        )
    goal_path = _resolve_quick_prompt_path(project_root, goal_target)
    if not goal_path.is_file():
        raise cli.PackageError(f"Goal file does not exist: {goal_path}")

    goal_text = goal_path.read_text(encoding="utf-8")
    goal_spec = optimize_goal.parse_goal(goal_text)
    goal_language = str(goal_spec.get("language") or "").strip().lower()

    cli._ensure_compile_repo_ready(project_root)
    package_id_raw = str(goal_spec.get("package") or "").strip()
    if not package_id_raw:
        package_id_raw = project_root.name or "package"
    package_id = cli.normalize_package_id(package_id_raw)

    hpc_profile = str(getattr(args, "hpc_profile", None) or "").strip()
    is_resume = bool(getattr(args, "resume", False))
    if is_resume:
        resume_benchmark_path = _resolve_goal_resume_benchmark_path(project_root)
        if isinstance(resume_benchmark_path, Path):
            resume_runner_path = optimize_state.goal_runner_path(project_root)
            resume_language = goal_language
            if not resume_language:
                suffix = resume_runner_path.suffix.strip().lower()
                if suffix == ".py":
                    resume_language = "python"
                elif suffix in {".sh", ".bash", ".zsh"}:
                    resume_language = "bash"
                else:
                    resume_language = "python"
            try:
                resume_benchmark_text = resume_benchmark_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise cli.PackageError(
                    f"Cannot read resume benchmark file: {exc}"
                ) from exc
            try:
                resume_runner_text = resume_runner_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise cli.PackageError(
                    f"Cannot read resume runner script: {exc}"
                ) from exc
            resume_cache_key = _goal_validation_cache_key(
                language=resume_language,
                benchmark_text=resume_benchmark_text,
                runner_text=resume_runner_text,
            )
            resume_cache_entry = _goal_validation_cache_lookup(
                project_root,
                resume_cache_key,
            )
            if resume_cache_entry is None:
                benchmark_payload, bench_error = _validate_goal_benchmark(
                    resume_benchmark_path
                )
                runner_error = _validate_goal_runner(
                    resume_runner_path,
                    language=resume_language,
                    project_root=project_root,
                    benchmark_path=resume_benchmark_path,
                    benchmark_payload=benchmark_payload,
                )
                if benchmark_payload is None:
                    raise cli.PackageError(
                        f"Goal resume benchmark is invalid: {bench_error}"
                    )
                if runner_error:
                    raise cli.PackageError(
                        f"Goal resume runner is invalid: {runner_error}"
                    )
            else:
                cli._print_tagged(
                    "optimize",
                    "goal mode: resume validation cache hit; skipping runner/benchmark revalidation",
                )
            cli._print_tagged(
                "optimize",
                (
                    "goal mode: --resume detected; reusing existing benchmark "
                    "artifacts and skipping source analysis/generation"
                ),
            )
            campaign_args = argparse.Namespace(**vars(args))
            campaign_args.package_id = package_id
            campaign_args.project_path = str(project_root)
            campaign_args.benchmark = str(resume_benchmark_path)
            campaign_args._optimize_mode = "goal"
            campaign_args._optimize_prompt_path = str(goal_path)
            skills_source = str(
                getattr(campaign_args, "skills_source", "auto") or "auto"
            ).strip()
            if skills_source == "auto":
                campaign_args.skills_source = (
                    "existing" if (project_root / "skills").is_dir() else "compile"
                )
            payload = run_campaign(campaign_args)
            payload["goal_mode"] = True
            payload["goal_path"] = str(goal_path)
            payload["goal_resume"] = True
            payload["scaffold_benchmark_path"] = str(resume_benchmark_path)
            _goal_validation_cache_store(
                project_root,
                cache_key=resume_cache_key,
                language=resume_language,
                benchmark_path=resume_benchmark_path,
                runner_path=resume_runner_path,
                benchmark_text=resume_benchmark_text,
                runner_text=resume_runner_text,
            )
            return payload

    tracked_files = _collect_tracked_files(project_root)

    # Infer language from goal spec or project structure
    language = (
        goal_language
        if goal_language
        else _infer_quick_language(project_root, tracked_files)
    )

    # Resolve provider settings
    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(getattr(args, "sandbox", None), str) and args.sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = args.sandbox.strip()
    model = runtime_policy.model
    reasoning_effort = runtime_policy.reasoning_effort
    provider_bin_override = cli.resolve_provider_binary_override(
        provider,
        raw_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
    )

    optimize_state.ensure_optimize_root(project_root)
    optimize_state.ensure_autogen_root(project_root)
    goal_rel = optimize_state.safe_relative(goal_path, project_root)
    autogen_rel = optimize_state.safe_relative(
        optimize_state.autogen_root(project_root), project_root
    )
    autogen_benchmark_rel = optimize_state.safe_relative(
        optimize_state.goal_benchmark_path(project_root), project_root
    )
    autogen_runner_rel = optimize_state.safe_relative(
        optimize_state.goal_runner_path(project_root), project_root
    )
    analysis_rel = optimize_state.safe_relative(
        optimize_state.goal_analysis_path(project_root), project_root
    )

    # ------------------------------------------------------------------
    # Phase 1: Source analysis
    # ------------------------------------------------------------------
    cli._print_tagged("optimize", "goal mode: analysing source code")
    analysis: dict[str, Any] = {}
    analysis_summary = ""
    review_notes = ""

    for attempt in range(1, GOAL_MAX_ANALYSIS_TURNS + 1):
        result = _run_goal_analysis_turn(
            cli=cli,
            project_root=project_root,
            goal_spec=goal_spec,
            goal_rel=goal_rel,
            language=language,
            tracked_files=tracked_files,
            autogen_rel=autogen_rel,
            provider=provider,
            provider_bin_override=provider_bin_override,
            sandbox_mode=sandbox_mode,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        assistant_text = str(result.get("assistant_text") or "")
        extracted = optimize_source_analysis.extract_source_analysis(assistant_text)
        if extracted:
            analysis = extracted
            analysis_summary = (
                optimize_source_analysis.extract_analysis_summary(assistant_text) or ""
            )
            review_notes = (
                optimize_source_analysis.extract_review_notes(assistant_text) or ""
            )
            break
        if attempt < GOAL_MAX_ANALYSIS_TURNS:
            cli._print_tagged(
                "optimize",
                f"source analysis attempt {attempt} did not produce structured output, retrying",
            )

    if not analysis:
        raise cli.PackageError(
            "Source analysis failed to produce a structured analysis after "
            f"{GOAL_MAX_ANALYSIS_TURNS} attempts.  Check the goal.md file and "
            "ensure the target package source code is accessible."
        )

    # Persist analysis
    optimize_state.write_json_file(
        optimize_state.goal_analysis_path(project_root), analysis
    )
    if analysis_summary:
        cli._print_tagged("optimize", f"analysis: {analysis_summary}")
    if review_notes:
        cli._print_tagged("optimize", f"review notes: {review_notes}")

    # ------------------------------------------------------------------
    # Phase 2: Benchmark generation
    # ------------------------------------------------------------------
    cli._print_tagged("optimize", "goal mode: generating benchmark files")
    benchmark_template, runner_template = _goal_reference_template_text(
        project_root, language
    )

    benchmark_yaml_text = ""
    runner_script_text = ""
    generation_review = ""

    for attempt in range(1, GOAL_MAX_GENERATION_TURNS + 1):
        gen_result = _run_goal_generation_turn(
            cli=cli,
            project_root=project_root,
            goal_spec=goal_spec,
            goal_rel=goal_rel,
            analysis=analysis,
            analysis_rel=analysis_rel,
            language=language,
            benchmark_template=benchmark_template,
            runner_template=runner_template,
            autogen_benchmark_rel=autogen_benchmark_rel,
            autogen_runner_rel=autogen_runner_rel,
            autogen_rel=autogen_rel,
            provider=provider,
            provider_bin_override=provider_bin_override,
            sandbox_mode=sandbox_mode,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        gen_text = str(gen_result.get("assistant_text") or "")

        # Try XML-tag extraction first; fall back to checking if the agent
        # wrote files directly (which is the preferred path since the agent
        # has tool access).
        extracted_yaml = optimize_source_analysis.extract_benchmark_yaml(gen_text)
        extracted_runner = optimize_source_analysis.extract_runner_script(gen_text)
        generation_review = (
            optimize_source_analysis.extract_review_notes(gen_text) or ""
        )

        bench_path = optimize_state.goal_benchmark_path(project_root)
        runner_path = optimize_state.goal_runner_path(project_root)

        if extracted_yaml:
            bench_path.parent.mkdir(parents=True, exist_ok=True)
            bench_path.write_text(extracted_yaml, encoding="utf-8")
        if extracted_runner:
            runner_path.parent.mkdir(parents=True, exist_ok=True)
            runner_path.write_text(extracted_runner, encoding="utf-8")
            optimize_state.ensure_executable(runner_path)

        # Read whatever is on disk (agent may have written directly)
        if bench_path.is_file():
            benchmark_yaml_text = bench_path.read_text(encoding="utf-8")
        if runner_path.is_file():
            runner_script_text = runner_path.read_text(encoding="utf-8")

        if benchmark_yaml_text and runner_script_text:
            break
        if attempt < GOAL_MAX_GENERATION_TURNS:
            cli._print_tagged(
                "optimize",
                f"benchmark generation attempt {attempt} incomplete, retrying",
            )

    if not benchmark_yaml_text or not runner_script_text:
        raise cli.PackageError(
            "Benchmark generation failed to produce both benchmark.yaml and "
            f"benchmark_runner.py after {GOAL_MAX_GENERATION_TURNS} attempts."
        )

    # ------------------------------------------------------------------
    # Phase 3: Validate generated files
    # ------------------------------------------------------------------
    cli._print_tagged("optimize", "goal mode: validating generated files")

    bench_path = optimize_state.goal_benchmark_path(project_root)
    runner_path = optimize_state.goal_runner_path(project_root)
    validation_cache_key = _goal_validation_cache_key(
        language=language,
        benchmark_text=benchmark_yaml_text,
        runner_text=runner_script_text,
    )
    validation_cache_entry = _goal_validation_cache_lookup(
        project_root,
        validation_cache_key,
    )

    if validation_cache_entry is not None:
        cli._print_tagged(
            "optimize",
            "goal mode: validation cache hit; skipping repeated benchmark/runner validation",
        )
        benchmark_payload, bench_error = _validate_goal_benchmark(bench_path)
        runner_error = ""
    else:
        benchmark_payload, bench_error = _validate_goal_benchmark(bench_path)
        runner_error = _validate_goal_runner(
            runner_path,
            language=language,
            project_root=project_root,
            benchmark_path=bench_path,
            benchmark_payload=benchmark_payload,
        )

    if (validation_cache_entry is None) and (bench_error or runner_error):
        errors = [e for e in (bench_error, runner_error) if e]
        cli._print_tagged("optimize", f"validation issues: {'; '.join(errors)}")
        # Attempt one repair cycle
        for repair_attempt in range(1, GOAL_MAX_REPAIR_ATTEMPTS + 1):
            cli._print_tagged(
                "optimize",
                f"goal mode: repair attempt {repair_attempt}",
            )
            repair_prompt = (
                "The previously generated benchmark files have validation errors.\n"
                f"Errors: {'; '.join(errors)}\n\n"
                "Please fix the files and write corrected versions to:\n"
                f"- `{autogen_benchmark_rel}`\n"
                f"- `{autogen_runner_rel}`\n\n"
                "The files must conform to the FermiLink benchmark contract as "
                "described in the previous turn.  Output the corrected files in\n"
                f"<{optimize_source_analysis.BENCHMARK_YAML_TAG}>...</{optimize_source_analysis.BENCHMARK_YAML_TAG}>\n"
                f"<{optimize_source_analysis.RUNNER_SCRIPT_TAG}>...</{optimize_source_analysis.RUNNER_SCRIPT_TAG}>\n"
                "tags.\n\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            )
            from fermilink.cli import optimize_source_analysis as osa

            repair_agents = osa.build_benchmark_generation_agents_md(
                goal_rel=goal_rel,
                analysis_rel=analysis_rel,
                autogen_rel=autogen_rel,
            )
            with optimize_git.temporary_optimize_agents(
                project_root,
                provider=provider,
                content=repair_agents,
            ):
                repair_result = cli._run_exec_chat_turn(
                    repo_dir=project_root,
                    prompt=repair_prompt,
                    sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
                    provider_bin_override=provider_bin_override,
                    provider=provider,
                    sandbox_policy=sandbox_policy,
                    model=model,
                    reasoning_effort=reasoning_effort,
                )
            repair_text = str(repair_result.get("assistant_text") or "")
            repaired_yaml = osa.extract_benchmark_yaml(repair_text)
            repaired_runner = osa.extract_runner_script(repair_text)
            if repaired_yaml:
                bench_path.write_text(repaired_yaml, encoding="utf-8")
            if repaired_runner:
                runner_path.write_text(repaired_runner, encoding="utf-8")
                optimize_state.ensure_executable(runner_path)
            # Re-read from disk (agent may have written directly)
            if bench_path.is_file():
                benchmark_yaml_text = bench_path.read_text(encoding="utf-8")
            if runner_path.is_file():
                runner_script_text = runner_path.read_text(encoding="utf-8")
            benchmark_payload, bench_error = _validate_goal_benchmark(bench_path)
            runner_error = _validate_goal_runner(
                runner_path,
                language=language,
                project_root=project_root,
                benchmark_path=bench_path,
                benchmark_payload=benchmark_payload,
            )
            if not bench_error and not runner_error:
                break

    if benchmark_payload is None:
        raise cli.PackageError(f"Generated benchmark.yaml is invalid: {bench_error}")
    if runner_error:
        raise cli.PackageError(f"Generated runner script is invalid: {runner_error}")
    validation_cache_key = _goal_validation_cache_key(
        language=language,
        benchmark_text=benchmark_yaml_text,
        runner_text=runner_script_text,
    )

    # ------------------------------------------------------------------
    # Phase 4: Write scaffold and delegate to run_campaign
    # ------------------------------------------------------------------
    all_review_notes = "\n".join(
        note for note in (review_notes, generation_review) if note
    )
    scaffold = _goal_scaffold(
        project_root,
        package_id=package_id,
        goal_spec=goal_spec,
        goal_path=goal_path,
        language=language,
        analysis=analysis,
        benchmark_yaml_text=benchmark_yaml_text,
        runner_script_text=runner_script_text,
        review_notes=all_review_notes,
        hpc_profile=hpc_profile,
    )

    cli._print_tagged(
        "optimize",
        "goal mode: benchmark files generated, starting optimization campaign",
    )

    # Prepare args for run_campaign
    campaign_args = argparse.Namespace(**vars(args))
    campaign_args.package_id = package_id
    campaign_args.project_path = str(project_root)
    campaign_args.benchmark = str(scaffold["benchmark_path"])
    campaign_args._optimize_mode = "goal"
    campaign_args._optimize_prompt_path = str(goal_path)
    skills_source = str(
        getattr(campaign_args, "skills_source", "auto") or "auto"
    ).strip()
    if skills_source == "auto":
        campaign_args.skills_source = (
            "existing" if (project_root / "skills").is_dir() else "compile"
        )

    payload = run_campaign(campaign_args)
    _goal_validation_cache_store(
        project_root,
        cache_key=validation_cache_key,
        language=language,
        benchmark_path=bench_path,
        runner_path=runner_path,
        benchmark_text=benchmark_yaml_text,
        runner_text=runner_script_text,
    )
    payload["goal_mode"] = True
    payload["goal_path"] = str(goal_path)
    payload["goal_analysis_path"] = str(scaffold["analysis_path"])
    payload["scaffold_manifest_path"] = str(scaffold["manifest_path"])
    payload["scaffold_benchmark_path"] = str(scaffold["benchmark_path"])
    payload["scaffold_runner_path"] = str(scaffold["runner_path"])
    payload["generated_run_script_path"] = str(scaffold["run_script_path"])
    payload["scaffold_language"] = str(scaffold["language"])
    payload["goal_review_notes"] = all_review_notes
    payload["scaffold_created_files"] = dict(scaffold["created_files"])
    return payload


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


def _write_benchmark_contract_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _normalize_rel_path(value: str) -> str:
    return str(value or "").strip().lstrip("./").replace("\\", "/").strip("/")


def _sync_controller_inputs_to_worker_repo(
    *,
    project_root: Path,
    worker_root: Path,
    rel_paths: set[str],
) -> None:
    normalized = sorted(
        {_normalize_rel_path(path) for path in rel_paths if _normalize_rel_path(path)}
    )
    root_resolved = project_root.resolve()
    for rel_path in normalized:
        source_path = (project_root / rel_path).resolve()
        try:
            source_path.relative_to(root_resolved)
        except ValueError:
            continue
        target_path = worker_root / rel_path
        if not source_path.exists():
            try:
                if target_path.is_dir() and not target_path.is_symlink():
                    shutil.rmtree(target_path)
                else:
                    target_path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        if source_path.is_dir() and not source_path.is_symlink():
            target_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)


def _collect_editable_files(root: Path, editable_paths: list[str]) -> set[str]:
    matches: set[str] = set()
    root_resolved = root.resolve()
    for current_dir, dirnames, filenames in os.walk(root):
        current = Path(current_dir).resolve()
        try:
            rel_dir = str(current.relative_to(root_resolved)).replace("\\", "/")
        except ValueError:
            continue
        if rel_dir in {".git", ".fermilink-optimize/runs"}:
            dirnames[:] = []
            continue
        filtered_dirnames: list[str] = []
        for item in dirnames:
            if item == ".git":
                continue
            candidate_rel = f"{rel_dir}/{item}" if rel_dir and rel_dir != "." else item
            candidate_rel = candidate_rel.replace("\\", "/")
            if candidate_rel == ".fermilink-optimize/runs":
                continue
            filtered_dirnames.append(item)
        dirnames[:] = filtered_dirnames
        for filename in filenames:
            rel_path = (
                f"{rel_dir}/{filename}" if rel_dir and rel_dir != "." else filename
            )
            rel_path = rel_path.replace("\\", "/").strip("/")
            if not rel_path:
                continue
            if _matches_any(rel_path, editable_paths):
                matches.add(rel_path)
    return matches


def _sync_worker_outputs_from_workspace(
    *,
    project_root: Path,
    worker_root: Path,
    worker_memory_rel: str,
    worker_memory_path: Path,
    editable_paths: list[str],
) -> None:
    src_worker_memory = worker_root / worker_memory_rel
    if src_worker_memory.is_file():
        worker_memory_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_worker_memory, worker_memory_path)

    worker_files = _collect_editable_files(worker_root, editable_paths)
    project_files = _collect_editable_files(project_root, editable_paths)

    for rel_path in sorted(worker_files):
        source_path = worker_root / rel_path
        target_path = project_root / rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source_path, target_path)
        except OSError:
            continue

    for rel_path in sorted(project_files - worker_files):
        target_path = project_root / rel_path
        try:
            target_path.unlink(missing_ok=True)
        except OSError:
            continue


def run_campaign(args: argparse.Namespace) -> dict[str, Any]:
    cli = _cli()
    package_id_raw = str(getattr(args, "package_id", None) or "").strip()
    project_path_raw = str(getattr(args, "project_path", None) or "").strip()
    benchmark_path_raw = str(getattr(args, "benchmark", None) or "").strip()
    if not package_id_raw or not project_path_raw or not benchmark_path_raw:
        raise cli.PackageError(
            "Optimize expert mode requires `<package_id> <project_path> --benchmark <path>`."
        )
    package_id = cli.normalize_package_id(package_id_raw)
    project_root = cli._resolve_project_path(project_path_raw)
    if not project_root.is_dir():
        raise cli.PackageError(f"Optimize path is not a directory: {project_root}")
    git_repo_initialized = cli._ensure_compile_repo_ready(project_root)
    benchmark_path = cli._resolve_project_path(benchmark_path_raw)
    if not benchmark_path.is_file():
        raise cli.PackageError(f"Benchmark file does not exist: {benchmark_path}")
    benchmark_payload = _load_benchmark(benchmark_path)
    worker_benchmark_payload, controller_benchmark_payload, benchmark_split = (
        _partition_benchmark_payload_by_split(benchmark_payload)
    )
    split_enabled = bool(benchmark_split.get("enabled"))
    train_case_ids = [
        str(item)
        for item in (benchmark_split.get("train_case_ids") or [])
        if str(item or "").strip()
    ]
    test_case_ids = [
        str(item)
        for item in (benchmark_split.get("test_case_ids") or [])
        if str(item or "").strip()
    ]
    benchmark_payload = worker_benchmark_payload

    optimize_git.ensure_local_excludes(project_root, [".fermilink-optimize/"])
    if (project_root / "skills").exists():
        optimize_git.ensure_local_excludes(project_root, ["skills/"])
    stale_instruction_paths = optimize_git.cleanup_stale_temporary_optimize_agents(
        project_root
    )
    if stale_instruction_paths:
        cli._print_tagged(
            "optimize",
            (
                "removed stale temporary instruction files: "
                f"{', '.join(stale_instruction_paths)}"
            ),
        )
    optimize_git.ensure_clean_repo(project_root, allow_dirty=bool(args.allow_dirty))
    run_mode = str(getattr(args, "_optimize_mode", "expert") or "expert")
    prompt_path_raw = str(getattr(args, "_optimize_prompt_path", None) or "").strip()
    prompt_path = Path(prompt_path_raw) if prompt_path_raw else None
    _write_campaign_run_lock(
        project_root,
        mode=run_mode,
        package_id=package_id,
        benchmark_path=benchmark_path,
        prompt_path=prompt_path,
    )
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
    worker_benchmark_path = benchmark_path
    if split_enabled:
        worker_benchmark_path = optimize_state.worker_benchmark_path(project_root)
        _write_benchmark_contract_file(worker_benchmark_path, benchmark_payload)
        cli._print_tagged(
            "optimize",
            (
                "benchmark split enabled: worker train cases="
                f"{len(train_case_ids)}, controller test cases={len(test_case_ids)}"
            ),
        )
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
    benchmark_rel = optimize_state.safe_relative(worker_benchmark_path, project_root)
    source_benchmark_rel = ""
    try:
        source_benchmark_rel = str(
            benchmark_path.resolve().relative_to(project_root.resolve())
        ).replace("\\", "/")
    except ValueError:
        source_benchmark_rel = ""
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
    hpc_constraints_block = _build_optimize_hpc_constraints_block(
        project_root,
        args=args,
    )
    hpc_profile_key = _resolve_hpc_profile_key(args)
    use_dynamic_submit_launcher = bool(
        hpc_profile_key
        and _runtime_mode(_runtime_config(benchmark_payload)) == "submit_poll"
    )
    evaluation_benchmark_payload = controller_benchmark_payload

    def _authoritative_benchmark_inputs(run_dir: Path) -> tuple[Path, str]:
        if not split_enabled:
            return benchmark_path, benchmark_rel
        benchmark_target_path = run_dir / "benchmark.controller.yaml"
        _write_benchmark_contract_file(
            benchmark_target_path,
            evaluation_benchmark_payload,
        )
        return (
            benchmark_target_path,
            optimize_state.safe_relative(benchmark_target_path, project_root),
        )

    if not str(state_payload.get("baseline_commit") or "").strip():
        cli._print_tagged("optimize", "running baseline benchmark")
        baseline_commit = optimize_git.head_sha(project_root)
        baseline_dir = optimize_state.runs_root(project_root) / "baseline"
        baseline_rel = optimize_state.safe_relative(baseline_dir, project_root)
        baseline_benchmark_path, baseline_benchmark_rel = (
            _authoritative_benchmark_inputs(baseline_dir)
        )
        baseline_metrics = _run_authoritative_benchmark_suite(
            project_root,
            benchmark_path=baseline_benchmark_path,
            benchmark_payload=evaluation_benchmark_payload,
            run_dir=baseline_dir,
            run_rel=baseline_rel,
            timeout_seconds=timeout_seconds,
            state_payload=state_payload,
            state_path=state_path,
            memory_path=memory_path,
            benchmark_rel=baseline_benchmark_rel,
            memory_rel=memory_rel,
            hpc_constraints_block=hpc_constraints_block,
            hpc_profile_key=hpc_profile_key,
            use_dynamic_submit_launcher=use_dynamic_submit_launcher,
            provider=provider,
            provider_bin_override=provider_bin_override,
            sandbox_mode=sandbox_mode,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
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
        state_payload["incumbent_metrics"] = _normalize_incumbent_metrics_for_state(
            evaluation_benchmark_payload,
            primary_metric_name=primary_metric_name,
            metrics=baseline_metrics,
        )
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

    baseline_metrics_for_correctness = (
        state_payload.get("baseline_metrics")
        if isinstance(state_payload.get("baseline_metrics"), dict)
        else {}
    )
    (
        evaluation_benchmark_payload,
        effective_correctness_info,
    ) = _effective_correctness_benchmark_payload(
        evaluation_benchmark_payload,
        baseline_metrics=baseline_metrics_for_correctness,
    )
    if bool(effective_correctness_info.get("upgraded")):
        field_tolerance_count = int(
            effective_correctness_info.get("field_tolerance_count") or 0
        )
        cli._print_tagged(
            "optimize",
            (
                "runner_only correctness upgraded to field_tolerances using baseline "
                f"metrics ({field_tolerance_count} inferred fields)"
            ),
        )
        state_payload["effective_correctness"] = {
            "mode": CORRECTNESS_MODE_FIELD_TOLERANCES,
            "source": "auto_inferred_from_baseline",
            "field_tolerance_count": field_tolerance_count,
            "updated_at_utc": optimize_state.utc_now_z(),
        }
        optimize_state.write_state(state_path, state_payload)

    if bool(getattr(args, "baseline_only", False)):
        incumbent_metrics = state_payload.get("incumbent_metrics")
        incumbent_summary = (
            incumbent_metrics.get("summary_metrics")
            if isinstance(incumbent_metrics, dict)
            else {}
        )
        incumbent_primary = _objective_primary_for_context(
            evaluation_benchmark_payload,
            incumbent_metrics=(
                incumbent_metrics if isinstance(incumbent_metrics, dict) else {}
            ),
            primary_metric_name=primary_metric_name,
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
                incumbent_primary
                if incumbent_primary is not None
                else (
                    incumbent_summary.get(primary_metric_name)
                    if isinstance(incumbent_summary, dict)
                    else None
                )
            ),
            "primary_metric_name": primary_metric_name,
            "accepted_count": int(state_payload.get("accepted_count") or 0),
            "rejected_count": int(state_payload.get("rejected_count") or 0),
            "status": "baseline_only",
        }

    worker_setup = optimize_git.ensure_worker_worktree(
        project_root,
        controller_branch=branch_name,
        start_commit=optimize_git.head_sha(project_root),
    )
    worker_repo_dir_raw = str(worker_setup.get("worker_root") or "").strip()
    if not worker_repo_dir_raw:
        raise cli.PackageError("Failed to resolve optimize worker worktree path.")
    worker_repo_dir = Path(worker_repo_dir_raw).resolve()
    if not worker_repo_dir.is_dir():
        raise cli.PackageError(
            f"Optimize worker worktree does not exist: {worker_repo_dir}"
        )
    optimize_git.ensure_local_excludes(worker_repo_dir, [".fermilink-optimize/"])
    if (project_root / "skills").exists():
        optimize_git.ensure_local_excludes(worker_repo_dir, ["skills/"])
        _sync_controller_inputs_to_worker_repo(
            project_root=project_root,
            worker_root=worker_repo_dir,
            rel_paths={"skills"},
        )

    worker_iteration_sync_paths: set[str] = {
        benchmark_rel,
        program_rel,
        memory_rel,
        worker_memory_rel,
        results_rel,
    }
    worker_hidden_paths: set[str] = {
        ".fermilink-optimize/runs",
        ".fermilink-optimize/state.json",
        ".fermilink-optimize/run.lock.json",
    }
    if split_enabled and source_benchmark_rel and source_benchmark_rel != benchmark_rel:
        worker_hidden_paths.add(source_benchmark_rel)

    editable_paths = _benchmark_editable_paths(benchmark_payload)
    immutable_paths = _benchmark_immutable_paths(benchmark_payload)
    worker_loop_config = _resolve_worker_loop_config(args, benchmark_payload)
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
        pre_iteration_untracked = set(optimize_git.list_untracked_paths(project_root))
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
        optimize_git.reset_worker_to_commit(worker_repo_dir, commit_sha=start_sha)
        optimize_git.clean_worker_untracked(worker_repo_dir)
        _sync_controller_inputs_to_worker_repo(
            project_root=project_root,
            worker_root=worker_repo_dir,
            rel_paths=worker_iteration_sync_paths,
        )
        optimize_git.cleanup_paths(worker_repo_dir, sorted(worker_hidden_paths))

        def _run_worker_turn(
            loop_iteration: int,
            _loop_max_iterations: int,
            prompt_text: str,
        ) -> dict[str, object]:
            result = cli._run_exec_chat_turn(
                repo_dir=worker_repo_dir,
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

        with optimize_git.with_worker_git_disabled(worker_repo_dir):
            with optimize_git.temporary_optimize_agents(
                worker_repo_dir,
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
        _sync_worker_outputs_from_workspace(
            project_root=project_root,
            worker_root=worker_repo_dir,
            worker_memory_rel=worker_memory_rel,
            worker_memory_path=worker_memory_path,
            editable_paths=editable_paths,
        )

        archived_worker_memory = optimize_state.archive_worker_memory(
            worker_memory_path,
            run_dir,
        )
        assistant_text = str(worker_loop_result.get("assistant_text") or "")
        final_worker_turn = worker_loop_result.get("run_result")
        final_worker_turn = (
            final_worker_turn if isinstance(final_worker_turn, dict) else {}
        )
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
        objective = _objective_config(evaluation_benchmark_payload)
        objective_direction = (
            str(objective.get("direction") or "minimize").strip().lower()
        )
        incumbent_primary = _objective_primary_for_context(
            evaluation_benchmark_payload,
            incumbent_metrics=incumbent_metrics,
            primary_metric_name=primary_metric_name,
        )
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
        controller_benchmark_rel_for_iteration = benchmark_rel

        evaluation_context: dict[str, Any] = {
            "worker_loop_status": str(worker_loop_result.get("status") or ""),
            "worker_loop_reason": str(worker_loop_result.get("reason") or ""),
            "worker_loop_iteration_count": int(
                worker_loop_result.get("iteration") or 0
            ),
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
            (
                controller_benchmark_path,
                controller_benchmark_rel_for_iteration,
            ) = _authoritative_benchmark_inputs(run_dir)
            candidate_metrics = _run_authoritative_benchmark_suite(
                project_root,
                benchmark_path=controller_benchmark_path,
                benchmark_payload=evaluation_benchmark_payload,
                run_dir=run_dir,
                run_rel=run_rel,
                timeout_seconds=timeout_seconds,
                state_payload=state_payload,
                state_path=state_path,
                memory_path=memory_path,
                benchmark_rel=controller_benchmark_rel_for_iteration,
                memory_rel=memory_rel,
                hpc_constraints_block=hpc_constraints_block,
                hpc_profile_key=hpc_profile_key,
                use_dynamic_submit_launcher=use_dynamic_submit_launcher,
                provider=provider,
                provider_bin_override=provider_bin_override,
                sandbox_mode=sandbox_mode,
                sandbox_policy=sandbox_policy,
                model=model,
                reasoning_effort=reasoning_effort,
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

            benchmark_status = str(candidate_metrics.get("status") or "unknown")
            if benchmark_status != "ok":
                hard_reject = True
                hard_status = benchmark_status
                hard_reason = f"benchmark {hard_status}"
            else:
                hard_validation = _hard_validate_candidate(
                    evaluation_benchmark_payload,
                    incumbent_metrics=incumbent_metrics,
                    candidate_metrics=candidate_metrics,
                )
                if hard_validation.get("correctness"):
                    evaluation_context["correctness"] = hard_validation.get(
                        "correctness"
                    )
                if hard_validation.get("performance_regression"):
                    evaluation_context["performance_regression"] = hard_validation.get(
                        "performance_regression"
                    )
                if hard_validation.get("category"):
                    evaluation_context["hard_reject_category"] = str(
                        hard_validation.get("category") or ""
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
                benchmark_rel=controller_benchmark_rel_for_iteration,
                program_rel=program_rel,
                memory_rel=memory_rel,
                results_rel=results_rel,
                run_rel=run_rel,
            )
            controller_prompt = optimize_prompts.build_controller_prompt(
                benchmark_payload=evaluation_benchmark_payload,
                benchmark_rel=controller_benchmark_rel_for_iteration,
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

        post_iteration_untracked = set(optimize_git.list_untracked_paths(project_root))
        cleanup_untracked = sorted(post_iteration_untracked - pre_iteration_untracked)

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
            if hard_reason:
                if controller_summary:
                    if hard_reason not in controller_summary:
                        controller_summary = f"{controller_summary}; {hard_reason}"
                else:
                    controller_summary = hard_reason
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
            state_payload["incumbent_metrics"] = _normalize_incumbent_metrics_for_state(
                evaluation_benchmark_payload,
                primary_metric_name=primary_metric_name,
                metrics=candidate_metrics,
            )
            optimize_git.cleanup_paths(project_root, cleanup_untracked)
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
            optimize_git.reset_to_commit(
                project_root,
                commit_sha=start_sha,
                cleanup_paths_list=cleanup_untracked,
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
    incumbent_primary = _objective_primary_for_context(
        evaluation_benchmark_payload,
        incumbent_metrics=(
            incumbent_metrics if isinstance(incumbent_metrics, dict) else {}
        ),
        primary_metric_name=primary_metric_name,
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
            incumbent_primary
            if incumbent_primary is not None
            else (
                incumbent_summary.get(primary_metric_name)
                if isinstance(incumbent_summary, dict)
                else None
            )
        ),
        "primary_metric_name": primary_metric_name,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "status": "completed",
    }
