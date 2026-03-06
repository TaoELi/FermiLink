from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_exec_loop_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    cmd_exec: CommandHandler,
    cmd_loop: CommandHandler,
    default_provider_bin: str,
) -> None:
    """
    Register parser arguments for exec loop.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction[argparse.ArgumentParser]
        Subparser collection created from the root parser.
    cmd_exec : CommandHandler
        Command handler for `exec` subcommands.
    cmd_loop : CommandHandler
        Command handler for `loop` subcommands.
    default_provider_bin : str
        Default compatibility override value for `--codex-bin`.

    Returns
    -------
    None
        No return value; parser objects are mutated in place.
    """
    exec_parser = subparsers.add_parser(
        "exec",
        help=(
            "Run one prompt locally with web-like package routing, second guess, "
            "package overlay symlinks, and AGENTS template sync."
        ),
    )
    exec_parser.add_argument(
        "prompt",
        nargs="+",
        help=(
            "Either prompt text, or a path to a markdown/text file containing "
            "the prompt (e.g. prompt.md)."
        ),
    )
    exec_parser.add_argument(
        "--package",
        dest="package_id",
        help="Pin one installed package id and skip auto routing.",
    )
    exec_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for this run. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    exec_parser.add_argument(
        "--hpc-profile",
        default=None,
        help=(
            "Optional JSON file with `slurm_default_partition`, `slurm_defaults`, "
            "and `slurm_resource_policy`; when set, execution prompt context is "
            "constrained to this HPC profile."
        ),
    )
    exec_parser.add_argument(
        "--codex-bin",
        default=default_provider_bin,
        help=(
            f"Codex executable path (default: {default_provider_bin}). "
            "Ignored when provider is not codex."
        ),
    )
    exec_parser.add_argument(
        "--init-git",
        action="store_true",
        help="Auto-run git init when current directory is not a git repository.",
    )
    exec_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of prompting/initializing when git repository is missing.",
    )
    exec_parser.set_defaults(func=cmd_exec)

    loop_parser = subparsers.add_parser(
        "loop",
        help=(
            "Run one autonomous loop iteration locally (web-like routing + overlay), "
            "persisting state in projects/memory.md until <promise>DONE</promise>."
        ),
    )
    loop_parser.add_argument(
        "prompt",
        nargs="+",
        help=(
            "Either prompt text, or a path to a markdown file containing the prompt "
            "(e.g. prompt.md)."
        ),
    )
    loop_parser.add_argument(
        "--package",
        dest="package_id",
        help="Pin one installed package id and skip auto routing.",
    )
    loop_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for this iteration. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    loop_parser.add_argument(
        "--hpc-profile",
        default=None,
        help=(
            "Optional JSON file with `slurm_default_partition`, `slurm_defaults`, "
            "and `slurm_resource_policy`; when set, execution prompt context is "
            "constrained to this HPC profile."
        ),
    )
    loop_parser.add_argument(
        "--codex-bin",
        default=default_provider_bin,
        help=(
            f"Codex executable path (default: {default_provider_bin}). "
            "Ignored when provider is not codex."
        ),
    )
    loop_parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Maximum loop iterations to run before stopping (default: 10).",
    )
    loop_parser.add_argument(
        "--wait-seconds",
        type=float,
        default=1.0,
        help=(
            "Polling interval seconds for <pid_number>/<slurm_job_number> waits; "
            "also used as fallback sleep between iterations when no wait tags are returned "
            "(default: 1)."
        ),
    )
    loop_parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=6000.0,
        help=(
            "Hard cap on per-iteration waiting for pid/slurm polling and wait "
            "hints (default: 6000)."
        ),
    )
    loop_parser.add_argument(
        "--pid-stall-seconds",
        type=float,
        default=900.0,
        help=(
            "If > 0, treat local pid polling as stalled when process CPU time does not "
            "advance for this many seconds and continue to next iteration early "
            "(default: 900; set 0 to disable)."
        ),
    )
    loop_parser.add_argument(
        "--init-git",
        action="store_true",
        help="Auto-run git init when current directory is not a git repository.",
    )
    loop_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of prompting/initializing when git repository is missing.",
    )
    loop_parser.set_defaults(func=cmd_loop)


def register_chat_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    cmd_chat: CommandHandler,
    default_provider_bin: str,
) -> None:
    """
    Register parser arguments for chat.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction[argparse.ArgumentParser]
        Subparser collection created from the root parser.
    cmd_chat : CommandHandler
        Command handler for `chat` subcommands.
    default_provider_bin : str
        Default compatibility override value for `--codex-bin`.

    Returns
    -------
    None
        No return value; parser objects are mutated in place.
    """
    chat_parser = subparsers.add_parser(
        "chat",
        help=(
            "Run interactive multi-turn local chat with web-like package routing, "
            "second guess, and package overlays."
        ),
    )
    chat_parser.add_argument(
        "--package",
        dest="package_id",
        help="Pin one installed package id for all turns and skip auto routing.",
    )
    chat_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for this chat session. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    chat_parser.add_argument(
        "--codex-bin",
        default=default_provider_bin,
        help=(
            f"Codex executable path (default: {default_provider_bin}). "
            "Ignored when provider is not codex."
        ),
    )
    chat_parser.add_argument(
        "--init-git",
        action="store_true",
        help="Auto-run git init when current directory is not a git repository.",
    )
    chat_parser.add_argument(
        "--no-init-git",
        action="store_true",
        help="Fail instead of prompting/initializing when git repository is missing.",
    )
    chat_parser.set_defaults(func=cmd_chat)
