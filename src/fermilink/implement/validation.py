from __future__ import annotations

import copy
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from fermilink.optimize import git as optimize_git

from . import contract as implement_contract


def _normalize_rel_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("/")


def expand_command(
    command: list[str],
    *,
    project_root: Path,
    run_dir: Path,
    contract_path: Path,
) -> list[str]:
    replacements = {
        "{project_root}": str(project_root),
        "{run_dir}": str(run_dir),
        "{contract}": str(contract_path),
    }
    expanded: list[str] = []
    for token in command:
        rendered = str(token)
        for key, value in replacements.items():
            rendered = rendered.replace(key, value)
        expanded.append(rendered)
    return expanded


def _changed_signatures(repo_dir: Path) -> set[tuple[str, str]]:
    signatures: set[tuple[str, str]] = set()
    for entry in optimize_git.list_changed_paths(repo_dir):
        path = _normalize_rel_path(str(entry.get("path") or ""))
        status = str(entry.get("status") or "").strip()
        if path and status and status != "??":
            signatures.add((status, path))
    return signatures


def run_pre_commands(
    project_root: Path,
    *,
    commands: list[list[str]],
    run_dir: Path,
    timeout_seconds: int,
    log_prefix: str,
    contract_path: Path,
) -> dict[str, Any] | None:
    """Run deterministic pre-commands and reject tracked source side effects."""

    if not commands:
        return None
    run_dir.mkdir(parents=True, exist_ok=True)
    baseline_changed = _changed_signatures(project_root)
    env = os.environ.copy()
    last_stdout = run_dir / f"{log_prefix}_0.stdout.log"
    last_stderr = run_dir / f"{log_prefix}_0.stderr.log"
    for index, command_template in enumerate(commands, start=1):
        command = expand_command(
            command_template,
            project_root=project_root,
            run_dir=run_dir,
            contract_path=contract_path,
        )
        last_stdout = run_dir / f"{log_prefix}_{index}.stdout.log"
        last_stderr = run_dir / f"{log_prefix}_{index}.stderr.log"
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
            last_stdout.write_text(str(exc.stdout or ""), encoding="utf-8")
            last_stderr.write_text(str(exc.stderr or ""), encoding="utf-8")
            return {
                "ok": False,
                "status": "pre_command_timeout",
                "reason": f"{log_prefix}[{index}] timed out",
                "command": command,
                "stdout_log": str(last_stdout),
                "stderr_log": str(last_stderr),
                "hard_reject": True,
            }
        except (OSError, ValueError) as exc:
            last_stdout.write_text("", encoding="utf-8")
            last_stderr.write_text(str(exc), encoding="utf-8")
            return {
                "ok": False,
                "status": "pre_command_crash",
                "reason": str(exc),
                "command": command,
                "stdout_log": str(last_stdout),
                "stderr_log": str(last_stderr),
                "hard_reject": True,
            }
        last_stdout.write_text(str(completed.stdout or ""), encoding="utf-8")
        last_stderr.write_text(str(completed.stderr or ""), encoding="utf-8")
        if completed.returncode != 0:
            return {
                "ok": False,
                "status": "pre_command_failed",
                "reason": f"{log_prefix}[{index}] exited {completed.returncode}",
                "return_code": int(completed.returncode),
                "command": command,
                "stdout_log": str(last_stdout),
                "stderr_log": str(last_stderr),
                "hard_reject": True,
            }

    post_changed = _changed_signatures(project_root)
    new_changes = sorted(post_changed - baseline_changed)
    if new_changes:
        rendered = [
            {"status": status, "path": path} for status, path in new_changes if path
        ]
        return {
            "ok": False,
            "status": "pre_command_side_effect",
            "reason": "pre_commands left tracked repository changes",
            "tracked_changes": rendered,
            "stdout_log": str(last_stdout),
            "stderr_log": str(last_stderr),
            "hard_reject": True,
        }
    return None


