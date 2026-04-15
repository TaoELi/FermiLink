"""Workspace-mode facade exports."""

from __future__ import annotations

from fermilink.workspace.clean import (
    clean_package_workspace,
    clean_workspace,
    cmd_clean,
    fermilink_clean_main,
)
from fermilink.workspace.hpc import cmd_hpc
from fermilink.workspace.init import (
    cmd_init,
    fermilink_init_main,
    initialize_package_workspace,
    initialize_workspace,
)

__all__ = [
    "clean_package_workspace",
    "clean_workspace",
    "cmd_clean",
    "cmd_hpc",
    "cmd_init",
    "fermilink_clean_main",
    "fermilink_init_main",
    "initialize_package_workspace",
    "initialize_workspace",
]


if __name__ == "__main__":
    raise SystemExit(fermilink_init_main())
