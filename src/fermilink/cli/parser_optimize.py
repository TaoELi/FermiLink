from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_optimize_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    add_json_option: Callable[[argparse.ArgumentParser], None],
    cmd_optimize: CommandHandler,
) -> None:
    """Register the standalone optimize command parser."""

    optimize_parser = subparsers.add_parser(
        "optimize",
        help=(
            "Run an optimization-only controller in a scientific package source "
            "tree using a fixed benchmark contract, static skills, and "
            "accept/reject git iteration."
        ),
    )
    add_json_option(optimize_parser)
    optimize_parser.add_argument("package_id", help="Scientific package id.")
    optimize_parser.add_argument(
        "project_path",
        help="Local scientific package source tree to optimize.",
    )
    optimize_parser.add_argument(
        "--benchmark",
        required=True,
        help="Benchmark YAML contract path.",
    )
    optimize_parser.add_argument(
        "--program",
        default=None,
        help=(
            "Optional optimize-program markdown path. Defaults to "
            "`.fermilink-optimize/program.md` inside the target project."
        ),
    )
    optimize_parser.add_argument(
        "--skills-source",
        choices=("auto", "existing", "channel", "compile"),
        default="auto",
        help=(
            "How to ensure `skills/` exists before optimization: `existing`, "
            "`channel`, `compile`, or `auto` (default)."
        ),
    )
    optimize_parser.add_argument(
        "--channel",
        default="skilled-scipkg",
        help="Curated source channel for `--skills-source channel`.",
    )
    optimize_parser.add_argument(
        "--version",
        dest="version_id",
        default=None,
        help="Optional curated package version id for `--skills-source channel`.",
    )
    optimize_parser.add_argument(
        "--require-verified",
        action="store_true",
        help="Require a verified curated package version when using channel skills.",
    )
    optimize_parser.add_argument(
        "--branch",
        default=None,
        help=(
            "Optimization branch name. Defaults to the benchmark campaign branch "
            "or `fermilink-optimize/<package_id>`."
        ),
    )
    optimize_parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Run only the incumbent baseline benchmark and exit.",
    )
    optimize_parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Initialize optimize state and validate inputs without running the loop.",
    )
    optimize_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an existing optimization campaign from local state.",
    )
    optimize_parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Override the benchmark campaign iteration cap.",
    )
    optimize_parser.add_argument(
        "--stop-on-consecutive-rejections",
        type=int,
        default=None,
        help="Override the benchmark rejection stop threshold.",
    )
    optimize_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="Override the benchmark timeout for each run.",
    )
    optimize_parser.add_argument(
        "--forever",
        action="store_true",
        help="Run indefinitely until interrupted instead of stopping at the cap.",
    )
    optimize_parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow startup from a dirty git working tree.",
    )
    optimize_parser.add_argument(
        "--sandbox",
        default=None,
        help="Optional provider sandbox override for optimize agent turns.",
    )
    optimize_parser.set_defaults(func=cmd_optimize)
