from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_workflow_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    cmd_reproduce: CommandHandler,
    cmd_research: CommandHandler,
    default_provider_bin: str,
) -> None:
    """
    Register parser arguments for workflow.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction[argparse.ArgumentParser]
        Subparser collection created from the root parser.
    cmd_reproduce : CommandHandler
        Command handler for `reproduce` subcommands.
    cmd_research : CommandHandler
        Command handler for `research` subcommands.
    default_provider_bin : str
        Default compatibility override value for `--codex-bin`.

    Returns
    -------
    None
        No return value; parser objects are mutated in place.
    """
    reproduce_parser = subparsers.add_parser(
        "reproduce",
        help=(
            "Run paper-level orchestration: generate/audit a multi-task plan, then "
            "execute each task via fermilink loop until all tasks are done."
        ),
    )
    reproduce_parser.add_argument(
        "prompt",
        nargs="+",
        help=(
            "Either paper request text, or a path to a file containing source content "
            "(e.g. paper.tex, paper.md, notes.txt)."
        ),
    )
    reproduce_parser.add_argument(
        "--package",
        dest="package_id",
        help="Pin one installed package id and skip auto routing.",
    )
    reproduce_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for planning/auditing/loop runs. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    reproduce_parser.add_argument(
        "--codex-bin",
        default=default_provider_bin,
        help=(
            f"Codex executable path (default: {default_provider_bin}). "
            "Ignored when provider is not codex."
        ),
    )
    reproduce_parser.add_argument(
        "--task-max-runs",
        type=int,
        default=5,
        help="Maximum outer loop reruns per task when loop is not done (default: 5).",
    )
    reproduce_parser.add_argument(
        "--planner-max-tries",
        type=int,
        default=2,
        help="Maximum planner retries to obtain valid plan JSON (default: 2).",
    )
    reproduce_parser.add_argument(
        "--auditor-max-tries",
        type=int,
        default=2,
        help="Maximum auditor retries to obtain valid plan JSON (default: 2).",
    )
    reproduce_parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Forwarded to inner loop: max iterations per loop run (default: 10).",
    )
    reproduce_parser.add_argument(
        "--wait-seconds",
        type=float,
        default=1.0,
        help=(
            "Forwarded to inner loop polling interval for "
            "<pid_number>/<slurm_job_number> waits; "
            "also used as fallback sleep when no wait tags are returned "
            "(default: 1)."
        ),
    )
    reproduce_parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=6000.0,
        help=(
            "Forwarded to inner loop hard cap for per-iteration pid/slurm polling "
            "and waits "
            "(default: 6000)."
        ),
    )
    reproduce_parser.add_argument(
        "--pid-stall-seconds",
        type=float,
        default=900.0,
        help=(
            "Forwarded to inner loop local-pid stall timeout. If > 0, a pid with no "
            "CPU-time progress for this long triggers early next-iteration handoff "
            "(default: 900; set 0 to disable)."
        ),
    )
    reproduce_parser.add_argument(
        "--hpc-profile",
        default=None,
        help=(
            "Optional JSON file with `slurm_default_partition`, `slurm_defaults`, "
            "and `slurm_resource_policy`; when set, workflow planning/task "
            "prompts/reporting are constrained to this HPC profile."
        ),
    )
    reproduce_parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only generate and persist plan/prompts; do not execute tasks.",
    )
    reproduce_parser.add_argument(
        "--report-only",
        action="store_true",
        help=(
            "Only generate/audit top-level report from existing run artifacts "
            "(requires --resume)."
        ),
    )
    reproduce_parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Skip final report generation after all tasks complete.",
    )
    resume_group = reproduce_parser.add_mutually_exclusive_group(required=False)
    resume_group.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
        help="Resume the latest matching in-progress reproduce run (default).",
    )
    resume_group.add_argument(
        "--restart",
        dest="resume",
        action="store_false",
        help="Start a fresh reproduce run and ignore resumable state.",
    )
    reproduce_parser.add_argument(
        "--init-git",
        action="store_true",
        help="Auto-run git init when current directory is not a git repository.",
    )
    reproduce_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of prompting/initializing when git repository is missing.",
    )
    reproduce_parser.set_defaults(func=cmd_reproduce)

    research_parser = subparsers.add_parser(
        "research",
        help=(
            "Run research orchestration: generate/audit a multi-task research plan, then "
            "execute each task via fermilink loop until all tasks are done."
        ),
    )
    research_parser.add_argument(
        "prompt",
        nargs="+",
        help=(
            "Either research request text, or a path to a file containing source content "
            "(e.g. idea.md, proposal.txt)."
        ),
    )
    research_parser.add_argument(
        "--package",
        dest="package_id",
        help="Pin one installed package id and skip auto routing.",
    )
    research_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for planning/auditing/loop runs. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    research_parser.add_argument(
        "--codex-bin",
        default=default_provider_bin,
        help=(
            f"Codex executable path (default: {default_provider_bin}). "
            "Ignored when provider is not codex."
        ),
    )
    research_parser.add_argument(
        "--task-max-runs",
        type=int,
        default=5,
        help="Maximum outer loop reruns per task when loop is not done (default: 5).",
    )
    research_parser.add_argument(
        "--planner-max-tries",
        type=int,
        default=2,
        help="Maximum planner retries to obtain valid plan JSON (default: 2).",
    )
    research_parser.add_argument(
        "--auditor-max-tries",
        type=int,
        default=2,
        help="Maximum auditor retries to obtain valid plan JSON (default: 2).",
    )
    research_parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Forwarded to inner loop: max iterations per loop run (default: 10).",
    )
    research_parser.add_argument(
        "--wait-seconds",
        type=float,
        default=0.0,
        help=(
            "Forwarded to inner loop polling interval for "
            "<pid_number>/<slurm_job_number> waits; "
            "also used as fallback sleep when no wait tags are returned "
            "(default: 0)."
        ),
    )
    research_parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=600.0,
        help=(
            "Forwarded to inner loop hard cap for per-iteration pid/slurm polling "
            "and waits "
            "(default: 600)."
        ),
    )
    research_parser.add_argument(
        "--pid-stall-seconds",
        type=float,
        default=900.0,
        help=(
            "Forwarded to inner loop local-pid stall timeout. If > 0, a pid with no "
            "CPU-time progress for this long triggers early next-iteration handoff "
            "(default: 900; set 0 to disable)."
        ),
    )
    research_parser.add_argument(
        "--hpc-profile",
        default=None,
        help=(
            "Optional JSON file with `slurm_default_partition`, `slurm_defaults`, "
            "and `slurm_resource_policy`; when set, workflow planning/task "
            "prompts/reporting are constrained to this HPC profile."
        ),
    )
    research_parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only generate and persist plan/prompts; do not execute tasks.",
    )
    research_parser.add_argument(
        "--report-only",
        action="store_true",
        help=(
            "Only generate/audit top-level report from existing run artifacts "
            "(requires --resume)."
        ),
    )
    research_parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Skip final report generation after all tasks complete.",
    )
    research_resume_group = research_parser.add_mutually_exclusive_group(required=False)
    research_resume_group.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
        help="Resume the latest matching in-progress research run (default).",
    )
    research_resume_group.add_argument(
        "--restart",
        dest="resume",
        action="store_false",
        help="Start a fresh research run and ignore resumable state.",
    )
    research_parser.add_argument(
        "--init-git",
        action="store_true",
        help="Auto-run git init when current directory is not a git repository.",
    )
    research_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of prompting/initializing when git repository is missing.",
    )
    research_parser.set_defaults(func=cmd_research)
