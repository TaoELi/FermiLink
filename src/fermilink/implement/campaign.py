from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from fermilink.optimize.main import _run_optimize_worker_loop

from . import contract as implement_contract
from . import git as implement_git
from . import goal as implement_goal
from . import prompts as implement_prompts
from . import source_analysis as implement_source_analysis
from . import state as implement_state
from . import validation as implement_validation


GOAL_MAX_ANALYSIS_TURNS = 3
GOAL_MAX_CONTRACT_TURNS = 3


def _cli():
    from fermilink import cli

    return cli


def _normalize_rel_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("/")


def _matches_any(path_text: str, patterns: list[str]) -> bool:
    normalized = _normalize_rel_path(path_text)
    for raw_pattern in patterns:
        pattern = _normalize_rel_path(raw_pattern)
        if pattern in {"**", "**/*"}:
            return True
        if pattern.endswith("/**") and normalized == pattern[:-3].rstrip("/"):
            return True
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
    return False


def _is_mutable_candidate_path(
    path_text: str,
    *,
    editable_paths: list[str],
    immutable_paths: list[str],
) -> bool:
    normalized = _normalize_rel_path(path_text)
    if not normalized:
        return False
    return _matches_any(normalized, editable_paths) and not _matches_any(
        normalized,
        immutable_paths,
    )


def _changed_signatures(project_root: Path) -> set[tuple[str, str]]:
    signatures: set[tuple[str, str]] = set()
    for entry in implement_git.list_changed_paths(project_root):
        status = str(entry.get("status") or "").strip()
        path_text = _normalize_rel_path(str(entry.get("path") or ""))
        if status and path_text:
            signatures.add((status, path_text))
    return signatures


def _new_changed_entries(
    project_root: Path,
    baseline: set[tuple[str, str]],
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for entry in implement_git.list_changed_paths(project_root):
        status = str(entry.get("status") or "").strip()
        path_text = _normalize_rel_path(str(entry.get("path") or ""))
        if not status or not path_text:
            continue
        if (status, path_text) not in baseline:
            entries.append({"status": status, "path": path_text})
    return entries


def _unexpected_tracked_changes(
    project_root: Path,
    *,
    baseline: set[tuple[str, str]],
    allowed_paths: list[str] | None = None,
    allowed_patterns: list[str] | None = None,
) -> list[dict[str, str]]:
    allowed_path_set = {
        _normalize_rel_path(path)
        for path in (allowed_paths or [])
        if _normalize_rel_path(path)
    }
    patterns = [
        _normalize_rel_path(pattern)
        for pattern in (allowed_patterns or [])
        if _normalize_rel_path(pattern)
    ]
    unexpected: list[dict[str, str]] = []
    for entry in _new_changed_entries(project_root, baseline):
        path_text = _normalize_rel_path(entry.get("path", ""))
        if path_text in allowed_path_set or _matches_any(path_text, patterns):
            continue
        unexpected.append(entry)
    return unexpected


def _file_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _snapshot_files(paths: list[Path]) -> dict[str, str | None]:
    return {str(path.resolve()): _file_digest(path) for path in paths}


def _changed_file_snapshots(
    before: dict[str, str | None],
    paths: list[Path],
) -> list[str]:
    changed: list[str] = []
    for path in paths:
        key = str(path.resolve())
        if before.get(key) != _file_digest(path):
            changed.append(str(path))
    return changed


def _write_run_text(run_dir: Path, filename: str, text: str) -> None:
    target = run_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(text or ""), encoding="utf-8")


def _write_run_json(run_dir: Path, filename: str, payload: dict[str, Any]) -> None:
    target = run_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _tracked_file_summary(project_root: Path) -> tuple[list[str], str]:
    completed = implement_git.run_git(project_root, ["ls-files", "-z"])
    tracked = [
        item.strip().replace("\\", "/")
        for item in str(completed.stdout or "").split("\0")
        if item.strip()
    ]
    if not tracked:
        return tracked, "(no tracked files)"
    dirs: dict[str, int] = {}
    for path in tracked:
        top = path.split("/", 1)[0] if "/" in path else "."
        dirs[top] = dirs.get(top, 0) + 1
    lines = [f"  {name}/ ({count} files)" for name, count in sorted(dirs.items())]
    if len(tracked) <= 100:
        lines.append("")
        lines.append("Full listing:")
        lines.extend(f"  {path}" for path in sorted(tracked))
    else:
        lines.append(f"  (total: {len(tracked)} tracked files)")
    return tracked, "\n".join(lines)


def _resolve_project_root(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "project_root", None) or getattr(args, "project_path", None) or "").strip()
    if not raw:
        raw = "."
    return Path(raw).expanduser().resolve()


def _resolve_goal_path(project_root: Path, raw_goal: str) -> Path:
    goal_path = Path(str(raw_goal or "").strip()).expanduser()
    if not goal_path.is_absolute():
        goal_path = (project_root / goal_path).resolve()
    return goal_path


def _resolve_implement_branch(
    contract_payload: dict[str, Any],
    *,
    package_id: str,
    override: str | None,
) -> str:
    if isinstance(override, str) and override.strip():
        return override.strip()
    campaign = implement_contract.campaign_config(contract_payload)
    preferred = str(campaign.get("incumbent_branch") or "").strip()
    if preferred:
        return preferred
    return f"fermilink-implement/{package_id}"


def _safe_float(raw: object, default: float = 0.0) -> float:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    try:
        return float(str(raw))
    except (TypeError, ValueError):
        return default


