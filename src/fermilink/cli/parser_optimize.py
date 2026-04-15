from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_optimize_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    add_json_option: Callable[[argparse.ArgumentParser], None],
    cmd_optimize: CommandHandler,
    supported_providers: tuple[str, ...],
) -> None:
    """Register the standalone optimize command parser."""

    optimize_parser = subparsers.add_parser(
        "optimize",
        help=(
            "Run optimize expert mode (`<package_id> <project_path> --benchmark ...`), "
            "goal mode (`goal.md`), or status (`status`)."
        ),
    )
    add_json_option(optimize_parser)
    optimize_parser.add_argument(
        "package_id",
        nargs="?",
        help=(
            "Expert mode package id, goal markdown path (for example goal.md), "
            "or literal `status`."
        ),
    )
    optimize_parser.add_argument(
        "project_path",
        nargs="?",
        help="Local scientific package source tree to optimize.",
    )
    optimize_parser.add_argument(
        "--benchmark",
        default=None,
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
        "--tail",
        type=int,
        default=30,
        help=(
            "When using `fermilink optimize status`, show this many recent results "
            "rows (default: 30)."
        ),
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
        "--worker-max-iterations",
        type=int,
        default=None,
        help=(
            "Override the inner optimize-worker loop iteration cap. Defaults to "
            "the benchmark worker.max_iterations setting or an internal default."
        ),
    )
    optimize_parser.add_argument(
        "--worker-provider",
        choices=supported_providers,
        default=None,
        help=(
            "Override only the optimize worker provider "
            f"({', '.join(supported_providers)})."
        ),
    )
    optimize_parser.add_argument(
        "--worker-model",
        default=None,
        help=(
            "Override only the optimize worker model (for example "
            "`gemini-2.5-pro`)."
        ),
    )
    optimize_parser.add_argument(
        "--worker-wait-seconds",
        type=float,
        default=None,
        help=(
            "Override inner worker-loop fallback sleep and pid/slurm poll interval "
            "seconds. Defaults to benchmark worker.wait_seconds or loop defaults."
        ),
    )
    optimize_parser.add_argument(
        "--worker-max-wait-seconds",
        type=float,
        default=None,
        help=(
            "Override the per-worker-iteration hard cap on pid/slurm polling and "
            "agent wait hints."
        ),
    )
    optimize_parser.add_argument(
        "--worker-pid-stall-seconds",
        type=float,
        default=None,
        help=(
            "Override the worker-loop local pid CPU-progress stall threshold "
            "before continuing for debug/resubmit."
        ),
    )
    optimize_parser.add_argument(
        "--hpc-profile",
        default=None,
        help=(
            "Optional JSON file with `slurm_default_partition`, `slurm_defaults`, "
            "and `slurm_resource_policy`; when set, the optimize worker-loop prompt "
            "is constrained to this HPC profile and `runtime.mode=submit_poll` "
            "benchmarks can auto-plan/reuse adaptive submission launchers."
        ),
    )
    optimize_parser.add_argument(
        "--goal",
        action="store_true",
        help=(
            "Treat the input markdown as a goal specification for goal-driven "
            "optimization.  FermiLink will analyse the source code and "
            "auto-generate benchmark files before starting the campaign.  "
            "When omitted, goal mode is auto-detected from the markdown "
            "structure."
        ),
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
