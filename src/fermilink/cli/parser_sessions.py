from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_exec_loop_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    cmd_exec: CommandHandler,
    cmd_loop: CommandHandler,
    default_codex_bin: str,
) -> None:
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
        "--codex-bin",
        default=default_codex_bin,
        help=(
            f"Codex executable path (default: {default_codex_bin}). "
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
        "--codex-bin",
        default=default_codex_bin,
        help=(
            f"Codex executable path (default: {default_codex_bin}). "
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
        default=0.0,
        help=(
            "Fallback sleep seconds between iterations when no valid "
            "<wait_seconds> tag is returned (default: 0)."
        ),
    )
    loop_parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=600.0,
        help=(
            "Hard cap on per-iteration sleep seconds after applying agent "
            "wait hints (default: 600)."
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
    default_codex_bin: str,
) -> None:
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
        default=default_codex_bin,
        help=(
            f"Codex executable path (default: {default_codex_bin}). "
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
