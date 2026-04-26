from __future__ import annotations

import copy
import shlex
import sys
from pathlib import Path
from typing import Any

import yaml

from . import state as implement_state


DEFAULT_MAX_ITERATIONS = 80
DEFAULT_STOP_ON_CONSECUTIVE_REJECTIONS = 20
DEFAULT_WORKER_MAX_ITERATIONS = 8
DEFAULT_WORKER_WAIT_SECONDS = 1.0
DEFAULT_WORKER_MAX_WAIT_SECONDS = 6000.0
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_MIN_SCORE_IMPROVEMENT = 1.0e-6


def _cli():
    from fermilink import cli

    return cli


def _str_list(payload: object) -> list[str]:
    if not isinstance(payload, list):
        return []
    return [
        str(item).strip()
        for item in payload
        if isinstance(item, str) and str(item).strip()
    ]


def _command_list_from_text(text: str) -> list[str]:
    try:
        tokens = shlex.split(str(text or "").strip())
    except ValueError:
        return []
    return [token for token in tokens if token]


def normalize_command_list(payload: object) -> list[list[str]]:
    """Normalize command specs to a list of token lists."""

    commands: list[list[str]] = []
    if isinstance(payload, str):
        command = _command_list_from_text(payload)
        return [command] if command else []
    if not isinstance(payload, list):
        return []
    for item in payload:
        if isinstance(item, str):
            command = _command_list_from_text(item)
        elif isinstance(item, list):
            command = [
                str(token).strip()
                for token in item
                if isinstance(token, str) and str(token).strip()
            ]
        else:
            command = []
        if command:
            commands.append(command)
    return commands


def _default_editable_scope(project_root: Path, goal_spec: dict[str, Any]) -> list[str]:
    explicit = _str_list(goal_spec.get("editable_scope"))
    if explicit:
        return explicit
    for candidate in ("src", "lib"):
        if (project_root / candidate).is_dir():
            return [f"{candidate}/**"]
    if any(project_root.glob("*.py")):
        return ["*.py", "tests/**"]
    return ["**/*"]


def _default_validation_commands(
    project_root: Path,
    goal_spec: dict[str, Any],
) -> list[list[str]]:
    explicit = normalize_command_list(goal_spec.get("validation_commands"))
    if explicit:
        return explicit
    validation_text = str(goal_spec.get("validation") or "").strip()
    if validation_text:
        commands: list[list[str]] = []
        for raw_line in validation_text.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith(("- ", "* ", "+ ")):
                stripped = stripped[2:].strip()
            if stripped.startswith("$"):
                stripped = stripped[1:].strip()
            if stripped.startswith("`") and stripped.endswith("`"):
                stripped = stripped[1:-1].strip()
            command = _command_list_from_text(stripped)
            if command:
                commands.append(command)
        if commands:
            return commands
    if (project_root / "tests").is_dir():
        return [[sys.executable, "-m", "pytest", "-q"]]
    return []


def _baseline_mode(goal_spec: dict[str, Any]) -> str:
    baseline = str(goal_spec.get("baseline_reference") or "").strip()
    if baseline:
        return "reference"
    return "exploratory"