def _positive_int(raw: object, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _worker_loop_config(
    args: argparse.Namespace,
    contract_payload: dict[str, Any],
) -> dict[str, float | int]:
    worker = implement_contract.worker_config(contract_payload)
    return {
        "max_iterations": _positive_int(
            getattr(args, "worker_max_iterations", None)
            if getattr(args, "worker_max_iterations", None) is not None
            else worker.get("max_iterations"),
            implement_contract.DEFAULT_WORKER_MAX_ITERATIONS,
        ),
        "wait_seconds": max(
            0.0,
            _safe_float(
                getattr(args, "worker_wait_seconds", None)
                if getattr(args, "worker_wait_seconds", None) is not None
                else worker.get("wait_seconds"),
                implement_contract.DEFAULT_WORKER_WAIT_SECONDS,
            ),
        ),
        "max_wait_seconds": max(
            0.0,
            _safe_float(
                getattr(args, "worker_max_wait_seconds", None)
                if getattr(args, "worker_max_wait_seconds", None) is not None
                else worker.get("max_wait_seconds"),
                implement_contract.DEFAULT_WORKER_MAX_WAIT_SECONDS,
            ),
        ),
        "pid_stall_seconds": max(
            0.0,
            _safe_float(
                getattr(args, "worker_pid_stall_seconds", None)
                if getattr(args, "worker_pid_stall_seconds", None) is not None
                else worker.get("pid_stall_seconds"),
                900.0,
            ),
        ),
    }


def _sync_paths_to_worker(
    *,
    project_root: Path,
    worker_root: Path,
    rel_paths: set[str],
) -> None:
    project_resolved = project_root.resolve()
    for raw_rel in sorted(rel_paths):
        rel_path = _normalize_rel_path(raw_rel)
        if not rel_path:
            continue
        source = (project_root / rel_path).resolve()
        try:
            source.relative_to(project_resolved)
        except ValueError:
            continue
        target = worker_root / rel_path
        if not source.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            continue
        if source.is_dir() and not source.is_symlink():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _collect_editable_files(
    root: Path,
    editable_paths: list[str],
    *,
    immutable_paths: list[str] | None = None,
) -> set[str]:
    matches: set[str] = set()
    immutable = immutable_paths or []
    root_resolved = root.resolve()
    for current_dir, dirnames, filenames in os.walk(root):
        current = Path(current_dir).resolve()
        try:
            rel_dir = str(current.relative_to(root_resolved)).replace("\\", "/")
        except ValueError:
            continue
        if rel_dir in {".git", ".fermilink-implement/runs"}:
            dirnames[:] = []
            continue
        filtered: list[str] = []
        for item in dirnames:
            if item == ".git":
                continue
            rel = f"{rel_dir}/{item}" if rel_dir and rel_dir != "." else item
            if rel.replace("\\", "/") == ".fermilink-implement/runs":
                continue
            filtered.append(item)
        dirnames[:] = filtered
        for filename in filenames:
            rel = f"{rel_dir}/{filename}" if rel_dir and rel_dir != "." else filename
            rel = rel.replace("\\", "/").strip("/")
            if rel and _is_mutable_candidate_path(
                rel,
                editable_paths=editable_paths,
                immutable_paths=immutable,
            ):
                matches.add(rel)
    return matches


def _sync_worker_outputs(
    *,
    project_root: Path,
    worker_root: Path,
    worker_memory_rel: str,
    worker_memory_path: Path,
    editable_paths: list[str],
    immutable_paths: list[str],
) -> None:
    source_memory = worker_root / worker_memory_rel
    if source_memory.is_file():
        worker_memory_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_memory, worker_memory_path)
    worker_files = _collect_editable_files(
        worker_root,
        editable_paths,
        immutable_paths=immutable_paths,
    )
    project_files = _collect_editable_files(
        project_root,
        editable_paths,
        immutable_paths=immutable_paths,
    )
    for rel_path in sorted(worker_files):
        source = worker_root / rel_path
        target = project_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copy2(source, target)
    for rel_path in sorted(project_files - worker_files):
        try:
            (project_root / rel_path).unlink(missing_ok=True)
        except OSError:
            pass


def _effective_immutable_paths(
    contract_payload: dict[str, Any],
    *,
    contract_rel: str,
    program_rel: str,
    results_rel: str,
) -> list[str]:
    protected = {
        ".fermilink-implement/**",
        ".fermilink-optimize/**",
        contract_rel,
        program_rel,
        results_rel,
    }
    protected.update(implement_contract.immutable_paths(contract_payload))
    return sorted(path for path in protected if path)


def _candidate_complete_integrity_ok(validation: dict[str, Any]) -> bool:
    return implement_validation.final_integrity_ok(validation)


def _maybe_lock_api(
    state_payload: dict[str, Any],
    *,
    contract_payload: dict[str, Any],
    validation: dict[str, Any],
    commit_sha: str,
    iteration: int,
) -> bool:
    api_config = contract_payload.get("api")
    if not isinstance(api_config, dict):
        return False
    if not bool(api_config.get("lock_after_first_passing_validation", False)):
        return False
    if bool(state_payload.get("api_locked", False)):
        return False
    if not bool(validation.get("api_ok")):
        return False
    state_payload["api_locked"] = True
    state_payload["api_locked_at_commit"] = commit_sha
    state_payload["api_locked_at_iteration"] = int(iteration)
    state_payload["locked_api"] = str(api_config.get("input") or "").strip()
    return True


def _initial_state(
    *,
    package_id: str,
    goal_rel: str,
    contract_rel: str,
    program_rel: str,
    memory_rel: str,
    results_rel: str,
    branch_name: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "package_id": package_id,
        "goal_path": goal_rel,
        "contract_path": contract_rel,
        "program_path": program_rel,
        "memory_path": memory_rel,
        "results_path": results_rel,
        "branch": branch_name,
        "started_at_utc": implement_state.utc_now_z(),
        "iteration": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "consecutive_rejections": 0,
        "baseline_commit": "",
        "baseline_validation": {},
        "incumbent_commit": "",
        "incumbent_validation": {},
        "complete": False,
    }


def _write_goal_snapshot(project_root: Path, goal_path: Path, goal_text: str) -> None:
    implement_state.ensure_autogen_root(project_root)
    implement_state.goal_copy_path(project_root).write_text(
        goal_text,
        encoding="utf-8",
    )


def _render_default_contract_yaml(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)


