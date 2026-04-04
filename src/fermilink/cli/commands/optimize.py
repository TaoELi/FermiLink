from __future__ import annotations

import argparse
from pathlib import Path

from fermilink.cli import optimize_controller, optimize_goal, optimize_state


def _cli():
    from fermilink import cli

    return cli


def _normalize_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _looks_like_quick_prompt(target: str) -> bool:
    lowered = target.lower()
    if lowered.endswith((".md", ".markdown", ".txt")):
        return True
    path = Path(target).expanduser()
    return path.is_file()


def _is_goal_markdown_file(target: str) -> bool:
    """Return True if *target* points to a goal-structured markdown file."""

    path = Path(target).expanduser()
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return optimize_goal.is_goal_markdown(text)


def _resolve_optimize_mode(args: argparse.Namespace) -> str:
    cli = _cli()
    target = _normalize_text(getattr(args, "package_id", None))
    project_path = _normalize_text(getattr(args, "project_path", None))
    benchmark = _normalize_text(getattr(args, "benchmark", None))
    explicit_goal = bool(getattr(args, "goal", False))

    if target.lower() == "status":
        return "status"

    if project_path:
        if not target:
            raise cli.PackageError(
                "Optimize expert mode requires `<package_id> <project_path>`."
            )
        return "expert"

    if target and _looks_like_quick_prompt(target):
        # Goal mode: explicit --goal flag or auto-detected goal structure
        if explicit_goal or _is_goal_markdown_file(target):
            return "goal"
        return "quick"

    if benchmark and not project_path:
        raise cli.PackageError(
            "Optimize expert mode requires `<package_id> <project_path>` when "
            "`--benchmark` is provided."
        )

    if not target:
        raise cli.PackageError(
            "Optimize requires one of: `status`, `<goal.md>`, `<prompt.md>`, or "
            "`<package_id> <project_path> --benchmark <path>`."
        )

    raise cli.PackageError(
        "Unable to infer optimize mode from arguments. Use `fermilink optimize "
        "status`, `fermilink optimize goal.md`, `fermilink optimize prompt.md`, "
        "or expert mode "
        "`fermilink optimize <package_id> <project_path> --benchmark <path>`."
    )


def _emit_status(args: argparse.Namespace, payload: dict[str, object]) -> int:
    cli = _cli()
    lines = [
        f"Optimize status: {payload.get('project_root')}",
        (
            f"Run lock: {payload.get('run_lock_status')} "
            f"(pid={payload.get('run_lock_pid')}, started={payload.get('run_lock_started_at')})"
        ),
        (
            f"Progress: iteration={payload.get('iteration')}, accepted="
            f"{payload.get('accepted_count')}, rejected={payload.get('rejected_count')}, "
            f"consecutive_rejections={payload.get('consecutive_rejections')}"
        ),
        (
            f"Incumbent: {payload.get('incumbent_commit')} "
            f"({payload.get('primary_metric_name')}={payload.get('incumbent_primary_metric')})"
        ),
        (
            f"Benchmark runtime: mode={payload.get('runtime_mode')}, "
            f"launcher={payload.get('launcher_status')}"
        ),
        "Recent results:",
        str(payload.get("recent_results") or "(no results yet)"),
    ]
    cli._emit_output(args, payload, lines)
    return 0


def _emit_campaign(args: argparse.Namespace, payload: dict[str, object]) -> int:
    cli = _cli()
    lines = [
        (
            f"Optimize campaign for '{payload.get('package_id')}' on branch "
            f"{payload.get('branch')}."
        ),
        (
            f"Incumbent commit: {payload.get('incumbent_commit')} "
            f"({payload.get('primary_metric_name')}="
            f"{payload.get('incumbent_primary_metric')})."
        ),
        (
            "Results: "
            f"{payload.get('accepted_count')} accepted, "
            f"{payload.get('rejected_count')} rejected."
        ),
    ]
    scaffold_benchmark = str(payload.get("scaffold_benchmark_path") or "").strip()
    run_script_path = str(payload.get("generated_run_script_path") or "").strip()
    scaffold_command_source = str(payload.get("scaffold_command_source") or "").strip()
    reference_examples = payload.get("scaffold_reference_examples")
    reference_examples = (
        reference_examples if isinstance(reference_examples, dict) else {}
    )
    if scaffold_benchmark:
        lines.append(f"Quick benchmark: {scaffold_benchmark}")
    if scaffold_command_source:
        lines.append(f"Case command source: {scaffold_command_source}")
    reference_benchmark = str(reference_examples.get("benchmark") or "").strip()
    reference_runner = str(reference_examples.get("runner") or "").strip()
    if reference_benchmark or reference_runner:
        lines.append(
            "Reference templates: "
            f"benchmark={reference_benchmark or 'n/a'}, "
            f"runner={reference_runner or 'n/a'}"
        )
    if run_script_path:
        lines.append(f"Generated run script: {run_script_path}")
    cli._emit_output(args, payload, lines)
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    """Execute optimize expert mode, quick mode, or campaign status."""

    cli = _cli()
    if bool(getattr(args, "baseline_only", False)) and bool(
        getattr(args, "plan_only", False)
    ):
        raise cli.PackageError("Cannot combine --baseline-only with --plan-only.")

    mode = _resolve_optimize_mode(args)
    lock_path: Path | None = None
    if mode == "expert":
        project_path = _normalize_text(getattr(args, "project_path", None))
        if project_path:
            lock_path = optimize_state.run_lock_path(
                cli._resolve_project_path(project_path)
            )
    elif mode in ("quick", "goal"):
        lock_path = optimize_state.run_lock_path(Path.cwd().resolve())
    try:
        if mode == "status":
            payload = optimize_controller.read_campaign_status(args)
            return _emit_status(args, payload)
        if mode == "goal":
            payload = optimize_controller.run_goal_campaign(args)
            return _emit_campaign(args, payload)
        if mode == "quick":
            payload = optimize_controller.run_quick_campaign(args)
            return _emit_campaign(args, payload)
        payload = optimize_controller.run_campaign(args)
    except KeyboardInterrupt:
        cli._print_tagged("optimize", "interrupted by user.", stderr=True)
        return 130
    finally:
        if isinstance(lock_path, Path):
            optimize_state.clear_run_lock(lock_path)

    return _emit_campaign(args, payload)