def build_default_contract(
    project_root: Path,
    *,
    package_id: str,
    goal_spec: dict[str, Any],
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic fallback contract from goal.md."""

    validation_commands = _default_validation_commands(project_root, goal_spec)
    build_commands = normalize_command_list(goal_spec.get("build_commands"))
    legacy_pre_commands = normalize_command_list(goal_spec.get("pre_commands"))
    pre_commands = build_commands if build_commands else legacy_pre_commands
    implementation_id = f"implement-{package_id}"
    target = str(goal_spec.get("target") or "").strip()
    done_criteria = _str_list(goal_spec.get("done_criteria"))
    desired_outputs = _str_list(goal_spec.get("desired_outputs"))
    workloads = _str_list(goal_spec.get("workloads"))
    input_api = str(goal_spec.get("input_api") or "").strip()
    if not input_api and isinstance(analysis, dict):
        api_candidates = analysis.get("proposed_api") or analysis.get("api")
        if isinstance(api_candidates, str):
            input_api = api_candidates.strip()
    if not input_api:
        input_api = "Infer and implement the smallest public API needed by goal.md."

    return {
        "schema_version": 1,
        "implementation_id": implementation_id,
        "package_id": package_id,
        "target": target,
        "baseline": {
            "mode": _baseline_mode(goal_spec),
            "reference": str(goal_spec.get("baseline_reference") or "").strip(),
            "optional": bool(goal_spec.get("baseline_optional", True)),
        },
        "api": {
            "input": input_api,
            "lock_after_first_passing_validation": True,
        },
        "outputs": {
            "desired": desired_outputs,
            "flexible": not bool(desired_outputs),
        },
        "workloads": workloads,
        "repo": {
            "editable_paths": _default_editable_scope(project_root, goal_spec),
            "immutable_paths": [
                ".fermilink-implement/**",
                ".fermilink-optimize/**",
                "skills/**",
            ],
        },
        "pre_commands": {
            "worker": copy.deepcopy(pre_commands),
            "controller": copy.deepcopy(pre_commands),
        },
        "validation": {
            "mode": "progressive",
            "commands": validation_commands,
            "allow_partial_improvements": True,
            "requires_complete_for_final": True,
        },
        "scoring": {
            "max_score": 100.0,
            "min_score_improvement": DEFAULT_MIN_SCORE_IMPROVEMENT,
            "complete_score": 100.0,
        },
        "campaign": {
            "max_iterations": DEFAULT_MAX_ITERATIONS,
            "stop_on_consecutive_rejections": DEFAULT_STOP_ON_CONSECUTIVE_REJECTIONS,
        },
        "worker": {
            "max_iterations": DEFAULT_WORKER_MAX_ITERATIONS,
            "wait_seconds": DEFAULT_WORKER_WAIT_SECONDS,
            "max_wait_seconds": DEFAULT_WORKER_MAX_WAIT_SECONDS,
            "pid_stall_seconds": 900.0,
        },
        "controller": {
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        },
        "guardrails": {
            "reject_out_of_scope_edits": True,
            "reject_validation_weakening": True,
            "reject_hardcoded_workload_answers": True,
            "reject_unrelated_algorithm_changes": True,
        },
        "done_criteria": done_criteria,
        "non_goals": _str_list(goal_spec.get("non_goals")),
        "autogen": {
            "created_at_utc": implement_state.utc_now_z(),
            "source": "default_contract",
        },
    }


def validate_contract(payload: dict[str, Any]) -> None:
    cli = _cli()
    if not isinstance(payload, dict):
        raise cli.PackageError("Implementation contract must be a YAML object.")
    if int(payload.get("schema_version") or 0) != 1:
        raise cli.PackageError("Implementation contract schema_version must be 1.")
    repo = payload.get("repo")
    if not isinstance(repo, dict):
        raise cli.PackageError("Implementation contract missing repo block.")
    editable_paths = repo.get("editable_paths")
    if not isinstance(editable_paths, list) or not _str_list(editable_paths):
        raise cli.PackageError("Implementation contract repo.editable_paths is required.")
    validation = payload.get("validation")
    if not isinstance(validation, dict):
        raise cli.PackageError("Implementation contract missing validation block.")
    commands = validation.get("commands")
    if commands is not None and not isinstance(commands, list):
        raise cli.PackageError("Implementation contract validation.commands must be a list.")
    pre_commands = payload.get("pre_commands")
    if pre_commands is not None:
        if not isinstance(pre_commands, dict):
            raise cli.PackageError("Implementation contract pre_commands must be an object.")
        for key in ("worker", "controller"):
            raw = pre_commands.get(key)
            if raw is None:
                continue
            if not isinstance(raw, list):
                raise cli.PackageError(
                    f"Implementation contract pre_commands.{key} must be a list."
                )
            for index, command in enumerate(raw, start=1):
                if not isinstance(command, list) or not command:
                    raise cli.PackageError(
                        "Implementation contract "
                        f"pre_commands.{key}[{index}] must be a non-empty list."
                    )
                if not all(isinstance(item, str) and item.strip() for item in command):
                    raise cli.PackageError(
                        "Implementation contract "
                        f"pre_commands.{key}[{index}] must contain strings."
                    )


def load_contract(path: Path) -> dict[str, Any]:
    cli = _cli()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise cli.PackageError(f"Failed to read implementation contract: {exc}") from exc
    except yaml.YAMLError as exc:
        raise cli.PackageError(
            f"Invalid YAML in implementation contract: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise cli.PackageError("Implementation contract must contain a YAML object.")
    validate_contract(payload)
    return payload


def write_contract(path: Path, payload: dict[str, Any]) -> None:
    validate_contract(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def editable_paths(payload: dict[str, Any]) -> list[str]:
    repo = payload.get("repo") if isinstance(payload, dict) else {}
    return _str_list(repo.get("editable_paths") if isinstance(repo, dict) else None)


def immutable_paths(payload: dict[str, Any]) -> list[str]:
    repo = payload.get("repo") if isinstance(payload, dict) else {}
    return _str_list(repo.get("immutable_paths") if isinstance(repo, dict) else None)


def pre_commands(payload: dict[str, Any], role: str) -> list[list[str]]:
    pre = payload.get("pre_commands")
    if not isinstance(pre, dict):
        return []
    return normalize_command_list(pre.get(role))


def validation_commands(payload: dict[str, Any]) -> list[list[str]]:
    validation = payload.get("validation")
    if not isinstance(validation, dict):
        return []
    return normalize_command_list(validation.get("commands"))


def campaign_config(payload: dict[str, Any]) -> dict[str, Any]:
    campaign = payload.get("campaign")
    return campaign if isinstance(campaign, dict) else {}


def worker_config(payload: dict[str, Any]) -> dict[str, Any]:
    worker = payload.get("worker")
    return worker if isinstance(worker, dict) else {}


def controller_config(payload: dict[str, Any]) -> dict[str, Any]:
    controller = payload.get("controller")
    return controller if isinstance(controller, dict) else {}


def scoring_config(payload: dict[str, Any]) -> dict[str, Any]:
    scoring = payload.get("scoring")
    return scoring if isinstance(scoring, dict) else {}
