from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_workflow_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    cmd_reproduce: CommandHandler,
    cmd_research: CommandHandler,
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
    post_task_audit_group = reproduce_parser.add_mutually_exclusive_group(
        required=False
    )
    post_task_audit_group.add_argument(
        "--post-task-plan-audit",
        dest="post_task_plan_audit",
        action="store_true",
        default=True,
        help=(
            "After each task loop, run a conservative audit that may update only "
            "remaining task prompts before continuing (default)."
        ),
    )
    post_task_audit_group.add_argument(
        "--no-post-task-plan-audit",
        dest="post_task_plan_audit",
        action="store_false",
        help=(
            "Disable the conservative post-task plan audit and run the original "
            "fixed task sequence."
        ),
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
        help=(
            "Explicitly auto-run git init when current directory is not a git "
            "repository (default behavior)."
        ),
    )
    reproduce_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of auto-initializing when git repository is missing.",
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
        dest="plan_only",
        action="store_true",
        help=(
            "Only generate and persist the research charter (question, approaches, "
            "risks) and phase-1 probes; do not execute them. Alias: --charter-only."
        ),
    )
    research_parser.add_argument(
        "--charter-only",
        dest="charter_only",
        action="store_true",
        help="Same as --plan-only: stop after generating the research charter.",
    )
    research_parser.add_argument(
        "--report-only",
        action="store_true",
        help=(
            "Only generate/audit the final paper from existing run artifacts "
            "(requires --resume)."
        ),
    )
    research_parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Skip final paper generation after the exploration loop concludes.",
    )
    research_parser.add_argument(
        "--max-phases",
        type=int,
        default=6,
        help=(
            "Maximum explore/reflect/re-plan phases before converging to a paper "
            "(default: 6)."
        ),
    )
    research_parser.add_argument(
        "--proof-depth",
        choices=("quick", "standard", "publication"),
        default="standard",
        help=(
            "Rigor forwarded to derivation (drvloop) probes: quick, standard, or "
            "publication (default: standard)."
        ),
    )
    research_parser.add_argument(
        "--enable-exploop",
        dest="enable_exploop",
        action="store_true",
        default=False,
        help=(
            "Allow experimental-measurement probes to run via exploop (drives real "
            "hardware; off by default)."
        ),
    )
    research_enable_code_group = research_parser.add_mutually_exclusive_group(
        required=False
    )
    research_enable_code_group.add_argument(
        "--enable-code",
        dest="enable_code",
        action="store_true",
        default=True,
        help="Allow write-code-from-scratch probes (default).",
    )
    research_enable_code_group.add_argument(
        "--no-enable-code",
        dest="enable_code",
        action="store_false",
        help="Disable write-code-from-scratch probes.",
    )
    research_enable_derivation_group = research_parser.add_mutually_exclusive_group(
        required=False
    )
    research_enable_derivation_group.add_argument(
        "--enable-derivation",
        dest="enable_derivation",
        action="store_true",
        default=True,
        help="Allow analytical-derivation probes via drvloop (default).",
    )
    research_enable_derivation_group.add_argument(
        "--no-enable-derivation",
        dest="enable_derivation",
        action="store_false",
        help="Disable analytical-derivation probes.",
    )
    research_allow_pivot_group = research_parser.add_mutually_exclusive_group(
        required=False
    )
    research_allow_pivot_group.add_argument(
        "--allow-pivot",
        dest="allow_pivot",
        action="store_true",
        default=True,
        help=(
            "Allow reflection to change approach or pivot the hypothesis between "
            "phases (default)."
        ),
    )
    research_allow_pivot_group.add_argument(
        "--no-allow-pivot",
        dest="allow_pivot",
        action="store_false",
        help="Forbid approach/hypothesis pivots; reflection may only deepen or converge.",
    )
    research_post_task_audit_group = research_parser.add_mutually_exclusive_group(
        required=False
    )
    research_post_task_audit_group.add_argument(
        "--post-task-plan-audit",
        dest="post_task_plan_audit",
        action="store_true",
        default=True,
        help=(
            "After each task loop, run a conservative audit that may update only "
            "remaining task prompts before continuing (default)."
        ),
    )
    research_post_task_audit_group.add_argument(
        "--no-post-task-plan-audit",
        dest="post_task_plan_audit",
        action="store_false",
        help=(
            "Disable the conservative post-task plan audit and run the original "
            "fixed task sequence."
        ),
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
        help=(
            "Explicitly auto-run git init when current directory is not a git "
            "repository (default behavior)."
        ),
    )
    research_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of auto-initializing when git repository is missing.",
    )
    research_parser.set_defaults(func=cmd_research)