def _parse_json_from_stdout(stdout_text: str) -> dict[str, Any] | None:
    text = str(stdout_text or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _normalize_milestone(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    milestone_id = str(raw.get("id") or raw.get("name") or "").strip()
    if not milestone_id:
        milestone_id = "milestone"
    status = str(raw.get("status") or "").strip().lower()
    if not status:
        status = "pass" if bool(raw.get("ok", False)) else "unknown"
    score_raw = raw.get("score")
    score = 0.0
    if isinstance(score_raw, (int, float)) and not isinstance(score_raw, bool):
        score = float(score_raw)
    return {
        "id": milestone_id,
        "status": status,
        "score": score,
        "notes": str(raw.get("notes") or raw.get("reason") or "").strip(),
    }


def _normalize_validation_payload(
    payload: dict[str, Any],
    *,
    fallback_ok: bool,
    fallback_status: str,
    fallback_score: float,
) -> dict[str, Any]:
    score_raw = payload.get("score")
    if isinstance(score_raw, (int, float)) and not isinstance(score_raw, bool):
        score = float(score_raw)
    else:
        score = fallback_score
    milestones: list[dict[str, Any]] = []
    raw_milestones = payload.get("milestones")
    if isinstance(raw_milestones, list):
        for item in raw_milestones:
            milestone = _normalize_milestone(item)
            if milestone is not None:
                milestones.append(milestone)
    cases = payload.get("cases")
    normalized: dict[str, Any] = {
        "ok": bool(payload.get("ok", fallback_ok)),
        "status": str(payload.get("status") or fallback_status),
        "score": score,
        "complete": bool(payload.get("complete", False)),
        "build_ok": bool(payload.get("build_ok", fallback_ok)),
        "api_ok": bool(payload.get("api_ok", fallback_ok)),
        "scientific_checks_ok": payload.get("scientific_checks_ok", fallback_ok),
        "milestones": milestones,
        "cases": cases if isinstance(cases, list) else [],
        "observables": (
            copy.deepcopy(payload.get("observables"))
            if isinstance(payload.get("observables"), dict)
            else {}
        ),
        "errors": (
            [str(item) for item in payload.get("errors")]
            if isinstance(payload.get("errors"), list)
            else []
        ),
    }
    return normalized


def run_validation_suite(
    project_root: Path,
    *,
    contract_payload: dict[str, Any],
    contract_path: Path,
    run_dir: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run controller pre-commands and progressive validation commands."""

    run_dir.mkdir(parents=True, exist_ok=True)
    pre_failure = run_pre_commands(
        project_root,
        commands=implement_contract.pre_commands(contract_payload, "controller"),
        run_dir=run_dir / "controller_pre_commands",
        timeout_seconds=timeout_seconds,
        log_prefix="controller_pre_command",
        contract_path=contract_path,
    )
    if pre_failure is not None:
        result = _normalize_validation_payload(
            pre_failure,
            fallback_ok=False,
            fallback_status=str(pre_failure.get("status") or "pre_command_failed"),
            fallback_score=0.0,
        )
        result["hard_reject"] = True
        _write_validation_result(run_dir, result)
        return result

    commands = implement_contract.validation_commands(contract_payload)
    if not commands:
        result = {
            "ok": True,
            "status": "no_validation_commands",
            "score": 0.0,
            "complete": False,
            "commands_ok": False,
            "build_ok": True,
            "api_ok": False,
            "scientific_checks_ok": "unknown",
            "milestones": [],
            "cases": [],
            "observables": {},
            "errors": ["contract validation.commands is empty"],
            "hard_reject": False,
        }
        _write_validation_result(run_dir, result)
        return result

    command_results: list[dict[str, Any]] = []
    pass_count = 0
    json_payloads: list[dict[str, Any]] = []
    for index, command_template in enumerate(commands, start=1):
        command = expand_command(
            command_template,
            project_root=project_root,
            run_dir=run_dir,
            contract_path=contract_path,
        )
        stdout_path = run_dir / f"validation_{index}.stdout.log"
        stderr_path = run_dir / f"validation_{index}.stderr.log"
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=str(project_root),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            elapsed = max(0.0, time.perf_counter() - started)
            stdout_text = str(completed.stdout or "")
            stderr_text = str(completed.stderr or "")
            stdout_path.write_text(stdout_text, encoding="utf-8")
            stderr_path.write_text(stderr_text, encoding="utf-8")
            passed = completed.returncode == 0
            if passed:
                pass_count += 1
            parsed = _parse_json_from_stdout(stdout_text)
            if parsed is not None:
                json_payloads.append(parsed)
            command_results.append(
                {
                    "id": f"validation-{index}",
                    "command": command,
                    "return_code": int(completed.returncode),
                    "passed": passed,
                    "elapsed_seconds": elapsed,
                    "stdout_log": str(stdout_path),
                    "stderr_log": str(stderr_path),
                    "parsed_json": parsed if parsed is not None else {},
                }
            )
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(str(exc.stdout or ""), encoding="utf-8")
            stderr_path.write_text(str(exc.stderr or ""), encoding="utf-8")
            command_results.append(
                {
                    "id": f"validation-{index}",
                    "command": command,
                    "return_code": 124,
                    "passed": False,
                    "elapsed_seconds": float(timeout_seconds),
                    "stdout_log": str(stdout_path),
                    "stderr_log": str(stderr_path),
                    "parsed_json": {},
                    "error": "timeout",
                }
            )
        except (OSError, ValueError) as exc:
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(str(exc), encoding="utf-8")
            command_results.append(
                {
                    "id": f"validation-{index}",
                    "command": command,
                    "return_code": 1,
                    "passed": False,
                    "elapsed_seconds": 0.0,
                    "stdout_log": str(stdout_path),
                    "stderr_log": str(stderr_path),
                    "parsed_json": {},
                    "error": str(exc),
                }
            )

    if json_payloads:
        # Prefer the final structured payload; validation drivers can aggregate
        # their own detailed scoring more accurately than the generic runner.
        result = _normalize_validation_payload(
            json_payloads[-1],
            fallback_ok=pass_count == len(commands),
            fallback_status="ok" if pass_count == len(commands) else "partial",
            fallback_score=0.0,
        )
    else:
        score = 100.0 * float(pass_count) / float(len(commands))
        result = {
            "ok": pass_count == len(commands),
            "status": "ok" if pass_count == len(commands) else "partial",
            "score": score,
            "complete": pass_count == len(commands),
            "build_ok": pass_count > 0,
            "api_ok": pass_count == len(commands),
            "scientific_checks_ok": pass_count == len(commands),
            "milestones": [
                {
                    "id": item["id"],
                    "status": "pass" if item["passed"] else "fail",
                    "score": 100.0 / float(len(commands)) if item["passed"] else 0.0,
                    "notes": "",
                }
                for item in command_results
            ],
            "cases": [],
            "observables": {},
            "errors": [
                str(item.get("error") or "")
                for item in command_results
                if not item.get("passed") and str(item.get("error") or "").strip()
            ],
        }
    result["commands_ok"] = pass_count == len(commands)
    result["command_results"] = command_results
    result["hard_reject"] = bool(result.get("hard_reject", False))
    _write_validation_result(run_dir, result)
    return result


def _write_validation_result(run_dir: Path, result: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "validation_result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def validation_score(result: dict[str, Any] | None) -> float:
    if not isinstance(result, dict):
        return 0.0
    raw = result.get("score")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return 0.0


def validation_complete(result: dict[str, Any] | None) -> bool:
    return bool(result.get("complete")) if isinstance(result, dict) else False


def _validation_flag_true(result: dict[str, Any], key: str) -> bool:
    value = result.get(key)
    if isinstance(value, str):
        return value.strip().lower() in {
            "true",
            "ok",
            "pass",
            "passed",
            "complete",
            "completed",
        }
    return bool(value)


def final_integrity_ok(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict) or not validation_complete(result):
        return False
    if "commands_ok" in result and not bool(result.get("commands_ok")):
        return False
    return (
        _validation_flag_true(result, "ok")
        and _validation_flag_true(result, "build_ok")
        and _validation_flag_true(result, "api_ok")
        and _validation_flag_true(result, "scientific_checks_ok")
    )


_CONTROLLER_REVIEW_PASS_VERDICTS = {
    "pass",
    "passed",
    "ok",
    "satisfied",
    "complete",
    "completed",
}


def _controller_review_decision(review: dict[str, Any] | None) -> str | None:
    if not isinstance(review, dict):
        return None
    decision = str(review.get("decision") or "").strip().upper()
    return decision if decision in {"ACCEPTED", "REJECTED"} else None


def _controller_review_requirement_evidence(requirement: dict[str, Any]) -> list[str]:
    evidence = requirement.get("evidence")
    if isinstance(evidence, str):
        evidence_text = evidence.strip()
        return [evidence_text] if evidence_text else []
    if not isinstance(evidence, list):
        return []
    return [str(item).strip() for item in evidence if str(item).strip()]


def controller_review_final_ok(review: dict[str, Any] | None) -> bool:
    """Return True when the controller independently proves final satisfaction."""

    if not isinstance(review, dict):
        return False
    if _controller_review_decision(review) != "ACCEPTED":
        return False
    if not _validation_flag_true(review, "final_complete"):
        return False
    requirements = review.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return False
    checked_required = False
    for raw_requirement in requirements:
        if not isinstance(raw_requirement, dict):
            continue
        if not bool(raw_requirement.get("required", True)):
            continue
        checked_required = True
        verdict = (
            str(raw_requirement.get("verdict") or raw_requirement.get("status") or "")
            .strip()
            .lower()
        )
        if verdict not in _CONTROLLER_REVIEW_PASS_VERDICTS:
            return False
        if not _controller_review_requirement_evidence(raw_requirement):
            return False
    return checked_required


def acceptance_decision(
    *,
    contract_payload: dict[str, Any],
    incumbent_validation: dict[str, Any],
    candidate_validation: dict[str, Any],
    controller_decision: str | None,
    controller_review: dict[str, Any] | None = None,
    hard_reject: bool,
    hard_reason: str,
) -> dict[str, Any]:
    if hard_reject:
        return {
            "accepted": False,
            "final_complete": False,
            "status": "rejected",
            "reason": hard_reason or "hard guard rejection",
        }
    effective_controller_decision = (
        _controller_review_decision(controller_review) or controller_decision
    )
    if effective_controller_decision != "ACCEPTED":
        return {
            "accepted": False,
            "final_complete": False,
            "status": "rejected",
            "reason": "controller rejected candidate",
        }
    if bool(candidate_validation.get("hard_reject")):
        return {
            "accepted": False,
            "final_complete": False,
            "status": "rejected",
            "reason": str(
                candidate_validation.get("reason") or "validation hard reject"
            ),
        }

    scoring = implement_contract.scoring_config(contract_payload)
    min_improvement = scoring.get("min_score_improvement", 0.0)
    try:
        min_delta = max(0.0, float(min_improvement))
    except (TypeError, ValueError):
        min_delta = 0.0
    old_score = validation_score(incumbent_validation)
    new_score = validation_score(candidate_validation)
    validation_reports_complete = validation_complete(candidate_validation)
    if validation_reports_complete and not final_integrity_ok(candidate_validation):
        return {
            "accepted": False,
            "final_complete": False,
            "status": "rejected",
            "reason": (
                "candidate reported complete=true without ok/build_ok/api_ok/"
                "scientific_checks_ok all passing"
            ),
        }
    semantic_final_ok = controller_review_final_ok(controller_review)
    complete = validation_reports_complete and semantic_final_ok
    improved = new_score > old_score + min_delta
    if complete or improved:
        if validation_reports_complete and not semantic_final_ok and improved:
            reason = (
                "validation reported complete, but controller review did not "
                "provide structured final target-satisfaction evidence; "
                f"accepting partial progress ({old_score:.6g} -> {new_score:.6g})"
            )
        else:
            reason = (
                "candidate satisfies final done criteria and controller review"
                if complete
                else f"score improved from {old_score:.6g} to {new_score:.6g}"
            )
        return {
            "accepted": True,
            "final_complete": complete,
            "status": "complete" if complete else "accepted_partial",
            "reason": reason,
        }
    if validation_reports_complete and not semantic_final_ok:
        return {
            "accepted": False,
            "final_complete": False,
            "status": "rejected",
            "reason": (
                "validation reported complete, but controller review did not "
                "provide structured final target-satisfaction evidence"
            ),
        }
    return {
        "accepted": False,
        "final_complete": False,
        "status": "rejected",
        "reason": f"score did not improve ({old_score:.6g} -> {new_score:.6g})",
    }
