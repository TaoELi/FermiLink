from __future__ import annotations

import argparse
import importlib


def _cli():
    from fermilink import cli

    return cli


def cmd_design(args: argparse.Namespace) -> int:
    """Execute standalone Phase 1 design mode via the CLI surface."""

    cli = _cli()
    design_main = importlib.import_module("fermilink.design.main")

    payload = design_main.run_pipeline(args)
    lines = design_main.build_summary_lines(payload)
    cli._emit_output(args, payload, lines)
    return 0
