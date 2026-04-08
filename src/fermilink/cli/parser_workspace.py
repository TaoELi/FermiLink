from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_workspace_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    cmd_init: CommandHandler,
    cmd_clean: CommandHandler,
) -> None:
    """Register parser arguments for workspace init/clean."""

    init_parser = subparsers.add_parser(
        "init",
        help=(
            "Initialize a destination folder with managed FermiLink payload "
            "artifacts (symlinked entries plus copied onboarding AGENTS.md)."
        ),
    )
    init_parser.add_argument(
        "destination",
        nargs="?",
        default=".",
        help="Destination directory (default: current directory).",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite conflicting files/symlinks in the destination directory.",
    )
    init_parser.set_defaults(func=cmd_init)

    clean_parser = subparsers.add_parser(
        "clean",
        help=(
            "Remove workspace artifacts created by `fermilink init` "
            "/ `fermilink-init`."
        ),
    )
    clean_parser.add_argument(
        "destination",
        nargs="?",
        default=".",
        help="Workspace directory to clean (default: current directory).",
    )
    clean_parser.add_argument(
        "--force",
        action="store_true",
        help="Remove conflicting managed paths even when they were modified.",
    )
    clean_parser.set_defaults(func=cmd_clean)