def _run_source_analysis_turn(
    *,
    project_root: Path,
    goal_spec: dict[str, Any],
    goal_rel: str,
    autogen_rel: str,
    tracked_file_summary: str,
    provider: str,
    provider_bin_override: str | None,
    sandbox_mode: str | None,
    sandbox_policy: str,
    model: str | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    cli = _cli()
    agents_md = implement_source_analysis.build_source_analysis_agents_md(
        goal_rel=goal_rel,
        autogen_rel=autogen_rel,
    )
    prompt = implement_source_analysis.build_source_analysis_prompt(
        goal_spec=goal_spec,
        goal_rel=goal_rel,
        tracked_file_summary=tracked_file_summary,
    )
    with implement_git.temporary_implement_agents(
        project_root,
        provider=provider,
        content=agents_md,
    ):
        return cli._run_exec_chat_turn(
            repo_dir=project_root,
            prompt=prompt,
            sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
            provider_bin_override=provider_bin_override,
            provider=provider,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
        )


def _run_contract_generation_turn(
    *,
    project_root: Path,
    goal_spec: dict[str, Any],
    goal_rel: str,
    analysis: dict[str, Any],
    analysis_rel: str,
    autogen_rel: str,
    contract_rel: str,
    runner_rel: str,
    default_contract_yaml: str,
    provider: str,
    provider_bin_override: str | None,
    sandbox_mode: str | None,
    sandbox_policy: str,
    model: str | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    cli = _cli()
    agents_md = implement_source_analysis.build_contract_generation_agents_md(
        goal_rel=goal_rel,
        analysis_rel=analysis_rel,
        autogen_rel=autogen_rel,
    )
    prompt = implement_source_analysis.build_contract_generation_prompt(
        goal_spec=goal_spec,
        goal_rel=goal_rel,
        analysis=analysis,
        analysis_rel=analysis_rel,
        default_contract_yaml=default_contract_yaml,
        contract_rel=contract_rel,
        runner_rel=runner_rel,
    )
    with implement_git.temporary_implement_agents(
        project_root,
        provider=provider,
        content=agents_md,
    ):
        return cli._run_exec_chat_turn(
            repo_dir=project_root,
            prompt=prompt,
            sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
            provider_bin_override=provider_bin_override,
            provider=provider,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
        )


def _scaffold_contract(
    project_root: Path,
    *,
    package_id: str,
    goal_spec: dict[str, Any],
    goal_rel: str,
    run_agents: bool,
    args: argparse.Namespace,
) -> dict[str, Any]:
    cli = _cli()
    implement_state.ensure_implement_root(project_root)
    implement_state.ensure_autogen_root(project_root)
    _, file_summary = _tracked_file_summary(project_root)
    autogen_rel = implement_state.safe_relative(
        implement_state.autogen_root(project_root),
        project_root,
    )
    analysis_path = implement_state.goal_analysis_path(project_root)
    contract_path = implement_state.contract_path(project_root)
    runner_path = implement_state.validation_runner_path(project_root)
    analysis_rel = implement_state.safe_relative(analysis_path, project_root)
    contract_rel = implement_state.safe_relative(contract_path, project_root)
    runner_rel = implement_state.safe_relative(runner_path, project_root)

    analysis: dict[str, Any] = {}
    review_notes = ""
    if run_agents:
        runtime_policy = cli.resolve_agent_runtime_policy()
        provider = runtime_policy.provider
        sandbox_policy = runtime_policy.sandbox_policy
        sandbox_mode = runtime_policy.sandbox_mode
        if isinstance(getattr(args, "sandbox", None), str) and args.sandbox.strip():
            sandbox_policy = "enforce"
            sandbox_mode = args.sandbox.strip()
        provider_bin_override = cli.resolve_provider_binary_override(
            provider,
            raw_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
        )
        for attempt in range(1, GOAL_MAX_ANALYSIS_TURNS + 1):
            turn_baseline = _changed_signatures(project_root)
            result = _run_source_analysis_turn(
                project_root=project_root,
                goal_spec=goal_spec,
                goal_rel=goal_rel,
                autogen_rel=autogen_rel,
                tracked_file_summary=file_summary,
                provider=provider,
                provider_bin_override=provider_bin_override,
                sandbox_mode=sandbox_mode,
                sandbox_policy=sandbox_policy,
                model=runtime_policy.model,
                reasoning_effort=runtime_policy.reasoning_effort,
            )
            unexpected_changes = _unexpected_tracked_changes(
                project_root,
                baseline=turn_baseline,
                allowed_patterns=[f"{autogen_rel}/**"],
            )
            if unexpected_changes:
                rendered = ", ".join(
                    entry["path"] for entry in unexpected_changes[:6]
                )
                raise cli.PackageError(
                    "Implement source analysis left tracked changes outside "
                    f"`{autogen_rel}`: {rendered}"
                )
            extracted = implement_source_analysis.extract_source_analysis(
                str(result.get("assistant_text") or "")
            )
            if extracted:
                analysis = extracted
                review_notes = (
                    implement_source_analysis.extract_review_notes(
                        str(result.get("assistant_text") or "")
                    )
                    or ""
                )
                break
            if attempt < GOAL_MAX_ANALYSIS_TURNS:
                cli._print_tagged(
                    "implement",
                    f"source analysis attempt {attempt} did not produce structured output, retrying",
                )
        if not analysis:
            raise cli.PackageError("Implement source analysis failed to produce JSON.")

    if not analysis:
        analysis = {
            "package": package_id,
            "target": str(goal_spec.get("target") or ""),
            "proposed_api": str(goal_spec.get("input_api") or ""),
            "validation_strategy": str(goal_spec.get("validation") or ""),
            "source": "default_no_agent",
        }
    implement_state.write_json_file(analysis_path, analysis)

    default_contract = implement_contract.build_default_contract(
        project_root,
        package_id=package_id,
        goal_spec=goal_spec,
        analysis=analysis,
    )
    contract_payload = copy.deepcopy(default_contract)
    default_contract_yaml = _render_default_contract_yaml(default_contract)
    generation_review_notes = ""

    if run_agents:
        runtime_policy = cli.resolve_agent_runtime_policy()
        provider = runtime_policy.provider
        sandbox_policy = runtime_policy.sandbox_policy
        sandbox_mode = runtime_policy.sandbox_mode
        if isinstance(getattr(args, "sandbox", None), str) and args.sandbox.strip():
            sandbox_policy = "enforce"
            sandbox_mode = args.sandbox.strip()
        provider_bin_override = cli.resolve_provider_binary_override(
            provider,
            raw_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
        )
        for attempt in range(1, GOAL_MAX_CONTRACT_TURNS + 1):
            turn_baseline = _changed_signatures(project_root)
            result = _run_contract_generation_turn(
                project_root=project_root,
                goal_spec=goal_spec,
                goal_rel=goal_rel,
                analysis=analysis,
                analysis_rel=analysis_rel,
                autogen_rel=autogen_rel,
                contract_rel=contract_rel,
                runner_rel=runner_rel,
                default_contract_yaml=default_contract_yaml,
                provider=provider,
                provider_bin_override=provider_bin_override,
                sandbox_mode=sandbox_mode,
                sandbox_policy=sandbox_policy,
                model=runtime_policy.model,
                reasoning_effort=runtime_policy.reasoning_effort,
            )
            unexpected_changes = _unexpected_tracked_changes(
                project_root,
                baseline=turn_baseline,
                allowed_patterns=[f"{autogen_rel}/**"],
            )
            if unexpected_changes:
                rendered = ", ".join(
                    entry["path"] for entry in unexpected_changes[:6]
                )
                raise cli.PackageError(
                    "Implement contract generation left tracked changes outside "
                    f"`{autogen_rel}`: {rendered}"
                )
            assistant_text = str(result.get("assistant_text") or "")
            extracted_contract = (
                implement_source_analysis.extract_implementation_contract(
                    assistant_text
                )
            )
            extracted_runner = implement_source_analysis.extract_validation_runner(
                assistant_text
            )
            generation_review_notes = (
                implement_source_analysis.extract_review_notes(assistant_text) or ""
            )
            if extracted_contract:
                contract_path.write_text(extracted_contract, encoding="utf-8")
            if extracted_runner:
                runner_path.write_text(extracted_runner, encoding="utf-8")
                implement_state.ensure_executable(runner_path)
            if contract_path.is_file():
                try:
                    contract_payload = implement_contract.load_contract(contract_path)
                except Exception:
                    if attempt >= GOAL_MAX_CONTRACT_TURNS:
                        raise
                    continue
                break
            if attempt < GOAL_MAX_CONTRACT_TURNS:
                cli._print_tagged(
                    "implement",
                    f"contract generation attempt {attempt} incomplete, retrying",
                )

    implement_contract.write_contract(contract_path, contract_payload)
    plan_path = implement_state.plan_path(project_root)
    plan_path.write_text(
        (
            "# FermiLink Implement Plan\n"
            "\n"
            f"- goal: `{goal_rel}`\n"
            f"- contract: `{contract_rel}`\n"
            f"- analysis: `{analysis_rel}`\n"
            f"- baseline_mode: {contract_payload.get('baseline', {}).get('mode') if isinstance(contract_payload.get('baseline'), dict) else 'unknown'}\n"
            f"- editable_paths: {', '.join(implement_contract.editable_paths(contract_payload))}\n"
            "\n"
            "The campaign accepts honest partial progress when validation score improves "
            "and stops after the contract reports complete=true.\n"
        ),
        encoding="utf-8",
    )
    return {
        "analysis_path": analysis_path,
        "contract_path": contract_path,
        "contract_payload": contract_payload,
        "validation_runner_path": runner_path,
        "plan_path": plan_path,
        "review_notes": "\n".join(
            note for note in (review_notes, generation_review_notes) if note
        ),
    }


def _run_lock_payload(
    project_root: Path,
    *,
    package_id: str,
    goal_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "pid": os.getpid(),
        "started_at_utc": implement_state.utc_now_z(),
        "mode": "implement",
        "project_root": str(project_root),
        "package_id": package_id,
        "goal_path": str(goal_path),
    }


def _write_run_lock(project_root: Path, *, package_id: str, goal_path: Path) -> None:
    cli = _cli()
    lock_path = implement_state.run_lock_path(project_root)
    existing = implement_state.load_json_file(lock_path)
    if isinstance(existing, dict):
        try:
            pid = int(existing.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0 and pid != os.getpid() and implement_state.pid_is_running(pid):
            started = str(existing.get("started_at_utc") or "").strip()
            raise cli.PackageError(
                "Implement campaign appears active in this repository "
                f"(pid={pid}, started={started or 'unknown'})."
            )
    implement_state.write_json_file(
        lock_path,
        _run_lock_payload(project_root, package_id=package_id, goal_path=goal_path),
    )


def read_campaign_status(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args)
    state_payload = implement_state.load_state(implement_state.state_path(project_root)) or {}
    lock_payload = implement_state.load_json_file(
        implement_state.run_lock_path(project_root)
    ) or {}
    try:
        lock_pid = int(lock_payload.get("pid") or 0)
    except (TypeError, ValueError):
        lock_pid = 0
    run_lock_status = "inactive"
    if lock_pid > 0 and implement_state.pid_is_running(lock_pid):
        run_lock_status = "active"
    elif lock_pid > 0:
        run_lock_status = "inactive_stale"
    incumbent_validation = (
        state_payload.get("incumbent_validation")
        if isinstance(state_payload.get("incumbent_validation"), dict)
        else {}
    )
    return {
        "status": "ok" if state_payload else "missing",
        "project_root": str(project_root),
        "state_path": str(implement_state.state_path(project_root)),
        "results_path": str(implement_state.results_path(project_root)),
        "run_lock_status": run_lock_status,
        "run_lock_pid": lock_pid if lock_pid > 0 else None,
        "iteration": int(state_payload.get("iteration") or 0),
        "accepted_count": int(state_payload.get("accepted_count") or 0),
        "rejected_count": int(state_payload.get("rejected_count") or 0),
        "incumbent_commit": str(state_payload.get("incumbent_commit") or ""),
        "incumbent_score": implement_validation.validation_score(incumbent_validation),
        "complete": bool(state_payload.get("complete", False)),
        "recent_results": implement_state.recent_results_text(
            implement_state.results_path(project_root),
            limit=int(getattr(args, "tail", 30) or 30),
        ),
    }


def run_goal_campaign(args: argparse.Namespace) -> dict[str, Any]:
    cli = _cli()
    project_root = _resolve_project_root(args)
    goal_target = str(
        getattr(args, "goal", None)
        or getattr(args, "goal_path", None)
        or getattr(args, "package_id", None)
        or ""
    ).strip()
    if not goal_target:
        raise cli.PackageError("Implement mode requires a goal markdown path.")
    goal_path = _resolve_goal_path(project_root, goal_target)
    if not goal_path.is_file():
        raise cli.PackageError(f"Goal file does not exist: {goal_path}")
    goal_text = goal_path.read_text(encoding="utf-8")
    goal_spec = implement_goal.parse_goal(goal_text)
    package_id_raw = str(goal_spec.get("package") or project_root.name or "package")
    package_id = cli.normalize_package_id(package_id_raw)

    git_repo_initialized = cli._ensure_compile_repo_ready(project_root)
    implement_git.ensure_local_excludes(project_root, [".fermilink-implement/"])
    if (project_root / "skills").exists():
        implement_git.ensure_local_excludes(project_root, ["skills/"])
    implement_git.cleanup_stale_temporary_implement_agents(project_root)
    implement_git.ensure_clean_repo(
        project_root,
        allow_dirty=bool(getattr(args, "allow_dirty", False)),
    )
    _write_run_lock(project_root, package_id=package_id, goal_path=goal_path)
    implement_state.ensure_implement_root(project_root)
    implement_state.ensure_autogen_root(project_root)
    _write_goal_snapshot(project_root, goal_path, goal_text)

    goal_rel = implement_state.safe_relative(goal_path, project_root)
    resume_existing_contract = bool(getattr(args, "resume", False)) and (
        implement_state.contract_path(project_root).is_file()
    )
    if resume_existing_contract:
        contract_path = implement_state.contract_path(project_root)
        contract_payload = implement_contract.load_contract(contract_path)
        scaffold = {
            "analysis_path": implement_state.goal_analysis_path(project_root),
            "contract_path": contract_path,
            "contract_payload": contract_payload,
            "validation_runner_path": implement_state.validation_runner_path(
                project_root
            ),
            "plan_path": implement_state.plan_path(project_root),
            "review_notes": "",
        }
    else:
        run_agents = not bool(getattr(args, "plan_only", False)) and not bool(
            getattr(args, "resume", False)
        )
        scaffold = _scaffold_contract(
            project_root,
            package_id=package_id,
            goal_spec=goal_spec,
            goal_rel=goal_rel,
            run_agents=run_agents,
            args=args,
        )
        contract_path = Path(scaffold["contract_path"])
        contract_payload = implement_contract.load_contract(contract_path)
    contract_rel = implement_state.safe_relative(contract_path, project_root)
    program_path = implement_state.program_path(project_root)
    program_rel = implement_state.safe_relative(program_path, project_root)
    results_path = implement_state.results_path(project_root)
    memory_path = implement_state.memory_path(project_root)
    worker_memory_path = implement_state.worker_memory_path(project_root)
    memory_rel = implement_state.safe_relative(memory_path, project_root)
    worker_memory_rel = implement_state.safe_relative(worker_memory_path, project_root)
    results_rel = implement_state.safe_relative(results_path, project_root)
    branch_name = _resolve_implement_branch(
        contract_payload,
        package_id=package_id,
        override=getattr(args, "branch", None),
    )
    branch_info = implement_git.checkout_implement_branch(
        project_root,
        branch_name=branch_name,
    )
    implement_state.ensure_program_file(
        program_path,
        content=implement_prompts.default_program_markdown(
            package_id=package_id,
            goal_rel=goal_rel,
            contract_rel=contract_rel,
        ),
    )
    implement_state.ensure_results_file(results_path)
    implement_state.ensure_memory_file(
        memory_path,
        package_id=package_id,
        goal_rel=goal_rel,
        contract_rel=contract_rel,
        branch_name=branch_name,
    )
    state_path = implement_state.state_path(project_root)
    state_payload = implement_state.load_state(state_path)
    if state_payload is None:
        state_payload = _initial_state(
            package_id=package_id,
            goal_rel=goal_rel,
            contract_rel=contract_rel,
            program_rel=program_rel,
            memory_rel=memory_rel,
            results_rel=results_rel,
            branch_name=branch_name,
        )
    state_payload["branch"] = branch_name
    implement_state.write_state(state_path, state_payload)

    if bool(getattr(args, "plan_only", False)):
        return {
            "status": "planned",
            "package_id": package_id,
            "branch": branch_name,
            "git_repo_initialized": git_repo_initialized,
            "state_path": str(state_path),
            "goal_path": str(goal_path),
            "contract_path": str(contract_path),
            "plan_path": str(scaffold["plan_path"]),
            "results_path": str(results_path),
        }

    controller = implement_contract.controller_config(contract_payload)
    timeout_seconds = _positive_int(
        getattr(args, "timeout_seconds", None)
        or controller.get("timeout_seconds"),
        implement_contract.DEFAULT_TIMEOUT_SECONDS,
    )
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
    worker_provider = str(getattr(args, "worker_provider", None) or provider).strip()
    raw_worker_model = getattr(args, "worker_model", None)
    if raw_worker_model is not None:
        worker_model = str(raw_worker_model).strip()
        if not worker_model:
            raise cli.PackageError("--worker-model cannot be empty.")
    elif worker_provider == provider:
        worker_model = model
    else:
        worker_model = None
    worker_provider_bin_override = cli.resolve_provider_binary_override(
        worker_provider,
        raw_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
    )

    if not str(state_payload.get("baseline_commit") or "").strip():
        baseline_commit = implement_git.head_sha(project_root)
        baseline_dir = implement_state.runs_root(project_root) / "baseline"
        cli._print_tagged("implement", "running initial validation")
        baseline_validation = implement_validation.run_validation_suite(
            project_root,
            contract_payload=contract_payload,
            contract_path=contract_path,
            run_dir=baseline_dir,
            timeout_seconds=timeout_seconds,
        )
        state_payload["baseline_commit"] = baseline_commit
        state_payload["baseline_validation"] = copy.deepcopy(baseline_validation)
        state_payload["incumbent_commit"] = baseline_commit
        state_payload["incumbent_validation"] = copy.deepcopy(baseline_validation)
        _maybe_lock_api(
            state_payload,
            contract_payload=contract_payload,
            validation=baseline_validation,
            commit_sha=baseline_commit,
            iteration=0,
        )
        implement_state.append_result(
            results_path,
            iteration=0,
            commit=baseline_commit[:12],
            status="baseline",
            score=implement_validation.validation_score(baseline_validation),
            complete=implement_validation.validation_complete(baseline_validation),
            description="initial validation",
        )
        implement_state.write_state(state_path, state_payload)

    if bool(getattr(args, "baseline_only", False)):
        incumbent_validation = state_payload.get("incumbent_validation")
        incumbent_validation = (
            incumbent_validation if isinstance(incumbent_validation, dict) else {}
        )
        return {
            "status": "baseline_only",
            "package_id": package_id,
            "branch": branch_name,
            "state_path": str(state_path),
            "contract_path": str(contract_path),
            "incumbent_commit": str(state_payload.get("incumbent_commit") or ""),
            "incumbent_score": implement_validation.validation_score(
                incumbent_validation
            ),
            "complete": implement_validation.validation_complete(incumbent_validation),
        }

    editable_paths = implement_contract.editable_paths(contract_payload)
    immutable_paths = _effective_immutable_paths(
        contract_payload,
        contract_rel=contract_rel,
        program_rel=program_rel,
        results_rel=results_rel,
    )
    agents_md = implement_prompts.build_worker_agents_md(
        goal_rel=goal_rel,
        contract_rel=contract_rel,
        program_rel=program_rel,
        controller_memory_rel=memory_rel,
        worker_memory_rel=worker_memory_rel,
        results_rel=results_rel,
        editable_paths=editable_paths,
        immutable_paths=immutable_paths,
    )
    max_iterations = _positive_int(
        getattr(args, "max_iterations", None)
        or implement_contract.campaign_config(contract_payload).get("max_iterations"),
        implement_contract.DEFAULT_MAX_ITERATIONS,
    )
    stop_on_consecutive_rejections = _positive_int(
        getattr(args, "stop_on_consecutive_rejections", None)
        or implement_contract.campaign_config(contract_payload).get(
            "stop_on_consecutive_rejections"
        ),
        implement_contract.DEFAULT_STOP_ON_CONSECUTIVE_REJECTIONS,
    )
    worker_loop_config = _worker_loop_config(args, contract_payload)

    def ensure_worker_repo(start_commit: str, *, sync_skills: bool = False) -> Path:
        setup = implement_git.ensure_worker_worktree(
            project_root,
            controller_branch=branch_name,
            start_commit=start_commit,
        )
        worker_root = Path(str(setup.get("worker_root") or "")).resolve()
        if not worker_root.is_dir():
            raise cli.PackageError(f"Implement worker worktree missing: {worker_root}")
        implement_git.ensure_local_excludes(worker_root, [".fermilink-implement/"])
        if (project_root / "skills").exists():
            implement_git.ensure_local_excludes(worker_root, ["skills/"])
            if sync_skills or bool(setup.get("created_worktree")):
                _sync_paths_to_worker(
                    project_root=project_root,
                    worker_root=worker_root,
                    rel_paths={"skills"},
                )
        return worker_root

    ensure_worker_repo(implement_git.head_sha(project_root), sync_skills=True)
    sync_paths = {
        goal_rel,
        contract_rel,
        program_rel,
        memory_rel,
        worker_memory_rel,
        results_rel,
    }
    hidden_worker_paths = {
        ".fermilink-implement/runs",
        ".fermilink-implement/state.json",
        ".fermilink-implement/run.lock.json",
    }

    iteration = int(state_payload.get("iteration") or 0)
    accepted_count = int(state_payload.get("accepted_count") or 0)
    rejected_count = int(state_payload.get("rejected_count") or 0)
    consecutive_rejections = int(state_payload.get("consecutive_rejections") or 0)
    final_complete = bool(state_payload.get("complete", False))

    while not final_complete:
        if not bool(getattr(args, "forever", False)) and iteration >= max_iterations:
            break
        if consecutive_rejections >= stop_on_consecutive_rejections:
            cli._print_tagged(
                "implement",
                (
                    "stopping after consecutive rejection limit "
                    f"({consecutive_rejections}/{stop_on_consecutive_rejections})"
                ),
            )
            break
        iteration += 1
        start_sha = implement_git.head_sha(project_root)
        pre_iteration_untracked = set(implement_git.list_untracked_paths(project_root))
        run_dir = implement_state.runs_root(project_root) / f"iter_{iteration:04d}"
        run_rel = implement_state.safe_relative(run_dir, project_root)
        recent_results = implement_state.recent_results_text(results_path)
        implement_state.reset_worker_memory_file(
            worker_memory_path,
            package_id=package_id,
            goal_rel=goal_rel,
            contract_rel=contract_rel,
            controller_memory_rel=memory_rel,
            results_rel=results_rel,
            worker_iteration=iteration,
        )
        prompt = implement_prompts.build_worker_prompt(
            goal_rel=goal_rel,
            contract_rel=contract_rel,
            program_rel=program_rel,
            controller_memory_rel=memory_rel,
            worker_memory_rel=worker_memory_rel,
            results_rel=results_rel,
            recent_results_text=recent_results,
            state_payload=state_payload,
            editable_paths=editable_paths,
        )
        _write_run_text(run_dir, "worker_prompt.txt", prompt)
        cli._print_tagged("implement", f"iteration {iteration}")

        worker_root = ensure_worker_repo(start_sha)
        implement_git.reset_worker_to_commit(worker_root, commit_sha=start_sha)
        implement_git.clean_worker_untracked(worker_root)
        _sync_paths_to_worker(
            project_root=project_root,
            worker_root=worker_root,
            rel_paths=sync_paths,
        )
        implement_git.cleanup_paths(worker_root, sorted(hidden_worker_paths))

        def run_worker_turn(
            loop_iteration: int,
            _loop_max_iterations: int,
            prompt_text: str,
        ) -> dict[str, object]:
            pre_failure = implement_validation.run_pre_commands(
                worker_root,
                commands=implement_contract.pre_commands(contract_payload, "worker"),
                run_dir=run_dir / "worker_pre_commands" / f"turn_{loop_iteration:04d}",
                timeout_seconds=timeout_seconds,
                log_prefix="worker_pre_command",
                contract_path=worker_root / contract_rel,
            )
            if pre_failure is not None:
                return {
                    "assistant_text": "",
                    "return_code": 1,
                    "stderr": str(pre_failure.get("reason") or ""),
                    "pre_command_failure": pre_failure,
                }
            result = cli._run_exec_chat_turn(
                repo_dir=worker_root,
                prompt=prompt_text,
                sandbox=(
                    sandbox_mode if sandbox_policy == "enforce" else None
                ),
                provider_bin_override=worker_provider_bin_override,
                provider=worker_provider,
                sandbox_policy=sandbox_policy,
                model=worker_model,
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

        with implement_git.with_worker_git_disabled(worker_root):
            with implement_git.temporary_implement_agents(
                worker_root,
                provider=worker_provider,
                content=agents_md,
            ):
                worker_loop_result = _run_optimize_worker_loop(
                    prompt=prompt,
                    max_iterations=int(worker_loop_config["max_iterations"]),
                    wait_seconds=float(worker_loop_config["wait_seconds"]),
                    max_wait_seconds=float(worker_loop_config["max_wait_seconds"]),
                    pid_stall_seconds=float(worker_loop_config["pid_stall_seconds"]),
                    run_turn=run_worker_turn,
                )
        worker_changed_entries = implement_git.list_changed_paths(worker_root)
        worker_forbidden_changed = [
            entry["path"]
            for entry in worker_changed_entries
            if _normalize_rel_path(entry.get("path", "")) != worker_memory_rel
            and not _is_mutable_candidate_path(
                entry.get("path", ""),
                editable_paths=editable_paths,
                immutable_paths=immutable_paths,
            )
        ]
        _sync_worker_outputs(
            project_root=project_root,
            worker_root=worker_root,
            worker_memory_rel=worker_memory_rel,
            worker_memory_path=worker_memory_path,
            editable_paths=editable_paths,
            immutable_paths=immutable_paths,
        )
        archived_worker_memory = implement_state.archive_worker_memory(
            worker_memory_path,
            run_dir,
        )
        assistant_text = str(worker_loop_result.get("assistant_text") or "")
        description = (
            implement_prompts.extract_implementation_description(assistant_text)
            or f"implement iteration {iteration}"
        )
        _write_run_json(
            run_dir,
            "worker_loop_result.json",
            {
                "status": str(worker_loop_result.get("status") or ""),
                "reason": str(worker_loop_result.get("reason") or ""),
                "iteration_count": int(worker_loop_result.get("iteration") or 0),
                "exit_code": int(worker_loop_result.get("exit_code") or 0),
                "assistant_text": assistant_text,
                "archived_worker_memory": (
                    str(archived_worker_memory) if archived_worker_memory else ""
                ),
                "worker_loop_config": worker_loop_config,
            },
        )

        changed_entries = implement_git.list_changed_paths(project_root)
        editable_changed = [
            entry["path"]
            for entry in changed_entries
            if _is_mutable_candidate_path(
                entry.get("path", ""),
                editable_paths=editable_paths,
                immutable_paths=immutable_paths,
            )
        ]
        forbidden_changed = [
            entry["path"]
            for entry in changed_entries
            if not _is_mutable_candidate_path(
                entry.get("path", ""),
                editable_paths=editable_paths,
                immutable_paths=immutable_paths,
            )
        ]

        candidate_commit: str | None = None
        candidate_validation: dict[str, Any] = {}
        hard_reject = False
        hard_reason = ""
        controller_decision: str | None = None
        controller_summary: str | None = None
        controller_result: dict[str, object] = {}
        validation_ran = False
        if str(worker_loop_result.get("status") or "") != "done":
            hard_reject = True
            hard_reason = (
                "worker loop did not finish cleanly: "
                f"{worker_loop_result.get('status') or 'unknown'}"
            )
        elif worker_forbidden_changed:
            hard_reject = True
            hard_reason = (
                "worker modified forbidden paths: "
                f"{', '.join(worker_forbidden_changed[:4])}"
            )
        elif forbidden_changed:
            hard_reject = True
            hard_reason = f"modified forbidden paths: {', '.join(forbidden_changed[:4])}"
        elif not editable_changed:
            hard_reject = True
            hard_reason = "worker loop finished without editable code changes"
        else:
            candidate_commit = implement_git.commit_paths(
                project_root,
                paths=editable_changed,
                message=f"fermilink implement iter {iteration}: {description}",
            )
            diff_stat = implement_git.run_git(
                project_root,
                ["diff", "--stat", f"{start_sha}..{candidate_commit}"],
            )
            _write_run_text(run_dir, "candidate_diff_stat.txt", diff_stat.stdout or "")
            diff_full = implement_git.run_git(
                project_root,
                ["diff", f"{start_sha}..{candidate_commit}"],
            )
            _write_run_text(run_dir, "candidate.diff", diff_full.stdout or "")
            validation_ran = True
            candidate_validation = implement_validation.run_validation_suite(
                project_root,
                contract_payload=contract_payload,
                contract_path=contract_path,
                run_dir=run_dir,
                timeout_seconds=timeout_seconds,
            )
            if bool(candidate_validation.get("hard_reject")):
                hard_reject = True
                hard_reason = str(candidate_validation.get("reason") or "validation hard reject")

        incumbent_validation = (
            state_payload.get("incumbent_validation")
            if isinstance(state_payload.get("incumbent_validation"), dict)
            else {}
        )
        validation_context: dict[str, Any] = {
            "worker_loop_status": str(worker_loop_result.get("status") or ""),
            "candidate_description": description,
            "changed_paths": [entry.get("path", "") for entry in changed_entries],
            "editable_changed_paths": editable_changed,
            "forbidden_changed_paths": forbidden_changed,
            "incumbent_score": implement_validation.validation_score(
                incumbent_validation
            ),
            "candidate_validation": candidate_validation,
            "candidate_score": implement_validation.validation_score(
                candidate_validation
            ),
            "candidate_complete": implement_validation.validation_complete(
                candidate_validation
            ),
            "candidate_final_integrity_ok": _candidate_complete_integrity_ok(
                candidate_validation
            ),
            "api_locked": bool(state_payload.get("api_locked", False)),
            "hard_reject": hard_reject,
            "hard_reject_reason": hard_reason,
        }
        _write_run_json(run_dir, "review_context.json", validation_context)

        if validation_ran and candidate_commit is not None:
            controller_baseline = _changed_signatures(project_root)
            protected_files = [
                contract_path,
                program_path,
                results_path,
                state_path,
                implement_state.validation_runner_path(project_root),
            ]
            protected_before = _snapshot_files(protected_files)
            controller_agents_md = implement_prompts.build_controller_agents_md(
                goal_rel=goal_rel,
                contract_rel=contract_rel,
                program_rel=program_rel,
                memory_rel=memory_rel,
                results_rel=results_rel,
                run_rel=run_rel,
            )
            controller_prompt = implement_prompts.build_controller_prompt(
                goal_rel=goal_rel,
                contract_rel=contract_rel,
                program_rel=program_rel,
                memory_rel=memory_rel,
                results_rel=results_rel,
                run_rel=run_rel,
                iteration=iteration,
                incumbent_commit=str(state_payload.get("incumbent_commit") or ""),
                candidate_commit=candidate_commit,
                worker_description=description,
                changed_paths=editable_changed,
                validation_context=validation_context,
                recent_results_text=recent_results,
            )
            _write_run_text(run_dir, "controller_prompt.txt", controller_prompt)
            with implement_git.temporary_implement_agents(
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
            controller_decision = implement_prompts.extract_decision(controller_text)
            controller_summary = implement_prompts.extract_controller_summary(
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
            unexpected_controller_changes = _unexpected_tracked_changes(
                project_root,
                baseline=controller_baseline,
                allowed_paths=[memory_rel],
            )
            protected_changes = _changed_file_snapshots(
                protected_before,
                protected_files,
            )
            if unexpected_controller_changes or protected_changes:
                hard_reject = True
                reasons: list[str] = []
                if unexpected_controller_changes:
                    reasons.append(
                        "tracked changes: "
                        + ", ".join(
                            entry["path"]
                            for entry in unexpected_controller_changes[:4]
                        )
                    )
                if protected_changes:
                    reasons.append(
                        "protected files changed: "
                        + ", ".join(protected_changes[:4])
                    )
                hard_reason = "controller review left repository changes"
                if reasons:
                    hard_reason = f"{hard_reason} ({'; '.join(reasons)})"
                validation_context["hard_reject"] = True
                validation_context["hard_reject_reason"] = hard_reason
                validation_context["post_controller_changes"] = (
                    unexpected_controller_changes
                )
                validation_context["protected_file_changes"] = protected_changes
                _write_run_json(run_dir, "review_context.json", validation_context)
        if validation_ran and int(controller_result.get("return_code") or 0) != 0:
            controller_decision = "REJECTED"
            controller_summary = controller_summary or "controller agent exited non-zero"
        if validation_ran and controller_decision not in {"ACCEPTED", "REJECTED"}:
            controller_decision = "REJECTED"
            controller_summary = controller_summary or "controller did not emit a valid decision"
        if not validation_ran:
            controller_summary = hard_reason or "candidate was not validated"

        decision = implement_validation.acceptance_decision(
            contract_payload=contract_payload,
            incumbent_validation=incumbent_validation,
            candidate_validation=candidate_validation,
            controller_decision=controller_decision,
            hard_reject=hard_reject,
            hard_reason=hard_reason,
        )
        event_description = description
        summary = controller_summary or str(decision.get("reason") or "")
        if summary:
            event_description = f"{description} [{summary}]"
        post_iteration_untracked = set(implement_git.list_untracked_paths(project_root))
        cleanup_untracked = sorted(post_iteration_untracked - pre_iteration_untracked)

        if bool(decision.get("accepted")) and candidate_commit:
            accepted_count += 1
            consecutive_rejections = 0
            state_payload["incumbent_commit"] = candidate_commit
            state_payload["incumbent_validation"] = copy.deepcopy(candidate_validation)
            _maybe_lock_api(
                state_payload,
                contract_payload=contract_payload,
                validation=candidate_validation,
                commit_sha=candidate_commit,
                iteration=iteration,
            )
            final_complete = bool(decision.get("final_complete"))
            state_payload["complete"] = final_complete
            implement_git.cleanup_paths(project_root, cleanup_untracked)
            status = "complete" if final_complete else "accepted_partial"
            implement_state.append_result(
                results_path,
                iteration=iteration,
                commit=candidate_commit[:12],
                status=status,
                score=implement_validation.validation_score(candidate_validation),
                complete=implement_validation.validation_complete(candidate_validation),
                description=event_description,
            )
        else:
            implement_git.reset_to_commit(
                project_root,
                commit_sha=start_sha,
                cleanup_paths_list=cleanup_untracked,
            )
            rejected_count += 1
            consecutive_rejections += 1
            status = str(decision.get("status") or "rejected")
            recorded_commit = candidate_commit[:12] if candidate_commit else start_sha[:12]
            implement_state.append_result(
                results_path,
                iteration=iteration,
                commit=recorded_commit,
                status=status,
                score=implement_validation.validation_score(candidate_validation),
                complete=implement_validation.validation_complete(candidate_validation),
                description=event_description,
            )

        state_payload["iteration"] = iteration
        state_payload["accepted_count"] = accepted_count
        state_payload["rejected_count"] = rejected_count
        state_payload["consecutive_rejections"] = consecutive_rejections
        implement_state.write_state(state_path, state_payload)

    incumbent_validation = (
        state_payload.get("incumbent_validation")
        if isinstance(state_payload.get("incumbent_validation"), dict)
        else {}
    )
    return {
        "status": "completed",
        "package_id": package_id,
        "branch": branch_name,
        "branch_info": branch_info,
        "git_repo_initialized": git_repo_initialized,
        "state_path": str(state_path),
        "goal_path": str(goal_path),
        "contract_path": str(contract_path),
        "memory_path": str(memory_path),
        "results_path": str(results_path),
        "incumbent_commit": str(state_payload.get("incumbent_commit") or ""),
        "incumbent_score": implement_validation.validation_score(incumbent_validation),
        "complete": bool(state_payload.get("complete", False)),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
    }
