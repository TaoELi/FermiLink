from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_implement_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    add_json_option: Callable[[argparse.ArgumentParser], None],
    cmd_implement: CommandHandler,
    supported_providers: tuple[str, ...],
) -> None:
    """Register the standalone implement command parser."""

    implement_parser = subparsers.add_parser(
        "implement",
        help=(
            "Run goal-driven implementation mode (`goal.md` or `run goal.md`) "
            "or status (`status`)."
        ),
    )
    add_json_option(implement_parser)
    implement_parser.add_argument(
        "goal",
        nargs="?",
        help="Implementation goal markdown path, literal `run`, or literal `status`.",
    )
    implement_parser.add_argument(
        "goal_path",
        nargs="?",
        help="Implementation goal markdown path when using `fermilink implement run`.",
    )
    implement_parser.add_argument(
        "--project-root",
        default=".",
        help="Local scientific package source tree to modify (default: current directory).",
    )
    implement_parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Initialize implement state and generated contract without running agents.",
    )
    implement_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing `.fermilink-implement` contract/state.",
    )
    implement_parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Run only incumbent validation and exit.",
    )
    implement_parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow startup from a dirty git working tree.",
    )
    implement_parser.add_argument(
        "--branch",
        default=None,
        help=(
            "Implementation branch name. Defaults to the contract campaign branch "
            "or `fermilink-implement/<package_id>`."
        ),
    )
    implement_parser.add_argument(
        "--tail",
        type=int,
        default=30,
        help=(
            "When using `fermilink implement status`, show this many recent "
            "results rows (default: 30)."
        ),
    )
    implement_parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Override the implementation campaign iteration cap.",
    )
    implement_parser.add_argument(
        "--stop-on-consecutive-rejections",
        type=int,
        default=None,
        help="Override the consecutive rejection stop threshold.",
    )
    implement_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="Override validation and pre-command timeout seconds.",
    )
    implement_parser.add_argument(
        "--worker-max-iterations",
        type=int,
        default=None,
        help="Override the inner implement-worker loop iteration cap.",
    )
    implement_parser.add_argument(
        "--worker-provider",
        choices=supported_providers,
        default=None,
        help=(
            "Override only the implement worker provider "
            f"({', '.join(supported_providers)})."
        ),
    )
    implement_parser.add_argument(
        "--worker-model",
        default=None,
        help="Override only the implement worker model.",
    )
    implement_parser.add_argument(
        "--worker-wait-seconds",
        type=float,
        default=None,
        help="Override inner worker-loop fallback sleep and pid/slurm poll interval.",
    )
    implement_parser.add_argument(
        "--worker-max-wait-seconds",
        type=float,
        default=None,
        help="Override the per-worker-iteration hard cap on pid/slurm polling.",
    )
    implement_parser.add_argument(
        "--worker-pid-stall-seconds",
        type=float,
        default=None,
        help="Override the worker-loop local pid CPU-progress stall threshold.",
    )
    implement_parser.add_argument(
        "--forever",
        action="store_true",
        help="Run indefinitely until interrupted instead of stopping at the cap.",
    )
    implement_parser.add_argument(
        "--sandbox",
        default=None,
        help="Optional provider sandbox override for implement agent turns.",
    )
    implement_parser.set_defaults(func=cmd_implement)
