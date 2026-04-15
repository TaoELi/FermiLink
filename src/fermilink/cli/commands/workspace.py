from __future__ import annotations

import argparse
import importlib


def _workspace_main():
    return importlib.import_module("fermilink.workspace.main")


def cmd_init(args: argparse.Namespace) -> int:
    """Execute workspace initialization via the CLI surface."""

    workspace_main = _workspace_main()
    return workspace_main.cmd_init(args)


def cmd_clean(args: argparse.Namespace) -> int:
    """Execute workspace cleanup via the CLI surface."""

    workspace_main = _workspace_main()
    return workspace_main.cmd_clean(args)


def cmd_hpc(args: argparse.Namespace) -> int:
    """Execute workspace HPC profile management via the CLI surface."""

    workspace_main = _workspace_main()
    return workspace_main.cmd_hpc(args)


def fermilink_init_main(argv: list[str] | None = None) -> int:
    """Execute the standalone `fermilink-init` entrypoint."""

    workspace_main = _workspace_main()
    return workspace_main.fermilink_init_main(argv)


def fermilink_clean_main(argv: list[str] | None = None) -> int:
    """Execute the standalone `fermilink-clean` entrypoint."""

    workspace_main = _workspace_main()
    return workspace_main.fermilink_clean_main(argv)


if __name__ == "__main__":
    raise SystemExit(fermilink_init_main())
