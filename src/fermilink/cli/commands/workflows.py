from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from fermilink.config import resolve_fermilink_home
from fermilink.cli.workflow_prompts import (
    LOOP_MEMORY_DIRNAME,
    LOOP_MEMORY_FILENAME,
    LOOP_PID_TOKEN_RE,
    LOOP_SLURM_JOB_TOKEN_RE,
    LOOP_WAIT_TOKEN_RE,
    REPRODUCE_AUDITOR_PROMPT_PREFIX,
    REPRODUCE_LOGS_DIRNAME,
    REPRODUCE_PLAN_FILENAME,
    REPRODUCE_PLAN_TAG,
    REPRODUCE_PLAN_TOKEN_RE,
    REPRODUCE_PLANNER_PROMPT_PREFIX,
    REPRODUCE_PROMPTS_DIRNAME,
    REPRODUCE_STATE_FILENAME,
    RESEARCH_AUDITOR_PROMPT_PREFIX,
    RESEARCH_PLAN_TAG,
    RESEARCH_PLAN_TOKEN_RE,
    RESEARCH_PLANNER_PROMPT_PREFIX,
    WORKFLOW_DATA_AUDITOR_PROMPT_PREFIX,
    WORKFLOW_DATA_DIRNAME,
    WORKFLOW_DATA_MANIFEST_FULL_FILENAME,
    WORKFLOW_DATA_MANIFEST_FILENAME,
    WORKFLOW_DATA_SUMMARY_FILENAME,
    WORKFLOW_REPORT_AUDITOR_PROMPT_PREFIX,
    WORKFLOW_REPORT_FILENAME,
    WORKFLOW_REPORT_GENERATOR_PROMPT_PREFIX,
    WORKFLOW_SUMMARIES_DIRNAME,
    WORKFLOW_PLAN_UPDATE_TAG,
    WORKFLOW_PLAN_UPDATE_TOKEN_RE,
    WORKFLOW_POST_TASK_PLAN_AUDITOR_PROMPT_PREFIX,
    WORKFLOW_TASK_DATA_MAP_FILENAME,
    WORKFLOW_TASK_DATA_MAP_TAG,
    WORKFLOW_TASK_DATA_MAP_TOKEN_RE,
)


def _cli():
    from fermilink import cli

    return cli


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


WorkflowStatusHook = Callable[[str], None]


def _emit_workflow_status(
    workflow_status_hook: WorkflowStatusHook | None, mode_text: str
) -> None:
    if not callable(workflow_status_hook):
        return
    status_text = str(mode_text or "").strip()
    if not status_text:
        return
    try:
        workflow_status_hook(status_text)
    except Exception:
        # Keep workflow execution resilient if optional status hooks fail.
        return


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    cli = _cli()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        raise cli.PackageError(f"Failed to write file: {path}: {exc}") from exc


_GIT_COMMIT_MAX_FILES = 500
_GIT_COMMIT_MAX_BYTES = 100 * 1024 * 1024  # 100 MB


def _default_checkpoint_payload() -> dict[str, str]:
    return {
        "status": "noop",
        "sha": "",
        "error": "",
        "memory_only": "false",
    }


def _normalize_checkpoint_payload(payload: object) -> dict[str, str]:
    normalized = _default_checkpoint_payload()
    if not isinstance(payload, dict):
        return normalized
    for key in normalized:
        raw_value = payload.get(key)
        if raw_value is None:
            continue
        normalized[key] = str(raw_value)
    return normalized


def _sum_changed_file_bytes(repo_dir: Path, porcelain_lines: list[str]) -> int:
    """Return total on-disk bytes of new/modified files listed in git status --porcelain."""
    total = 0
    for line in porcelain_lines:
        if len(line) < 4:
            continue
        path_part = line[3:]
        # Rename format: "old -> new" — the new path is what lives on disk.
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]
        path_part = path_part.strip().strip('"')
        try:
            total += (repo_dir / path_part).stat().st_size
        except OSError:
            # Deleted files or unresolvable paths — skip.
            pass
    return total


def _workflow_checkpoint_commit(
    *,
    repo_dir: Path,
    commit_message: str,
) -> dict[str, str]:
    """Create a best-effort git checkpoint commit for workflow/session surfaces."""

    payload = _default_checkpoint_payload()
    if not (repo_dir / ".git").exists():
        return payload
    git_bin = shutil.which("git")
    if not git_bin:
        payload["status"] = "failed"
        payload["error"] = "git binary not found on PATH"
        return payload

    def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git_bin, *args],
            cwd=str(repo_dir),
            text=True,
            capture_output=True,
            check=False,
        )

    def _detail(proc: subprocess.CompletedProcess[str]) -> str:
        text = (proc.stderr or proc.stdout or "").strip()
        if not text:
            text = f"git command failed with exit code {proc.returncode}"
        return text[:500]

    repo_check = _run_git(["rev-parse", "--is-inside-work-tree"])
    if repo_check.returncode != 0:
        return payload

    # Inspect the working tree before touching the index so we never stage
    # large datasets into .git/objects.
    status_proc = _run_git(["status", "--porcelain"])
    if status_proc.returncode != 0:
        payload["status"] = "failed"
        payload["error"] = _detail(status_proc)
        return payload

    changed_lines = [l for l in (status_proc.stdout or "").splitlines() if l.strip()]
    file_count = len(changed_lines)
    total_bytes = _sum_changed_file_bytes(repo_dir, changed_lines)
    over_limit = (
        file_count > _GIT_COMMIT_MAX_FILES or total_bytes > _GIT_COMMIT_MAX_BYTES
    )

    if over_limit:
        memory_path = repo_dir / LOOP_MEMORY_DIRNAME / LOOP_MEMORY_FILENAME
        if not memory_path.is_file():
            # Nothing useful to commit; leave status as "noop".
            return payload
        memory_rel = str(memory_path.relative_to(repo_dir))
        add_proc = _run_git(["add", "--", memory_rel])
        commit_message = (
            f"{commit_message} [memory-only: {file_count} files / "
            f"{total_bytes // (1024 * 1024)} MB exceeded limit]"
        )
        payload["memory_only"] = "true"
    else:
        add_proc = _run_git(["add", "-A"])

    if add_proc.returncode != 0:
        payload["status"] = "failed"
        payload["error"] = _detail(add_proc)
        return payload

    staged_proc = _run_git(["diff", "--cached", "--quiet"])
    if staged_proc.returncode == 0:
        return payload
    if staged_proc.returncode != 1:
        payload["status"] = "failed"
        payload["error"] = _detail(staged_proc)
        return payload

    commit_proc = _run_git(["commit", "-m", commit_message])
    if commit_proc.returncode != 0:
        payload["status"] = "failed"
        payload["error"] = _detail(commit_proc)
        return payload

    sha_proc = _run_git(["rev-parse", "--verify", "HEAD"])
    if sha_proc.returncode != 0:
        payload["status"] = "failed"
        payload["error"] = _detail(sha_proc)
        return payload

    payload["status"] = "committed"
    payload["sha"] = (sha_proc.stdout or "").strip()
    return payload


def _workflow_completion_commit(
    *,
    repo_dir: Path,
    mode_name: str,
) -> dict[str, str]:
    """Create a best-effort repository checkpoint when a mode finishes."""

    mode_label = str(mode_name or "").strip() or "unknown"
    commit_message = f"fermilink {mode_label}: completion checkpoint {_utc_now_z()}"
    return _workflow_checkpoint_commit(
        repo_dir=repo_dir,
        commit_message=commit_message,
    )


def _attempt_mode_completion_commit(
    *,
    repo_dir: Path,
    args: argparse.Namespace,
    mode_name: str,
) -> dict[str, str]:
    """Best-effort completion commit shared by all modes (exec, loop, research, reproduce)."""
    if bool(getattr(args, "_fermilink_disable_completion_commit", False)):
        return _default_checkpoint_payload()
    try:
        return _normalize_checkpoint_payload(
            _workflow_completion_commit(
                repo_dir=repo_dir,
                mode_name=mode_name,
            )
        )
    except Exception as exc:
        # Keep command completion resilient if best-effort commit fails unexpectedly.
        payload = _default_checkpoint_payload()
        payload["status"] = "failed"
        payload["error"] = str(exc)[:500]
        return payload


DEFAULT_DATA_MAX_FILES = 4000
DEFAULT_DATA_MAX_TOTAL_BYTES = 1_073_741_824
DEFAULT_DATA_MAX_FILE_BYTES = 67_108_864
DEFAULT_DATA_HASH_MAX_BYTES = 1_048_576
DEFAULT_DATA_PROMPT_MAX_FILES = 240
DEFAULT_DATA_LARGE_THRESHOLD_FILES = 500
DEFAULT_DATA_LARGE_THRESHOLD_CHARS = 125_000
DEFAULT_DATA_TASK_SLICE_MAX_FILES = 120
DEFAULT_DATA_TASK_SLICE_MAX_CHARS = 45_000
DEFAULT_DATA_TASK_FALLBACK_FILES = 8

DATA_MANIFEST_SCHEMA_VERSION = 2
DATA_FILTER_RULES_VERSION = 1
DATA_MAPPING_MODE_GLOBAL = "global"
DATA_MAPPING_MODE_PER_TASK_LARGE = "per_task_large"

NOISE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".ipynb_checkpoints",
}
NOISE_BASENAME_EXACT = {".ds_store", "nohup.out"}
NOISE_SUFFIXES = {".tmp", ".swp", ".bak"}
SLURM_BASENAME_RE = re.compile(r"^slurm-[^/]+\.(?:out|err)$")
SBATCH_TOKEN_RE = re.compile(r"(^|[^A-Za-z0-9_])sbatch($|[^A-Za-z0-9_])")
SBATCH_PARSABLE_RE = re.compile(r"--parsable(?:\s|$)")
SBATCH_AFTEROK_RE = re.compile(r"--dependency\s*=\s*afterok\s*:")
WORKFLOW_FINAL_JOB_ID_MARKER = "FERMILINK_FINAL_JOB_ID="
WORKFLOW_UPSTREAM_JOB_ID_ENV = "FERMILINK_UPSTREAM_JOB_ID"
WORKFLOW_ALLINONE_BATCH_SCRIPT_FILENAME = "00_run_all.sh"
WORKFLOW_SIMULATION_BATCH_SCRIPT_FILENAME = "01_run_simulations.sh"
WORKFLOW_POSTPROCESS_BATCH_SCRIPT_FILENAME = "02_run_postprocess.sh"
WORKFLOW_PLOT_BATCH_SCRIPT_FILENAME = "03_run_plots.sh"
WORKFLOW_SIMULATION_JOB_MAP_FILENAME = "simulation_job_ids.tsv"
WORKFLOW_POSTPROCESS_JOB_MAP_FILENAME = "postprocess_job_ids.tsv"
WORKFLOW_PLOT_JOB_MAP_FILENAME = "plot_job_ids.tsv"
WORKFLOW_HPC_CONTRACT_ERRORS_FILENAME = "hpc_contract_errors.json"
WORKFLOW_HPC_VALIDATION_MAX_ATTEMPTS = 6
WORKFLOW_HPC_VALIDATION_STALL_LIMIT = 2
WORKFLOW_HPC_FEEDBACK_MAX_ISSUES = 80
WORKFLOW_TASK_SIMULATION_SCRIPT_FILENAME = "run_simulation.sh"
WORKFLOW_TASK_POSTPROCESS_SCRIPT_FILENAME = "run_postprocess.sh"
WORKFLOW_TASK_PLOT_SCRIPT_FILENAME = "run_plot.sh"
WORKFLOW_DEFAULT_HPC_PROFILE_FILENAME = "HPC_PROFILE.json"
WORKFLOW_LEGACY_HPC_PROFILE_FILENAME = "hpc_profile.json"
WORKFLOW_DEFAULT_HOME_HPC_COMMANDS = {"exec", "loop", "research", "reproduce"}

WORKFLOW_UNIFIED_MEMORY_STAGE_INSTRUCTIONS = (
    "Unified-memory requirements (apply in this stage):\n"
    "- Before acting, read `projects/memory.md`.\n"
    "- After completing this stage, update `projects/memory.md` with concise entries:\n"
    "  - `## Short-Term Memory (Operational) -> ### Plan`\n"
    "  - `## Short-Term Memory (Operational) -> ### Progress log`\n"
    "- Do not create duplicate memory section headings; update existing sections in place.\n"
    "- Update long-term sections only when there is durable information:\n"
    "  - `### File map` for stable file/purpose mapping changes.\n"
    "  - `### Simulation history` for run/job milestones and outcomes.\n"
    "  - `### Key results` for validated, reproducible outcomes (include artifact paths).\n"
    "  - `### Parameter source mapping` for simulation parameter/setting provenance.\n"
    "  - `### Simulation uncertainty` for uncertainty, assumptions, and confidence gaps.\n"
    "  - `### Suggested skills updates` for recurring failure patterns and concrete fixes.\n"
)

USEFUL_SUFFIXES = {
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".py",
    ".ipynb",
    ".xyz",
    ".cif",
    ".npy",
    ".npz",
    ".h5",
}

BOOST_PATH_TOKENS = {
    "input",
    "inputs",
    "config",
    "configs",
    "geometry",
    "geom",
    "postprocess",
    "post_process",
    "plot",
    "plots",
    "script",
    "scripts",
}
PENALTY_PATH_TOKENS = {"dump", "stderr", "stdout", "debug", "tmp", "temp", "cache"}

TASK_SLICE_COVERAGE_CATEGORIES = {
    "input",
    "config",
    "postprocess",
    "plot",
    "script",
}


def _repo_relative_path(repo_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_dir))
    except ValueError:
        return str(path)


def _normalize_positive_int(
    raw_value: object, *, flag_name: str, minimum: int = 1
) -> int:
    cli = _cli()
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError(f"{flag_name} must be an integer.") from exc
    if value < minimum:
        raise cli.PackageError(f"{flag_name} must be >= {minimum}.")
    return value


def _normalize_data_scan_limits(args: argparse.Namespace) -> dict[str, int]:
    max_files = _normalize_positive_int(
        getattr(args, "data_max_files", DEFAULT_DATA_MAX_FILES),
        flag_name="--data-max-files",
        minimum=1,
    )
    max_total_bytes = _normalize_positive_int(
        getattr(args, "data_max_total_bytes", DEFAULT_DATA_MAX_TOTAL_BYTES),
        flag_name="--data-max-total-bytes",
        minimum=1,
    )
    max_file_bytes = _normalize_positive_int(
        getattr(args, "data_max_file_bytes", DEFAULT_DATA_MAX_FILE_BYTES),
        flag_name="--data-max-file-bytes",
        minimum=1,
    )
    hash_max_bytes = _normalize_positive_int(
        getattr(args, "data_hash_max_bytes", DEFAULT_DATA_HASH_MAX_BYTES),
        flag_name="--data-hash-max-bytes",
        minimum=1,
    )
    if max_file_bytes > max_total_bytes:
        max_file_bytes = max_total_bytes
    if hash_max_bytes > max_file_bytes:
        hash_max_bytes = max_file_bytes
    return {
        "max_files": max_files,
        "max_total_bytes": max_total_bytes,
        "max_file_bytes": max_file_bytes,
        "hash_max_bytes": hash_max_bytes,
    }


def _default_data_filter_settings() -> dict[str, object]:
    return {
        "ignore_noise": True,
        "drop_slurm_outputs": True,
        "drop_cache_dirs": True,
        "drop_ephemeral_suffixes": True,
        "family_collapse": {
            "enabled": True,
            "max_examples_per_family": 3,
        },
    }


def _default_data_mapping_thresholds() -> dict[str, int]:
    return {
        "large_threshold_files": DEFAULT_DATA_LARGE_THRESHOLD_FILES,
        "large_threshold_chars": DEFAULT_DATA_LARGE_THRESHOLD_CHARS,
        "task_slice_max_files": DEFAULT_DATA_TASK_SLICE_MAX_FILES,
        "task_slice_max_chars": DEFAULT_DATA_TASK_SLICE_MAX_CHARS,
    }


def _default_local_hpc_context() -> dict[str, object]:
    return {
        "enabled": False,
        "mode": "local",
        "scheduler": "none",
        "source": "default_local",
        "profile": {},
    }


def _normalize_hpc_profile(
    *,
    raw_profile: dict[str, object],
    profile_path: Path,
    error_prefix: str = "--hpc-profile",
) -> dict[str, object]:
    cli = _cli()
    profile_label = str(profile_path)
    required_keys = (
        "slurm_default_partition",
        "slurm_defaults",
        "slurm_resource_policy",
    )
    normalized: dict[str, object] = {}
    for key in required_keys:
        raw_value = raw_profile.get(key)
        if raw_value is None:
            raise cli.PackageError(
                f"{error_prefix} missing required `{key}` in {profile_label}."
            )
        if not isinstance(raw_value, str):
            raise cli.PackageError(
                f"{error_prefix} `{key}` must be a non-empty string in {profile_label}."
            )
        text_value = " ".join(raw_value.strip().split())
        if not text_value:
            raise cli.PackageError(
                f"{error_prefix} `{key}` must be a non-empty string in {profile_label}."
            )
        normalized[key] = text_value
    return normalized


def _resolve_default_home_hpc_profile_path() -> Path | None:
    home = resolve_fermilink_home()
    for filename in (
        WORKFLOW_DEFAULT_HPC_PROFILE_FILENAME,
        WORKFLOW_LEGACY_HPC_PROFILE_FILENAME,
    ):
        candidate = home / filename
        if candidate.is_file():
            return candidate
    return None


