from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_workspace_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    cmd_init: CommandHandler,
    cmd_clean: CommandHandler,
    cmd_hpc: CommandHandler,
) -> None:
    """Register parser arguments for workspace init/clean/hpc."""

    init_parser = subparsers.add_parser(
        "init",
        help=(
            "Initialize a destination folder with managed FermiLink payload "
            "artifacts, or create a package-linked local workspace via "
            "`fermilink init <pkg-id>`."
        ),
    )
    init_parser.add_argument(
        "init_target",
        nargs="?",
        default=".",
        help=(
            "Destination directory for classic init, or an installed package id "
            "for package-linked init."
        ),
    )
    init_parser.add_argument(
        "destination",
        nargs="?",
        default=None,
        help="Optional destination directory for `fermilink init <pkg-id> <destination>`.",
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

    hpc_parser = subparsers.add_parser(
        "hpc",
        help=(
            "Manage persistent HPC profile settings used as defaults by "
            "exec/loop/research/reproduce."
        ),
    )
    hpc_subparsers = hpc_parser.add_subparsers(dest="hpc_command", required=False)
    hpc_set_parser = hpc_subparsers.add_parser(
        "set",
        help="Install a global HPC profile from JSON.",
    )
    hpc_set_parser.add_argument(
        "file",
        type=str,
        help="Path to a JSON profile file.",
    )
    hpc_parser.set_defaults(func=cmd_hpc)
