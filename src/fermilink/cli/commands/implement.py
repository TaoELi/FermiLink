from __future__ import annotations

import argparse
from pathlib import Path


def _cli():
    from fermilink import cli

    return cli


def _implement_controller():
    from fermilink.implement import main as implement_controller

    return implement_controller


def _implement_state():
    from fermilink.implement import state as implement_state

    return implement_state


def _normalize_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _project_root_from_args(args: argparse.Namespace) -> Path:
    raw = _normalize_text(getattr(args, "project_root", None)) or "."
    return Path(raw).expanduser().resolve()


def _resolve_implement_mode(args: argparse.Namespace) -> str:
    cli = _cli()
    target = _normalize_text(getattr(args, "goal", None))
    goal_path = _normalize_text(getattr(args, "goal_path", None))

    if target.lower() == "status":
        if goal_path:
            raise cli.PackageError(
                "`fermilink implement status` does not accept a goal path."
            )
        return "status"
    if target.lower() == "run":
        if not goal_path:
            raise cli.PackageError(
                "`fermilink implement run` requires a goal markdown path."
            )
        args.goal = goal_path
        args.goal_path = None
        return "run"
    if goal_path:
        raise cli.PackageError(
            "Use `fermilink implement <goal.md>` or "
            "`fermilink implement run <goal.md>`."
        )
    if not target:
        raise cli.PackageError(
            "Implement requires one of: `status`, `<goal.md>`, or `run <goal.md>`."
        )
    return "run"


def _emit_status(args: argparse.Namespace, payload: dict[str, object]) -> int:
    cli = _cli()
    lines = [
        f"Implement status: {payload.get('project_root')}",
        (
            f"Run lock: {payload.get('run_lock_status')} "
            f"(pid={payload.get('run_lock_pid')})"
        ),
        (
            f"Progress: iteration={payload.get('iteration')}, accepted="
            f"{payload.get('accepted_count')}, rejected={payload.get('rejected_count')}"
        ),
        (
            f"Incumbent: {payload.get('incumbent_commit')} "
            f"(score={payload.get('incumbent_score')}, "
            f"complete={payload.get('complete')})"
        ),
        "Recent results:",
        str(payload.get("recent_results") or "(no results yet)"),
    ]
    cli._emit_output(args, payload, lines)
    return 0


def _emit_campaign(args: argparse.Namespace, payload: dict[str, object]) -> int:
    cli = _cli()
    status = str(payload.get("status") or "completed")
    lines = [
        (
            f"Implement campaign for '{payload.get('package_id')}' on branch "
            f"{payload.get('branch')}."
        ),
        f"Status: {status}.",
    ]
    plan_path = str(payload.get("plan_path") or "").strip()
    contract_path = str(payload.get("contract_path") or "").strip()
    if contract_path:
        lines.append(f"Contract: {contract_path}")
    if plan_path:
        lines.append(f"Plan: {plan_path}")
    if status != "planned":
        lines.extend(
            [
                (
                    f"Incumbent commit: {payload.get('incumbent_commit')} "
                    f"(score={payload.get('incumbent_score')}, "
                    f"complete={payload.get('complete')})."
                ),
                (
                    "Results: "
                    f"{payload.get('accepted_count')} accepted, "
                    f"{payload.get('rejected_count')} rejected."
                ),
            ]
        )
    results_path = str(payload.get("results_path") or "").strip()
    if results_path:
        lines.append(f"Results ledger: {results_path}")
    cli._emit_output(args, payload, lines)
    return 0


def cmd_implement(args: argparse.Namespace) -> int:
    """Execute implement goal mode or campaign status."""

    cli = _cli()
    raw_worker_model = getattr(args, "worker_model", None)
    if raw_worker_model is not None:
        worker_model_text = str(raw_worker_model).strip()
        if not worker_model_text:
            raise cli.PackageError("--worker-model cannot be empty.")
        args.worker_model = worker_model_text
    if bool(getattr(args, "baseline_only", False)) and bool(
        getattr(args, "plan_only", False)
    ):
        raise cli.PackageError("Cannot combine --baseline-only with --plan-only.")

    mode = _resolve_implement_mode(args)
    lock_path: Path | None = None
    if mode == "run":
        lock_path = _implement_state().run_lock_path(_project_root_from_args(args))
    try:
        if mode == "status":
            payload = _implement_controller().read_campaign_status(args)
            return _emit_status(args, payload)
        payload = _implement_controller().run_goal_campaign(args)
    except KeyboardInterrupt:
        cli._print_tagged("implement", "interrupted by user.", stderr=True)
        return 130
    finally:
        if isinstance(lock_path, Path):
            _implement_state().clear_run_lock(lock_path)

    return _emit_campaign(args, payload)