def _resolve_invocation_hpc_context(
    *, repo_dir: Path, args: argparse.Namespace
) -> dict[str, object]:
    cli = _cli()
    command = str(getattr(args, "command", "") or "").strip().lower()
    raw_hpc_profile = str(getattr(args, "hpc_profile", "") or "").strip()
    source = "cli_hpc_profile"
    profile_path_input = raw_hpc_profile
    if raw_hpc_profile:
        resolved_profile_path = cli._resolve_project_path(raw_hpc_profile)
        if not resolved_profile_path.exists():
            raise cli.PackageError(
                f"--hpc-profile does not exist: {resolved_profile_path}"
            )
        if not resolved_profile_path.is_file():
            raise cli.PackageError(
                f"--hpc-profile must be a file: {resolved_profile_path}"
            )
        try:
            payload = json.loads(resolved_profile_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise cli.PackageError(
                f"Failed to read --hpc-profile: {resolved_profile_path}: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise cli.PackageError(
                f"--hpc-profile must contain valid JSON: {resolved_profile_path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise cli.PackageError(
                f"--hpc-profile root JSON must be an object: {resolved_profile_path}"
            )
        normalized_profile = _normalize_hpc_profile(
            raw_profile=payload,
            profile_path=resolved_profile_path,
        )
    else:
        if command not in WORKFLOW_DEFAULT_HOME_HPC_COMMANDS:
            return _default_local_hpc_context()
        resolved_profile_path = _resolve_default_home_hpc_profile_path()
        if resolved_profile_path is None:
            return _default_local_hpc_context()
        try:
            payload = json.loads(resolved_profile_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise cli.PackageError(
                f"Failed to read default HPC profile: {resolved_profile_path}: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise cli.PackageError(
                "Default HPC profile must contain valid JSON: "
                f"{resolved_profile_path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise cli.PackageError(
                f"Default HPC profile root JSON must be an object: {resolved_profile_path}"
            )
        normalized_profile = _normalize_hpc_profile(
            raw_profile=payload,
            profile_path=resolved_profile_path,
            error_prefix="Default HPC profile",
        )
        source = "default_home_hpc_profile"
        profile_path_input = str(resolved_profile_path)

    fingerprint = hashlib.sha256(
        json.dumps(normalized_profile, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "enabled": True,
        "mode": "hpc_slurm",
        "scheduler": "slurm",
        "source": source,
        "profile_path": str(resolved_profile_path),
        "profile_path_input": profile_path_input,
        "profile_relpath": _repo_relative_path(repo_dir, resolved_profile_path),
        "fingerprint": fingerprint,
        "profile": normalized_profile,
    }


def _is_hpc_context_enabled(hpc_context: object) -> bool:
    return isinstance(hpc_context, dict) and bool(hpc_context.get("enabled"))


def _coerce_saved_hpc_context(state: dict[str, object]) -> dict[str, object]:
    raw = state.get("hpc_context")
    if not isinstance(raw, dict):
        return _default_local_hpc_context()

    enabled = bool(raw.get("enabled"))
    if not enabled:
        normalized_local = _default_local_hpc_context()
        source = str(raw.get("source") or "").strip()
        if source:
            normalized_local["source"] = source
        return normalized_local

    profile = raw.get("profile")
    normalized: dict[str, object] = {
        "enabled": True,
        "mode": "hpc_slurm",
        "scheduler": "slurm",
        "source": str(raw.get("source") or "cli_hpc_profile").strip()
        or "cli_hpc_profile",
        "profile": profile if isinstance(profile, dict) else {},
    }
    profile_path = str(raw.get("profile_path") or "").strip()
    if profile_path:
        normalized["profile_path"] = profile_path
    profile_path_input = str(raw.get("profile_path_input") or "").strip()
    if profile_path_input:
        normalized["profile_path_input"] = profile_path_input
    profile_relpath = str(raw.get("profile_relpath") or "").strip()
    if profile_relpath:
        normalized["profile_relpath"] = profile_relpath
    fingerprint = str(raw.get("fingerprint") or "").strip()
    if fingerprint:
        normalized["fingerprint"] = fingerprint
    return normalized


def _assert_hpc_context_compatible(
    *,
    run_id: str,
    workflow_name: str,
    state_hpc_context: dict[str, object],
    invocation_hpc_context: dict[str, object],
) -> None:
    cli = _cli()
    state_enabled = bool(state_hpc_context.get("enabled"))
    invocation_enabled = bool(invocation_hpc_context.get("enabled"))
    if state_enabled != invocation_enabled:
        expected = "--hpc-profile <json>" if state_enabled else "without --hpc-profile"
        current = (
            "--hpc-profile <json>" if invocation_enabled else "without --hpc-profile"
        )
        raise cli.PackageError(
            f"Run {run_id} was created {expected}; current invocation is {current}. "
            "Use --restart or rerun with matching --hpc-profile mode."
        )
    if not state_enabled:
        return

    state_scheduler = str(state_hpc_context.get("scheduler") or "").strip().lower()
    invocation_scheduler = (
        str(invocation_hpc_context.get("scheduler") or "").strip().lower()
    )
    if state_scheduler != invocation_scheduler:
        raise cli.PackageError(
            f"Run {run_id} was created with scheduler={state_scheduler!r}; "
            f"current {workflow_name} invocation uses {invocation_scheduler!r}. "
            "Use --restart or rerun with matching --hpc-profile."
        )

    state_profile_path = str(state_hpc_context.get("profile_path") or "").strip()
    invocation_profile_path = str(
        invocation_hpc_context.get("profile_path") or ""
    ).strip()
    if (
        state_profile_path
        and invocation_profile_path
        and state_profile_path != invocation_profile_path
    ):
        raise cli.PackageError(
            f"Run {run_id} was created with --hpc-profile={state_profile_path!r}; "
            f"current {workflow_name} invocation uses {invocation_profile_path!r}. "
            "Use --restart or rerun with matching --hpc-profile."
        )

    state_fingerprint = str(state_hpc_context.get("fingerprint") or "").strip()
    invocation_fingerprint = str(
        invocation_hpc_context.get("fingerprint") or ""
    ).strip()
    if (
        state_fingerprint
        and invocation_fingerprint
        and state_fingerprint != invocation_fingerprint
    ):
        raise cli.PackageError(
            f"Run {run_id} was created with a different --hpc-profile content fingerprint. "
            "Use --restart or rerun with matching --hpc-profile content."
        )


def _build_hpc_prompt_lines(hpc_context: dict[str, object] | None) -> list[str]:
    context = (
        hpc_context if isinstance(hpc_context, dict) else _default_local_hpc_context()
    )
    lines: list[str] = []
    if not bool(context.get("enabled")):
        lines.extend(
            [
                "- execution_target: local machine (workflow default).",
                "- Do not assume SLURM/HPC resources by default.",
                "- Generate local-run-ready commands/scripts by default.",
            ]
        )
        return lines

    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    default_partition = str(profile.get("slurm_default_partition") or "").strip()
    slurm_defaults = str(profile.get("slurm_defaults") or "").strip()
    slurm_resource_policy = str(profile.get("slurm_resource_policy") or "").strip()
    lines.extend(
        [
            "- execution_target: HPC SLURM.",
            (
                f"- slurm_default_partition: `{default_partition}`."
                if default_partition
                else "- slurm_default_partition: not specified."
            ),
        ]
    )
    lines.append(
        (
            f"- slurm_defaults: `{slurm_defaults}`."
            if slurm_defaults
            else "- slurm_defaults: not specified."
        )
    )
    if slurm_resource_policy:
        if slurm_resource_policy.endswith((".", "!", "?")):
            lines.append(f"- slurm_resource_policy: {slurm_resource_policy}")
        else:
            lines.append(f"- slurm_resource_policy: {slurm_resource_policy}.")
    else:
        lines.append("- slurm_resource_policy: not specified.")
    lines.append(
        "- Generate SLURM-ready scripts and machine-tuned run instructions using this profile."
    )
    return lines


def _resolve_invocation_data_context(
    *,
    repo_dir: Path,
    run_dir: Path,
    workflow_name: str,
    args: argparse.Namespace,
) -> dict[str, object]:
    cli = _cli()
    limits = _normalize_data_scan_limits(args)
    filter_settings = _default_data_filter_settings()
    mapping_thresholds = _default_data_mapping_thresholds()
    raw_data_dir = str(getattr(args, "data_dir", "") or "").strip()
    if not raw_data_dir:
        return {
            "enabled": False,
            "workflow": workflow_name,
            "read_only": True,
            "limits": limits,
            "filter_settings": filter_settings,
            "mapping_thresholds": mapping_thresholds,
            "artifacts": {},
        }

    resolved_data_dir = cli._resolve_project_path(raw_data_dir)
    if not resolved_data_dir.exists():
        raise cli.PackageError(f"--data-dir does not exist: {resolved_data_dir}")
    if not resolved_data_dir.is_dir():
        raise cli.PackageError(f"--data-dir must be a directory: {resolved_data_dir}")
    try:
        # Force an explicit readability check instead of silently degrading.
        next(resolved_data_dir.iterdir(), None)
    except OSError as exc:
        raise cli.PackageError(
            f"--data-dir is not readable: {resolved_data_dir}: {exc}"
        ) from exc

    data_artifacts_root = run_dir / WORKFLOW_DATA_DIRNAME
    data_manifest_full_path = data_artifacts_root / WORKFLOW_DATA_MANIFEST_FULL_FILENAME
    data_manifest_path = data_artifacts_root / WORKFLOW_DATA_MANIFEST_FILENAME
    data_summary_path = data_artifacts_root / WORKFLOW_DATA_SUMMARY_FILENAME
    task_data_map_path = data_artifacts_root / WORKFLOW_TASK_DATA_MAP_FILENAME
    return {
        "enabled": True,
        "workflow": workflow_name,
        "source_path": str(resolved_data_dir),
        "source_path_input": raw_data_dir,
        "read_only": not bool(getattr(args, "data_writable", False)),
        "limits": limits,
        "filter_settings": filter_settings,
        "mapping_thresholds": mapping_thresholds,
        "artifacts": {
            "root": _repo_relative_path(repo_dir, data_artifacts_root),
            "manifest_full": _repo_relative_path(repo_dir, data_manifest_full_path),
            "manifest_compact": _repo_relative_path(repo_dir, data_manifest_path),
            "manifest": _repo_relative_path(repo_dir, data_manifest_path),
            "summary": _repo_relative_path(repo_dir, data_summary_path),
            "task_map": _repo_relative_path(repo_dir, task_data_map_path),
        },
    }


def _is_data_context_enabled(data_context: object) -> bool:
    return isinstance(data_context, dict) and bool(data_context.get("enabled"))


def _coerce_saved_data_context(state: dict[str, object]) -> dict[str, object]:
    raw = state.get("data_context")
    if not isinstance(raw, dict):
        return {
            "enabled": False,
            "read_only": True,
            "limits": {},
            "filter_settings": _default_data_filter_settings(),
            "mapping_thresholds": _default_data_mapping_thresholds(),
            "artifacts": {},
        }
    enabled = bool(raw.get("enabled"))
    artifacts = raw.get("artifacts")
    normalized_artifacts: dict[str, object] = {}
    if isinstance(artifacts, dict):
        normalized_artifacts = dict(artifacts)
        manifest_rel = str(artifacts.get("manifest") or "").strip()
        if manifest_rel and not str(artifacts.get("manifest_compact") or "").strip():
            normalized_artifacts["manifest_compact"] = manifest_rel
        manifest_full_rel = str(artifacts.get("manifest_full") or "").strip()
        if manifest_full_rel and not manifest_rel:
            normalized_artifacts["manifest"] = manifest_full_rel

    normalized: dict[str, object] = {
        "enabled": enabled,
        "read_only": bool(raw.get("read_only", True)),
        "limits": raw.get("limits") if isinstance(raw.get("limits"), dict) else {},
        "filter_settings": (
            raw.get("filter_settings")
            if isinstance(raw.get("filter_settings"), dict)
            else _default_data_filter_settings()
        ),
        "mapping_thresholds": (
            raw.get("mapping_thresholds")
            if isinstance(raw.get("mapping_thresholds"), dict)
            else _default_data_mapping_thresholds()
        ),
        "artifacts": normalized_artifacts,
    }
    source_path = str(raw.get("source_path") or "").strip()
    if source_path:
        normalized["source_path"] = source_path
    source_path_input = str(raw.get("source_path_input") or "").strip()
    if source_path_input:
        normalized["source_path_input"] = source_path_input
    manifest_fingerprint = str(raw.get("manifest_fingerprint") or "").strip()
    if manifest_fingerprint:
        normalized["manifest_fingerprint"] = manifest_fingerprint
    manifest_full_fingerprint = str(raw.get("manifest_full_fingerprint") or "").strip()
    if manifest_full_fingerprint:
        normalized["manifest_full_fingerprint"] = manifest_full_fingerprint
    manifest_compact_fingerprint = str(
        raw.get("manifest_compact_fingerprint") or ""
    ).strip()
    if manifest_compact_fingerprint:
        normalized["manifest_compact_fingerprint"] = manifest_compact_fingerprint
    mapping_mode = str(raw.get("mapping_mode") or "").strip()
    if mapping_mode:
        normalized["mapping_mode"] = mapping_mode
    manifest_stats = raw.get("manifest_stats")
    if isinstance(manifest_stats, dict):
        normalized["manifest_stats"] = manifest_stats
    return normalized


def _assert_data_context_compatible(
    *,
    run_id: str,
    workflow_name: str,
    state_data_context: dict[str, object],
    invocation_data_context: dict[str, object],
) -> None:
    cli = _cli()
    state_enabled = bool(state_data_context.get("enabled"))
    invocation_enabled = bool(invocation_data_context.get("enabled"))
    if state_enabled != invocation_enabled:
        expected = "--data-dir <path>" if state_enabled else "without --data-dir"
        current = "--data-dir <path>" if invocation_enabled else "without --data-dir"
        raise cli.PackageError(
            f"Run {run_id} was created {expected}; current invocation is {current}. "
            "Use --restart or rerun with matching data-dir mode."
        )
    if not state_enabled:
        return

    state_source = str(state_data_context.get("source_path") or "").strip()
    invocation_source = str(invocation_data_context.get("source_path") or "").strip()
    if state_source != invocation_source:
        raise cli.PackageError(
            f"Run {run_id} was created with --data-dir={state_source!r}; "
            f"current {workflow_name} invocation uses {invocation_source!r}. "
            "Use --restart or rerun with matching --data-dir."
        )
    if bool(state_data_context.get("read_only", True)) != bool(
        invocation_data_context.get("read_only", True)
    ):
        expected = (
            "read-only"
            if bool(state_data_context.get("read_only", True))
            else "writable"
        )
        current = (
            "read-only"
            if bool(invocation_data_context.get("read_only", True))
            else "writable"
        )
        raise cli.PackageError(
            f"Run {run_id} data mode is {expected}; current invocation is {current}. "
            "Use --restart or rerun with matching data write policy."
        )

    state_limits = (
        state_data_context.get("limits")
        if isinstance(state_data_context.get("limits"), dict)
        else {}
    )
    invocation_limits = (
        invocation_data_context.get("limits")
        if isinstance(invocation_data_context.get("limits"), dict)
        else {}
    )
    if state_limits != invocation_limits:
        raise cli.PackageError(
            f"Run {run_id} was created with different data scan limits. "
            "Use --restart or rerun with matching --data-max-* flags."
        )

    state_filter_settings = (
        state_data_context.get("filter_settings")
        if isinstance(state_data_context.get("filter_settings"), dict)
        else {}
    )
    invocation_filter_settings = (
        invocation_data_context.get("filter_settings")
        if isinstance(invocation_data_context.get("filter_settings"), dict)
        else {}
    )
    if state_filter_settings != invocation_filter_settings:
        raise cli.PackageError(
            f"Run {run_id} was created with different data filtering settings. "
            "Use --restart or rerun with matching filter configuration."
        )

    state_mapping_thresholds = (
        state_data_context.get("mapping_thresholds")
        if isinstance(state_data_context.get("mapping_thresholds"), dict)
        else {}
    )
    invocation_mapping_thresholds = (
        invocation_data_context.get("mapping_thresholds")
        if isinstance(invocation_data_context.get("mapping_thresholds"), dict)
        else {}
    )
    if state_mapping_thresholds != invocation_mapping_thresholds:
        raise cli.PackageError(
            f"Run {run_id} was created with different data mapping thresholds. "
            "Use --restart or rerun with matching thresholds."
        )


def _infer_data_file_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".csv", ".tsv", ".txt", ".dat"}:
        return "tabular_or_text"
    if ext in {".json", ".yaml", ".yml", ".toml", ".ini", ".xml"}:
        return "structured_text"
    if ext in {
        ".py",
        ".ipynb",
        ".jl",
        ".m",
        ".r",
        ".c",
        ".h",
        ".hpp",
        ".cpp",
        ".f90",
    }:
        return "code"
    if ext in {".md", ".rst", ".tex", ".pdf"}:
        return "document"
    if ext in {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".tif", ".tiff"}:
        return "image"
    if ext in {".npy", ".npz", ".h5", ".hdf5", ".mat", ".bin"}:
        return "binary_data"
    if ext in {".log", ".out"}:
        return "log"
    if ext in {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z"}:
        return "archive"
    return "unknown"


def _data_file_usefulness_score(entry: dict[str, object]) -> float:
    file_type = str(entry.get("type") or "unknown")
    size = int(entry.get("size") or 0)
    path = str(entry.get("path") or "").replace("\\", "/")
    suffix = Path(path).suffix.lower()
    basename = Path(path).name.lower()
    score = 0.0
    if file_type == "tabular_or_text":
        score += 4.0
    elif file_type == "structured_text":
        score += 3.5
    elif file_type == "binary_data":
        score += 3.0
    elif file_type == "document":
        score += 2.5
    elif file_type == "code":
        score += 2.0
    elif file_type == "log":
        score += 1.5
    elif file_type == "unknown":
        score += 0.8

    lowered = path.lower()
    if any(token in lowered for token in BOOST_PATH_TOKENS):
        score += 1.5
    if any(
        token in lowered
        for token in (
            "param",
            "dataset",
            "data",
            "reference",
            "figure",
            "result",
        )
    ):
        score += 1.2
    if suffix in USEFUL_SUFFIXES:
        score += 1.0
    if any(token in lowered for token in PENALTY_PATH_TOKENS):
        score -= 1.2
    if basename.startswith("slurm-") and basename.endswith((".out", ".err")):
        score -= 4.0
    if basename in {"nohup.out"}:
        score -= 4.0
    if size == 0:
        score -= 1.0
    elif size > 0 and size <= 1024:
        score -= 0.3
    return score


def _scan_data_dir_inventory(
    *,
    data_dir: Path,
    max_files: int,
    max_total_bytes: int,
    max_file_bytes: int,
    hash_max_bytes: int,
    include_hash: bool,
) -> dict[str, object]:
    files: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    indexed_bytes = 0
    truncated_reason = ""
    fingerprint = hashlib.sha256()
    fingerprint.update(f"root={data_dir}\n".encode("utf-8"))
    fingerprint.update(
        (
            "limits="
            f"{max_files}:{max_total_bytes}:{max_file_bytes}:{hash_max_bytes}\n"
        ).encode("utf-8")
    )

    stop_scan = False
    for root, dir_names, file_names in os.walk(data_dir):
        dir_names.sort()
        file_names.sort()
        root_path = Path(root)
        for file_name in file_names:
            file_path = root_path / file_name
            try:
                file_stat = file_path.stat()
            except OSError as exc:
                rel_unreadable = _repo_relative_path(data_dir, file_path).replace(
                    "\\", "/"
                )
                skipped_item = {
                    "path": rel_unreadable,
                    "reason": "unreadable",
                    "error": str(exc),
                }
                skipped.append(skipped_item)
                fingerprint.update(f"S|{rel_unreadable}|unreadable\n".encode("utf-8"))
                continue

            rel_path = _repo_relative_path(data_dir, file_path).replace("\\", "/")
            size = int(file_stat.st_size)
            mtime_ns = int(file_stat.st_mtime_ns)
            if size > max_file_bytes:
                skipped_item = {
                    "path": rel_path,
                    "size": size,
                    "mtime_ns": mtime_ns,
                    "reason": "file_too_large",
                }
                skipped.append(skipped_item)
                fingerprint.update(
                    f"S|{rel_path}|{size}|{mtime_ns}|file_too_large\n".encode("utf-8")
                )
                continue
            if len(files) >= max_files:
                truncated_reason = "max_files"
                stop_scan = True
                break
            if indexed_bytes + size > max_total_bytes:
                truncated_reason = "max_total_bytes"
                stop_scan = True
                break

            item: dict[str, object] = {
                "path": rel_path,
                "size": size,
                "mtime_ns": mtime_ns,
                "type": _infer_data_file_type(file_path),
            }
            if include_hash and size <= hash_max_bytes:
                try:
                    item["sha256"] = hashlib.sha256(file_path.read_bytes()).hexdigest()
                except OSError as exc:
                    item["hash_error"] = str(exc)

            files.append(item)
            indexed_bytes += size
            fingerprint.update(f"F|{rel_path}|{size}|{mtime_ns}\n".encode("utf-8"))
        if stop_scan:
            break

    fingerprint.update(f"truncated_reason={truncated_reason}\n".encode("utf-8"))
    return {
        "files": files,
        "skipped": skipped,
        "indexed_bytes": indexed_bytes,
        "truncated": bool(truncated_reason),
        "truncated_reason": truncated_reason,
        "fingerprint": fingerprint.hexdigest(),
    }


def _stable_payload_fingerprint(payload: object) -> str:
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _noise_exclusion_rule_for_path(
    rel_path: str, *, filter_settings: dict[str, object]
) -> str:
    normalized = rel_path.replace("\\", "/").strip("/")
    if not normalized:
        return "invalid_path"
    parts = [part for part in normalized.split("/") if part]
    basename = parts[-1]
    basename_lower = basename.lower()

    drop_cache_dirs = bool(filter_settings.get("drop_cache_dirs", True))
    if drop_cache_dirs and any(part in NOISE_DIR_NAMES for part in parts):
        return "cache_dir_noise"

    ignore_noise = bool(filter_settings.get("ignore_noise", True))
    if ignore_noise and basename_lower in NOISE_BASENAME_EXACT:
        return "system_noise"

    drop_slurm_outputs = bool(filter_settings.get("drop_slurm_outputs", True))
    if drop_slurm_outputs and SLURM_BASENAME_RE.match(basename_lower):
        return "slurm_output_noise"

    drop_ephemeral_suffixes = bool(filter_settings.get("drop_ephemeral_suffixes", True))
    suffix = Path(basename).suffix.lower()
    if drop_ephemeral_suffixes and suffix in NOISE_SUFFIXES:
        return "ephemeral_suffix_noise"
    return ""


def _normalize_family_stem(stem: str) -> str:
    normalized = stem.lower().strip()
    normalized = re.sub(r"[a-f0-9]{8,}", "{hash}", normalized)
    normalized = re.sub(
        r"(job|run|seed|step|iter|trial|chunk|batch)[-_]?\d+",
        r"\1_{num}",
        normalized,
    )
    normalized = re.sub(r"\d+", "{num}", normalized)
    normalized = re.sub(r"[_-]{2,}", "_", normalized)
    normalized = normalized.strip("_-")
    return normalized or "file"


def _build_compact_data_manifest(
    *,
    full_manifest: dict[str, object],
    filter_settings: dict[str, object],
) -> dict[str, object]:
    full_files = _manifest_file_items(full_manifest)
    excluded_count_by_rule: Counter[str] = Counter()
    retained_files: list[dict[str, object]] = []
    for item in full_files:
        path = str(item.get("path") or "").strip().replace("\\", "/")
        if not path:
            excluded_count_by_rule["invalid_path"] += 1
            continue
        rule = _noise_exclusion_rule_for_path(path, filter_settings=filter_settings)
        if rule:
            excluded_count_by_rule[rule] += 1
            continue
        retained_files.append(dict(item))

    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for item in retained_files:
        path = str(item.get("path") or "").strip().replace("\\", "/")
        if not path:
            continue
        parsed = Path(path)
        parent_dir = str(parsed.parent).replace("\\", "/")
        if parent_dir == ".":
            parent_dir = ""
        key = (parent_dir, _normalize_family_stem(parsed.stem), parsed.suffix.lower())
        grouped[key].append(item)

    family_settings = (
        filter_settings.get("family_collapse")
        if isinstance(filter_settings.get("family_collapse"), dict)
        else {}
    )
    max_examples = int(family_settings.get("max_examples_per_family") or 3)
    compact_files: list[dict[str, object]] = []
    collapsed_file_count = 0
    for family_key in sorted(grouped.keys()):
        family_items = grouped[family_key]
        ranked_family = sorted(
            family_items,
            key=lambda item: (
                -_data_file_usefulness_score(item),
                str(item.get("path") or ""),
            ),
        )
        representative = dict(ranked_family[0])
        representative_path = str(representative.get("path") or "").strip()
        family_paths = sorted(
            str(item.get("path") or "").strip()
            for item in ranked_family
            if str(item.get("path") or "").strip()
        )
        family_examples = [
            path for path in family_paths if path and path != representative_path
        ][:max_examples]
        family_count = len(family_paths)
        collapsed_file_count += max(0, family_count - 1)
        representative["family_count"] = family_count
        representative["family_examples"] = family_examples
        representative["representative_score"] = round(
            _data_file_usefulness_score(representative), 4
        )
        compact_files.append(representative)

    compact_files.sort(
        key=lambda item: (
            -_data_file_usefulness_score(item),
            str(item.get("path") or ""),
        )
    )

    excluded_by_rule_sorted = {
        key: int(excluded_count_by_rule[key]) for key in sorted(excluded_count_by_rule)
    }
    prompt_char_estimate = sum(
        len(str(item.get("path") or "")) + 96 for item in compact_files
    )
    full_stats = full_manifest.get("stats")
    if not isinstance(full_stats, dict):
        full_stats = {}

    compact_stats = {
        "indexed_files": len(compact_files),
        "indexed_bytes": sum(int(item.get("size") or 0) for item in compact_files),
        "skipped_files": len(full_manifest.get("skipped") or []),
        "truncated": bool(full_stats.get("truncated")),
        "truncated_reason": str(full_stats.get("truncated_reason") or ""),
        "full_indexed_files": int(full_stats.get("indexed_files") or len(full_files)),
        "full_indexed_bytes": int(full_stats.get("indexed_bytes") or 0),
        "excluded_files": int(sum(excluded_count_by_rule.values())),
        "excluded_count_by_rule": excluded_by_rule_sorted,
        "family_collapsed_files": collapsed_file_count,
        "family_group_count": len(grouped),
        "prompt_char_estimate": prompt_char_estimate,
    }

    compact_payload: dict[str, object] = {
        "manifest_kind": "compact",
        "schema_version": DATA_MANIFEST_SCHEMA_VERSION,
        "filter_rules_version": DATA_FILTER_RULES_VERSION,
        "generated_at_utc": _utc_now_z(),
        "data_dir": str(full_manifest.get("data_dir") or ""),
        "scan_limits": (
            full_manifest.get("scan_limits")
            if isinstance(full_manifest.get("scan_limits"), dict)
            else {}
        ),
        "filter_settings": filter_settings,
        "full_manifest_fingerprint": str(full_manifest.get("fingerprint") or ""),
        "files": compact_files,
        "skipped": (
            full_manifest.get("skipped")
            if isinstance(full_manifest.get("skipped"), list)
            else []
        ),
        "stats": compact_stats,
    }
    compact_payload["fingerprint"] = _stable_payload_fingerprint(
        {
            "manifest_kind": "compact",
            "schema_version": DATA_MANIFEST_SCHEMA_VERSION,
            "filter_rules_version": DATA_FILTER_RULES_VERSION,
            "full_manifest_fingerprint": compact_payload["full_manifest_fingerprint"],
            "filter_settings": filter_settings,
            "files": compact_files,
            "stats": compact_stats,
        }
    )
    return compact_payload


def _is_manifest_payload_compatible(
    payload: dict[str, object],
    *,
    manifest_kind: str,
    source_data_dir: Path,
    limits: dict[str, object],
    fingerprint: str | None = None,
    filter_settings: dict[str, object] | None = None,
) -> bool:
    if str(payload.get("manifest_kind") or "") != manifest_kind:
        return False
    if int(payload.get("schema_version") or 0) != DATA_MANIFEST_SCHEMA_VERSION:
        return False
    if int(payload.get("filter_rules_version") or 0) != DATA_FILTER_RULES_VERSION:
        return False
    if str(payload.get("data_dir") or "") != str(source_data_dir):
        return False
    payload_limits = payload.get("scan_limits")
    if not isinstance(payload_limits, dict) or payload_limits != limits:
        return False
    if isinstance(filter_settings, dict):
        payload_filters = payload.get("filter_settings")
        if not isinstance(payload_filters, dict) or payload_filters != filter_settings:
            return False
    if isinstance(fingerprint, str) and fingerprint:
        if str(payload.get("fingerprint") or "") != fingerprint:
            return False
    return True


def _load_json_if_exists(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_text_file(path: Path, content: str) -> None:
    cli = _cli()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise cli.PackageError(f"Failed to write file: {path}: {exc}") from exc


def _render_data_summary_markdown(manifest: dict[str, object]) -> str:
    files = manifest.get("files")
    if not isinstance(files, list):
        files = []
    skipped = manifest.get("skipped")
    if not isinstance(skipped, list):
        skipped = []
    stats = manifest.get("stats")
    if not isinstance(stats, dict):
        stats = {}

    type_counts: Counter[str] = Counter()
    for item in files:
        if not isinstance(item, dict):
            continue
        type_counts[str(item.get("type") or "unknown")] += 1

    sorted_files = [
        item for item in files if isinstance(item, dict) and str(item.get("path") or "")
    ]
    sorted_files.sort(
        key=lambda item: (
            -_data_file_usefulness_score(item),
            str(item.get("path") or ""),
        )
    )
    likely_useful = sorted_files[:12]
    unknown_files = [
        item for item in sorted_files if str(item.get("type") or "") == "unknown"
    ][:12]

    manifest_kind = str(manifest.get("manifest_kind") or "legacy")
    indexed_files = int(stats.get("indexed_files") or len(files))
    indexed_bytes = int(stats.get("indexed_bytes") or 0)
    truncated = bool(stats.get("truncated"))
    truncated_reason = str(stats.get("truncated_reason") or "").strip()
    full_indexed_files = int(stats.get("full_indexed_files") or indexed_files)
    full_indexed_bytes = int(stats.get("full_indexed_bytes") or indexed_bytes)
    excluded_files = int(stats.get("excluded_files") or 0)
    family_collapsed_files = int(stats.get("family_collapsed_files") or 0)
    prompt_char_estimate = int(stats.get("prompt_char_estimate") or 0)
    compact_ratio = (
        (indexed_files / full_indexed_files) if full_indexed_files > 0 else 1.0
    )
    excluded_count_by_rule = stats.get("excluded_count_by_rule")
    if not isinstance(excluded_count_by_rule, dict):
        excluded_count_by_rule = {}

    lines = [
        "# Data Summary",
        "",
        "## Scope",
        f"- source_data_dir: {manifest.get('data_dir')}",
        f"- manifest_kind: {manifest_kind}",
        f"- schema_version: {manifest.get('schema_version')}",
        f"- filter_rules_version: {manifest.get('filter_rules_version')}",
        f"- indexed_files: {indexed_files}",
        f"- indexed_bytes: {indexed_bytes}",
        f"- full_indexed_files: {full_indexed_files}",
        f"- full_indexed_bytes: {full_indexed_bytes}",
        f"- compact_ratio: {compact_ratio:.4f}",
        f"- excluded_files: {excluded_files}",
        f"- family_collapsed_files: {family_collapsed_files}",
        f"- prompt_char_estimate: {prompt_char_estimate}",
        f"- skipped_files: {len(skipped)}",
        f"- inventory_truncated: {truncated}",
    ]
    if truncated_reason:
        lines.append(f"- truncated_reason: {truncated_reason}")

    lines.extend(["", "## Type Breakdown"])
    if type_counts:
        for type_name, count in sorted(type_counts.items(), key=lambda item: item[0]):
            lines.append(f"- {type_name}: {count}")
    else:
        lines.append("- no indexed files")

    lines.extend(["", "## Likely Useful Subsets"])
    if likely_useful:
        for item in likely_useful:
            lines.append(
                f"- {item.get('path')} ({item.get('type')}, {item.get('size')} bytes)"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Unknown/Low-Confidence Areas"])
    if unknown_files:
        for item in unknown_files:
            lines.append(f"- {item.get('path')} (type unknown)")
    else:
        lines.append("- none detected")

    if skipped:
        lines.extend(["", "## Skipped Files"])
        for item in skipped[:12]:
            if isinstance(item, dict):
                path = str(item.get("path") or "")
                reason = str(item.get("reason") or "skipped")
                size = item.get("size")
                if isinstance(size, int):
                    lines.append(f"- {path} ({reason}, {size} bytes)")
                else:
                    lines.append(f"- {path} ({reason})")
        if len(skipped) > 12:
            lines.append(f"- ... {len(skipped) - 12} more skipped entries")

    if excluded_count_by_rule:
        lines.extend(["", "## Excluded By Rule"])
        for rule, count in sorted(
            excluded_count_by_rule.items(), key=lambda item: item[0]
        ):
            lines.append(f"- {rule}: {int(count)}")

    return "\n".join(lines).strip() + "\n"


def _prepare_workflow_data_artifacts(
    *,
    repo_dir: Path,
    run_dir: Path,
    data_context: dict[str, object],
) -> dict[str, object]:
    cli = _cli()
    if not _is_data_context_enabled(data_context):
        return data_context

    source_path = str(data_context.get("source_path") or "").strip()
    if not source_path:
        raise cli.PackageError("Data context is missing source_path.")
    source_data_dir = Path(source_path)
    limits = data_context.get("limits")
    if not isinstance(limits, dict):
        raise cli.PackageError("Data context is missing scan limits.")
    filter_settings = data_context.get("filter_settings")
    if not isinstance(filter_settings, dict):
        filter_settings = _default_data_filter_settings()
    mapping_thresholds = data_context.get("mapping_thresholds")
    if not isinstance(mapping_thresholds, dict):
        mapping_thresholds = _default_data_mapping_thresholds()

    max_files = int(limits.get("max_files") or DEFAULT_DATA_MAX_FILES)
    max_total_bytes = int(limits.get("max_total_bytes") or DEFAULT_DATA_MAX_TOTAL_BYTES)
    max_file_bytes = int(limits.get("max_file_bytes") or DEFAULT_DATA_MAX_FILE_BYTES)
    hash_max_bytes = int(limits.get("hash_max_bytes") or DEFAULT_DATA_HASH_MAX_BYTES)

    data_root = run_dir / WORKFLOW_DATA_DIRNAME
    data_root.mkdir(parents=True, exist_ok=True)
    full_manifest_path = data_root / WORKFLOW_DATA_MANIFEST_FULL_FILENAME
    compact_manifest_path = data_root / WORKFLOW_DATA_MANIFEST_FILENAME
    summary_path = data_root / WORKFLOW_DATA_SUMMARY_FILENAME

    fast_scan = _scan_data_dir_inventory(
        data_dir=source_data_dir,
        max_files=max_files,
        max_total_bytes=max_total_bytes,
        max_file_bytes=max_file_bytes,
        hash_max_bytes=hash_max_bytes,
        include_hash=False,
    )
    fast_fingerprint = str(fast_scan.get("fingerprint") or "")

    existing_full_manifest = _load_json_if_exists(full_manifest_path)
    existing_compact_manifest = _load_json_if_exists(compact_manifest_path)
    use_cached_manifest = False
    full_manifest_payload: dict[str, object] = {}
    compact_manifest_payload: dict[str, object] = {}
    if isinstance(existing_full_manifest, dict) and isinstance(
        existing_compact_manifest, dict
    ):
        full_ok = _is_manifest_payload_compatible(
            existing_full_manifest,
            manifest_kind="full",
            source_data_dir=source_data_dir,
            limits=limits,
            fingerprint=fast_fingerprint,
        )
        compact_ok = _is_manifest_payload_compatible(
            existing_compact_manifest,
            manifest_kind="compact",
            source_data_dir=source_data_dir,
            limits=limits,
            filter_settings=filter_settings,
        )
        linked = str(existing_compact_manifest.get("full_manifest_fingerprint") or "")
        full_fingerprint = str(existing_full_manifest.get("fingerprint") or "")
        if full_ok and compact_ok and linked == full_fingerprint:
            full_manifest_payload = existing_full_manifest
            compact_manifest_payload = existing_compact_manifest
            use_cached_manifest = True

    if not use_cached_manifest:
        full_scan = _scan_data_dir_inventory(
            data_dir=source_data_dir,
            max_files=max_files,
            max_total_bytes=max_total_bytes,
            max_file_bytes=max_file_bytes,
            hash_max_bytes=hash_max_bytes,
            include_hash=True,
        )
        full_manifest_payload = {
            "manifest_kind": "full",
            "schema_version": DATA_MANIFEST_SCHEMA_VERSION,
            "filter_rules_version": DATA_FILTER_RULES_VERSION,
            "generated_at_utc": _utc_now_z(),
            "data_dir": str(source_data_dir),
            "scan_limits": limits,
            "filter_settings": filter_settings,
            "fingerprint": str(full_scan.get("fingerprint") or ""),
            "files": (
                full_scan.get("files")
                if isinstance(full_scan.get("files"), list)
                else []
            ),
            "skipped": (
                full_scan.get("skipped")
                if isinstance(full_scan.get("skipped"), list)
                else []
            ),
            "stats": {
                "indexed_files": len(full_scan.get("files") or []),
                "indexed_bytes": int(full_scan.get("indexed_bytes") or 0),
                "skipped_files": len(full_scan.get("skipped") or []),
                "truncated": bool(full_scan.get("truncated")),
                "truncated_reason": str(full_scan.get("truncated_reason") or ""),
            },
        }
        compact_manifest_payload = _build_compact_data_manifest(
            full_manifest=full_manifest_payload,
            filter_settings=filter_settings,
        )
        _write_json_atomic(full_manifest_path, full_manifest_payload)
        _write_json_atomic(compact_manifest_path, compact_manifest_payload)

    summary_text = _render_data_summary_markdown(compact_manifest_payload)
    _write_text_file(summary_path, summary_text)

    stats = compact_manifest_payload.get("stats")
    data_context["artifacts"] = {
        "root": _repo_relative_path(repo_dir, data_root),
        "manifest_full": _repo_relative_path(repo_dir, full_manifest_path),
        "manifest_compact": _repo_relative_path(repo_dir, compact_manifest_path),
        "manifest": _repo_relative_path(repo_dir, compact_manifest_path),
        "summary": _repo_relative_path(repo_dir, summary_path),
        "task_map": _repo_relative_path(
            repo_dir, data_root / WORKFLOW_TASK_DATA_MAP_FILENAME
        ),
    }
    data_context["filter_settings"] = filter_settings
    data_context["mapping_thresholds"] = mapping_thresholds
    data_context["manifest_full_fingerprint"] = str(
        full_manifest_payload.get("fingerprint") or ""
    )
    data_context["manifest_compact_fingerprint"] = str(
        compact_manifest_payload.get("fingerprint") or ""
    )
    data_context["manifest_fingerprint"] = str(
        compact_manifest_payload.get("fingerprint") or ""
    )
    data_context["guard_fingerprint"] = fast_fingerprint
    if isinstance(stats, dict):
        data_context["manifest_stats"] = stats
    return data_context


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


def _render_reproduce_task_prompt(
    task: dict[str, object],
    *,
    hpc_context: dict[str, object] | None = None,
) -> str:
    task_id = str(task.get("id") or "task")
    title = str(task.get("title") or "Reproduce task").strip()
    objective = str(task.get("objective") or "").strip()
    figure_targets = _normalize_string_list(task.get("figure_targets"))
    simulation_requirements = _normalize_string_list(
        task.get("simulation_requirements")
    )
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
        lines.extend(
            ["", "## Figure targets", *[f"- {item}" for item in figure_targets]]
        )
    if simulation_requirements:
        lines.extend(
            [
                "",
                "## Simulation requirements",
                *[f"- {item}" for item in simulation_requirements],
            ]
        )
    if parameter_constraints:
        lines.extend(
            [
                "",
                "## Parameter constraints",
                *[f"- {item}" for item in parameter_constraints],
            ]
        )
    if plot_requirements:
        lines.extend(
            ["", "## Plot requirements", *[f"- {item}" for item in plot_requirements]]
        )
    if acceptance_checks:
        lines.extend(
            ["", "## Acceptance checks", *[f"- {item}" for item in acceptance_checks]]
        )
    lines.extend(["", "## Execution target", *_build_hpc_prompt_lines(hpc_context)])
    lines.extend(
        [
            "",
            "## Execution notes",
            "- Keep scripts, data, and plots reproducible.",
            "- Save run details and blockers in projects/memory.md.",
            "- Execute simulation work required by the task plan.",
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
    return _extract_tagged_json_payload(
        assistant_text, token_re=REPRODUCE_PLAN_TOKEN_RE
    )


def _extract_research_plan_payload(assistant_text: str) -> dict[str, object] | None:
    return _extract_tagged_json_payload(assistant_text, token_re=RESEARCH_PLAN_TOKEN_RE)


def _extract_task_data_map_payload(assistant_text: str) -> dict[str, object] | None:
    return _extract_tagged_json_payload(
        assistant_text, token_re=WORKFLOW_TASK_DATA_MAP_TOKEN_RE
    )


def _extract_workflow_plan_update_payload(
    assistant_text: str,
) -> dict[str, object] | None:
    return _extract_tagged_json_payload(
        assistant_text, token_re=WORKFLOW_PLAN_UPDATE_TOKEN_RE
    )


def _coerce_confidence(raw: object, *, default: float = 0.5) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _manifest_paths_set(manifest_payload: dict[str, object]) -> set[str]:
    files = manifest_payload.get("files")
    if not isinstance(files, list):
        return set()
    paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip().replace("\\", "/")
        if path:
            paths.add(path)
    return paths


def _manifest_file_items(
    manifest_payload: dict[str, object],
) -> list[dict[str, object]]:
    files = manifest_payload.get("files")
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def _tokenize_task_text(raw_text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", raw_text.lower())
        if token not in {"task", "with", "from", "that", "this", "figure"}
    }


def _task_blob(task: dict[str, object]) -> str:
    return " ".join(
        [
            str(task.get("id") or ""),
            str(task.get("title") or ""),
            str(task.get("objective") or ""),
            " ".join(_normalize_string_list(task.get("figure_targets"))),
            " ".join(_normalize_string_list(task.get("simulation_requirements"))),
            " ".join(_normalize_string_list(task.get("parameter_constraints"))),
            " ".join(_normalize_string_list(task.get("plot_requirements"))),
            " ".join(_normalize_string_list(task.get("acceptance_checks"))),
        ]
    )


def _manifest_entry_category(item: dict[str, object]) -> str:
    path = str(item.get("path") or "").lower()
    suffix = Path(path).suffix.lower()
    if any(token in path for token in ("input", "inputs", "dataset", "raw")):
        return "input"
    if any(token in path for token in ("config", "configs", "param", "setting")):
        return "config"
    if any(token in path for token in ("postprocess", "post_process", "analysis")):
        return "postprocess"
    if any(token in path for token in ("plot", "plots", "figure", "fig")):
        return "plot"
    if any(token in path for token in ("script", "scripts", "code")) or suffix in {
        ".py",
        ".ipynb",
    }:
        return "script"
    return "other"


def _task_manifest_relevance_score(
    *,
    task_tokens: set[str],
    file_item: dict[str, object],
) -> float:
    path = str(file_item.get("path") or "").replace("/", " ")
    path_tokens = _tokenize_task_text(path)
    overlap = len(task_tokens.intersection(path_tokens))
    score = _data_file_usefulness_score(file_item) + overlap * 1.4
    if overlap > 0:
        score += 1.5
    return score


def _select_task_manifest_slice(
    *,
    task: dict[str, object],
    manifest_payload: dict[str, object],
    mapping_thresholds: dict[str, object],
) -> list[dict[str, object]]:
    manifest_files = _manifest_file_items(manifest_payload)
    if not manifest_files:
        return []
    task_tokens = _tokenize_task_text(_task_blob(task))
    scored = sorted(
        (
            (
                _task_manifest_relevance_score(
                    task_tokens=task_tokens,
                    file_item=item,
                ),
                item,
            )
            for item in manifest_files
        ),
        key=lambda pair: (-pair[0], str(pair[1].get("path") or "")),
    )
    max_files = int(
        mapping_thresholds.get("task_slice_max_files")
        or DEFAULT_DATA_TASK_SLICE_MAX_FILES
    )
    max_chars = int(
        mapping_thresholds.get("task_slice_max_chars")
        or DEFAULT_DATA_TASK_SLICE_MAX_CHARS
    )
    selected: list[dict[str, object]] = []
    selected_paths: set[str] = set()
    selected_chars = 0

    def _try_add(item: dict[str, object]) -> bool:
        nonlocal selected_chars
        path = str(item.get("path") or "").strip()
        if not path or path in selected_paths:
            return False
        estimated_chars = len(path) + 128
        if len(selected) >= max_files:
            return False
        if selected and selected_chars + estimated_chars > max_chars:
            return False
        selected.append(item)
        selected_paths.add(path)
        selected_chars += estimated_chars
        return True

    for category in sorted(TASK_SLICE_COVERAGE_CATEGORIES):
        for _, item in scored:
            if _manifest_entry_category(item) != category:
                continue
            if _try_add(item):
                break

    for _, item in scored:
        _try_add(item)
        if len(selected) >= max_files or selected_chars >= max_chars:
            break

    if not selected:
        for _, item in scored[:max_files]:
            if not _try_add(item):
                break
    return selected


def _estimate_mapping_prompt_chars(
    *,
    manifest_payload: dict[str, object],
    planner_plan: dict[str, object],
    summary_text: str,
) -> int:
    stats = manifest_payload.get("stats")
    prompt_char_estimate = 0
    if isinstance(stats, dict):
        prompt_char_estimate = int(stats.get("prompt_char_estimate") or 0)
    if prompt_char_estimate <= 0:
        prompt_char_estimate = sum(
            len(str(item.get("path") or "")) + 96
            for item in _manifest_file_items(manifest_payload)
        )
    plan_chars = len(json.dumps(planner_plan, separators=(",", ":"), sort_keys=True))
    return prompt_char_estimate + len(summary_text) + plan_chars + 2_000


def _select_data_mapping_mode(
    *,
    manifest_payload: dict[str, object],
    planner_plan: dict[str, object],
    summary_text: str,
    mapping_thresholds: dict[str, object],
) -> str:
    file_count = len(_manifest_file_items(manifest_payload))
    estimated_chars = _estimate_mapping_prompt_chars(
        manifest_payload=manifest_payload,
        planner_plan=planner_plan,
        summary_text=summary_text,
    )
    large_threshold_files = int(
        mapping_thresholds.get("large_threshold_files")
        or DEFAULT_DATA_LARGE_THRESHOLD_FILES
    )
    large_threshold_chars = int(
        mapping_thresholds.get("large_threshold_chars")
        or DEFAULT_DATA_LARGE_THRESHOLD_CHARS
    )
    if file_count > large_threshold_files or estimated_chars > large_threshold_chars:
        return DATA_MAPPING_MODE_PER_TASK_LARGE
    return DATA_MAPPING_MODE_GLOBAL


def _normalize_task_data_map(
    raw_payload: object,
    *,
    plan_tasks: list[dict[str, object]],
    manifest_payload: dict[str, object],
    source_stage: str,
    allowed_paths: set[str] | None = None,
) -> dict[str, object]:
    cli = _cli()
    if not isinstance(raw_payload, dict):
        raise cli.PackageError("Task data map must be a JSON object.")

    manifest_paths = (
        allowed_paths
        if isinstance(allowed_paths, set)
        else _manifest_paths_set(manifest_payload)
    )
    raw_tasks = raw_payload.get("tasks")
    if not isinstance(raw_tasks, list):
        raise cli.PackageError("Task data map must include `tasks` list.")

    raw_by_id: dict[str, dict[str, object]] = {}
    for item in raw_tasks:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or "").strip()
        if task_id and task_id not in raw_by_id:
            raw_by_id[task_id] = item

    normalized_tasks: list[dict[str, object]] = []
    for index, task in enumerate(plan_tasks, start=1):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or f"task_{index:03d}").strip()
        raw_task = raw_by_id.get(task_id, {})
        files_payload = raw_task.get("files")
        normalized_files: list[dict[str, object]] = []
        seen_paths: set[str] = set()
        if isinstance(files_payload, list):
            for file_item in files_payload:
                path = ""
                rationale = ""
                confidence = 0.5
                if isinstance(file_item, dict):
                    path = str(file_item.get("path") or "").strip().replace("\\", "/")
                    rationale = str(file_item.get("rationale") or "").strip()
                    confidence = _coerce_confidence(file_item.get("confidence"))
                elif isinstance(file_item, str):
                    path = file_item.strip().replace("\\", "/")
                if not path or path in seen_paths:
                    continue
                if path not in manifest_paths:
                    raise cli.PackageError(
                        f"Task {task_id} references unknown manifest path: {path}"
                    )
                seen_paths.add(path)
                normalized_files.append(
                    {
                        "path": path,
                        "rationale": rationale or "Mapped by workflow data auditor.",
                        "confidence": confidence,
                    }
                )

        normalized_task = {
            "id": task_id,
            "files": normalized_files,
            "unknowns": _normalize_string_list(raw_task.get("unknowns")),
            "notes": _normalize_string_list(raw_task.get("notes")),
        }
        normalized_tasks.append(normalized_task)

    normalized_payload = {
        "version": 1,
        "source_stage": source_stage,
        "generated_at_utc": _utc_now_z(),
        "tasks": normalized_tasks,
        "global_unknowns": _normalize_string_list(raw_payload.get("global_unknowns")),
    }
    mapping_mode = str(raw_payload.get("mapping_mode") or "").strip()
    if mapping_mode:
        normalized_payload["mapping_mode"] = mapping_mode
    return normalized_payload


def _build_fallback_task_data_map(
    *,
    plan_tasks: list[dict[str, object]],
    manifest_payload: dict[str, object],
    reason: str,
) -> dict[str, object]:
    manifest_files = _manifest_file_items(manifest_payload)
    ranked_manifest = sorted(
        manifest_files,
        key=lambda item: (
            -_data_file_usefulness_score(item),
            str(item.get("path") or ""),
        ),
    )
    default_candidates = ranked_manifest[:12]

    normalized_tasks: list[dict[str, object]] = []
    for index, task in enumerate(plan_tasks, start=1):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or f"task_{index:03d}").strip()
        task_blob = " ".join(
            [
                str(task.get("title") or ""),
                str(task.get("objective") or ""),
                " ".join(_normalize_string_list(task.get("figure_targets"))),
                " ".join(_normalize_string_list(task.get("simulation_requirements"))),
                " ".join(_normalize_string_list(task.get("parameter_constraints"))),
            ]
        )
        task_tokens = _tokenize_task_text(task_blob)
        scored: list[tuple[float, dict[str, object]]] = []
        for file_item in ranked_manifest:
            path = str(file_item.get("path") or "")
            path_tokens = _tokenize_task_text(path.replace("/", " "))
            overlap = len(task_tokens.intersection(path_tokens))
            score = _data_file_usefulness_score(file_item) + overlap * 1.2
            if overlap > 0:
                score += 1.0
            scored.append((score, file_item))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("path") or "")))
        selected = [item for _, item in scored[:DEFAULT_DATA_TASK_FALLBACK_FILES]]
        if not selected:
            selected = default_candidates[:DEFAULT_DATA_TASK_FALLBACK_FILES]

        files_payload: list[dict[str, object]] = []
        for file_item in selected:
            path = str(file_item.get("path") or "").strip()
            if not path:
                continue
            files_payload.append(
                {
                    "path": path,
                    "rationale": "Heuristic fallback selection from manifest metadata.",
                    "confidence": 0.25,
                }
            )
        normalized_tasks.append(
            {
                "id": task_id,
                "files": files_payload,
                "unknowns": [
                    "LLM task-data mapping fallback was used; verify file relevance manually."
                ],
                "notes": [reason],
            }
        )

    return {
        "version": 1,
        "source_stage": "heuristic_fallback",
        "generated_at_utc": _utc_now_z(),
        "tasks": normalized_tasks,
        "global_unknowns": [reason],
    }


def _build_manifest_prompt_excerpt(
    manifest_payload: dict[str, object],
    *,
    file_items: list[dict[str, object]] | None = None,
    max_files: int = DEFAULT_DATA_PROMPT_MAX_FILES,
    max_chars: int | None = None,
) -> str:
    files = (
        [item for item in file_items if isinstance(item, dict)]
        if isinstance(file_items, list)
        else _manifest_file_items(manifest_payload)
    )
    files = sorted(
        files,
        key=lambda item: (
            -_data_file_usefulness_score(item),
            str(item.get("path") or ""),
        ),
    )
    lines: list[str] = []
    char_budget = max_chars if isinstance(max_chars, int) and max_chars > 0 else None
    used_chars = 0
    used_files = 0
    for item in files:
        if used_files >= max_files:
            break
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        family_count = int(item.get("family_count") or 1)
        examples = _normalize_string_list(item.get("family_examples"))
        line = (
            f"- {path} | type={item.get('type')} | size={item.get('size')} | "
            f"mtime_ns={item.get('mtime_ns')} | family_count={family_count}"
        )
        if examples:
            line += f" | more_like_this={', '.join(examples)}"
        projected = used_chars + len(line) + 1
        if char_budget is not None and used_files > 0 and projected > char_budget:
            break
        lines.append(line)
        used_chars = projected
        used_files += 1
    if len(files) > used_files:
        lines.append(f"- ... {len(files) - used_files} more indexed files")

    skipped = manifest_payload.get("skipped")
    if file_items is None and isinstance(skipped, list) and skipped:
        lines.append("")
        lines.append("Skipped entries:")
        for item in skipped[:24]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {item.get('path')} | reason={item.get('reason')} | size={item.get('size')}"
            )
        if len(skipped) > 24:
            lines.append(f"- ... {len(skipped) - 24} more skipped entries")
    return "\n".join(lines).strip()


