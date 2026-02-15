from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fermilink.cli.workflow_prompts import (
    LOOP_MEMORY_DIRNAME,
    LOOP_MEMORY_FILENAME,
    LOOP_WAIT_TOKEN_RE,
    REPRODUCE_ARCHIVE_DIRNAME,
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
    WORKFLOW_DRY_RUN_AUDITOR_PROMPT_SUFFIX,
    WORKFLOW_DRY_RUN_LOOP_PREAMBLE,
    WORKFLOW_DRY_RUN_PLANNER_PROMPT_SUFFIX,
    WORKFLOW_REPORT_AUDITOR_PROMPT_PREFIX,
    WORKFLOW_REPORT_FILENAME,
    WORKFLOW_REPORT_GENERATOR_PROMPT_PREFIX,
    WORKFLOW_SUMMARIES_DIRNAME,
)


def _cli():
    from fermilink import cli

    return cli


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    cli = _cli()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        raise cli.PackageError(f"Failed to write file: {path}: {exc}") from exc


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
    task: dict[str, object], *, dry_run: bool = False
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
    if dry_run:
        lines.extend(
            [
                "",
                "## Dry-run deliverables",
                "- Prepare simulation input/config files only (do not run simulations).",
                "- Prepare post-processing scripts for expected simulation outputs.",
                "- Prepare plotting scripts for the target figures.",
                "- Create or update README.md with exact future simulation commands and validation steps.",
            ]
        )
    lines.extend(
        [
            "",
            "## Execution notes",
            "- Keep scripts, data, and plots reproducible.",
            "- Save run details and blockers in projects/memory.md.",
            "- Do not execute full simulations in dry-run mode."
            if dry_run
            else "- Execute simulation work only when required by the task plan.",
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


def _normalize_automation_plan(
    raw_plan: object,
    *,
    source_description: str,
    dry_run: bool = False,
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
        if not prompt_markdown:
            prompt_markdown = _render_reproduce_task_prompt(
                normalized_task, dry_run=dry_run
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
    dry_run: bool = False,
) -> dict[str, object]:
    return _normalize_automation_plan(
        raw_plan,
        source_description=source_description,
        dry_run=dry_run,
    )


def _normalize_research_plan(
    raw_plan: object,
    *,
    source_description: str,
    dry_run: bool = False,
) -> dict[str, object]:
    return _normalize_automation_plan(
        raw_plan,
        source_description=source_description,
        dry_run=dry_run,
    )


def _run_reproduce_exec_turn(
    *,
    repo_dir: Path,
    prompt: str,
    requested_package_id: str | None,
    sandbox_override: str | None,
    codex_bin: str,
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
    if isinstance(sandbox_override, str) and sandbox_override.strip():
        sandbox_policy = "enforce"
        sandbox_mode = sandbox_override.strip()

    provider_bin = codex_bin if provider == "codex" else None
    selection = cli._resolve_exec_package_selection(
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
            codex_bin=provider_bin,
            provider=provider,
            sandbox_policy=sandbox_policy,
        )
    finally:
        cli._cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)

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
    dry_run: bool = False,
) -> dict[str, object]:
    """Generate + audit a workflow plan used by both `reproduce` and `research`."""

    cli = _cli()
    planner_prompt_parts = [
        f"{planner_prompt_prefix}\n\n"
        f"Paper source: {source_description}\n\n"
        "Paper content / request:\n"
        f"{source_text.strip()}\n"
    ]
    if dry_run:
        planner_prompt_parts.append(f"\n{WORKFLOW_DRY_RUN_PLANNER_PROMPT_SUFFIX}\n")
    planner_prompt = "".join(planner_prompt_parts)
    planner_plan: dict[str, object] | None = None
    for attempt in range(1, planner_max_tries + 1):
        cli._print_tagged(log_tag, f"planner attempt {attempt}/{planner_max_tries}")
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=planner_prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            codex_bin=codex_bin,
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
                dry_run=dry_run,
            )
        except cli.PackageError as exc:
            cli._print_tagged(log_tag, f"planner response invalid: {exc}", stderr=True)
            continue
        break
    if planner_plan is None:
        raise cli.PackageError(
            f"Unable to generate a valid {log_tag} plan from planner response."
        )

    audited_plan: dict[str, object] | None = None
    auditor_prompt_parts = [
        f"{auditor_prompt_prefix}\n\n"
        f"Paper source: {source_description}\n\n"
        "Original paper content / request:\n"
        f"{source_text.strip()}\n\n"
        "Candidate plan JSON:\n"
        f"{json.dumps(planner_plan, indent=2)}\n"
    ]
    if dry_run:
        auditor_prompt_parts.append(f"\n{WORKFLOW_DRY_RUN_AUDITOR_PROMPT_SUFFIX}\n")
    auditor_prompt = "".join(auditor_prompt_parts)
    for attempt in range(1, auditor_max_tries + 1):
        cli._print_tagged(log_tag, f"auditor attempt {attempt}/{auditor_max_tries}")
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=auditor_prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            codex_bin=codex_bin,
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
                dry_run=dry_run,
            )
        except cli.PackageError as exc:
            cli._print_tagged(log_tag, f"auditor response invalid: {exc}", stderr=True)
            continue
        break
    if audited_plan is None:
        raise cli.PackageError(
            f"Unable to generate a valid {log_tag} plan from auditor response."
        )
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
    dry_run: bool = False,
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
        dry_run=dry_run,
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
    dry_run: bool = False,
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
        dry_run=dry_run,
    )


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
            return memory_path

    started_at = _utc_now_z()
    source_line = f"- prompt_source: {prompt_file}\n" if prompt_file else ""
    context_block = ""
    if isinstance(workflow_context_lines, list):
        normalized_context = [
            str(line).rstrip()
            for line in workflow_context_lines
            if isinstance(line, str) and line.strip()
        ]
        if normalized_context:
            context_block = (
                "\n" "## Workflow context\n" + "\n".join(normalized_context) + "\n"
            )
    initial = (
        "# FermiLink Loop Memory\n"
        "\n"
        f"- started_at_utc: {started_at}\n"
        f"{source_line}"
        "\n"
        "## Original request\n"
        f"{user_prompt.strip()}\n"
        f"{context_block}"
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
        raise cli.PackageError(
            f"Failed to create loop memory file: {memory_path}: {exc}"
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
        if lowered in {"initialized", "(fill in a small checklist plan)"}:
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
    dry_run: bool = False,
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
        dry_run=dry_run,
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


def _validate_report_stage_artifacts(
    *,
    stage_label: str,
    report_path: Path,
    summary_paths: list[Path],
    before_report_signature: tuple[int, int, str] | None,
    before_summary_signatures: dict[str, tuple[int, int, str] | None],
    required_marker: str,
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

    artifacts_updated = report_signature != before_report_signature
    if not artifacts_updated:
        for path_text, after_signature in summary_signatures.items():
            if after_signature != before_summary_signatures.get(path_text):
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
    codex_bin: str,
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
    generation_marker = (
        f"<!-- FERMILINK_REPORT_STAGE:generated run_id={run_id} -->"
    )
    audit_marker = f"<!-- FERMILINK_REPORT_STAGE:audited run_id={run_id} -->"

    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(repo_dir))
        except ValueError:
            return str(path)

    summary_paths: list[Path] = []
    task_lines: list[str] = []
    for index, task in enumerate(tasks_state, start=1):
        task_id = (
            str(task.get("id") or f"task_{index:03d}").strip() or f"task_{index:03d}"
        )
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
        "6) Include this exact marker line anywhere in the report:\n"
        f"{generation_marker}\n"
    )

    generation_failure_reason = ""
    for attempt in range(1, 3):
        cli._print_tagged(workflow_name, f"report generation attempt {attempt}/2")
        before_report_signature = _capture_file_signature(report_path)
        before_summary_signatures = _capture_signatures(summary_paths)
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=generator_prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            codex_bin=codex_bin,
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
            )
            if generation_validation_error is None:
                break
            generation_failure_reason = generation_validation_error
            cli._print_tagged(workflow_name, generation_validation_error, stderr=True)
        else:
            generation_failure_reason = (
                f"{workflow_name.title()} report generation failed with exit code {return_code}."
            )
        if attempt == 2:
            raise cli.PackageError(
                generation_failure_reason
                or (
                    f"{workflow_name.title()} report generation failed "
                    f"(exit code {return_code})."
                )
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
        "5) Ensure the report contains this exact marker line:\n"
        f"{audit_marker}\n"
    )
    audit_failure_reason = ""
    for attempt in range(1, 3):
        cli._print_tagged(workflow_name, f"report audit attempt {attempt}/2")
        before_report_signature = _capture_file_signature(report_path)
        before_summary_signatures = _capture_signatures(summary_paths)
        run_result = _run_reproduce_exec_turn(
            repo_dir=repo_dir,
            prompt=auditor_prompt,
            requested_package_id=requested_package_id,
            sandbox_override=sandbox_override,
            codex_bin=codex_bin,
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
            )
            if audit_validation_error is None:
                break
            audit_failure_reason = audit_validation_error
            cli._print_tagged(workflow_name, audit_validation_error, stderr=True)
        else:
            audit_failure_reason = (
                f"{workflow_name.title()} report audit failed with exit code {return_code}."
            )
        if attempt == 2:
            raise cli.PackageError(
                audit_failure_reason
                or f"{workflow_name.title()} report audit failed (exit code {return_code})."
            )

    return {
        "report_path": str(report_path),
        "summaries_root": str(summaries_root),
        "summary_count": len(summary_paths),
    }


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
    dry_run = bool(getattr(args, "dry_run", False))
    if plan_only and report_only:
        raise cli.PackageError("Cannot combine --plan-only and --report-only.")
    if report_only and skip_report:
        raise cli.PackageError("Cannot combine --report-only and --skip-report.")

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

    if run_dir is None or state is None:
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
            "dry_run": dry_run,
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
        cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)
        try:
            latest_path.write_text(run_id + "\n", encoding="utf-8")
        except OSError as exc:
            raise cli.PackageError(
                f"Failed to write latest {workflow_name} run file: {latest_path}: {exc}"
            ) from exc
    else:
        cli._print_tagged(workflow_name, f"resuming run: {run_dir.name}")
    state_dry_run = bool(state.get("dry_run", False))
    if state_dry_run != dry_run:
        expected_mode = "--dry-run" if state_dry_run else "without --dry-run"
        current_mode = "--dry-run" if dry_run else "without --dry-run"
        raise cli.PackageError(
            f"Run {run_dir.name} was created {expected_mode}; current invocation is "
            f"{current_mode}. Use --restart or rerun with matching dry-run mode."
        )
    state["dry_run"] = state_dry_run

    prompts_dir = run_dir / cli.REPRODUCE_PROMPTS_DIRNAME
    logs_dir = run_dir / cli.REPRODUCE_LOGS_DIRNAME
    archive_dir = run_dir / cli.REPRODUCE_ARCHIVE_DIRNAME
    prompts_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    cli._print_tagged(workflow_name, f"run dir: {run_dir.relative_to(repo_dir)}")

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
            source_text=user_prompt,
            source_description=source_description,
            requested_package_id=args.package_id,
            sandbox_override=args.sandbox,
            codex_bin=args.codex_bin,
            planner_max_tries=planner_max_tries,
            auditor_max_tries=auditor_max_tries,
            dry_run=dry_run,
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
        run_dir=run_dir,
        state=state,
        source_description=source_description,
        workflow_name=workflow_name,
        dry_run=dry_run,
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
    task_runs_state = state.get("task_runs")
    if not isinstance(task_runs_state, dict):
        task_runs_state = {}
        state["task_runs"] = task_runs_state

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
                codex_bin=args.codex_bin,
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
        report_path = str(report_info.get("report_path") or "").strip()
        if report_path:
            try:
                relative_report = str(Path(report_path).relative_to(repo_dir))
            except Exception:
                relative_report = report_path
            cli._print_tagged(workflow_name, f"report: {relative_report}")
        return 0

    state["status"] = "running_tasks"
    state["updated_at_utc"] = cli._utc_now_z()
    cli._write_json_atomic(run_dir / cli.REPRODUCE_STATE_FILENAME, state)

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
                        codex_bin=args.codex_bin,
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
                cli._print_tagged(workflow_name, f"report: {relative_report}")
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
        plan_path = run_dir / REPRODUCE_PLAN_FILENAME
        state_path = run_dir / REPRODUCE_STATE_FILENAME
        archived_memory_paths = sorted(archive_dir.glob("memory_*.md"))
        latest_archived_memory_path = (
            archived_memory_paths[-1] if archived_memory_paths else None
        )

        def _memory_relpath(path: Path) -> str:
            try:
                return str(path.relative_to(repo_dir))
            except ValueError:
                return str(path)

        workflow_prompt_preamble_lines = [
            "Workflow preflight (research/reproduce mode):",
            "- Before acting, read `projects/memory.md`.",
            f"- Before acting, read `{_memory_relpath(plan_path)}`.",
        ]
        if latest_archived_memory_path is not None:
            workflow_prompt_preamble_lines.append(
                (
                    "- Before acting, read latest archived memory "
                    f"`{_memory_relpath(latest_archived_memory_path)}`."
                )
            )
        else:
            workflow_prompt_preamble_lines.append(
                "- No archived memory exists yet for this run."
            )
        if dry_run:
            workflow_prompt_preamble_lines.extend(["", WORKFLOW_DRY_RUN_LOOP_PREAMBLE])
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
                "- dry_run: true (prepare artifacts only; do not execute simulations)"
                if dry_run
                else "- dry_run: false (normal execution)",
                f"- plan_json: {_memory_relpath(plan_path)} (overall workflow task plan)",
                f"- state_json: {_memory_relpath(state_path)} (workflow progress and task status)",
            ]
            if archived_memory_paths:
                workflow_context_lines.append(
                    "- previous_memory_archives: snapshots from completed prior task runs"
                )
                for archived_path in archived_memory_paths[-20:]:
                    workflow_context_lines.append(
                        f"  - {_memory_relpath(archived_path)} (prior run memory snapshot)"
                    )
            else:
                workflow_context_lines.append("- previous_memory_archives: none yet")
            if latest_archived_memory_path is not None:
                workflow_context_lines.append(
                    (
                        "- latest_memory_archive: "
                        f"{_memory_relpath(latest_archived_memory_path)} "
                        "(most recent completed task run memory)"
                    )
                )
                handoff_summary = _summarize_archived_memory(
                    latest_archived_memory_path
                )
                if handoff_summary:
                    workflow_context_lines.append(
                        "- handoff_summary_from_latest_archive: quick continuity notes"
                    )
                    for item in handoff_summary:
                        workflow_context_lines.append(f"  - {item}")
            cli._ensure_loop_memory(
                repo_dir=repo_dir,
                user_prompt=task_prompt_text,
                prompt_file=str(prompt_path),
                overwrite=True,
                workflow_context_lines=workflow_context_lines,
            )

        run_number = task_runs + 1
        cli._print_tagged(
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
            workflow_prompt_preamble=workflow_prompt_preamble,
        )
        started_at = cli._utc_now_z()
        code = cli._cmd_loop(loop_args)
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
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

        if loop_status == "done":
            cli._archive_loop_memory(
                repo_dir=repo_dir,
                archive_dir=archive_dir,
                task_id=task_id,
                run_count=run_number,
            )
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
            state["status"] = "failed"
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
    return cmd_plan_workflow(
        args,
        workflow_name="reproduce",
        runs_dir_name=cli.REPRODUCE_RUNS_DIR,
        generate_plan=cli._generate_reproduce_plan,
    )


def cmd_research(args: argparse.Namespace) -> int:
    """Run multi-task autonomous research orchestration.

    Dependency note:
    - `research` delegates task execution to `loop`.
    - `loop` depends on the same execution/routing stack used by `exec`.
    """

    cli = _cli()
    return cmd_plan_workflow(
        args,
        workflow_name="research",
        runs_dir_name=cli.RESEARCH_RUNS_DIR,
        generate_plan=cli._generate_research_plan,
    )
