from __future__ import annotations

import argparse

from fermilink.cli import optimize_controller


def _cli():
    from fermilink import cli

    return cli


def cmd_optimize(args: argparse.Namespace) -> int:
    """Execute the standalone optimize controller."""

    cli = _cli()
    if bool(getattr(args, "baseline_only", False)) and bool(
        getattr(args, "plan_only", False)
    ):
        raise cli.PackageError("Cannot combine --baseline-only with --plan-only.")

    try:
        payload = optimize_controller.run_campaign(args)
    except KeyboardInterrupt:
        cli._print_tagged("optimize", "interrupted by user.", stderr=True)
        return 130

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
    cli._emit_output(args, payload, lines)
    return 0