def _generate_task_data_map(
    *,
    repo_dir: Path,
    run_dir: Path | None,
    source_description: str,
    planner_plan: dict[str, object],
    compact_manifest_payload: dict[str, object],
    full_manifest_payload: dict[str, object] | None,
    summary_text: str,
    requested_package_id: str | None,
    sandbox_override: str | None,
    provider_bin_override: str,
    max_tries: int,
    log_tag: str,
    data_context: dict[str, object],
) -> dict[str, object]:
    cli = _cli()
    raw_plan_tasks = planner_plan.get("tasks")
    if not isinstance(raw_plan_tasks, list):
        raise cli.PackageError("Planner plan is missing task list for data mapping.")
    plan_tasks = [task for task in raw_plan_tasks if isinstance(task, dict)]
    mapping_thresholds = (
        data_context.get("mapping_thresholds")
        if isinstance(data_context.get("mapping_thresholds"), dict)
        else _default_data_mapping_thresholds()
    )
    mapping_mode = _select_data_mapping_mode(
        manifest_payload=compact_manifest_payload,
        planner_plan=planner_plan,
        summary_text=summary_text,
        mapping_thresholds=mapping_thresholds,
    )
    data_context["mapping_mode"] = mapping_mode
    data_context["mapping_mode_selected_at_utc"] = _utc_now_z()
    cli._print_tagged(log_tag, f"data mapping mode: {mapping_mode}")

    allowed_paths = _manifest_paths_set(compact_manifest_payload)
    if isinstance(full_manifest_payload, dict):
        allowed_paths = allowed_paths.union(_manifest_paths_set(full_manifest_payload))

    def _run_and_normalize_map(
        *,
        prompt: str,
        target_tasks: list[dict[str, object]],
        source_stage: str,
        attempt_label: str,
    ) -> dict[str, object] | None:
        for attempt in range(1, max_tries + 1):
            cli._print_tagged(
                log_tag,
                f"{attempt_label} attempt {attempt}/{max_tries}",
            )
            run_result = _run_reproduce_exec_turn(
                repo_dir=repo_dir,
                prompt=prompt,
                requested_package_id=requested_package_id,
                sandbox_override=sandbox_override,
                provider_bin_override=provider_bin_override,
                data_context=data_context,
            )
            return_code = int(run_result.get("return_code") or 0)
            if return_code != 0:
                cli._print_tagged(
                    log_tag,
                    f"{attempt_label} run exited with code {return_code}.",
                    stderr=True,
                )
                continue
            assistant_text = str(run_result.get("assistant_text") or "")
            raw_payload = _extract_task_data_map_payload(assistant_text)
            if raw_payload is None:
                cli._print_tagged(
                    log_tag,
                    f"{attempt_label} response missing <{WORKFLOW_TASK_DATA_MAP_TAG}> block.",
                    stderr=True,
                )
                continue
            try:
                return _normalize_task_data_map(
                    raw_payload,
                    plan_tasks=target_tasks,
                    manifest_payload=compact_manifest_payload,
                    source_stage=source_stage,
                    allowed_paths=allowed_paths,
                )
            except cli.PackageError as exc:
                cli._print_tagged(
                    log_tag,
                    f"{attempt_label} response invalid: {exc}",
                    stderr=True,
                )
                continue
        return None

    if mapping_mode == DATA_MAPPING_MODE_GLOBAL:
        prompt = (
            f"{WORKFLOW_DATA_AUDITOR_PROMPT_PREFIX}\n\n"
            f"Workflow: {log_tag}\n"
            f"Source description: {source_description}\n"
            f"Data directory: {data_context.get('source_path')}\n"
            "Mapping mode: global\n\n"
            "Draft planner task JSON:\n"
            f"{json.dumps(planner_plan, indent=2)}\n\n"
            "Data summary markdown:\n"
            f"{summary_text.strip()}\n\n"
            "Compact data manifest excerpt:\n"
            f"{_build_manifest_prompt_excerpt(compact_manifest_payload)}\n"
        )
        normalized_payload = _run_and_normalize_map(
            prompt=prompt,
            target_tasks=plan_tasks,
            source_stage="data_auditor_global",
            attempt_label="data auditor global",
        )
        if normalized_payload is None:
            normalized_payload = _build_fallback_task_data_map(
                plan_tasks=plan_tasks,
                manifest_payload=compact_manifest_payload,
                reason="data auditor output invalid; used deterministic fallback mapping",
            )
            cli._print_tagged(
                log_tag,
                "data auditor fallback activated (deterministic heuristic map).",
                stderr=True,
            )
        normalized_payload["mapping_mode"] = DATA_MAPPING_MODE_GLOBAL
        return normalized_payload

    task_entries: list[dict[str, object]] = []
    global_unknowns: list[str] = []
    max_slice_files = int(
        mapping_thresholds.get("task_slice_max_files")
        or DEFAULT_DATA_TASK_SLICE_MAX_FILES
    )
    max_slice_chars = int(
        mapping_thresholds.get("task_slice_max_chars")
        or DEFAULT_DATA_TASK_SLICE_MAX_CHARS
    )
    for index, task in enumerate(plan_tasks, start=1):
        task_id = str(task.get("id") or f"task_{index:03d}").strip()
        task_slice = _select_task_manifest_slice(
            task=task,
            manifest_payload=compact_manifest_payload,
            mapping_thresholds=mapping_thresholds,
        )
        slice_excerpt = _build_manifest_prompt_excerpt(
            compact_manifest_payload,
            file_items=task_slice,
            max_files=max_slice_files,
            max_chars=max_slice_chars,
        )
        prompt = (
            f"{WORKFLOW_DATA_AUDITOR_PROMPT_PREFIX}\n\n"
            f"Workflow: {log_tag}\n"
            f"Source description: {source_description}\n"
            f"Data directory: {data_context.get('source_path')}\n"
            "Mapping mode: per_task_large\n\n"
            "Task JSON:\n"
            f"{json.dumps(task, indent=2)}\n\n"
            "Data summary markdown:\n"
            f"{summary_text.strip()}\n\n"
            "Task-specific compact manifest slice:\n"
            f"{slice_excerpt}\n"
        )
        normalized_payload = _run_and_normalize_map(
            prompt=prompt,
            target_tasks=[task],
            source_stage="data_auditor_per_task",
            attempt_label=f"data auditor task {task_id}",
        )
        if normalized_payload is None:
            fallback_reason = f"data auditor output invalid for {task_id}; used deterministic fallback mapping"
            normalized_payload = _build_fallback_task_data_map(
                plan_tasks=[task],
                manifest_payload=compact_manifest_payload,
                reason=fallback_reason,
            )
            global_unknowns.append(fallback_reason)
            cli._print_tagged(
                log_tag,
                f"data auditor fallback activated for {task_id}.",
                stderr=True,
            )
        mapped_tasks = normalized_payload.get("tasks")
        if (
            isinstance(mapped_tasks, list)
            and mapped_tasks
            and isinstance(mapped_tasks[0], dict)
        ):
            task_entries.append(mapped_tasks[0])
        else:
            task_entries.append(
                {
                    "id": task_id,
                    "files": [],
                    "unknowns": [
                        "LLM task-data mapping returned no parseable task entry."
                    ],
                    "notes": ["empty per-task mapping payload"],
                }
            )
        if run_dir is not None:
            partial_payload = {
                "version": 1,
                "source_stage": "data_auditor_per_task_large",
                "generated_at_utc": _utc_now_z(),
                "mapping_mode": DATA_MAPPING_MODE_PER_TASK_LARGE,
                "tasks": [dict(item) for item in task_entries],
                "global_unknowns": list(global_unknowns),
            }
            _materialize_task_data_artifacts(
                repo_dir=repo_dir,
                run_dir=run_dir,
                plan=planner_plan,
                task_data_map=partial_payload,
            )

    return {
        "version": 1,
        "source_stage": "data_auditor_per_task_large",
        "generated_at_utc": _utc_now_z(),
        "mapping_mode": DATA_MAPPING_MODE_PER_TASK_LARGE,
        "tasks": task_entries,
        "global_unknowns": global_unknowns,
    }


def _render_task_data_context_markdown(
    *,
    task_id: str,
    task_title: str,
    task_entry: dict[str, object],
    task_map_relpath: str,
) -> str:
    files = task_entry.get("files")
    if not isinstance(files, list):
        files = []
    unknowns = _normalize_string_list(task_entry.get("unknowns"))
    notes = _normalize_string_list(task_entry.get("notes"))
    lines = [
        f"# Data Context: {task_id}",
        "",
        f"- task_title: {task_title or task_id}",
        f"- task_map_source: `{task_map_relpath}`",
        "",
        "## Scope Rule",
        "- Only use files listed below unless strong evidence supports expansion.",
        (
            "- If expansion is required, update task_data_map.json and this file with "
            "new file paths, rationale, and confidence before continuing."
        ),
        "",
        "## Allowed Files",
    ]
    if files:
        for item in files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            rationale = str(item.get("rationale") or "").strip()
            confidence = _coerce_confidence(item.get("confidence"))
            if not path:
                continue
            lines.append(
                f"- `{path}` (confidence: {confidence:.2f})"
                + (f" - {rationale}" if rationale else "")
            )
    else:
        lines.append(
            "- No mapped files yet. Review data_summary.md and task_data_map.json."
        )

    lines.extend(["", "## Unknowns"])
    if unknowns:
        for item in unknowns:
            lines.append(f"- {item}")
    else:
        lines.append("- none recorded")

    lines.extend(["", "## Notes"])
    if notes:
        for item in notes:
            lines.append(f"- {item}")
    else:
        lines.append("- none")

    return "\n".join(lines).strip() + "\n"


def _materialize_task_data_artifacts(
    *,
    repo_dir: Path,
    run_dir: Path,
    plan: dict[str, object],
    task_data_map: dict[str, object],
) -> dict[str, object]:
    data_root = run_dir / WORKFLOW_DATA_DIRNAME
    data_root.mkdir(parents=True, exist_ok=True)
    task_map_path = data_root / WORKFLOW_TASK_DATA_MAP_FILENAME
    task_map_rel = _repo_relative_path(repo_dir, task_map_path)
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
        plan["tasks"] = tasks

    map_tasks = task_data_map.get("tasks")
    map_by_id: dict[str, dict[str, object]] = {}
    if isinstance(map_tasks, list):
        for item in map_tasks:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("id") or "").strip()
            if task_id and task_id not in map_by_id:
                map_by_id[task_id] = item

    ordered_tasks: list[dict[str, object]] = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or f"task_{index:03d}").strip()
        task_title = str(task.get("title") or task_id).strip()
        task_entry = map_by_id.get(
            task_id,
            {
                "id": task_id,
                "files": [],
                "unknowns": ["No task-data mapping available yet."],
                "notes": [],
            },
        )
        context_rel = f"{WORKFLOW_DATA_DIRNAME}/{task_id}.md"
        context_path = run_dir / context_rel
        context_markdown = _render_task_data_context_markdown(
            task_id=task_id,
            task_title=task_title,
            task_entry=task_entry,
            task_map_relpath=task_map_rel,
        )
        _write_text_file(context_path, context_markdown)
        task_entry = dict(task_entry)
        task_entry["id"] = task_id
        task_entry["context_file"] = context_rel
        ordered_tasks.append(task_entry)
        task["data_context_file"] = context_rel

    task_data_map["tasks"] = ordered_tasks
    task_data_map["updated_at_utc"] = _utc_now_z()
    _write_json_atomic(task_map_path, task_data_map)
    return task_data_map


def _synchronize_task_data_artifacts_with_plan(
    *,
    repo_dir: Path,
    run_dir: Path,
    plan: dict[str, object],
    manifest_payload: dict[str, object],
    existing_task_map: dict[str, object] | None,
) -> dict[str, object]:
    plan_tasks = plan.get("tasks")
    if not isinstance(plan_tasks, list):
        plan_tasks = []
        plan["tasks"] = plan_tasks

    if isinstance(existing_task_map, dict):
        try:
            normalized = _normalize_task_data_map(
                existing_task_map,
                plan_tasks=[task for task in plan_tasks if isinstance(task, dict)],
                manifest_payload=manifest_payload,
                source_stage=str(existing_task_map.get("source_stage") or "resync"),
            )
        except Exception:
            normalized = _build_fallback_task_data_map(
                plan_tasks=[task for task in plan_tasks if isinstance(task, dict)],
                manifest_payload=manifest_payload,
                reason="existing task_data_map invalid during plan sync",
            )
    else:
        normalized = _build_fallback_task_data_map(
            plan_tasks=[task for task in plan_tasks if isinstance(task, dict)],
            manifest_payload=manifest_payload,
            reason="task_data_map missing during plan sync",
        )
    return _materialize_task_data_artifacts(
        repo_dir=repo_dir,
        run_dir=run_dir,
        plan=plan,
        task_data_map=normalized,
    )


def _scan_diff_preview(
    before_scan: dict[str, object], after_scan: dict[str, object], *, max_items: int = 6
) -> list[str]:
    before_files = before_scan.get("files")
    after_files = after_scan.get("files")
    before_map: dict[str, tuple[int, int]] = {}
    after_map: dict[str, tuple[int, int]] = {}
    if isinstance(before_files, list):
        for item in before_files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            size = int(item.get("size") or 0)
            mtime_ns = int(item.get("mtime_ns") or 0)
            if path:
                before_map[path] = (size, mtime_ns)
    if isinstance(after_files, list):
        for item in after_files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            size = int(item.get("size") or 0)
            mtime_ns = int(item.get("mtime_ns") or 0)
            if path:
                after_map[path] = (size, mtime_ns)

    added = sorted(path for path in after_map if path not in before_map)
    removed = sorted(path for path in before_map if path not in after_map)
    modified = sorted(
        path
        for path in after_map
        if path in before_map and after_map[path] != before_map[path]
    )
    lines: list[str] = []
    for path in added[:max_items]:
        lines.append(f"+ {path}")
    for path in removed[:max_items]:
        lines.append(f"- {path}")
    for path in modified[:max_items]:
        lines.append(f"~ {path}")
    if not lines:
        lines.append("(no per-file diff within indexed scope)")
    return lines


def _normalize_automation_plan(
    raw_plan: object,
    *,
    source_description: str,
    hpc_context: dict[str, object] | None = None,
) -> dict[str, object]:
    cli = _cli()
    if not isinstance(raw_plan, dict):
        raise cli.PackageError("Reproduce plan must be a JSON object.")

    raw_tasks = raw_plan.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise cli.PackageError("Reproduce plan must include a non-empty `tasks` list.")

    normalized_tasks: list[dict[str, object]] = []
    used_ids: set[str] = set()
    for index, raw_task in enumerate(raw_tasks, start=1):
        if not isinstance(raw_task, dict):
            raise cli.PackageError(f"Task {index} in reproduce plan is not an object.")

        task_id = _sanitize_task_id(raw_task.get("id"), index, used_ids)
        title = str(raw_task.get("title") or f"Task {index}").strip()
        objective = str(raw_task.get("objective") or "").strip()
        figure_targets = _normalize_string_list(raw_task.get("figure_targets"))
        simulation_requirements = _normalize_string_list(
            raw_task.get("simulation_requirements")
        )
        parameter_constraints = _normalize_string_list(
            raw_task.get("parameter_constraints")
        )
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
        data_context_file = str(raw_task.get("data_context_file") or "").strip()
        if data_context_file:
            normalized_task["data_context_file"] = data_context_file
        if not prompt_markdown:
            prompt_markdown = _render_reproduce_task_prompt(
                normalized_task, hpc_context=hpc_context
            ).strip()
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
    hpc_context: dict[str, object] | None = None,
) -> dict[str, object]:
    return _normalize_automation_plan(
        raw_plan,
        source_description=source_description,
        hpc_context=hpc_context,
    )


def _normalize_research_plan(
    raw_plan: object,
    *,
    source_description: str,
    hpc_context: dict[str, object] | None = None,
) -> dict[str, object]:
    return _normalize_automation_plan(
        raw_plan,
        source_description=source_description,
        hpc_context=hpc_context,
    )


def _run_reproduce_exec_turn(
    *,
    repo_dir: Path,
    prompt: str,
    requested_package_id: str | None,
    sandbox_override: str | None,
    provider_bin_override: str,
    data_context: dict[str, object] | None = None,
) -> dict[str, object]:
    """Execute one planning/reporting turn used by `reproduce` and `research`.

    Dependency note:
    - Shared by planner, auditor, and report-generation stages.
    - Uses the same package-routing/overlay execution path as `exec` and `loop`.
    """

    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    model = runtime_policy.model
    reasoning_effort = runtime_policy.reasoning_effort
    if isinstance(sandbox_override, str) and sandbox_override.strip():
        sandbox_policy = "enforce"
        sandbox_mode = sandbox_override.strip()

    data_guard_before: dict[str, object] | None = None
    data_guard_source: Path | None = None
    data_guard_limits: dict[str, int] | None = None
    if _is_data_context_enabled(data_context) and bool(
        data_context.get("read_only", True)
    ):
        source_path = str(data_context.get("source_path") or "").strip()
        limits = data_context.get("limits")
        if source_path and isinstance(limits, dict):
            data_guard_source = Path(source_path)
            data_guard_limits = {
                "max_files": int(limits.get("max_files") or DEFAULT_DATA_MAX_FILES),
                "max_total_bytes": int(
                    limits.get("max_total_bytes") or DEFAULT_DATA_MAX_TOTAL_BYTES
                ),
                "max_file_bytes": int(
                    limits.get("max_file_bytes") or DEFAULT_DATA_MAX_FILE_BYTES
                ),
                "hash_max_bytes": int(
                    limits.get("hash_max_bytes") or DEFAULT_DATA_HASH_MAX_BYTES
                ),
            }
            data_guard_before = _scan_data_dir_inventory(
                data_dir=data_guard_source,
                max_files=data_guard_limits["max_files"],
                max_total_bytes=data_guard_limits["max_total_bytes"],
                max_file_bytes=data_guard_limits["max_file_bytes"],
                hash_max_bytes=data_guard_limits["hash_max_bytes"],
                include_hash=False,
            )

    provider_bin = cli.resolve_provider_binary_override(
        provider,
        raw_override=provider_bin_override,
    )
    selection = cli._resolve_exec_package_selection(
        user_prompt=prompt,
        scipkg_root=scipkg_root,
        repo_dir=repo_dir,
        requested_package_id=requested_package_id,
        provider=provider,
        provider_bin=provider_bin,
        sandbox_policy=sandbox_policy,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    package_id = selection.get("package_id")
    if not isinstance(package_id, str) or not package_id:
        raise cli.PackageError("No package selected for reproduce execution.")

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
        run_result = cli._run_exec_chat_turn(
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
            provider_bin_override=provider_bin,
            provider=provider,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    finally:
        cli._cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)

    if (
        data_guard_before is not None
        and data_guard_source is not None
        and isinstance(data_guard_limits, dict)
    ):
        data_guard_after = _scan_data_dir_inventory(
            data_dir=data_guard_source,
            max_files=int(data_guard_limits["max_files"]),
            max_total_bytes=int(data_guard_limits["max_total_bytes"]),
            max_file_bytes=int(data_guard_limits["max_file_bytes"]),
            hash_max_bytes=int(data_guard_limits["hash_max_bytes"]),
            include_hash=False,
        )
        before_fingerprint = str(data_guard_before.get("fingerprint") or "")
        after_fingerprint = str(data_guard_after.get("fingerprint") or "")
        if before_fingerprint != after_fingerprint:
            diff_lines = _scan_diff_preview(data_guard_before, data_guard_after)
            raise cli.PackageError(
                "Read-only data guard violation: --data-dir was modified during "
                "planning/auditing/reporting turn.\n"
                f"data_dir: {data_guard_source}\n"
                "indexed diff preview:\n" + "\n".join(diff_lines)
            )

    return run_result


def _generate_mode_plan(
    *,
    repo_dir: Path,
    run_dir: Path | None,
    source_text: str,
    source_description: str,
    requested_package_id: str | None,
    sandbox_override: str | None,
    provider_bin_override: str,
    planner_max_tries: int,
    auditor_max_tries: int,
    planner_prompt_prefix: str,
    auditor_prompt_prefix: str,
    plan_tag: str,
    extract_payload,
    normalize_plan,
    log_tag: str,
    data_context: dict[str, object] | None = None,
    hpc_context: dict[str, object] | None = None,
    workflow_status_hook: WorkflowStatusHook | None = None,
) -> dict[str, object]:
    """Generate + audit a workflow plan used by both `reproduce` and `research`."""

    cli = _cli()
    planner_prompt_parts = [
        f"{planner_prompt_prefix}\n\n"
        f"{WORKFLOW_UNIFIED_MEMORY_STAGE_INSTRUCTIONS}\n"
        "\n"
        f"Paper source: {source_description}\n\n"
        "Paper content / request:\n"
        f"{source_text.strip()}\n"
    ]
    planner_prompt_parts.append(
        "\nExecution target constraints:\n"
        + "\n".join(_build_hpc_prompt_lines(hpc_context))
        + "\n"
    )
    planner_prompt = "".join(planner_prompt_parts)
    planner_plan: dict[str, object] | None = None
    _emit_workflow_status(workflow_status_hook, f"{log_tag} plan")
    for attempt in range(1, planner_max_tries + 1):
        cli._print_tagged(log_tag, f"planner attempt {attempt}/{planner_max_tries}")
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=planner_prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            provider_bin_override=provider_bin_override,
            data_context=data_context,
        )
        return_code = int(run_result.get("return_code") or 0)
        if return_code != 0:
            raise cli.PackageError(
                f"{log_tag.title()} planner agent run failed with exit code {return_code}."
            )
        assistant_text = str(run_result.get("assistant_text") or "")
        raw_payload = extract_payload(assistant_text)
        if raw_payload is None:
            cli._print_tagged(
                log_tag,
                f"planner response missing <{plan_tag}> JSON block.",
                stderr=True,
            )
            continue
        try:
            planner_plan = normalize_plan(
                raw_payload,
                source_description=source_description,
                hpc_context=hpc_context,
            )
        except cli.PackageError as exc:
            cli._print_tagged(log_tag, f"planner response invalid: {exc}", stderr=True)
            continue
        break
    if planner_plan is None:
        raise cli.PackageError(
            f"Unable to generate a valid {log_tag} plan from planner response."
        )

    draft_task_data_map: dict[str, object] | None = None
    if _is_data_context_enabled(data_context):
        if run_dir is None:
            raise cli.PackageError("Internal error: run_dir required for data mapping.")
        artifacts = data_context.get("artifacts")
        if not isinstance(artifacts, dict):
            raise cli.PackageError("Data context artifacts are missing.")
        manifest_rel = str(
            artifacts.get("manifest_compact") or artifacts.get("manifest") or ""
        ).strip()
        manifest_full_rel = str(artifacts.get("manifest_full") or "").strip()
        summary_rel = str(artifacts.get("summary") or "").strip()
        if not manifest_rel or not summary_rel:
            raise cli.PackageError("Data context artifacts are incomplete.")
        manifest_path = repo_dir / manifest_rel
        manifest_full_path = repo_dir / manifest_full_rel if manifest_full_rel else None
        summary_path = repo_dir / summary_rel
        compact_manifest_payload = _load_json_if_exists(manifest_path)
        if not isinstance(compact_manifest_payload, dict):
            raise cli.PackageError(f"Missing data manifest: {manifest_path}")
        full_manifest_payload = (
            _load_json_if_exists(manifest_full_path)
            if isinstance(manifest_full_path, Path)
            else None
        )
        try:
            summary_text = summary_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise cli.PackageError(
                f"Failed to read data summary file: {summary_path}: {exc}"
            ) from exc
        draft_task_data_map = _generate_task_data_map(
            repo_dir=repo_dir,
            run_dir=run_dir,
            source_description=source_description,
            planner_plan=planner_plan,
            compact_manifest_payload=compact_manifest_payload,
            full_manifest_payload=full_manifest_payload,
            summary_text=summary_text,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            provider_bin_override=provider_bin_override,
            max_tries=auditor_max_tries,
            log_tag=log_tag,
            data_context=data_context,
        )
        draft_task_data_map = _materialize_task_data_artifacts(
            repo_dir=repo_dir,
            run_dir=run_dir,
            plan=planner_plan,
            task_data_map=draft_task_data_map,
        )

    audited_plan: dict[str, object] | None = None
    auditor_prompt_parts = [
        f"{auditor_prompt_prefix}\n\n"
        f"{WORKFLOW_UNIFIED_MEMORY_STAGE_INSTRUCTIONS}\n"
        "\n"
        f"Paper source: {source_description}\n\n"
        "Original paper content / request:\n"
        f"{source_text.strip()}\n\n"
        "Candidate plan JSON:\n"
        f"{json.dumps(planner_plan, indent=2)}\n"
    ]
    if _is_data_context_enabled(data_context):
        artifacts = data_context.get("artifacts")
        if isinstance(artifacts, dict):
            summary_rel = str(artifacts.get("summary") or "").strip()
            manifest_rel = str(
                artifacts.get("manifest_compact") or artifacts.get("manifest") or ""
            ).strip()
            manifest_full_rel = str(artifacts.get("manifest_full") or "").strip()
            task_map_rel = str(artifacts.get("task_map") or "").strip()
            if summary_rel:
                auditor_prompt_parts.append(f"\nData summary file: {summary_rel}\n")
            if manifest_rel:
                auditor_prompt_parts.append(
                    f"Compact data manifest file: {manifest_rel}\n"
                )
            if manifest_full_rel:
                auditor_prompt_parts.append(
                    f"Full data manifest file: {manifest_full_rel}\n"
                )
            if task_map_rel:
                auditor_prompt_parts.append(
                    f"Draft task-data map file: {task_map_rel}\n"
                )
        if isinstance(draft_task_data_map, dict):
            auditor_prompt_parts.append(
                "\nDraft task-data map JSON:\n"
                f"{json.dumps(draft_task_data_map, indent=2)}\n"
            )
        auditor_prompt_parts.append(
            "\nData-scope rule for final prompts:\n"
            "- For each task, include direction to use only mapped files by default.\n"
            "- Require map updates when strong evidence suggests expansion.\n"
        )
    auditor_prompt_parts.append(
        "\nExecution target constraints:\n"
        + "\n".join(_build_hpc_prompt_lines(hpc_context))
        + "\n"
    )
    auditor_prompt = "".join(auditor_prompt_parts)
    _emit_workflow_status(workflow_status_hook, f"{log_tag} audit")
    for attempt in range(1, auditor_max_tries + 1):
        cli._print_tagged(log_tag, f"auditor attempt {attempt}/{auditor_max_tries}")
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=auditor_prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            provider_bin_override=provider_bin_override,
            data_context=data_context,
        )
        return_code = int(run_result.get("return_code") or 0)
        if return_code != 0:
            raise cli.PackageError(
                f"{log_tag.title()} auditor agent run failed with exit code {return_code}."
            )
        assistant_text = str(run_result.get("assistant_text") or "")
        raw_payload = extract_payload(assistant_text)
        if raw_payload is None:
            cli._print_tagged(
                log_tag,
                f"auditor response missing <{plan_tag}> JSON block.",
                stderr=True,
            )
            continue
        try:
            audited_plan = normalize_plan(
                raw_payload,
                source_description=source_description,
                hpc_context=hpc_context,
            )
        except cli.PackageError as exc:
            cli._print_tagged(log_tag, f"auditor response invalid: {exc}", stderr=True)
            continue
        break
    if audited_plan is None:
        raise cli.PackageError(
            f"Unable to generate a valid {log_tag} plan from auditor response."
        )
    if _is_data_context_enabled(data_context):
        if run_dir is None:
            raise cli.PackageError("Internal error: run_dir required for data mapping.")
        artifacts = data_context.get("artifacts")
        if not isinstance(artifacts, dict):
            raise cli.PackageError("Data context artifacts are missing.")
        manifest_rel = str(
            artifacts.get("manifest_compact") or artifacts.get("manifest") or ""
        ).strip()
        if not manifest_rel:
            raise cli.PackageError("Data context manifest artifact is missing.")
        manifest_payload = _load_json_if_exists(repo_dir / manifest_rel)
        if not isinstance(manifest_payload, dict):
            raise cli.PackageError(
                f"Missing data manifest for task mapping: {repo_dir / manifest_rel}"
            )
        _synchronize_task_data_artifacts_with_plan(
            repo_dir=repo_dir,
            run_dir=run_dir,
            plan=audited_plan,
            manifest_payload=manifest_payload,
            existing_task_map=draft_task_data_map,
        )
    return audited_plan


def _generate_reproduce_plan(
    *,
    repo_dir: Path,
    source_text: str,
    source_description: str,
    requested_package_id: str | None,
    sandbox_override: str | None,
    provider_bin_override: str,
    planner_max_tries: int,
    auditor_max_tries: int,
    run_dir: Path | None = None,
    data_context: dict[str, object] | None = None,
    hpc_context: dict[str, object] | None = None,
    workflow_status_hook: WorkflowStatusHook | None = None,
) -> dict[str, object]:
    return _generate_mode_plan(
        repo_dir=repo_dir,
        run_dir=run_dir,
        source_text=source_text,
        source_description=source_description,
        requested_package_id=requested_package_id,
        sandbox_override=sandbox_override,
        provider_bin_override=provider_bin_override,
        planner_max_tries=planner_max_tries,
        auditor_max_tries=auditor_max_tries,
        planner_prompt_prefix=REPRODUCE_PLANNER_PROMPT_PREFIX,
        auditor_prompt_prefix=REPRODUCE_AUDITOR_PROMPT_PREFIX,
        plan_tag=REPRODUCE_PLAN_TAG,
        extract_payload=_extract_reproduce_plan_payload,
        normalize_plan=_normalize_reproduce_plan,
        log_tag="reproduce",
        data_context=data_context,
        hpc_context=hpc_context,
        workflow_status_hook=workflow_status_hook,
    )


def _generate_research_plan(
    *,
    repo_dir: Path,
    source_text: str,
    source_description: str,
    requested_package_id: str | None,
    sandbox_override: str | None,
    provider_bin_override: str,
    planner_max_tries: int,
    auditor_max_tries: int,
    run_dir: Path | None = None,
    data_context: dict[str, object] | None = None,
    hpc_context: dict[str, object] | None = None,
    workflow_status_hook: WorkflowStatusHook | None = None,
) -> dict[str, object]:
    return _generate_mode_plan(
        repo_dir=repo_dir,
        run_dir=run_dir,
        source_text=source_text,
        source_description=source_description,
        requested_package_id=requested_package_id,
        sandbox_override=sandbox_override,
        provider_bin_override=provider_bin_override,
        planner_max_tries=planner_max_tries,
        auditor_max_tries=auditor_max_tries,
        planner_prompt_prefix=RESEARCH_PLANNER_PROMPT_PREFIX,
        auditor_prompt_prefix=RESEARCH_AUDITOR_PROMPT_PREFIX,
        plan_tag=RESEARCH_PLAN_TAG,
        extract_payload=_extract_research_plan_payload,
        normalize_plan=_normalize_research_plan,
        log_tag="research",
        data_context=data_context,
        hpc_context=hpc_context,
        workflow_status_hook=workflow_status_hook,
    )


WORKFLOW_PLAN_UPDATE_DECISIONS = {
    "no_change",
    "update_remaining",
    "continue_with_failed_task",
    "abort",
}


def _fallback_workflow_plan_update(
    *,
    task_id: str,
    reason: str,
) -> dict[str, object]:
    return {
        "version": 1,
        "decision": "no_change",
        "reason": str(reason or "post-task plan audit produced no valid update"),
        "completed_or_failed_task_id": task_id,
        "remaining_tasks": [],
        "audit_notes": [str(reason or "no valid update")],
    }


def _normalize_workflow_plan_update(
    raw_payload: object,
    *,
    source_description: str,
    completed_or_failed_task_id: str,
    hpc_context: dict[str, object] | None = None,
) -> dict[str, object]:
    cli = _cli()
    if not isinstance(raw_payload, dict):
        raise cli.PackageError("Workflow plan update must be a JSON object.")

    decision = str(raw_payload.get("decision") or "").strip()
    if decision not in WORKFLOW_PLAN_UPDATE_DECISIONS:
        raise cli.PackageError(
            "Workflow plan update decision must be one of: "
            + ", ".join(sorted(WORKFLOW_PLAN_UPDATE_DECISIONS))
        )

    payload_task_id = str(
        raw_payload.get("completed_or_failed_task_id") or ""
    ).strip()
    if payload_task_id and payload_task_id != completed_or_failed_task_id:
        raise cli.PackageError(
            "Workflow plan update task id mismatch: "
            f"{payload_task_id!r} != {completed_or_failed_task_id!r}"
        )

    remaining_tasks: list[dict[str, object]] = []
    if decision in {"update_remaining", "continue_with_failed_task"}:
        raw_remaining_tasks = raw_payload.get("remaining_tasks")
        if not isinstance(raw_remaining_tasks, list):
            raise cli.PackageError(
                "Workflow plan update must include `remaining_tasks` list."
            )
        if raw_remaining_tasks:
            normalized_remaining_plan = _normalize_automation_plan(
                {
                    "version": 1,
                    "paper_source": source_description,
                    "assumptions": [],
                    "tasks": raw_remaining_tasks,
                },
                source_description=source_description,
                hpc_context=hpc_context,
            )
            normalized_tasks = normalized_remaining_plan.get("tasks")
            if not isinstance(normalized_tasks, list):
                raise cli.PackageError(
                    "Workflow plan update remaining tasks did not normalize to a list."
                )
            remaining_tasks = [
                item for item in normalized_tasks if isinstance(item, dict)
            ]

    return {
        "version": 1,
        "decision": decision,
        "reason": str(raw_payload.get("reason") or "").strip(),
        "completed_or_failed_task_id": completed_or_failed_task_id,
        "remaining_tasks": remaining_tasks,
        "audit_notes": _normalize_string_list(raw_payload.get("audit_notes")),
    }


def _workflow_plan_remaining_task_ids(
    *,
    run_dir: Path,
    completed_or_failed_count: int,
) -> list[str]:
    plan_path = run_dir / REPRODUCE_PLAN_FILENAME
    plan_payload = _load_json_if_exists(plan_path)
    if not isinstance(plan_payload, dict):
        return []
    tasks = plan_payload.get("tasks")
    if not isinstance(tasks, list):
        return []
    task_ids: list[str] = []
    for index, item in enumerate(tasks[completed_or_failed_count:], start=1):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or f"task_{index:03d}").strip()
        if task_id:
            task_ids.append(task_id)
    return task_ids


def _changed_remaining_task_ids(
    previous_tasks: list[dict[str, object]],
    new_tasks: list[dict[str, object]],
) -> list[str]:
    previous_by_id = {
        str(item.get("id") or "").strip(): item
        for item in previous_tasks
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    new_by_id = {
        str(item.get("id") or "").strip(): item
        for item in new_tasks
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    changed: set[str] = set(previous_by_id).symmetric_difference(set(new_by_id))
    for task_id, new_task in new_by_id.items():
        previous_task = previous_by_id.get(task_id)
        if previous_task is None:
            continue
        if previous_task != new_task:
            changed.add(task_id)
    return sorted(changed)


def _append_workflow_plan_revision(
    state: dict[str, object],
    *,
    trigger_task_id: str,
    trigger_status: str,
    decision: str,
    reason: str,
    audit_notes: list[str],
    previous_remaining_task_ids: list[str],
    new_remaining_task_ids: list[str],
    changed_task_ids: list[str],
) -> None:
    revisions = state.get("plan_revisions")
    if not isinstance(revisions, list):
        revisions = []
        state["plan_revisions"] = revisions
    revisions.append(
        {
            "version": 1,
            "updated_at_utc": _utc_now_z(),
            "trigger_task_id": trigger_task_id,
            "trigger_status": trigger_status,
            "decision": decision,
            "reason": reason,
            "audit_notes": audit_notes,
            "previous_remaining_task_ids": previous_remaining_task_ids,
            "new_remaining_task_ids": new_remaining_task_ids,
            "changed_task_ids": changed_task_ids,
        }
    )


def _run_post_task_plan_update(
    *,
    repo_dir: Path,
    run_dir: Path,
    workflow_name: str,
    source_description: str,
    task_id: str,
    task_status: str,
    run_number: int,
    completed_or_failed_count: int,
    requested_package_id: str | None,
    sandbox_override: str | None,
    provider_bin_override: str,
    max_tries: int,
    data_context: dict[str, object] | None = None,
    hpc_context: dict[str, object] | None = None,
    workflow_status_hook: WorkflowStatusHook | None = None,
) -> dict[str, object]:
    cli = _cli()
    plan_path = run_dir / REPRODUCE_PLAN_FILENAME
    state_path = run_dir / REPRODUCE_STATE_FILENAME
    log_path = (
        run_dir
        / REPRODUCE_LOGS_DIRNAME
        / f"{task_id}_run_{run_number:02d}.json"
    )
    plan_payload = _load_json_if_exists(plan_path)
    if not isinstance(plan_payload, dict):
        return _fallback_workflow_plan_update(
            task_id=task_id,
            reason=f"missing workflow plan for post-task audit: {plan_path}",
        )
    plan_tasks = plan_payload.get("tasks")
    if not isinstance(plan_tasks, list):
        return _fallback_workflow_plan_update(
            task_id=task_id,
            reason="workflow plan has no task list for post-task audit",
        )
    remaining_plan_tasks = [
        item
        for item in plan_tasks[completed_or_failed_count:]
        if isinstance(item, dict)
    ]
    if not remaining_plan_tasks:
        return _fallback_workflow_plan_update(
            task_id=task_id,
            reason="no remaining workflow tasks to audit",
        )

    state_payload = _load_json_if_exists(state_path) or {}
    log_payload = _load_json_if_exists(log_path) or {}

    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(repo_dir))
        except ValueError:
            return str(path)

    prompt = (
        f"{WORKFLOW_POST_TASK_PLAN_AUDITOR_PROMPT_PREFIX}\n\n"
        f"{WORKFLOW_UNIFIED_MEMORY_STAGE_INSTRUCTIONS}\n"
        "\n"
        f"Workflow: {workflow_name}\n"
        f"Source description: {source_description}\n"
        f"Trigger task id: {task_id}\n"
        f"Trigger task status: {task_status}\n"
        "Completed-or-failed task count before future tasks: "
        f"{completed_or_failed_count}\n"
        "\n"
        "Execution target constraints:\n"
        + "\n".join(_build_hpc_prompt_lines(hpc_context))
        + "\n\n"
        "Artifacts to read if needed:\n"
        f"- Memory: projects/memory.md\n"
        f"- Plan JSON: {_display_path(plan_path)}\n"
        f"- State JSON: {_display_path(state_path)}\n"
        f"- Trigger task run log: {_display_path(log_path)}\n"
        "\n"
        "Current full plan JSON:\n"
        f"{json.dumps(plan_payload, indent=2)}\n\n"
        "Current state JSON excerpt:\n"
        f"{json.dumps(state_payload, indent=2)}\n\n"
        "Trigger task run log JSON:\n"
        f"{json.dumps(log_payload, indent=2)}\n\n"
        "Current remaining future tasks JSON:\n"
        f"{json.dumps(remaining_plan_tasks, indent=2)}\n"
    )

    tries = max(1, int(max_tries or 1))
    _emit_workflow_status(
        workflow_status_hook, f"{workflow_name} post-task plan audit"
    )
    for attempt in range(1, tries + 1):
        cli._print_tagged(
            workflow_name,
            f"post-task plan audit attempt {attempt}/{tries}",
        )
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            provider_bin_override=provider_bin_override,
            data_context=data_context,
        )
        return_code = int(run_result.get("return_code") or 0)
        if return_code != 0:
            cli._print_tagged(
                workflow_name,
                f"post-task plan audit exited with code {return_code}.",
                stderr=True,
            )
            continue
        raw_payload = _extract_workflow_plan_update_payload(
            str(run_result.get("assistant_text") or "")
        )
        if raw_payload is None:
            cli._print_tagged(
                workflow_name,
                "post-task plan audit missing "
                f"<{WORKFLOW_PLAN_UPDATE_TAG}> JSON block.",
                stderr=True,
            )
            continue
        try:
            return _normalize_workflow_plan_update(
                raw_payload,
                source_description=source_description,
                completed_or_failed_task_id=task_id,
                hpc_context=hpc_context,
            )
        except cli.PackageError as exc:
            cli._print_tagged(
                workflow_name,
                f"post-task plan audit response invalid: {exc}",
                stderr=True,
            )
            continue
    return _fallback_workflow_plan_update(
        task_id=task_id,
        reason="post-task plan audit did not return a valid update; keeping plan unchanged",
    )


def _apply_remaining_task_updates(
    *,
    repo_dir: Path,
    run_dir: Path,
    state: dict[str, object],
    workflow_name: str,
    source_description: str,
    completed_or_failed_count: int,
    remaining_tasks: list[dict[str, object]],
    data_context: dict[str, object] | None = None,
    hpc_context: dict[str, object] | None = None,
) -> list[str]:
    cli = _cli()
    plan_path = run_dir / REPRODUCE_PLAN_FILENAME
    plan_payload = _load_json_if_exists(plan_path)
    if not isinstance(plan_payload, dict):
        raise cli.PackageError(f"Missing workflow plan for update: {plan_path}")
    plan_tasks = plan_payload.get("tasks")
    if not isinstance(plan_tasks, list):
        raise cli.PackageError(f"Workflow plan has no task list: {plan_path}")

    prefix_count = max(0, min(int(completed_or_failed_count), len(plan_tasks)))
    completed_plan_tasks = [
        item for item in plan_tasks[:prefix_count] if isinstance(item, dict)
    ]
    previous_remaining_tasks = [
        item for item in plan_tasks[prefix_count:] if isinstance(item, dict)
    ]
    completed_ids = {
        str(item.get("id") or "").strip()
        for item in completed_plan_tasks
        if str(item.get("id") or "").strip()
    }

    new_ids: list[str] = []
    seen_new_ids: set[str] = set()
    for index, task in enumerate(remaining_tasks, start=1):
        if not isinstance(task, dict):
            raise cli.PackageError(f"Updated remaining task {index} is invalid.")
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            raise cli.PackageError(f"Updated remaining task {index} has no id.")
        if task_id in completed_ids:
            raise cli.PackageError(
                f"Updated remaining task reuses completed/failed task id: {task_id}"
            )
        if task_id in seen_new_ids:
            raise cli.PackageError(
                f"Updated remaining tasks contain duplicate id: {task_id}"
            )
        seen_new_ids.add(task_id)
        new_ids.append(task_id)

    prompts_dir = run_dir / REPRODUCE_PROMPTS_DIRNAME
    prompts_dir.mkdir(parents=True, exist_ok=True)
    updated_plan_tasks: list[dict[str, object]] = []
    updated_state_tasks: list[dict[str, object]] = []
    for task in remaining_tasks:
        task_id = str(task.get("id") or "").strip()
        prompt_markdown = str(task.get("prompt_markdown") or "").strip()
        if not prompt_markdown:
            raise cli.PackageError(
                f"Updated task {task_id} has empty prompt_markdown."
            )
        prompt_rel = f"{REPRODUCE_PROMPTS_DIRNAME}/{task_id}.md"
        prompt_path = run_dir / prompt_rel
        try:
            prompt_path.write_text(prompt_markdown.strip() + "\n", encoding="utf-8")
        except OSError as exc:
            raise cli.PackageError(
                f"Failed to write updated task prompt file: {prompt_path}: {exc}"
            ) from exc

        plan_task = dict(task)
        plan_task["prompt_file"] = prompt_rel
        updated_plan_tasks.append(plan_task)
        state_task: dict[str, object] = {
            "id": task_id,
            "title": str(task.get("title") or task_id),
            "prompt_file": prompt_rel,
        }
        data_context_file = str(task.get("data_context_file") or "").strip()
        if data_context_file:
            state_task["data_context_file"] = data_context_file
        updated_state_tasks.append(state_task)

    plan_payload["tasks"] = [*completed_plan_tasks, *updated_plan_tasks]
    if _is_data_context_enabled(data_context):
        artifacts = (
            data_context.get("artifacts") if isinstance(data_context, dict) else {}
        )
        if not isinstance(artifacts, dict):
            raise cli.PackageError("Data context artifacts missing during plan update.")
        manifest_rel = str(
            artifacts.get("manifest_compact") or artifacts.get("manifest") or ""
        ).strip()
        task_map_rel = str(artifacts.get("task_map") or "").strip()
        if not manifest_rel:
            raise cli.PackageError(
                "Data context manifest path missing during plan update."
            )
        manifest_payload = _load_json_if_exists(repo_dir / manifest_rel)
        if not isinstance(manifest_payload, dict):
            raise cli.PackageError(
                f"Failed to load data manifest during plan update: {repo_dir / manifest_rel}"
            )
        existing_task_map = (
            _load_json_if_exists(repo_dir / task_map_rel) if task_map_rel else None
        )
        normalized_plan_map = _synchronize_task_data_artifacts_with_plan(
            repo_dir=repo_dir,
            run_dir=run_dir,
            plan=plan_payload,
            manifest_payload=manifest_payload,
            existing_task_map=existing_task_map,
        )
        if isinstance(data_context, dict):
            data_context["task_map_updated_at_utc"] = str(
                normalized_plan_map.get("updated_at_utc") or _utc_now_z()
            )

    _write_json_atomic(plan_path, plan_payload)

    state_tasks = state.get("tasks")
    completed_state_tasks = (
        [item for item in state_tasks[:prefix_count] if isinstance(item, dict)]
        if isinstance(state_tasks, list)
        else []
    )
    state["tasks"] = [*completed_state_tasks, *updated_state_tasks]
    task_runs_raw = state.get("task_runs")
    task_runs = task_runs_raw if isinstance(task_runs_raw, dict) else {}
    state["task_runs"] = {
        str(task.get("id") or ""): int(
            task_runs.get(str(task.get("id") or ""), 0) or 0
        )
        for task in state["tasks"]
        if isinstance(task, dict) and str(task.get("id") or "").strip()
    }
    state["last_error"] = ""
    changed_task_ids = _changed_remaining_task_ids(
        previous_remaining_tasks,
        updated_plan_tasks,
    )
    cli._print_tagged(
        workflow_name,
        (
            "post-task plan audit updated remaining tasks: "
            + (
                ", ".join(changed_task_ids)
                if changed_task_ids
                else "no content changes"
            )
        ),
    )
    return changed_task_ids


def _archive_loop_memory(
    *, repo_dir: Path, archive_dir: Path, task_id: str, run_count: int
) -> None:
    cli = _cli()
    memory_path = repo_dir / LOOP_MEMORY_DIRNAME / LOOP_MEMORY_FILENAME
    if not memory_path.is_file():
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"memory_{task_id}_run_{run_count:02d}.md"
    try:
        shutil.copy2(memory_path, archive_path)
    except OSError as exc:
        raise cli.PackageError(
            f"Failed to archive loop memory to {archive_path}: {exc}"
        ) from exc


UNIFIED_MEMORY_SHORT_TERM_HEADING = "## Short-Term Memory (Operational)"
UNIFIED_MEMORY_LONG_TERM_HEADING = "## Long-Term Memory (Persistent)"
UNIFIED_MEMORY_PLAN_HEADING = "### Plan"
UNIFIED_MEMORY_PROGRESS_HEADING = "### Progress log"
UNIFIED_MEMORY_FILE_MAP_HEADING = "### File map"
UNIFIED_MEMORY_SIM_HISTORY_HEADING = "### Simulation history"
UNIFIED_MEMORY_KEY_RESULTS_HEADING = "### Key results"
UNIFIED_MEMORY_PARAM_SOURCE_HEADING = "### Parameter source mapping"
UNIFIED_MEMORY_SIM_UNCERTAINTY_HEADING = "### Simulation uncertainty"
UNIFIED_MEMORY_SKILLS_UPDATES_HEADING = "### Suggested skills updates"

UNIFIED_MEMORY_SHORT_TERM_BLOCK = (
    f"{UNIFIED_MEMORY_SHORT_TERM_HEADING}\n"
    "\n"
    f"{UNIFIED_MEMORY_PLAN_HEADING}\n"
    "- [ ] (fill in a small checklist plan)\n"
    "\n"
    f"{UNIFIED_MEMORY_PROGRESS_HEADING}\n"
    "- initialized\n"
)

UNIFIED_MEMORY_LONG_TERM_BLOCK = (
    f"{UNIFIED_MEMORY_LONG_TERM_HEADING}\n"
    "\n"
    f"{UNIFIED_MEMORY_FILE_MAP_HEADING}\n"
    "- (path | purpose | notes)\n"
    "\n"
    f"{UNIFIED_MEMORY_SIM_HISTORY_HEADING}\n"
    "- (run_id | objective | status | artifacts | notes)\n"
    "\n"
    f"{UNIFIED_MEMORY_KEY_RESULTS_HEADING}\n"
    "- (result_id | metric | value | conditions | evidence_path)\n"
    "\n"
    f"{UNIFIED_MEMORY_PARAM_SOURCE_HEADING}\n"
    "- (run_id | parameter_or_setting | value | source | evidence_path | notes)\n"
    "\n"
    f"{UNIFIED_MEMORY_SIM_UNCERTAINTY_HEADING}\n"
    "- (run_id | uncertainty_or_assumption | impact | mitigation_or_next_step | status)\n"
    "\n"
    f"{UNIFIED_MEMORY_SKILLS_UPDATES_HEADING}\n"
    "- (<package_id> | issue_pattern | proposed_skill_update | evidence | status)\n"
)


def _memory_heading_exists(content: str, heading: str) -> bool:
    return bool(re.search(rf"(?m)^\s*{re.escape(heading)}\s*$", content))


def _append_memory_block(content: str, block: str) -> str:
    normalized = content.rstrip()
    if normalized:
        normalized += "\n\n"
    return normalized + block.rstrip() + "\n"


def _iter_unified_memory_sections(
    content: str,
    *,
    heading: str,
) -> list[tuple[int, int, str]]:
    heading_re = re.compile(rf"(?m)^\s*{re.escape(heading)}\s*$")
    next_same_level_re = re.compile(r"(?m)^##\s+.+$")
    sections: list[tuple[int, int, str]] = []
    for match in heading_re.finditer(content):
        next_match = next_same_level_re.search(content, match.end())
        end = next_match.start() if next_match is not None else len(content)
        sections.append((match.start(), end, content[match.start() : end]))
    return sections


UNIFIED_MEMORY_LONG_TERM_PLACEHOLDERS = {
    "(path | purpose | notes)",
    "(run_id | objective | status | artifacts | notes)",
    "(result_id | metric | value | conditions | evidence_path)",
    "(run_id | parameter_or_setting | value | source | evidence_path | notes)",
    "(run_id | uncertainty_or_assumption | impact | mitigation_or_next_step | status)",
    "(<package_id> | issue_pattern | proposed_skill_update | evidence | status)",
}


def _score_long_term_block(block: str) -> tuple[int, int, int]:
    informative_bullets = 0
    nonempty_lines = 0
    for raw_line in block.splitlines()[1:]:
        stripped = raw_line.strip()
        if not stripped:
            continue
        nonempty_lines += 1
        if not stripped.startswith("- "):
            continue
        item = stripped[2:].strip()
        lowered = item.lower()
        if not item:
            continue
        if lowered in UNIFIED_MEMORY_LONG_TERM_PLACEHOLDERS:
            continue
        informative_bullets += 1
    return informative_bullets, nonempty_lines, len(block.strip())


def _canonicalize_unified_memory_sections(content: str) -> str:
    short_sections = _iter_unified_memory_sections(
        content, heading=UNIFIED_MEMORY_SHORT_TERM_HEADING
    )
    long_sections = _iter_unified_memory_sections(
        content, heading=UNIFIED_MEMORY_LONG_TERM_HEADING
    )
    if len(short_sections) <= 1 and len(long_sections) <= 1:
        return content

    chosen_short = (
        short_sections[-1][2].rstrip()
        if short_sections
        else UNIFIED_MEMORY_SHORT_TERM_BLOCK.rstrip()
    )
    if long_sections:
        scored_candidates = [
            (_score_long_term_block(section_text), idx, section_text.rstrip())
            for idx, (_, _, section_text) in enumerate(long_sections)
        ]
        scored_candidates.sort(key=lambda item: (item[0], item[1]))
        chosen_long = scored_candidates[-1][2]
    else:
        chosen_long = UNIFIED_MEMORY_LONG_TERM_BLOCK.rstrip()

    spans = sorted((start, end) for start, end, _ in [*short_sections, *long_sections])
    merged_spans: list[list[int]] = []
    for start, end in spans:
        if not merged_spans or start > merged_spans[-1][1]:
            merged_spans.append([start, end])
            continue
        merged_spans[-1][1] = max(merged_spans[-1][1], end)

    insert_at = merged_spans[0][0]
    prefix = content[:insert_at].rstrip()

    cursor = insert_at
    suffix_parts: list[str] = []
    for start, end in merged_spans:
        if start > cursor:
            suffix_parts.append(content[cursor:start])
        cursor = max(cursor, end)
    suffix_parts.append(content[cursor:])
    suffix = "".join(suffix_parts).strip()

    canonical_memory = f"{chosen_short}\n\n{chosen_long}"
    parts: list[str] = []
    if prefix:
        parts.append(prefix)
    parts.append(canonical_memory)
    if suffix:
        parts.append(suffix)
    return "\n\n".join(parts).rstrip() + "\n"


def _normalize_workflow_context_lines(
    workflow_context_lines: list[str] | None,
) -> list[str]:
    if not isinstance(workflow_context_lines, list):
        return []
    return [
        str(line).rstrip()
        for line in workflow_context_lines
        if isinstance(line, str) and line.strip()
    ]


def _upsert_workflow_context_block(
    content: str, workflow_context_lines: list[str] | None
) -> str:
    normalized_context = _normalize_workflow_context_lines(workflow_context_lines)
    if not normalized_context:
        return content

    without_context = re.sub(
        r"(?ms)^## Workflow context\s*$\n.*?(?=^##\s|\Z)",
        "",
        content,
    )
    block = "## Workflow context\n" + "\n".join(normalized_context) + "\n"
    short_match = re.search(
        rf"(?m)^\s*{re.escape(UNIFIED_MEMORY_SHORT_TERM_HEADING)}\s*$",
        without_context,
    )
    if short_match is None:
        return _append_memory_block(without_context, block)

    prefix = without_context[: short_match.start()].rstrip()
    suffix = without_context[short_match.start() :].lstrip()
    parts: list[str] = []
    if prefix:
        parts.append(prefix)
    parts.append(block.rstrip())
    if suffix:
        parts.append(suffix)
    return "\n\n".join(parts).rstrip() + "\n"


def _upgrade_loop_memory_schema(memory_path: Path) -> None:
    cli = _cli()
    try:
        content = memory_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise cli.PackageError(
            f"Failed to read loop memory file for schema upgrade: {memory_path}: {exc}"
        ) from exc

    upgraded = _canonicalize_unified_memory_sections(content)
    if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_SHORT_TERM_HEADING):
        # Backward compatibility: migrate legacy top-level sections when present.
        upgraded = re.sub(
            r"(?m)^##\s+Plan\s*$",
            f"{UNIFIED_MEMORY_SHORT_TERM_HEADING}\n\n{UNIFIED_MEMORY_PLAN_HEADING}",
            upgraded,
            count=1,
        )
        upgraded = re.sub(
            r"(?m)^##\s+Progress\s+log\s*$",
            UNIFIED_MEMORY_PROGRESS_HEADING,
            upgraded,
            count=1,
        )
        if _memory_heading_exists(
            upgraded, UNIFIED_MEMORY_PLAN_HEADING
        ) and not _memory_heading_exists(upgraded, UNIFIED_MEMORY_SHORT_TERM_HEADING):
            upgraded = re.sub(
                rf"(?m)^\s*{re.escape(UNIFIED_MEMORY_PLAN_HEADING)}\s*$",
                f"{UNIFIED_MEMORY_SHORT_TERM_HEADING}\n\n{UNIFIED_MEMORY_PLAN_HEADING}",
                upgraded,
                count=1,
            )

    if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_SHORT_TERM_HEADING):
        upgraded = _append_memory_block(upgraded, UNIFIED_MEMORY_SHORT_TERM_BLOCK)
    else:
        if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_PLAN_HEADING):
            upgraded = _append_memory_block(
                upgraded,
                f"{UNIFIED_MEMORY_PLAN_HEADING}\n- [ ] (fill in a small checklist plan)\n",
            )
        if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_PROGRESS_HEADING):
            upgraded = _append_memory_block(
                upgraded, f"{UNIFIED_MEMORY_PROGRESS_HEADING}\n- initialized\n"
            )

    if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_LONG_TERM_HEADING):
        upgraded = _append_memory_block(upgraded, UNIFIED_MEMORY_LONG_TERM_BLOCK)
    else:
        if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_FILE_MAP_HEADING):
            upgraded = _append_memory_block(
                upgraded,
                f"{UNIFIED_MEMORY_FILE_MAP_HEADING}\n- (path | purpose | notes)\n",
            )
        if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_SIM_HISTORY_HEADING):
            upgraded = _append_memory_block(
                upgraded,
                f"{UNIFIED_MEMORY_SIM_HISTORY_HEADING}\n"
                "- (run_id | objective | status | artifacts | notes)\n",
            )
        if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_KEY_RESULTS_HEADING):
            upgraded = _append_memory_block(
                upgraded,
                f"{UNIFIED_MEMORY_KEY_RESULTS_HEADING}\n"
                "- (result_id | metric | value | conditions | evidence_path)\n",
            )
        if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_PARAM_SOURCE_HEADING):
            upgraded = _append_memory_block(
                upgraded,
                f"{UNIFIED_MEMORY_PARAM_SOURCE_HEADING}\n"
                "- (run_id | parameter_or_setting | value | source | evidence_path | notes)\n",
            )
        if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_SIM_UNCERTAINTY_HEADING):
            upgraded = _append_memory_block(
                upgraded,
                f"{UNIFIED_MEMORY_SIM_UNCERTAINTY_HEADING}\n"
                "- (run_id | uncertainty_or_assumption | impact | mitigation_or_next_step | status)\n",
            )
        if not _memory_heading_exists(upgraded, UNIFIED_MEMORY_SKILLS_UPDATES_HEADING):
            upgraded = _append_memory_block(
                upgraded,
                f"{UNIFIED_MEMORY_SKILLS_UPDATES_HEADING}\n"
                "- (<package_id> | issue_pattern | proposed_skill_update | evidence | status)\n",
            )

    if upgraded == content:
        return
    try:
        memory_path.write_text(upgraded, encoding="utf-8")
    except OSError as exc:
        raise cli.PackageError(
            f"Failed to update loop memory schema at {memory_path}: {exc}"
        ) from exc


def _ensure_loop_memory(
    *,
    repo_dir: Path,
    user_prompt: str,
    prompt_file: str | None,
    overwrite: bool = False,
    workflow_context_lines: list[str] | None = None,
) -> Path:
    cli = _cli()
    projects_dir = repo_dir / LOOP_MEMORY_DIRNAME
    if projects_dir.exists() and projects_dir.is_symlink():
        raise cli.PackageError(
            f"{projects_dir} is a symlink. Remove it and create a real directory "
            "so fermilink loop can persist long-term memory safely."
        )
    if projects_dir.exists() and not projects_dir.is_dir():
        raise cli.PackageError(f"{projects_dir} exists but is not a directory.")
    projects_dir.mkdir(parents=True, exist_ok=True)

    memory_path = projects_dir / LOOP_MEMORY_FILENAME
    if memory_path.exists():
        if memory_path.is_dir():
            raise cli.PackageError(f"{memory_path} exists but is a directory.")
        if not overwrite:
            _upgrade_loop_memory_schema(memory_path)
            try:
                content = memory_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise cli.PackageError(
                    f"Failed to read memory file for workflow context update: {memory_path}: {exc}"
                ) from exc
            updated = _upsert_workflow_context_block(content, workflow_context_lines)
            if updated != content:
                try:
                    memory_path.write_text(updated, encoding="utf-8")
                except OSError as exc:
                    raise cli.PackageError(
                        f"Failed to update workflow context in memory file: {memory_path}: {exc}"
                    ) from exc
            return memory_path

    started_at = _utc_now_z()
    source_line = f"- prompt_source: {prompt_file}\n" if prompt_file else ""
    context_block = ""
    normalized_context = _normalize_workflow_context_lines(workflow_context_lines)
    if normalized_context:
        context_block = (
            "\n" "## Workflow context\n" + "\n".join(normalized_context) + "\n"
        )
    initial = (
        "# FermiLink Unified Memory\n"
        "\n"
        "- schema_version: 1\n"
        f"- started_at_utc: {started_at}\n"
        f"- last_updated_utc: {started_at}\n"
        f"{source_line}"
        "\n"
        "## Original request\n"
        f"{user_prompt.strip()}\n"
        f"{context_block}"
        "\n"
        f"{UNIFIED_MEMORY_SHORT_TERM_BLOCK}\n"
        f"{UNIFIED_MEMORY_LONG_TERM_BLOCK}"
    )
    try:
        memory_path.write_text(initial, encoding="utf-8")
    except OSError as exc:
        raise cli.PackageError(
            f"Failed to create loop memory file: {memory_path}: {exc}"
        ) from exc
    return memory_path


def _reset_loop_short_term_memory(
    *,
    repo_dir: Path,
    user_prompt: str,
    prompt_file: str | None,
    workflow_context_lines: list[str] | None = None,
) -> Path:
    """Reset only short-term memory while preserving long-term sections."""

    cli = _cli()
    memory_path = _ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt=user_prompt,
        prompt_file=prompt_file,
        overwrite=False,
        workflow_context_lines=workflow_context_lines,
    )
    try:
        content = memory_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise cli.PackageError(
            f"Failed to read loop memory file for short-term reset: {memory_path}: {exc}"
        ) from exc

    short_match = re.search(
        rf"(?m)^\s*{re.escape(UNIFIED_MEMORY_SHORT_TERM_HEADING)}\s*$", content
    )
    long_match = re.search(
        rf"(?m)^\s*{re.escape(UNIFIED_MEMORY_LONG_TERM_HEADING)}\s*$", content
    )
    if (
        short_match is None
        or long_match is None
        or long_match.start() <= short_match.start()
    ):
        _upgrade_loop_memory_schema(memory_path)
        try:
            content = memory_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise cli.PackageError(
                "Failed to reload loop memory file after schema upgrade: "
                f"{memory_path}: {exc}"
            ) from exc
        short_match = re.search(
            rf"(?m)^\s*{re.escape(UNIFIED_MEMORY_SHORT_TERM_HEADING)}\s*$", content
        )
        long_match = re.search(
            rf"(?m)^\s*{re.escape(UNIFIED_MEMORY_LONG_TERM_HEADING)}\s*$", content
        )
        if (
            short_match is None
            or long_match is None
            or long_match.start() <= short_match.start()
        ):
            raise cli.PackageError(
                "Loop memory file is missing required short-term/long-term headings "
                f"after schema upgrade: {memory_path}"
            )

    prefix = content[: short_match.start()].rstrip()
    suffix = content[long_match.start() :].lstrip()
    parts: list[str] = []
    if prefix:
        parts.append(prefix)
    parts.append(UNIFIED_MEMORY_SHORT_TERM_BLOCK.rstrip())
    if suffix:
        parts.append(suffix)
    updated = "\n\n".join(parts).rstrip() + "\n"
    if updated == content:
        return memory_path
    try:
        memory_path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        raise cli.PackageError(
            f"Failed to rewrite loop short-term memory at {memory_path}: {exc}"
        ) from exc
    return memory_path


def _truncate_handoff_line(text: str, *, max_chars: int = 180) -> str:
    cleaned = str(text).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    overflow = len(cleaned) - max_chars
    return f"{cleaned[:max_chars]}... ({overflow} more chars)"


def _summarize_archived_memory(path: Path, *, max_items: int = 3) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    candidates: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("- "):
            item = line[2:].strip()
        else:
            item = line
        lowered = item.lower()
        if lowered in {
            "initialized",
            "(fill in a small checklist plan)",
            "(path | purpose | notes)",
            "(run_id | objective | status | artifacts | notes)",
            "(result_id | metric | value | conditions | evidence_path)",
            "(run_id | parameter_or_setting | value | source | evidence_path | notes)",
            "(run_id | uncertainty_or_assumption | impact | mitigation_or_next_step | status)",
            "(issue_pattern | proposed_skill_update | evidence | status)",
        }:
            continue
        if lowered.startswith("started_at_utc:") or lowered.startswith(
            "prompt_source:"
        ):
            continue
        if lowered.startswith("[ ]"):
            continue
        candidates.append(_truncate_handoff_line(item))

    if not candidates:
        return []
    return candidates[-max_items:]


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


def _extract_loop_pid_numbers(assistant_text: str) -> list[int]:
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        return []
    matches = LOOP_PID_TOKEN_RE.findall(assistant_text)
    if not matches:
        return []
    seen: set[int] = set()
    pids: list[int] = []
    for raw in matches:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        pids.append(pid)
    return pids


def _extract_loop_slurm_job_numbers(assistant_text: str) -> list[str]:
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        return []
    matches = LOOP_SLURM_JOB_TOKEN_RE.findall(assistant_text)
    if not matches:
        return []
    seen: set[str] = set()
    job_ids: list[str] = []
    for raw in matches:
        job_id = str(raw).strip()
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        job_ids.append(job_id)
    return job_ids


def _materialize_mode_plan(
    *,
    run_dir: Path,
    plan: dict[str, object],
    state: dict[str, object],
    workflow_name: str,
) -> None:
    cli = _cli()
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise cli.PackageError(
            f"{workflow_name.title()} planner produced no executable tasks."
        )

    prompts_dir = run_dir / REPRODUCE_PROMPTS_DIRNAME
    prompts_dir.mkdir(parents=True, exist_ok=True)
    plan_tasks: list[dict[str, object]] = []
    state_tasks: list[dict[str, object]] = []
    task_runs: dict[str, int] = {}

    for index, task_obj in enumerate(tasks, start=1):
        if not isinstance(task_obj, dict):
            raise cli.PackageError(f"Task {index} in {workflow_name} plan is invalid.")
        task_id = (
            str(task_obj.get("id") or f"task_{index:03d}").strip()
            or f"task_{index:03d}"
        )
        prompt_markdown = str(task_obj.get("prompt_markdown") or "").strip()
        if not prompt_markdown:
            raise cli.PackageError(f"Task {task_id} has empty `prompt_markdown`.")
        prompt_rel = f"{REPRODUCE_PROMPTS_DIRNAME}/{task_id}.md"
        prompt_path = run_dir / prompt_rel
        try:
            prompt_path.write_text(prompt_markdown.strip() + "\n", encoding="utf-8")
        except OSError as exc:
            raise cli.PackageError(
                f"Failed to write task prompt file: {prompt_path}: {exc}"
            ) from exc
        plan_task = dict(task_obj)
        plan_task["prompt_file"] = prompt_rel
        plan_tasks.append(plan_task)
        state_task: dict[str, object] = {
            "id": task_id,
            "title": str(task_obj.get("title") or task_id),
            "prompt_file": prompt_rel,
        }
        data_context_file = str(task_obj.get("data_context_file") or "").strip()
        if data_context_file:
            state_task["data_context_file"] = data_context_file
        state_tasks.append(state_task)
        task_runs[task_id] = 0

    plan["tasks"] = plan_tasks
    _write_json_atomic(run_dir / REPRODUCE_PLAN_FILENAME, plan)
    state["tasks"] = state_tasks
    state["task_runs"] = task_runs
    state["current_task_index"] = 0
    state["last_error"] = ""


def _maybe_sync_mode_plan_from_disk(
    *,
    repo_dir: Path,
    run_dir: Path,
    state: dict[str, object],
    source_description: str,
    workflow_name: str,
    data_context: dict[str, object] | None = None,
    hpc_context: dict[str, object] | None = None,
) -> bool:
    cli = _cli()
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
        raise cli.PackageError(
            f"Failed to read {workflow_name} plan file: {plan_path}: {exc}"
        ) from exc
    if not isinstance(raw_plan, dict):
        raise cli.PackageError(
            f"{workflow_name.title()} plan file is not a JSON object: {plan_path}"
        )

    normalized_plan = _normalize_automation_plan(
        raw_plan,
        source_description=source_description,
        hpc_context=hpc_context,
    )
    if _is_data_context_enabled(data_context):
        artifacts = (
            data_context.get("artifacts") if isinstance(data_context, dict) else {}
        )
        if not isinstance(artifacts, dict):
            raise cli.PackageError("Data context artifacts missing during plan sync.")
        manifest_rel = str(
            artifacts.get("manifest_compact") or artifacts.get("manifest") or ""
        ).strip()
        task_map_rel = str(artifacts.get("task_map") or "").strip()
        if not manifest_rel:
            raise cli.PackageError(
                "Data context manifest path missing during plan sync."
            )
        manifest_payload = _load_json_if_exists(repo_dir / manifest_rel)
        if not isinstance(manifest_payload, dict):
            raise cli.PackageError(
                f"Failed to load data manifest during plan sync: {repo_dir / manifest_rel}"
            )
        existing_task_map = (
            _load_json_if_exists(repo_dir / task_map_rel) if task_map_rel else None
        )
        normalized_plan_map = _synchronize_task_data_artifacts_with_plan(
            repo_dir=repo_dir,
            run_dir=run_dir,
            plan=normalized_plan,
            manifest_payload=manifest_payload,
            existing_task_map=existing_task_map,
        )
        if isinstance(data_context, dict):
            data_context["task_map_updated_at_utc"] = str(
                normalized_plan_map.get("updated_at_utc") or _utc_now_z()
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


def _capture_file_signature(path: Path) -> tuple[int, int, str] | None:
    if not path.is_file():
        return None
    try:
        file_stat = path.stat()
        payload = path.read_bytes()
    except OSError:
        return None
    return (
        int(len(payload)),
        int(file_stat.st_mtime_ns),
        hashlib.sha256(payload).hexdigest(),
    )


def _capture_signatures(
    paths: list[Path],
) -> dict[str, tuple[int, int, str] | None]:
    return {str(path): _capture_file_signature(path) for path in paths}


def _set_file_executable(path: Path) -> None:
    cli = _cli()
    try:
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)
    except OSError as exc:
        raise cli.PackageError(
            f"Failed to mark script executable: {path}: {exc}"
        ) from exc


def _render_workflow_stage_driver_script(
    *,
    workflow_name: str,
    run_id: str,
    stage_label: str,
    task_script_relpaths: list[tuple[str, str]],
    upstream_job_map_filename: str | None = None,
    emitted_job_map_filename: str | None = None,
) -> str:
    stage_slug = stage_label.lower().replace(" ", "_")
    upstream_job_map_rel = (
        str(upstream_job_map_filename or "").strip()
        if upstream_job_map_filename is not None
        else ""
    )
    emitted_job_map_rel = (
        str(emitted_job_map_filename or "").strip()
        if emitted_job_map_filename is not None
        else ""
    )
    entries = [
        f"  {shlex.quote(task_id + '|' + rel_path)}"
        for task_id, rel_path in task_script_relpaths
    ]
    total_count = len(task_script_relpaths)
    lines: list[str] = [
        "#!/usr/bin/env bash",
        "set -u -o pipefail",
        "",
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'RUN_DIR="$SCRIPT_DIR"',
        "",
        f'STAGE_LABEL="{stage_label}"',
        f'STAGE_SLUG="{stage_slug}"',
        f'WORKFLOW_NAME="{workflow_name}"',
        f'RUN_ID="{run_id}"',
        f"TOTAL_COUNT={total_count}",
        "SUCCESS_COUNT=0",
        "FAILURES=()",
        f'UPSTREAM_JOB_MAP_REL="{upstream_job_map_rel}"',
        f'EMITTED_JOB_MAP_REL="{emitted_job_map_rel}"',
        'UPSTREAM_JOB_MAP=""',
        'EMITTED_JOB_MAP=""',
        'if [[ -n "$UPSTREAM_JOB_MAP_REL" ]]; then',
        '  UPSTREAM_JOB_MAP="$RUN_DIR/$UPSTREAM_JOB_MAP_REL"',
        "fi",
        'if [[ -n "$EMITTED_JOB_MAP_REL" ]]; then',
        '  EMITTED_JOB_MAP="$RUN_DIR/$EMITTED_JOB_MAP_REL"',
        '  : > "$EMITTED_JOB_MAP"',
        "fi",
        "",
        "_lookup_upstream_job_id() {",
        '  local lookup_task="$1"',
        '  if [[ -z "$UPSTREAM_JOB_MAP" || ! -f "$UPSTREAM_JOB_MAP" ]]; then',
        "    return 0",
        "  fi",
        "  while IFS=$'\\t' read -r map_task map_job_id; do",
        '    if [[ "$map_task" == "$lookup_task" ]]; then',
        '      printf "%s" "$map_job_id"',
        "      return 0",
        "    fi",
        '  done < "$UPSTREAM_JOB_MAP"',
        "  return 0",
        "}",
        "",
        "_extract_final_job_id() {",
        '  local log_path="$1"',
        '  local marker_line=""',
        '  if [[ ! -f "$log_path" ]]; then',
        "    return 0",
        "  fi",
        f'  marker_line="$(grep -E \'^{WORKFLOW_FINAL_JOB_ID_MARKER}\' "$log_path" | tail -n 1 || true)"',
        '  if [[ -z "$marker_line" ]]; then',
        "    return 0",
        "  fi",
        f'  marker_line="${{marker_line#{WORKFLOW_FINAL_JOB_ID_MARKER}}}"',
        "  marker_line=\"${marker_line//$'\\r'/}\"",
        '  marker_line="$(printf "%s" "$marker_line" | tr -d "[:space:]")"',
        '  printf "%s" "$marker_line"',
        "}",
        "",
        "TASK_ENTRIES=(",
    ]
    if entries:
        lines.extend(entries)
    else:
        lines.append("  ''")
    lines.extend(
        [
            ")",
            "",
            'echo "[${STAGE_SLUG}] workflow=${WORKFLOW_NAME} run_id=${RUN_ID}"',
            'echo "[${STAGE_SLUG}] tasks=${TOTAL_COUNT}"',
            "",
            'for entry in "${TASK_ENTRIES[@]}"; do',
            '  if [[ -z "$entry" ]]; then',
            "    continue",
            "  fi",
            '  task_id="${entry%%|*}"',
            '  rel_script="${entry#*|}"',
            '  task_script="$RUN_DIR/$rel_script"',
            "",
            '  if [[ ! -f "$task_script" ]]; then',
            '    echo "[warn] ${task_id}: missing script ${rel_script}" >&2',
            '    FAILURES+=("${task_id}:missing_script")',
            "    continue",
            "  fi",
            "",
            '  upstream_job_id="$(_lookup_upstream_job_id "$task_id")"',
            '  if [[ -n "$upstream_job_id" ]]; then',
            '    echo "[run] ${task_id}: bash ${rel_script} (upstream_job_id=${upstream_job_id})"',
            "  else",
            '    echo "[run] ${task_id}: bash ${rel_script}"',
            "  fi",
            "",
            '  task_log="$RUN_DIR/.${STAGE_SLUG}_${task_id}.log"',
            '  : > "$task_log"',
            '  if [[ -n "$upstream_job_id" ]]; then',
            f'    {WORKFLOW_UPSTREAM_JOB_ID_ENV}="$upstream_job_id" bash "$task_script" > >(tee "$task_log") 2>&1',
            "  else",
            '    bash "$task_script" > >(tee "$task_log") 2>&1',
            "  fi",
            "  exit_code=$?",
            '  final_job_id="$(_extract_final_job_id "$task_log")"',
            '  rm -f "$task_log"',
            "",
            "  if [[ $exit_code -eq 0 ]]; then",
            "    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))",
            '    if [[ -n "$EMITTED_JOB_MAP" && -n "$final_job_id" ]]; then',
            '      printf "%s\\t%s\\n" "$task_id" "$final_job_id" >> "$EMITTED_JOB_MAP"',
            '      echo "[meta] ${task_id}: final_job_id=${final_job_id}"',
            "    fi",
            '    echo "[ok] ${task_id}"',
            "  else",
            '    echo "[error] ${task_id}: exit=${exit_code}" >&2',
            '    FAILURES+=("${task_id}:exit_${exit_code}")',
            "  fi",
            "done",
            "",
            'echo "[summary] ${STAGE_SLUG}: success=${SUCCESS_COUNT}/${TOTAL_COUNT}"',
            'if [[ -n "$EMITTED_JOB_MAP_REL" ]]; then',
            '  echo "[summary] ${STAGE_SLUG}: job_map=${EMITTED_JOB_MAP_REL}"',
            "fi",
            "if [[ ${#FAILURES[@]} -gt 0 ]]; then",
            '  echo "[summary] ${STAGE_SLUG}: failures=${#FAILURES[@]}" >&2',
            '  for item in "${FAILURES[@]}"; do',
            '    echo "  - ${item}" >&2',
            "  done",
            "  exit 1",
            "fi",
            "exit 0",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_workflow_all_in_one_driver_script(
    *,
    workflow_name: str,
    run_id: str,
    task_stage_script_relpaths: list[tuple[str, str, str, str]],
) -> str:
    entries = [
        f"  {shlex.quote(task_id + '|' + sim_rel + '|' + post_rel + '|' + plot_rel)}"
        for task_id, sim_rel, post_rel, plot_rel in task_stage_script_relpaths
    ]
    total_count = len(task_stage_script_relpaths)
    lines: list[str] = [
        "#!/usr/bin/env bash",
        "set -u -o pipefail",
        "",
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'RUN_DIR="$SCRIPT_DIR"',
        "",
        f'WORKFLOW_NAME="{workflow_name}"',
        f'RUN_ID="{run_id}"',
        f"TOTAL_COUNT={total_count}",
        "SUCCESS_COUNT=0",
        "FAILURES=()",
        'POLL_SECONDS="${FERMILINK_POLL_SECONDS:-20}"',
        'MAX_POLLS="${FERMILINK_MAX_POLLS:-0}"',
        'STAGE_FINAL_JOB_ID=""',
        "",
        'if ! [[ "$POLL_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then',
        '  echo "[error] invalid FERMILINK_POLL_SECONDS=${POLL_SECONDS}" >&2',
        "  exit 2",
        "fi",
        'if ! [[ "$MAX_POLLS" =~ ^[0-9]+$ ]]; then',
        '  echo "[error] invalid FERMILINK_MAX_POLLS=${MAX_POLLS}" >&2',
        "  exit 2",
        "fi",
        "",
        "_extract_final_job_id() {",
        '  local log_path="$1"',
        '  local marker_line=""',
        '  if [[ ! -f "$log_path" ]]; then',
        "    return 0",
        "  fi",
        f'  marker_line="$(grep -E \'^{WORKFLOW_FINAL_JOB_ID_MARKER}\' "$log_path" | tail -n 1 || true)"',
        '  if [[ -z "$marker_line" ]]; then',
        "    return 0",
        "  fi",
        f'  marker_line="${{marker_line#{WORKFLOW_FINAL_JOB_ID_MARKER}}}"',
        "  marker_line=\"${marker_line//$'\\r'/}\"",
        '  marker_line="$(printf "%s" "$marker_line" | tr -d "[:space:]")"',
        '  printf "%s" "$marker_line"',
        "}",
        "",
        "_query_slurm_job_state() {",
        '  local job_id="$1"',
        '  local state=""',
        "  if command -v sacct >/dev/null 2>&1; then",
        '    state="$(sacct -n -o State -j "$job_id" 2>/dev/null | awk \'NF {print $1; exit}\')"',
        "  fi",
        '  if [[ -z "$state" ]] && command -v squeue >/dev/null 2>&1; then',
        '    if squeue -h -j "$job_id" 2>/dev/null | grep -q .; then',
        '      state="PENDING"',
        "    fi",
        "  fi",
        '  state="${state%%+*}"',
        '  state="${state%% *}"',
        '  printf "%s" "$state"',
        "}",
        "",
        "_wait_for_slurm_job() {",
        '  local job_id="$1"',
        '  local stage_ref="$2"',
        "  local poll_count=0",
        "  if ! command -v sacct >/dev/null 2>&1 && ! command -v squeue >/dev/null 2>&1; then",
        '    echo "[error] ${stage_ref}: cannot wait for job ${job_id}; both `sacct` and `squeue` are unavailable" >&2',
        "    return 1",
        "  fi",
        "  while true; do",
        '    local state=""',
        '    state="$(_query_slurm_job_state "$job_id")"',
        '    if [[ "$state" == "COMPLETED" ]]; then',
        '      echo "[wait] ${stage_ref}: job ${job_id} COMPLETED"',
        "      return 0",
        "    fi",
        '    case "$state" in',
        "      FAILED|CANCELLED|TIMEOUT|NODE_FAIL|OUT_OF_MEMORY|PREEMPTED|BOOT_FAIL|DEADLINE|REVOKED|SPECIAL_EXIT|STOPPED)",
        '        echo "[error] ${stage_ref}: job ${job_id} finished in state ${state}" >&2',
        "        return 1",
        "        ;;",
        "    esac",
        "    poll_count=$((poll_count + 1))",
        '    if [[ "$MAX_POLLS" -gt 0 && "$poll_count" -ge "$MAX_POLLS" ]]; then',
        '      echo "[error] ${stage_ref}: exceeded FERMILINK_MAX_POLLS=${MAX_POLLS} while waiting for job ${job_id}" >&2',
        "      return 1",
        "    fi",
        '    if [[ -n "$state" ]]; then',
        '      echo "[wait] ${stage_ref}: job ${job_id} state=${state}; sleeping ${POLL_SECONDS}s"',
        "    else",
        '      echo "[wait] ${stage_ref}: job ${job_id} state unavailable; sleeping ${POLL_SECONDS}s"',
        "    fi",
        '    sleep "$POLL_SECONDS"',
        "  done",
        "}",
        "",
        "_run_stage_script() {",
        '  local task_id="$1"',
        '  local stage_label="$2"',
        '  local rel_script="$3"',
        '  local upstream_job_id="${4:-}"',
        '  local stage_script="$RUN_DIR/$rel_script"',
        '  local stage_log="$RUN_DIR/.run_all_${task_id}_${stage_label}.log"',
        '  STAGE_FINAL_JOB_ID=""',
        '  if [[ ! -f "$stage_script" ]]; then',
        '    echo "[error] ${task_id}/${stage_label}: missing script ${rel_script}" >&2',
        "    return 127",
        "  fi",
        '  if [[ -n "$upstream_job_id" ]]; then',
        '    echo "[run] ${task_id}/${stage_label}: bash ${rel_script} (upstream_job_id=${upstream_job_id})"',
        "  else",
        '    echo "[run] ${task_id}/${stage_label}: bash ${rel_script}"',
        "  fi",
        '  : > "$stage_log"',
        '  if [[ -n "$upstream_job_id" ]]; then',
        f'    {WORKFLOW_UPSTREAM_JOB_ID_ENV}="$upstream_job_id" bash "$stage_script" > >(tee "$stage_log") 2>&1',
        "  else",
        '    bash "$stage_script" > >(tee "$stage_log") 2>&1',
        "  fi",
        "  local exit_code=$?",
        '  local final_job_id=""',
        '  final_job_id="$(_extract_final_job_id "$stage_log")"',
        '  rm -f "$stage_log"',
        '  STAGE_FINAL_JOB_ID="$final_job_id"',
        '  if [[ "$exit_code" -ne 0 ]]; then',
        '    echo "[error] ${task_id}/${stage_label}: exit=${exit_code}" >&2',
        '    return "$exit_code"',
        "  fi",
        '  if [[ -n "$final_job_id" ]]; then',
        '    echo "[meta] ${task_id}/${stage_label}: final_job_id=${final_job_id}"',
        "  fi",
        "  return 0",
        "}",
        "",
        "TASK_ENTRIES=(",
    ]
    if entries:
        lines.extend(entries)
    else:
        lines.append("  ''")
    lines.extend(
        [
            ")",
            "",
            'echo "[run_all] workflow=${WORKFLOW_NAME} run_id=${RUN_ID}"',
            'echo "[run_all] tasks=${TOTAL_COUNT}"',
            "",
            'for entry in "${TASK_ENTRIES[@]}"; do',
            '  if [[ -z "$entry" ]]; then',
            "    continue",
            "  fi",
            '  task_id="${entry%%|*}"',
            '  rest="${entry#*|}"',
            '  sim_rel="${rest%%|*}"',
            '  rest="${rest#*|}"',
            '  post_rel="${rest%%|*}"',
            '  plot_rel="${rest#*|}"',
            "",
            '  sim_job_id=""',
            '  post_job_id=""',
            '  plot_job_id=""',
            "",
            '  if ! _run_stage_script "$task_id" "simulation" "$sim_rel" ""; then',
            "    exit_code=$?",
            '    FAILURES+=("${task_id}:simulation_exit_${exit_code}")',
            "    continue",
            "  fi",
            '  sim_job_id="$STAGE_FINAL_JOB_ID"',
            '  if [[ -n "$sim_job_id" ]]; then',
            '    if ! _wait_for_slurm_job "$sim_job_id" "${task_id}/simulation"; then',
            '      FAILURES+=("${task_id}:simulation_job_wait_failed_${sim_job_id}")',
            "      continue",
            "    fi",
            "  fi",
            "",
            '  if ! _run_stage_script "$task_id" "postprocess" "$post_rel" "$sim_job_id"; then',
            "    exit_code=$?",
            '    FAILURES+=("${task_id}:postprocess_exit_${exit_code}")',
            "    continue",
            "  fi",
            '  post_job_id="$STAGE_FINAL_JOB_ID"',
            '  if [[ -n "$post_job_id" ]]; then',
            '    if ! _wait_for_slurm_job "$post_job_id" "${task_id}/postprocess"; then',
            '      FAILURES+=("${task_id}:postprocess_job_wait_failed_${post_job_id}")',
            "      continue",
            "    fi",
            "  fi",
            "",
            '  if ! _run_stage_script "$task_id" "plot" "$plot_rel" "$post_job_id"; then',
            "    exit_code=$?",
            '    FAILURES+=("${task_id}:plot_exit_${exit_code}")',
            "    continue",
            "  fi",
            '  plot_job_id="$STAGE_FINAL_JOB_ID"',
            '  if [[ -n "$plot_job_id" ]]; then',
            '    if ! _wait_for_slurm_job "$plot_job_id" "${task_id}/plot"; then',
            '      FAILURES+=("${task_id}:plot_job_wait_failed_${plot_job_id}")',
            "      continue",
            "    fi",
            "  fi",
            "",
            "  SUCCESS_COUNT=$((SUCCESS_COUNT + 1))",
            '  echo "[ok] ${task_id}: simulation + postprocess + plot completed"',
            "done",
            "",
            'echo "[summary] run_all: success=${SUCCESS_COUNT}/${TOTAL_COUNT}"',
            "if [[ ${#FAILURES[@]} -gt 0 ]]; then",
            '  echo "[summary] run_all: failures=${#FAILURES[@]}" >&2',
            '  for item in "${FAILURES[@]}"; do',
            '    echo "  - ${item}" >&2',
            "  done",
            "  exit 1",
            "fi",
            "exit 0",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_workflow_stage_driver_scripts(
    *,
    run_dir: Path,
    workflow_name: str,
    run_id: str,
    simulation_task_scripts: list[tuple[str, Path]],
    postprocess_task_scripts: list[tuple[str, Path]],
    plot_task_scripts: list[tuple[str, Path]],
) -> dict[str, object]:
    run_all_path = run_dir / WORKFLOW_ALLINONE_BATCH_SCRIPT_FILENAME
    simulation_path = run_dir / WORKFLOW_SIMULATION_BATCH_SCRIPT_FILENAME
    postprocess_path = run_dir / WORKFLOW_POSTPROCESS_BATCH_SCRIPT_FILENAME
    plot_path = run_dir / WORKFLOW_PLOT_BATCH_SCRIPT_FILENAME
    simulation_job_map_path = run_dir / WORKFLOW_SIMULATION_JOB_MAP_FILENAME
    postprocess_job_map_path = run_dir / WORKFLOW_POSTPROCESS_JOB_MAP_FILENAME
    plot_job_map_path = run_dir / WORKFLOW_PLOT_JOB_MAP_FILENAME

    simulation_rel = [
        (task_id, str(path.relative_to(run_dir)))
        for task_id, path in simulation_task_scripts
    ]
    postprocess_rel = [
        (task_id, str(path.relative_to(run_dir)))
        for task_id, path in postprocess_task_scripts
    ]
    plot_rel = [
        (task_id, str(path.relative_to(run_dir))) for task_id, path in plot_task_scripts
    ]
    if not (
        len(simulation_task_scripts)
        == len(postprocess_task_scripts)
        == len(plot_task_scripts)
    ):
        cli = _cli()
        raise cli.PackageError(
            "Workflow stage script lists are inconsistent; cannot generate run-all script."
        )
    run_all_rel = [
        (
            task_id,
            str(sim_path.relative_to(run_dir)),
            str(post_path.relative_to(run_dir)),
            str(plot_path.relative_to(run_dir)),
        )
        for (task_id, sim_path), (_, post_path), (_, plot_path) in zip(
            simulation_task_scripts, postprocess_task_scripts, plot_task_scripts
        )
    ]

    _write_text_file(
        run_all_path,
        _render_workflow_all_in_one_driver_script(
            workflow_name=workflow_name,
            run_id=run_id,
            task_stage_script_relpaths=run_all_rel,
        ),
    )
    _write_text_file(
        simulation_path,
        _render_workflow_stage_driver_script(
            workflow_name=workflow_name,
            run_id=run_id,
            stage_label="simulation",
            task_script_relpaths=simulation_rel,
            emitted_job_map_filename=WORKFLOW_SIMULATION_JOB_MAP_FILENAME,
        ),
    )
    _write_text_file(
        postprocess_path,
        _render_workflow_stage_driver_script(
            workflow_name=workflow_name,
            run_id=run_id,
            stage_label="postprocess",
            task_script_relpaths=postprocess_rel,
            upstream_job_map_filename=WORKFLOW_SIMULATION_JOB_MAP_FILENAME,
            emitted_job_map_filename=WORKFLOW_POSTPROCESS_JOB_MAP_FILENAME,
        ),
    )
    _write_text_file(
        plot_path,
        _render_workflow_stage_driver_script(
            workflow_name=workflow_name,
            run_id=run_id,
            stage_label="plot",
            task_script_relpaths=plot_rel,
            upstream_job_map_filename=WORKFLOW_POSTPROCESS_JOB_MAP_FILENAME,
            emitted_job_map_filename=WORKFLOW_PLOT_JOB_MAP_FILENAME,
        ),
    )
    _set_file_executable(run_all_path)
    _set_file_executable(simulation_path)
    _set_file_executable(postprocess_path)
    _set_file_executable(plot_path)
    return {
        "run_all_script_path": str(run_all_path),
        "simulation_script_path": str(simulation_path),
        "postprocess_script_path": str(postprocess_path),
        "plot_script_path": str(plot_path),
        "simulation_job_map_path": str(simulation_job_map_path),
        "postprocess_job_map_path": str(postprocess_job_map_path),
        "plot_job_map_path": str(plot_job_map_path),
    }


def _non_comment_shell_lines(script_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in script_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def _build_hpc_contract_issue(
    *,
    stage_label: str,
    task_id: str,
    script_rel: str,
    code: str,
    message: str,
) -> dict[str, str]:
    return {
        "stage": stage_label,
        "task_id": task_id,
        "script_path": script_rel,
        "code": code,
        "message": message,
    }


def _format_hpc_contract_issue(issue: dict[str, str]) -> str:
    stage_label = str(issue.get("stage") or "stage").strip() or "stage"
    task_id = str(issue.get("task_id") or "task").strip() or "task"
    script_rel = str(issue.get("script_path") or "unknown").strip() or "unknown"
    message = (
        str(issue.get("message") or "validation issue").strip() or "validation issue"
    )
    return f"{stage_label}:{task_id} ({script_rel}) {message}"


def _collect_hpc_stage_task_script_issues(
    *,
    repo_dir: Path,
    stage_label: str,
    task_scripts: list[tuple[str, Path]],
    require_upstream_dependency: bool,
    require_dependency_for_multi_sbatch: bool,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for task_id, script_path in task_scripts:
        script_rel = _repo_relative_path(repo_dir, script_path)
        try:
            script_text = script_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            issues.append(
                _build_hpc_contract_issue(
                    stage_label=stage_label,
                    task_id=task_id,
                    script_rel=script_rel,
                    code="read_failed",
                    message=f"failed to read script: {exc}",
                )
            )
            continue

        command_lines = _non_comment_shell_lines(script_text)
        sbatch_count = sum(
            1 for line in command_lines if SBATCH_TOKEN_RE.search(line) is not None
        )
        if sbatch_count <= 0:
            continue

        has_parsable = any(
            SBATCH_PARSABLE_RE.search(line) is not None for line in command_lines
        )
        has_afterok = any(
            SBATCH_AFTEROK_RE.search(line) is not None for line in command_lines
        )
        has_final_job_marker = any(
            WORKFLOW_FINAL_JOB_ID_MARKER in line for line in command_lines
        )
        has_upstream_env_ref = any(
            WORKFLOW_UPSTREAM_JOB_ID_ENV in line for line in command_lines
        )

        if not has_parsable:
            issues.append(
                _build_hpc_contract_issue(
                    stage_label=stage_label,
                    task_id=task_id,
                    script_rel=script_rel,
                    code="missing_parsable",
                    message="uses `sbatch` without `--parsable`.",
                )
            )
        if not has_final_job_marker:
            issues.append(
                _build_hpc_contract_issue(
                    stage_label=stage_label,
                    task_id=task_id,
                    script_rel=script_rel,
                    code="missing_final_job_marker",
                    message=(
                        "uses `sbatch` without emitting "
                        f"`{WORKFLOW_FINAL_JOB_ID_MARKER}<job_id>`."
                    ),
                )
            )
        if (
            require_dependency_for_multi_sbatch
            and sbatch_count >= 2
            and not has_afterok
        ):
            issues.append(
                _build_hpc_contract_issue(
                    stage_label=stage_label,
                    task_id=task_id,
                    script_rel=script_rel,
                    code="missing_afterok_chain",
                    message=(
                        "has multiple `sbatch` commands without "
                        "`--dependency=afterok:<job_id>` chaining."
                    ),
                )
            )
        if require_upstream_dependency:
            if not has_upstream_env_ref:
                issues.append(
                    _build_hpc_contract_issue(
                        stage_label=stage_label,
                        task_id=task_id,
                        script_rel=script_rel,
                        code="missing_upstream_env_ref",
                        message=(
                            "uses `sbatch` without referencing "
                            f"`{WORKFLOW_UPSTREAM_JOB_ID_ENV}`."
                        ),
                    )
                )
            if not has_afterok:
                issues.append(
                    _build_hpc_contract_issue(
                        stage_label=stage_label,
                        task_id=task_id,
                        script_rel=script_rel,
                        code="missing_upstream_afterok",
                        message=(
                            "uses `sbatch` without an `--dependency=afterok:` "
                            "path for upstream gating."
                        ),
                    )
                )
    return issues


def _collect_hpc_workflow_task_script_issues(
    *,
    repo_dir: Path,
    simulation_task_scripts: list[tuple[str, Path]],
    postprocess_task_scripts: list[tuple[str, Path]],
    plot_task_scripts: list[tuple[str, Path]],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    issues.extend(
        _collect_hpc_stage_task_script_issues(
            repo_dir=repo_dir,
            stage_label="simulation",
            task_scripts=simulation_task_scripts,
            require_upstream_dependency=False,
            require_dependency_for_multi_sbatch=True,
        )
    )
    issues.extend(
        _collect_hpc_stage_task_script_issues(
            repo_dir=repo_dir,
            stage_label="postprocess",
            task_scripts=postprocess_task_scripts,
            require_upstream_dependency=True,
            require_dependency_for_multi_sbatch=False,
        )
    )
    issues.extend(
        _collect_hpc_stage_task_script_issues(
            repo_dir=repo_dir,
            stage_label="plot",
            task_scripts=plot_task_scripts,
            require_upstream_dependency=True,
            require_dependency_for_multi_sbatch=False,
        )
    )
    return issues


def _summarize_hpc_contract_issues(issues: list[dict[str, str]]) -> str | None:
    if not issues:
        return None
    rendered = [_format_hpc_contract_issue(issue) for issue in issues]
    return "HPC SLURM task-script contract validation failed: " + "; ".join(rendered)


def _hpc_contract_issues_fingerprint(issues: list[dict[str, str]]) -> str:
    normalized = [
        {
            "stage": str(item.get("stage") or ""),
            "task_id": str(item.get("task_id") or ""),
            "script_path": str(item.get("script_path") or ""),
            "code": str(item.get("code") or ""),
            "message": str(item.get("message") or ""),
        }
        for item in issues
        if isinstance(item, dict)
    ]
    normalized.sort(
        key=lambda item: (
            item["stage"],
            item["task_id"],
            item["script_path"],
            item["code"],
            item["message"],
        )
    )
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _render_hpc_contract_feedback_block(
    *,
    stage_label: str,
    attempt: int,
    max_attempts: int,
    issues: list[dict[str, str]],
) -> str:
    limited = issues[:WORKFLOW_HPC_FEEDBACK_MAX_ISSUES]
    issue_lines = [f"- {_format_hpc_contract_issue(issue)}" for issue in limited]
    if len(issues) > len(limited):
        issue_lines.append(
            f"- ... plus {len(issues) - len(limited)} additional issues."
        )
    issue_json = json.dumps({"issues": limited}, indent=2)
    return (
        "\n"
        "Validation feedback from previous attempt (must fix all before continuing):\n"
        f"- Stage: {stage_label}\n"
        f"- Attempt: {attempt}/{max_attempts}\n"
        "- Focus edits on failing task scripts only; keep unrelated files unchanged.\n"
        "- Keep scripts valid bash with shebang + `set -euo pipefail`.\n"
        + "\n".join(issue_lines)
        + "\n"
        "Structured issue payload:\n"
        f"{issue_json}\n"
    )


def _write_hpc_contract_errors_artifact(
    *,
    path: Path,
    workflow_name: str,
    run_id: str,
    stage_label: str,
    attempt: int,
    max_attempts: int,
    issues: list[dict[str, str]],
    stage_errors: list[str],
    resolved: bool,
) -> None:
    payload: dict[str, object] = {
        "version": 1,
        "updated_at_utc": _utc_now_z(),
        "workflow": workflow_name,
        "run_id": run_id,
        "stage": stage_label,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "resolved": resolved,
        "issue_count": len(issues),
        "issues": issues,
        "stage_errors": stage_errors,
    }
    _write_text_file(path, json.dumps(payload, indent=2) + "\n")


def _validate_hpc_stage_task_scripts(
    *,
    repo_dir: Path,
    stage_label: str,
    task_scripts: list[tuple[str, Path]],
    require_upstream_dependency: bool,
    require_dependency_for_multi_sbatch: bool,
) -> list[str]:
    issues = _collect_hpc_stage_task_script_issues(
        repo_dir=repo_dir,
        stage_label=stage_label,
        task_scripts=task_scripts,
        require_upstream_dependency=require_upstream_dependency,
        require_dependency_for_multi_sbatch=require_dependency_for_multi_sbatch,
    )
    return [_format_hpc_contract_issue(issue) for issue in issues]


def _validate_hpc_workflow_task_scripts(
    *,
    repo_dir: Path,
    simulation_task_scripts: list[tuple[str, Path]],
    postprocess_task_scripts: list[tuple[str, Path]],
    plot_task_scripts: list[tuple[str, Path]],
) -> str | None:
    issues = _collect_hpc_workflow_task_script_issues(
        repo_dir=repo_dir,
        simulation_task_scripts=simulation_task_scripts,
        postprocess_task_scripts=postprocess_task_scripts,
        plot_task_scripts=plot_task_scripts,
    )
    return _summarize_hpc_contract_issues(issues)


def _validate_report_stage_artifacts(
    *,
    stage_label: str,
    report_path: Path,
    summary_paths: list[Path],
    before_report_signature: tuple[int, int, str] | None,
    before_summary_signatures: dict[str, tuple[int, int, str] | None],
    required_marker: str,
    required_files: list[Path] | None = None,
    before_required_signatures: dict[str, tuple[int, int, str] | None] | None = None,
) -> str | None:
    errors: list[str] = []
    report_signature = _capture_file_signature(report_path)
    report_text = ""
    if report_signature is None:
        errors.append(f"missing report file: {report_path}")
    else:
        if report_signature[0] <= 0:
            errors.append(f"empty report file: {report_path}")
        try:
            report_text = report_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"failed to read report file: {report_path}: {exc}")
        if required_marker not in report_text:
            errors.append(f"report missing required marker: {required_marker}")

    summary_signatures = _capture_signatures(summary_paths)
    for summary_path in summary_paths:
        summary_signature = summary_signatures.get(str(summary_path))
        if summary_signature is None:
            errors.append(f"missing summary file: {summary_path}")
            continue
        if summary_signature[0] <= 0:
            errors.append(f"empty summary file: {summary_path}")

    required_signatures: dict[str, tuple[int, int, str] | None] = {}
    if isinstance(required_files, list):
        required_signatures = _capture_signatures(required_files)
        for required_path in required_files:
            required_signature = required_signatures.get(str(required_path))
            if required_signature is None:
                errors.append(f"missing required file: {required_path}")
                continue
            if required_signature[0] <= 0:
                errors.append(f"empty required file: {required_path}")

    artifacts_updated = report_signature != before_report_signature
    if not artifacts_updated:
        for path_text, after_signature in summary_signatures.items():
            if after_signature != before_summary_signatures.get(path_text):
                artifacts_updated = True
                break
    if (
        not artifacts_updated
        and isinstance(before_required_signatures, dict)
        and required_signatures
    ):
        for path_text, after_signature in required_signatures.items():
            if after_signature != before_required_signatures.get(path_text):
                artifacts_updated = True
                break
    if not artifacts_updated:
        errors.append("no report/summary artifacts were updated in this stage")

    if errors:
        return f"{stage_label} validation failed: {'; '.join(errors)}"
    return None


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
    provider_bin_override: str,
    data_context: dict[str, object] | None = None,
    hpc_context: dict[str, object] | None = None,
    workflow_status_hook: WorkflowStatusHook | None = None,
) -> dict[str, object]:
    """Generate and audit final workflow reports for `reproduce` and `research`."""

    cli = _cli()
    plan_path = run_dir / REPRODUCE_PLAN_FILENAME
    if not plan_path.is_file():
        raise cli.PackageError(
            f"Missing plan file for {workflow_name} report generation: {plan_path}"
        )

    summaries_root = run_dir / WORKFLOW_SUMMARIES_DIRNAME
    summaries_root.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / WORKFLOW_REPORT_FILENAME
    run_id = run_dir.name
    generation_marker = f"<!-- FERMILINK_REPORT_STAGE:generated run_id={run_id} -->"
    audit_marker = f"<!-- FERMILINK_REPORT_STAGE:audited run_id={run_id} -->"
    hpc_contract_errors_path = run_dir / WORKFLOW_HPC_CONTRACT_ERRORS_FILENAME

    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(repo_dir))
        except ValueError:
            return str(path)

    summary_paths: list[Path] = []
    simulation_task_scripts: list[tuple[str, Path]] = []
    postprocess_task_scripts: list[tuple[str, Path]] = []
    plot_task_scripts: list[tuple[str, Path]] = []
    task_lines: list[str] = []
    for index, task in enumerate(tasks_state, start=1):
        task_id = (
            str(task.get("id") or f"task_{index:03d}").strip() or f"task_{index:03d}"
        )
        summary_path = summaries_root / task_id / "summary.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_paths.append(summary_path)
        simulation_task_script = (
            summaries_root / task_id / WORKFLOW_TASK_SIMULATION_SCRIPT_FILENAME
        )
        postprocess_task_script = (
            summaries_root / task_id / WORKFLOW_TASK_POSTPROCESS_SCRIPT_FILENAME
        )
        plot_task_script = summaries_root / task_id / WORKFLOW_TASK_PLOT_SCRIPT_FILENAME
        simulation_task_scripts.append((task_id, simulation_task_script))
        postprocess_task_scripts.append((task_id, postprocess_task_script))
        plot_task_scripts.append((task_id, plot_task_script))
        task_title = str(task.get("title") or task_id).strip() or task_id
        task_lines.append(
            "\n".join(
                [
                    f"- {task_id}: {task_title}",
                    f"  - summary: {_display_path(summary_path)}",
                    f"  - simulation script: {_display_path(simulation_task_script)}",
                    f"  - postprocess script: {_display_path(postprocess_task_script)}",
                    f"  - plot script: {_display_path(plot_task_script)}",
                ]
            )
        )

    required_stage_files: list[Path] = (
        [path for _, path in simulation_task_scripts]
        + [path for _, path in postprocess_task_scripts]
        + [path for _, path in plot_task_scripts]
    )
    hpc_prompt_lines = _build_hpc_prompt_lines(hpc_context)
    execution_target_block = "\n".join(hpc_prompt_lines)
    hpc_mode = _is_hpc_context_enabled(hpc_context)
    hpc_task_script_requirements = ""
    if hpc_mode:
        hpc_task_script_requirements = (
            "7) In HPC SLURM mode, when any stage script uses `sbatch`, capture job ids via `sbatch --parsable` and emit the final submitted job id as `FERMILINK_FINAL_JOB_ID=<job_id>`.\n"
            "8) In `run_simulation.sh`, when multiple `sbatch` submissions have intrinsic ordering (for example equilibration then production), chain them with `--dependency=afterok:<job_id>`.\n"
            "9) In `run_postprocess.sh` and `run_plot.sh`, honor optional environment variable `FERMILINK_UPSTREAM_JOB_ID`; when set and using `sbatch`, apply `--dependency=afterok:${FERMILINK_UPSTREAM_JOB_ID}` to the first submission.\n"
        )

    generator_prompt = (
        f"{WORKFLOW_REPORT_GENERATOR_PROMPT_PREFIX}\n"
        f"{WORKFLOW_UNIFIED_MEMORY_STAGE_INSTRUCTIONS}\n"
        "\n"
        f"Workflow: {workflow_name}\n"
        f"Source description: {source_description}\n"
        "\n"
        "Use these artifacts:\n"
        f"- Plan JSON: {_display_path(plan_path)}\n"
        f"- Task prompt directory: {_display_path(run_dir / REPRODUCE_PROMPTS_DIRNAME)}\n"
        f"- Existing run logs directory: {_display_path(run_dir / REPRODUCE_LOGS_DIRNAME)}\n"
        "\n"
        "Execution target constraints:\n"
        f"{execution_target_block}\n"
        "\n"
        "For each task, create/update one summary and three runnable task scripts at:\n"
        + "\n".join(task_lines)
        + "\n\n"
        "Then create/update a polished top-level markdown report at:\n"
        f"- {_display_path(report_path)}\n"
        "\n"
        "Task script requirements:\n"
        "1) Each task script must be valid bash with shebang (`#!/usr/bin/env bash`).\n"
        "2) Each task script should use `set -euo pipefail`.\n"
        "3) `run_simulation.sh` should contain simulation submission/execution commands for that task.\n"
        "4) `run_postprocess.sh` should contain post-processing commands for that task.\n"
        "5) `run_plot.sh` should contain plotting commands for that task.\n"
        "6) Prefer concrete commands from task artifacts; if uncertain, include explicit TODO comments while keeping script syntax valid.\n"
        f"{hpc_task_script_requirements}"
        "\n"
        "Report requirements:\n"
        "1) Brief objective and methodology sections.\n"
        "2) Per-task results linked to corresponding task summaries.\n"
        "3) Include markdown figure/image links to generated outputs whenever files exist.\n"
        "4) Explicitly note missing artifacts or limitations.\n"
        "5) End with concise conclusions and next-step suggestions.\n"
        "6) Include this exact marker line anywhere in the report:\n"
        f"{generation_marker}\n"
    )

    generation_max_attempts = WORKFLOW_HPC_VALIDATION_MAX_ATTEMPTS if hpc_mode else 2
    generation_feedback_block = ""
    generation_failure_reason = ""
    generation_hpc_last_fingerprint = ""
    generation_hpc_stall_count = 0
    generation_success = False
    generation_last_hpc_issues: list[dict[str, str]] = []
    _emit_workflow_status(workflow_status_hook, "summary")
    for attempt in range(1, generation_max_attempts + 1):
        cli._print_tagged(
            workflow_name,
            f"report generation attempt {attempt}/{generation_max_attempts}",
        )
        before_report_signature = _capture_file_signature(report_path)
        before_summary_signatures = _capture_signatures(summary_paths)
        before_required_signatures = _capture_signatures(required_stage_files)
        generation_prompt_with_feedback = (
            generator_prompt + generation_feedback_block
            if generation_feedback_block
            else generator_prompt
        )
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=generation_prompt_with_feedback,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            provider_bin_override=provider_bin_override,
            data_context=data_context,
        )
        return_code = int(run_result.get("return_code") or 0)
        if return_code == 0:
            generation_validation_error = _validate_report_stage_artifacts(
                stage_label=f"{workflow_name} report generation",
                report_path=report_path,
                summary_paths=summary_paths,
                before_report_signature=before_report_signature,
                before_summary_signatures=before_summary_signatures,
                required_marker=generation_marker,
                required_files=required_stage_files,
                before_required_signatures=before_required_signatures,
            )
            generation_last_hpc_issues = (
                _collect_hpc_workflow_task_script_issues(
                    repo_dir=repo_dir,
                    simulation_task_scripts=simulation_task_scripts,
                    postprocess_task_scripts=postprocess_task_scripts,
                    plot_task_scripts=plot_task_scripts,
                )
                if hpc_mode
                else []
            )
            hpc_script_validation_error = _summarize_hpc_contract_issues(
                generation_last_hpc_issues
            )
            stage_errors = [
                error
                for error in [generation_validation_error, hpc_script_validation_error]
                if isinstance(error, str) and error.strip()
            ]
            if not stage_errors:
                generation_success = True
                break

            generation_failure_reason = "; ".join(stage_errors)
            for error in stage_errors:
                cli._print_tagged(workflow_name, error, stderr=True)

            if hpc_mode and generation_last_hpc_issues:
                _write_hpc_contract_errors_artifact(
                    path=hpc_contract_errors_path,
                    workflow_name=workflow_name,
                    run_id=run_id,
                    stage_label="generation",
                    attempt=attempt,
                    max_attempts=generation_max_attempts,
                    issues=generation_last_hpc_issues,
                    stage_errors=stage_errors,
                    resolved=False,
                )
                generation_feedback_block = _render_hpc_contract_feedback_block(
                    stage_label="generation",
                    attempt=attempt,
                    max_attempts=generation_max_attempts,
                    issues=generation_last_hpc_issues,
                )
                fingerprint = _hpc_contract_issues_fingerprint(
                    generation_last_hpc_issues
                )
                if (
                    generation_hpc_last_fingerprint
                    and fingerprint == generation_hpc_last_fingerprint
                ):
                    generation_hpc_stall_count += 1
                else:
                    generation_hpc_stall_count = 0
                generation_hpc_last_fingerprint = fingerprint
                if generation_hpc_stall_count >= WORKFLOW_HPC_VALIDATION_STALL_LIMIT:
                    generation_failure_reason = (
                        generation_failure_reason
                        + " Repeated identical HPC contract issues detected with no progress."
                    )
                    break
            else:
                generation_feedback_block = ""
        else:
            generation_failure_reason = f"{workflow_name.title()} report generation failed with exit code {return_code}."
            generation_feedback_block = ""
    if not generation_success:
        raise cli.PackageError(
            generation_failure_reason
            or f"{workflow_name.title()} report generation failed."
        )

    auditor_prompt = (
        f"{WORKFLOW_REPORT_AUDITOR_PROMPT_PREFIX}\n"
        f"{WORKFLOW_UNIFIED_MEMORY_STAGE_INSTRUCTIONS}\n"
        "\n"
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
        "5) Ensure the report contains this exact marker line:\n"
        f"{audit_marker}\n"
    )
    audit_max_attempts = WORKFLOW_HPC_VALIDATION_MAX_ATTEMPTS if hpc_mode else 2
    audit_feedback_block = ""
    audit_failure_reason = ""
    audit_hpc_last_fingerprint = ""
    audit_hpc_stall_count = 0
    audit_success = False
    audit_last_hpc_issues: list[dict[str, str]] = []
    _emit_workflow_status(workflow_status_hook, "summary audit")
    for attempt in range(1, audit_max_attempts + 1):
        cli._print_tagged(
            workflow_name,
            f"report audit attempt {attempt}/{audit_max_attempts}",
        )
        before_report_signature = _capture_file_signature(report_path)
        before_summary_signatures = _capture_signatures(summary_paths)
        before_required_signatures = _capture_signatures(required_stage_files)
        audit_prompt_with_feedback = (
            auditor_prompt + audit_feedback_block
            if audit_feedback_block
            else auditor_prompt
        )
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=audit_prompt_with_feedback,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            provider_bin_override=provider_bin_override,
            data_context=data_context,
        )
        return_code = int(run_result.get("return_code") or 0)
        if return_code == 0:
            audit_validation_error = _validate_report_stage_artifacts(
                stage_label=f"{workflow_name} report audit",
                report_path=report_path,
                summary_paths=summary_paths,
                before_report_signature=before_report_signature,
                before_summary_signatures=before_summary_signatures,
                required_marker=audit_marker,
                required_files=required_stage_files,
                before_required_signatures=before_required_signatures,
            )
            audit_last_hpc_issues = (
                _collect_hpc_workflow_task_script_issues(
                    repo_dir=repo_dir,
                    simulation_task_scripts=simulation_task_scripts,
                    postprocess_task_scripts=postprocess_task_scripts,
                    plot_task_scripts=plot_task_scripts,
                )
                if hpc_mode
                else []
            )
            hpc_script_validation_error = _summarize_hpc_contract_issues(
                audit_last_hpc_issues
            )
            stage_errors = [
                error
                for error in [audit_validation_error, hpc_script_validation_error]
                if isinstance(error, str) and error.strip()
            ]
            if not stage_errors:
                audit_success = True
                break

            audit_failure_reason = "; ".join(stage_errors)
            for error in stage_errors:
                cli._print_tagged(workflow_name, error, stderr=True)

            if hpc_mode and audit_last_hpc_issues:
                _write_hpc_contract_errors_artifact(
                    path=hpc_contract_errors_path,
                    workflow_name=workflow_name,
                    run_id=run_id,
                    stage_label="audit",
                    attempt=attempt,
                    max_attempts=audit_max_attempts,
                    issues=audit_last_hpc_issues,
                    stage_errors=stage_errors,
                    resolved=False,
                )
                audit_feedback_block = _render_hpc_contract_feedback_block(
                    stage_label="audit",
                    attempt=attempt,
                    max_attempts=audit_max_attempts,
                    issues=audit_last_hpc_issues,
                )
                fingerprint = _hpc_contract_issues_fingerprint(audit_last_hpc_issues)
                if (
                    audit_hpc_last_fingerprint
                    and fingerprint == audit_hpc_last_fingerprint
                ):
                    audit_hpc_stall_count += 1
                else:
                    audit_hpc_stall_count = 0
                audit_hpc_last_fingerprint = fingerprint
                if audit_hpc_stall_count >= WORKFLOW_HPC_VALIDATION_STALL_LIMIT:
                    audit_failure_reason = (
                        audit_failure_reason
                        + " Repeated identical HPC contract issues detected with no progress."
                    )
                    break
            else:
                audit_feedback_block = ""
        else:
            audit_failure_reason = f"{workflow_name.title()} report audit failed with exit code {return_code}."
            audit_feedback_block = ""
    if not audit_success:
        raise cli.PackageError(
            audit_failure_reason or f"{workflow_name.title()} report audit failed."
        )

    if hpc_mode:
        _write_hpc_contract_errors_artifact(
            path=hpc_contract_errors_path,
            workflow_name=workflow_name,
            run_id=run_id,
            stage_label="final",
            attempt=0,
            max_attempts=0,
            issues=[],
            stage_errors=[],
            resolved=True,
        )

    stage_driver_paths = _write_workflow_stage_driver_scripts(
        run_dir=run_dir,
        workflow_name=workflow_name,
        run_id=run_id,
        simulation_task_scripts=simulation_task_scripts,
        postprocess_task_scripts=postprocess_task_scripts,
        plot_task_scripts=plot_task_scripts,
    )

    result = {
        "report_path": str(report_path),
        "summaries_root": str(summaries_root),
        "summary_count": len(summary_paths),
        **stage_driver_paths,
    }
    if hpc_mode:
        result["hpc_contract_errors_path"] = str(hpc_contract_errors_path)
    return result


def cmd_plan_workflow(
    args: argparse.Namespace,
    *,
    workflow_name: str,
    runs_dir_name: str,
    generate_plan,
) -> int:
    """Shared orchestration backbone for `reproduce` and `research`.

    Dependency note:
    - This planner/executor is reused by both major workflow commands.
    - Task execution is delegated to `loop`, which itself reuses the `exec` stack.
    """

    cli = _cli()
    repo_dir = Path.cwd().resolve()
    cli._ensure_exec_repo_ready(repo_dir, args)

    user_prompt, prompt_file = cli._resolve_exec_like_user_prompt(args)
    source_description = prompt_file or "inline prompt"
    plan_only = bool(getattr(args, "plan_only", False))
    report_only = bool(getattr(args, "report_only", False))
    skip_report = bool(getattr(args, "skip_report", False))
    post_task_plan_audit = bool(getattr(args, "post_task_plan_audit", True))
    if plan_only and report_only:
        raise cli.PackageError("Cannot combine --plan-only and --report-only.")
    if report_only and skip_report:
        raise cli.PackageError("Cannot combine --report-only and --skip-report.")
    if report_only:
        cli._ensure_loop_memory(
            repo_dir=repo_dir,
            user_prompt=user_prompt,
            prompt_file=prompt_file,
            overwrite=False,
        )
    else:
        cli._reset_loop_short_term_memory(
            repo_dir=repo_dir,
            user_prompt=user_prompt,
            prompt_file=prompt_file,
            workflow_context_lines=[
                f"- workflow: {workflow_name}",
                "- stage: workflow_entry",
            ],
        )

    task_max_runs_raw = getattr(args, "task_max_runs", 5)
    try:
        task_max_runs = int(task_max_runs_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--task-max-runs must be an integer.") from exc
    if task_max_runs < 1:
        raise cli.PackageError("--task-max-runs must be >= 1.")

    planner_max_tries_raw = getattr(args, "planner_max_tries", 2)
    try:
        planner_max_tries = int(planner_max_tries_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--planner-max-tries must be an integer.") from exc
    if planner_max_tries < 1:
        raise cli.PackageError("--planner-max-tries must be >= 1.")

    auditor_max_tries_raw = getattr(args, "auditor_max_tries", 2)
    try:
        auditor_max_tries = int(auditor_max_tries_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--auditor-max-tries must be an integer.") from exc
    if auditor_max_tries < 1:
        raise cli.PackageError("--auditor-max-tries must be >= 1.")

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
    workflow_status_hook_raw = getattr(args, "_fermilink_workflow_status_hook", None)
    workflow_status_hook: WorkflowStatusHook | None = (
        workflow_status_hook_raw if callable(workflow_status_hook_raw) else None
    )

    projects_dir = repo_dir / cli.LOOP_MEMORY_DIRNAME
    runs_root = projects_dir / runs_dir_name
    latest_path = runs_root / cli.REPRODUCE_LATEST_RUN_FILENAME
    runs_root.mkdir(parents=True, exist_ok=True)

    source_fingerprint = hashlib.sha256(user_prompt.strip().encode("utf-8")).hexdigest()
    resume_enabled = bool(getattr(args, "resume", True))
    if report_only and not resume_enabled:
        raise cli.PackageError(
            "--report-only requires --resume (do not use --restart)."
        )
    run_dir: Path | None = None
    state: dict[str, object] | None = None

    if resume_enabled and latest_path.is_file():
        try:
            latest_run_id = latest_path.read_text(encoding="utf-8").strip()
            if latest_run_id:
                candidate = runs_root / latest_run_id
                candidate_state_path = candidate / cli.REPRODUCE_STATE_FILENAME
                if candidate_state_path.is_file():
                    loaded = json.loads(
                        candidate_state_path.read_text(encoding="utf-8")
                    )
                    if isinstance(loaded, dict):
                        same_source = (
                            str(loaded.get("source_fingerprint") or "")
                            == source_fingerprint
                        )
                        status = str(loaded.get("status") or "")
                        if same_source and (status not in {"completed"} or report_only):
                            run_dir = candidate
                            state = loaded
        except (OSError, json.JSONDecodeError):
            run_dir = None
            state = None

    created_new_run = run_dir is None or state is None
    if created_new_run:
        if report_only:
            raise cli.PackageError(
                f"--report-only requires an existing resumable {workflow_name} run for this prompt."
            )
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_dir = runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "version": 1,
            "run_id": run_id,
            "status": "planning",
            "created_at_utc": cli._utc_now_z(),
            "updated_at_utc": cli._utc_now_z(),
            "source_fingerprint": source_fingerprint,
            "source_description": source_description,
            "source_prompt_file": prompt_file,
            "source_prompt_preview": user_prompt[:400],
            "current_task_index": 0,
            "tasks": [],
            "task_runs": {},
            "last_error": "",
        }
    else:
        cli._print_tagged(workflow_name, f"resuming run: {run_dir.name}")

    if report_only and not created_new_run:
        # Report-only runs should finalize from existing run artifacts/config and
        # must not be blocked by invocation-time mode drift from legacy runs.
        state_data_context = _coerce_saved_data_context(state)
        state_hpc_context = _coerce_saved_hpc_context(state)
        state["data_context"] = state_data_context
        state["hpc_context"] = state_hpc_context
    else:
        invocation_data_context = _resolve_invocation_data_context(
            repo_dir=repo_dir,
            run_dir=run_dir,
            workflow_name=workflow_name,
            args=args,
        )
        invocation_hpc_context = _resolve_invocation_hpc_context(
            repo_dir=repo_dir,
            args=args,
        )

        # Workflow CLI no longer exposes --data-dir; keep workflow data context fixed
        # to invocation defaults and ignore legacy saved run-state toggles.
        state_data_context = invocation_data_context
        state["data_context"] = state_data_context
        if created_new_run:
            state_hpc_context = invocation_hpc_context
        else:
            saved_hpc_context = _coerce_saved_hpc_context(state)
            _assert_hpc_context_compatible(
                run_id=str(state.get("run_id") or run_dir.name),
                workflow_name=workflow_name,
                state_hpc_context=saved_hpc_context,
                invocation_hpc_context=invocation_hpc_context,
            )
            state_hpc_context = invocation_hpc_context
        state["hpc_context"] = state_hpc_context

    if created_new_run:
        cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
        try:
            latest_path.write_text(run_dir.name + "\n", encoding="utf-8")
        except OSError as exc:
            raise cli.PackageError(
                f"Failed to write latest {workflow_name} run file: {latest_path}: {exc}"
            ) from exc

    if "dry_run" in state:
        state.pop("dry_run", None)

    prompts_dir = run_dir / cli.REPRODUCE_PROMPTS_DIRNAME
    logs_dir = run_dir / cli.REPRODUCE_LOGS_DIRNAME
    prompts_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    if _is_data_context_enabled(state_data_context):
        (run_dir / WORKFLOW_DATA_DIRNAME).mkdir(parents=True, exist_ok=True)
        state_data_context = _prepare_workflow_data_artifacts(
            repo_dir=repo_dir,
            run_dir=run_dir,
            data_context=state_data_context,
        )
        state["data_context"] = state_data_context
        cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
        artifacts = (
            state_data_context.get("artifacts")
            if isinstance(state_data_context.get("artifacts"), dict)
            else {}
        )
        if isinstance(artifacts, dict):
            manifest_rel = str(
                artifacts.get("manifest_compact") or artifacts.get("manifest") or ""
            ).strip()
            manifest_full_rel = str(artifacts.get("manifest_full") or "").strip()
            summary_rel = str(artifacts.get("summary") or "").strip()
            if manifest_rel:
                cli._print_tagged(
                    workflow_name, f"data manifest (compact): {manifest_rel}"
                )
            if manifest_full_rel:
                cli._print_tagged(
                    workflow_name, f"data manifest (full): {manifest_full_rel}"
                )
            if summary_rel:
                cli._print_tagged(workflow_name, f"data summary: {summary_rel}")
    cli._print_tagged(workflow_name, f"run dir: {run_dir.relative_to(repo_dir)}")
    if _is_hpc_context_enabled(state_hpc_context):
        hpc_profile_rel = str(state_hpc_context.get("profile_relpath") or "").strip()
        hpc_profile_path = str(state_hpc_context.get("profile_path") or "").strip()
        hpc_profile_label = hpc_profile_rel or hpc_profile_path
        cli._print_tagged(workflow_name, "execution target: hpc_slurm")
        if hpc_profile_label:
            cli._print_tagged(workflow_name, f"hpc profile: {hpc_profile_label}")
    else:
        cli._print_tagged(workflow_name, "execution target: local")

    state_status = str(state.get("status") or "planning")
    tasks_state_raw = state.get("tasks")
    has_existing_tasks = isinstance(tasks_state_raw, list) and bool(tasks_state_raw)
    if state_status == "planning" or not has_existing_tasks:
        if report_only:
            raise cli.PackageError(
                f"--report-only requires an existing {workflow_name} run with generated tasks."
            )
        plan = generate_plan(
            repo_dir=repo_dir,
            run_dir=run_dir,
            source_text=user_prompt,
            source_description=source_description,
            requested_package_id=args.package_id,
            sandbox_override=args.sandbox,
            provider_bin_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
            planner_max_tries=planner_max_tries,
            auditor_max_tries=auditor_max_tries,
            data_context=state_data_context,
            hpc_context=state_hpc_context,
            workflow_status_hook=workflow_status_hook,
        )
        cli._materialize_mode_plan(
            run_dir=run_dir,
            plan=plan,
            state=state,
            workflow_name=workflow_name,
        )
        state["status"] = (
            "plan_ready" if bool(getattr(args, "plan_only", False)) else "running_tasks"
        )
        state["updated_at_utc"] = cli._utc_now_z()
        cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
        cli._print_tagged(
            workflow_name, f"plan ready with {len(state.get('tasks') or [])} tasks"
        )
    elif state_status == "completed" and not report_only:
        cli._print_tagged(workflow_name, "run already completed")
        print(cli.LOOP_DONE_TOKEN)
        return 0

    plan_synced = cli._maybe_sync_mode_plan_from_disk(
        repo_dir=repo_dir,
        run_dir=run_dir,
        state=state,
        source_description=source_description,
        workflow_name=workflow_name,
        data_context=state_data_context,
        hpc_context=state_hpc_context,
    )
    if plan_synced:
        cli._print_tagged(workflow_name, "synced plan from plan.json")

    if plan_only:
        cli._print_tagged(
            workflow_name, f"plan-only mode: {run_dir.relative_to(repo_dir)}"
        )
        return 0

    tasks_state = state.get("tasks")
    if not isinstance(tasks_state, list) or not tasks_state:
        raise cli.PackageError(f"No tasks found in {workflow_name} state.")
    total_task_count = len(tasks_state)
    task_runs_state = state.get("task_runs")
    if not isinstance(task_runs_state, dict):
        task_runs_state = {}
        state["task_runs"] = task_runs_state

    def _print_report_artifacts(report_payload: object) -> None:
        if not isinstance(report_payload, dict):
            return
        artifact_labels = [
            ("report_path", "report"),
            ("run_all_script_path", "run-all script"),
            ("simulation_script_path", "simulation script"),
            ("postprocess_script_path", "postprocess script"),
            ("plot_script_path", "plot script"),
            ("simulation_job_map_path", "simulation job map"),
            ("postprocess_job_map_path", "postprocess job map"),
            ("plot_job_map_path", "plot job map"),
            ("hpc_contract_errors_path", "hpc contract validation"),
        ]
        for key, label in artifact_labels:
            raw_path = str(report_payload.get(key) or "").strip()
            if not raw_path:
                continue
            try:
                relative = str(Path(raw_path).relative_to(repo_dir))
            except Exception:
                relative = raw_path
            cli._print_tagged(workflow_name, f"{label}: {relative}")

    if report_only:
        try:
            report_info = cli._finalize_workflow_report(
                repo_dir=repo_dir,
                run_dir=run_dir,
                runs_root=runs_root,
                workflow_name=workflow_name,
                source_description=source_description,
                tasks_state=tasks_state,
                requested_package_id=args.package_id,
                sandbox_override=args.sandbox,
                provider_bin_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
                data_context=state_data_context,
                hpc_context=state_hpc_context,
                workflow_status_hook=workflow_status_hook,
            )
        except cli.PackageError as exc:
            state["last_error"] = str(exc)
            state["updated_at_utc"] = cli._utc_now_z()
            cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
            cli._print_tagged(workflow_name, str(exc), stderr=True)
            return 1
        state["report"] = report_info
        state["last_error"] = ""
        state["updated_at_utc"] = cli._utc_now_z()
        cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
        _print_report_artifacts(report_info)
        return 0

    state["status"] = "running_tasks"
    state["updated_at_utc"] = cli._utc_now_z()
    cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)

    data_artifacts = (
        state_data_context.get("artifacts")
        if isinstance(state_data_context.get("artifacts"), dict)
        else {}
    )
    data_manifest_rel = (
        str(
            data_artifacts.get("manifest_compact")
            or data_artifacts.get("manifest")
            or ""
        ).strip()
        if isinstance(data_artifacts, dict)
        else ""
    )
    data_manifest_full_rel = (
        str(data_artifacts.get("manifest_full") or "").strip()
        if isinstance(data_artifacts, dict)
        else ""
    )
    data_summary_rel = (
        str(data_artifacts.get("summary") or "").strip()
        if isinstance(data_artifacts, dict)
        else ""
    )
    data_task_map_rel = (
        str(data_artifacts.get("task_map") or "").strip()
        if isinstance(data_artifacts, dict)
        else ""
    )
    data_guard_enabled = _is_data_context_enabled(state_data_context) and bool(
        state_data_context.get("read_only", True)
    )
    data_guard_source = (
        Path(str(state_data_context.get("source_path") or ""))
        if data_guard_enabled
        else None
    )
    data_limits = (
        state_data_context.get("limits")
        if isinstance(state_data_context.get("limits"), dict)
        else {}
    )
    data_guard_limits = (
        {
            "max_files": int(data_limits.get("max_files") or DEFAULT_DATA_MAX_FILES),
            "max_total_bytes": int(
                data_limits.get("max_total_bytes") or DEFAULT_DATA_MAX_TOTAL_BYTES
            ),
            "max_file_bytes": int(
                data_limits.get("max_file_bytes") or DEFAULT_DATA_MAX_FILE_BYTES
            ),
            "hash_max_bytes": int(
                data_limits.get("hash_max_bytes") or DEFAULT_DATA_HASH_MAX_BYTES
            ),
        }
        if data_guard_enabled
        else {}
    )

    def _post_task_plan_audit(
        *,
        task_id: str,
        trigger_status: str,
        run_number: int,
        completed_or_failed_count: int,
        apply_decisions: set[str],
    ) -> str:
        if not post_task_plan_audit:
            return "disabled"
        if completed_or_failed_count >= len(tasks_state):
            return "no_remaining_tasks"

        previous_remaining_task_ids = _workflow_plan_remaining_task_ids(
            run_dir=run_dir,
            completed_or_failed_count=completed_or_failed_count,
        )
        plan_update = _run_post_task_plan_update(
            repo_dir=repo_dir,
            run_dir=run_dir,
            workflow_name=workflow_name,
            source_description=source_description,
            task_id=task_id,
            task_status=trigger_status,
            run_number=run_number,
            completed_or_failed_count=completed_or_failed_count,
            requested_package_id=args.package_id,
            sandbox_override=args.sandbox,
            provider_bin_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
            max_tries=auditor_max_tries,
            data_context=state_data_context,
            hpc_context=state_hpc_context,
            workflow_status_hook=workflow_status_hook,
        )
        decision = str(plan_update.get("decision") or "no_change").strip()
        reason = str(plan_update.get("reason") or "").strip()
        audit_notes = _normalize_string_list(plan_update.get("audit_notes"))
        changed_task_ids: list[str] = []

        if decision in {"update_remaining", "continue_with_failed_task"}:
            if decision not in apply_decisions:
                reason = (
                    f"post-task plan audit decision {decision!r} is not valid for "
                    f"trigger status {trigger_status!r}; keeping plan unchanged"
                )
                audit_notes.append(reason)
                decision = "no_change"
            else:
                remaining_tasks = plan_update.get("remaining_tasks")
                if not isinstance(remaining_tasks, list):
                    remaining_tasks = []
                try:
                    changed_task_ids = _apply_remaining_task_updates(
                        repo_dir=repo_dir,
                        run_dir=run_dir,
                        state=state,
                        workflow_name=workflow_name,
                        source_description=source_description,
                        completed_or_failed_count=completed_or_failed_count,
                        remaining_tasks=[
                            item for item in remaining_tasks if isinstance(item, dict)
                        ],
                        data_context=state_data_context,
                        hpc_context=state_hpc_context,
                    )
                except cli.PackageError as exc:
                    reason = (
                        "post-task plan audit update could not be applied; "
                        f"keeping plan unchanged: {exc}"
                    )
                    audit_notes.append(reason)
                    decision = "no_change"
                    changed_task_ids = []

        new_remaining_task_ids = _workflow_plan_remaining_task_ids(
            run_dir=run_dir,
            completed_or_failed_count=completed_or_failed_count,
        )
        _append_workflow_plan_revision(
            state,
            trigger_task_id=task_id,
            trigger_status=trigger_status,
            decision=decision,
            reason=reason,
            audit_notes=audit_notes,
            previous_remaining_task_ids=previous_remaining_task_ids,
            new_remaining_task_ids=new_remaining_task_ids,
            changed_task_ids=changed_task_ids,
        )
        return decision

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
                    "updated_at_utc": cli._utc_now_z(),
                }
                cli._print_tagged(
                    workflow_name, "skipping report generation (--skip-report)"
                )
            else:
                try:
                    report_info = cli._finalize_workflow_report(
                        repo_dir=repo_dir,
                        run_dir=run_dir,
                        runs_root=runs_root,
                        workflow_name=workflow_name,
                        source_description=source_description,
                        tasks_state=tasks_state,
                        requested_package_id=args.package_id,
                        sandbox_override=args.sandbox,
                        provider_bin_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
                        data_context=state_data_context,
                        hpc_context=state_hpc_context,
                        workflow_status_hook=workflow_status_hook,
                    )
                except cli.PackageError as exc:
                    state["status"] = "failed"
                    state["last_error"] = str(exc)
                    state["updated_at_utc"] = cli._utc_now_z()
                    cli._write_json_atomic(
                        run_dir / cli.REPRODUCE_STATE_FILENAME, state
                    )
                    cli._print_tagged(workflow_name, str(exc), stderr=True)
                    return 1
                state["report"] = report_info
            state["status"] = "completed"
            state["last_error"] = ""
            state["updated_at_utc"] = cli._utc_now_z()
            cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
            _print_report_artifacts(state.get("report"))
            print(cli.LOOP_DONE_TOKEN)
            return 0

        task = tasks_state[current_index]
        if not isinstance(task, dict):
            raise cli.PackageError(
                f"Task index {current_index} in {workflow_name} state is invalid."
            )
        task_id = str(task.get("id") or f"task_{current_index + 1:03d}").strip()
        prompt_rel = str(task.get("prompt_file") or "").strip()
        if not prompt_rel:
            raise cli.PackageError(
                f"Task {task_id} is missing `prompt_file` in {workflow_name} state."
            )
        prompt_path = run_dir / prompt_rel
        if not prompt_path.is_file():
            raise cli.PackageError(f"Task prompt file does not exist: {prompt_path}")
        task_data_rel = str(task.get("data_context_file") or "").strip()
        if _is_data_context_enabled(state_data_context) and not task_data_rel:
            task_data_rel = f"{WORKFLOW_DATA_DIRNAME}/{task_id}.md"
        task_data_path = run_dir / task_data_rel if task_data_rel else None
        if _is_data_context_enabled(state_data_context):
            if task_data_path is None or not task_data_path.is_file():
                raise cli.PackageError(
                    f"Task data context file does not exist: {task_data_path}"
                )
        plan_path = run_dir / REPRODUCE_PLAN_FILENAME
        state_path = run_dir / REPRODUCE_STATE_FILENAME

        def _memory_relpath(path: Path) -> str:
            try:
                return str(path.relative_to(repo_dir))
            except ValueError:
                return str(path)

        workflow_prompt_preamble_lines = [
            "Workflow preflight (research/reproduce mode):",
            "- Before acting, read short/long term memory at `projects/memory.md`.",
            f"- Before acting, read the full workflow plan at `{_memory_relpath(plan_path)}`.",
        ]
        if isinstance(prompt_file, str) and prompt_file.strip():
            workflow_prompt_preamble_lines.append(
                "- Before acting, optionally read original paper or request at "
                f"`{_memory_relpath(Path(prompt_file))}` for additional context if needed."
            )
        else:
            workflow_prompt_preamble_lines.append(
                "- Before acting, read original request from "
                "`projects/memory.md` under `## Original request`."
            )
        if _is_data_context_enabled(state_data_context) and task_data_path is not None:
            workflow_prompt_preamble_lines.extend(
                [
                    f"- Before acting, read `{_memory_relpath(task_data_path)}`.",
                    (
                        "- Data scope rule: only use files listed in the task data "
                        "context unless strong evidence requires expansion."
                    ),
                    (
                        "- If scope expansion is required, update "
                        f"`{data_task_map_rel}` and `{_memory_relpath(task_data_path)}` "
                        "with rationale/confidence before continuing."
                    ),
                ]
            )
        workflow_prompt_preamble_lines.extend(
            [
                "",
                "Execution target constraints:",
                *_build_hpc_prompt_lines(state_hpc_context),
            ]
        )
        workflow_prompt_preamble_lines.extend(
            [
                "",
                "Simulation policy:",
                "- Execute the simulations required by each task and record reproducible results.",
            ]
        )
        workflow_prompt_preamble = "\n".join(workflow_prompt_preamble_lines).strip()

        task_runs = int(task_runs_state.get(task_id, 0))
        if task_runs == 0:
            try:
                task_prompt_text = prompt_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError as exc:
                raise cli.PackageError(
                    f"Failed to read task prompt file: {prompt_path}: {exc}"
                ) from exc

            workflow_context_lines = [
                f"- workflow: {workflow_name}",
                "- simulation_execution: enforced (run required simulations; no dry-run mode)",
                f"- plan_json: {_memory_relpath(plan_path)} (overall workflow task plan)",
                f"- state_json: {_memory_relpath(state_path)} (workflow progress and task status)",
            ]
            workflow_context_lines.extend(
                [
                    "- execution_target_context:",
                    *_build_hpc_prompt_lines(state_hpc_context),
                ]
            )
            if _is_data_context_enabled(state_data_context):
                if data_manifest_rel:
                    workflow_context_lines.append(
                        f"- data_manifest_json: {data_manifest_rel} (compact indexed data inventory)"
                    )
                if data_manifest_full_rel:
                    workflow_context_lines.append(
                        f"- data_manifest_full_json: {data_manifest_full_rel} (full deterministic indexed data inventory)"
                    )
                if data_summary_rel:
                    workflow_context_lines.append(
                        f"- data_summary_markdown: {data_summary_rel} (human-readable data scope)"
                    )
                if data_task_map_rel:
                    workflow_context_lines.append(
                        f"- task_data_map_json: {data_task_map_rel} (task-to-file mapping)"
                    )
                if task_data_path is not None:
                    workflow_context_lines.append(
                        f"- task_data_context: {_memory_relpath(task_data_path)} (allowed per-task files)"
                    )
                mapping_mode = str(state_data_context.get("mapping_mode") or "").strip()
                if mapping_mode:
                    workflow_context_lines.append(
                        f"- data_mapping_mode: {mapping_mode}"
                    )
            cli._reset_loop_short_term_memory(
                repo_dir=repo_dir,
                user_prompt=task_prompt_text,
                prompt_file=str(prompt_path),
                workflow_context_lines=workflow_context_lines,
            )

        run_number = task_runs + 1
        task_ordinal = current_index + 1
        cli._print_tagged(
            workflow_name,
            (
                f"task {task_ordinal}/{total_task_count} "
                f"{task_id} run {run_number}/{task_max_runs}"
            ),
        )

        def _workflow_loop_iteration_hook(
            iteration: int, max_loop_iterations: int
        ) -> None:
            _emit_workflow_status(
                workflow_status_hook,
                (
                    f"{workflow_name} task {task_ordinal}/{total_task_count} "
                    f"loop {iteration}/{max_loop_iterations}"
                ),
            )

        loop_args = argparse.Namespace(
            command="loop",
            prompt=[str(prompt_path)],
            package_id=args.package_id,
            sandbox=args.sandbox,
            max_iterations=max_iterations,
            wait_seconds=wait_seconds,
            max_wait_seconds=max_wait_seconds,
            pid_stall_seconds=pid_stall_seconds,
            init_git=args.init_git,
            no_init_git=args.no_init_git,
            workflow_prompt_preamble=workflow_prompt_preamble,
            _fermilink_loop_iteration_hook=_workflow_loop_iteration_hook,
        )
        data_guard_before_scan: dict[str, object] | None = None
        if (
            data_guard_enabled
            and data_guard_source is not None
            and isinstance(data_guard_limits, dict)
        ):
            data_guard_before_scan = _scan_data_dir_inventory(
                data_dir=data_guard_source,
                max_files=int(data_guard_limits["max_files"]),
                max_total_bytes=int(data_guard_limits["max_total_bytes"]),
                max_file_bytes=int(data_guard_limits["max_file_bytes"]),
                hash_max_bytes=int(data_guard_limits["hash_max_bytes"]),
                include_hash=False,
            )
        started_at = cli._utc_now_z()
        code = cli._cmd_loop(loop_args)
        loop_completion_commit = _normalize_checkpoint_payload(
            getattr(loop_args, "_fermilink_completion_commit", None)
        )
        if (
            data_guard_before_scan is not None
            and data_guard_source is not None
            and isinstance(data_guard_limits, dict)
        ):
            data_guard_after_scan = _scan_data_dir_inventory(
                data_dir=data_guard_source,
                max_files=int(data_guard_limits["max_files"]),
                max_total_bytes=int(data_guard_limits["max_total_bytes"]),
                max_file_bytes=int(data_guard_limits["max_file_bytes"]),
                hash_max_bytes=int(data_guard_limits["hash_max_bytes"]),
                include_hash=False,
            )
            before_fingerprint = str(data_guard_before_scan.get("fingerprint") or "")
            after_fingerprint = str(data_guard_after_scan.get("fingerprint") or "")
            if before_fingerprint != after_fingerprint:
                state["status"] = "failed"
                state["last_error"] = (
                    "Read-only data guard violation: --data-dir changed during task "
                    f"{task_id} run {run_number}."
                )
                state["updated_at_utc"] = cli._utc_now_z()
                cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
                diff_lines = _scan_diff_preview(
                    data_guard_before_scan, data_guard_after_scan
                )
                cli._print_tagged(
                    workflow_name,
                    str(state["last_error"])
                    + "\n"
                    + f"data_dir: {data_guard_source}\n"
                    + "indexed diff preview:\n"
                    + "\n".join(diff_lines),
                    stderr=True,
                )
                return 1
        loop_outcome_payload = getattr(loop_args, "_fermilink_loop_outcome", None)
        loop_status = ""
        loop_reason = ""
        provider_exit_code: int | None = None
        if isinstance(loop_outcome_payload, dict):
            loop_status = str(loop_outcome_payload.get("status") or "").strip()
            loop_reason = str(loop_outcome_payload.get("reason") or "").strip()
            provider_exit_code_raw = loop_outcome_payload.get("provider_exit_code")
            if isinstance(provider_exit_code_raw, int):
                provider_exit_code = provider_exit_code_raw
        if not loop_status:
            if code == 0:
                loop_status = "done"
                loop_reason = "loop_exit_code_0"
            elif code == 1:
                loop_status = "incomplete_max_iterations"
                loop_reason = "legacy_loop_exit_code_1"
            else:
                loop_status = "provider_failure"
                loop_reason = f"legacy_loop_exit_code_{code}"
                provider_exit_code = code
        finished_at = cli._utc_now_z()
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
                        "loop_status": loop_status,
                        "loop_reason": loop_reason,
                        "provider_exit_code": provider_exit_code,
                        "completion_commit_status": str(
                            loop_completion_commit.get("status") or ""
                        ),
                        "completion_commit_sha": str(
                            loop_completion_commit.get("sha") or ""
                        ),
                        "completion_commit_error": str(
                            loop_completion_commit.get("error") or ""
                        ),
                        "completion_commit_memory_only": str(
                            loop_completion_commit.get("memory_only") or "false"
                        ),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

        if loop_status == "done":
            if post_task_plan_audit:
                task["status"] = "done"
                task["completed_at_utc"] = finished_at
                audit_decision = _post_task_plan_audit(
                    task_id=task_id,
                    trigger_status="done",
                    run_number=run_number,
                    completed_or_failed_count=current_index + 1,
                    apply_decisions={"update_remaining"},
                )
                if audit_decision == "abort":
                    state["status"] = "failed"
                    state["last_error"] = (
                        f"Post-task plan audit requested abort after task {task_id}."
                    )
                    state["updated_at_utc"] = cli._utc_now_z()
                    cli._write_json_atomic(
                        run_dir / cli.REPRODUCE_STATE_FILENAME, state
                    )
                    cli._print_tagged(
                        workflow_name, str(state["last_error"]), stderr=True
                    )
                    return 1
                tasks_state = (
                    state.get("tasks")
                    if isinstance(state.get("tasks"), list)
                    else tasks_state
                )
                task_runs_state = (
                    state.get("task_runs")
                    if isinstance(state.get("task_runs"), dict)
                    else task_runs_state
                )
                total_task_count = len(tasks_state)
            state["current_task_index"] = current_index + 1
            state["last_error"] = ""
            cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
            continue

        if loop_status == "incomplete_max_iterations" and run_number < task_max_runs:
            state["last_error"] = (
                f"Task {task_id} did not reach {cli.LOOP_DONE_TOKEN}; retrying "
                f"({run_number}/{task_max_runs})."
            )
            cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
            continue

        if loop_status == "incomplete_max_iterations":
            audit_decision = "disabled"
            if post_task_plan_audit:
                task["status"] = "failed_continuation_candidate"
                task["failed_at_utc"] = finished_at
                audit_decision = _post_task_plan_audit(
                    task_id=task_id,
                    trigger_status="failed",
                    run_number=run_number,
                    completed_or_failed_count=current_index + 1,
                    apply_decisions={"continue_with_failed_task"},
                )
            if audit_decision == "continue_with_failed_task":
                task["status"] = "failed_continued"
                state["current_task_index"] = current_index + 1
                state["last_error"] = ""
                state["updated_at_utc"] = cli._utc_now_z()
                tasks_state = (
                    state.get("tasks")
                    if isinstance(state.get("tasks"), list)
                    else tasks_state
                )
                task_runs_state = (
                    state.get("task_runs")
                    if isinstance(state.get("task_runs"), dict)
                    else task_runs_state
                )
                total_task_count = len(tasks_state)
                cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
                cli._print_tagged(
                    workflow_name,
                    f"continuing after failed task {task_id} per post-task plan audit",
                    stderr=True,
                )
                continue

            state["status"] = "failed"
            if audit_decision == "abort":
                state["last_error"] = (
                    f"Post-task plan audit requested abort after failed task {task_id}."
                )
            else:
                state["last_error"] = (
                    f"Task {task_id} exceeded --task-max-runs ({task_max_runs}) "
                    f"without {cli.LOOP_DONE_TOKEN}."
                )
            state["updated_at_utc"] = cli._utc_now_z()
            cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
            cli._print_tagged(workflow_name, str(state["last_error"]), stderr=True)
            return 1

        state["status"] = "failed"
        if loop_status == "provider_failure" and provider_exit_code is not None:
            state["last_error"] = (
                f"Task {task_id} failed with provider exit code {provider_exit_code}."
            )
        else:
            state["last_error"] = (
                f"Task {task_id} failed with loop status {loop_status or 'unknown'} "
                f"(exit code {code})."
            )
        state["updated_at_utc"] = cli._utc_now_z()
        cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
        cli._print_tagged(workflow_name, str(state["last_error"]), stderr=True)
        return code


def cmd_reproduce(args: argparse.Namespace) -> int:
    """Run publication reproduction orchestration.

    Dependency note:
    - `reproduce` delegates task execution to `loop`.
    - `loop` depends on the same execution/routing stack used by `exec`.
    """

    cli = _cli()
    repo_dir = Path.cwd().resolve()
    try:
        return cmd_plan_workflow(
            args,
            workflow_name="reproduce",
            runs_dir_name=cli.REPRODUCE_RUNS_DIR,
            generate_plan=cli._generate_reproduce_plan,
        )
    finally:
        _attempt_mode_completion_commit(
            repo_dir=repo_dir,
            args=args,
            mode_name="reproduce",
        )


def cmd_research(args: argparse.Namespace) -> int:
    """Run multi-task autonomous research orchestration.

    Dependency note:
    - `research` delegates task execution to `loop`.
    - `loop` depends on the same execution/routing stack used by `exec`.
    """

    cli = _cli()
    repo_dir = Path.cwd().resolve()
    try:
        return cmd_plan_workflow(
            args,
            workflow_name="research",
            runs_dir_name=cli.RESEARCH_RUNS_DIR,
            generate_plan=cli._generate_research_plan,
        )
    finally:
        _attempt_mode_completion_commit(
            repo_dir=repo_dir,
            args=args,
            mode_name="research",
        )
