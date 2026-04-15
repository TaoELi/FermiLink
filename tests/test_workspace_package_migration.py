from __future__ import annotations

import importlib
from pathlib import Path


def test_cli_workspace_surface_keeps_wrapper_files() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    cli_dir = repo_root / "src" / "fermilink" / "cli"
    workspace_files = sorted(
        str(path.relative_to(cli_dir)).replace("\\", "/")
        for path in cli_dir.rglob("*workspace*.py")
    )

    assert workspace_files == [
        "commands/workspace.py",
        "parser_workspace.py",
    ]


def test_only_parser_workspace_module_exists_for_workspace_parsing() -> None:
    parser_workspace = importlib.import_module("fermilink.cli.parser_workspace")

    assert callable(parser_workspace.register_workspace_parsers)

    try:
        importlib.import_module("fermilink.cli.parse_workspace")
    except ModuleNotFoundError:
        pass
    else:  # pragma: no cover - defensive; absence is the contract
        raise AssertionError("fermilink.cli.parse_workspace should not exist")


def test_workspace_package_is_split_by_subroutine_and_shared_modules() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace_dir = repo_root / "src" / "fermilink" / "workspace"
    workspace_files = sorted(
        str(path.relative_to(workspace_dir)).replace("\\", "/")
        for path in workspace_dir.rglob("*.py")
    )

    assert workspace_files == [
        "__init__.py",
        "clean.py",
        "common.py",
        "filesystem.py",
        "hpc.py",
        "init.py",
        "main.py",
        "payload.py",
    ]


def test_workspace_package_facades_expose_primary_entrypoints() -> None:
    workspace_pkg = importlib.import_module("fermilink.workspace")
    workspace_main = importlib.import_module("fermilink.workspace.main")
    workspace_init = importlib.import_module("fermilink.workspace.init")
    workspace_clean = importlib.import_module("fermilink.workspace.clean")
    workspace_hpc = importlib.import_module("fermilink.workspace.hpc")

    assert workspace_pkg.initialize_workspace is workspace_main.initialize_workspace
    assert workspace_pkg.clean_workspace is workspace_main.clean_workspace
    assert workspace_pkg.cmd_init is workspace_main.cmd_init
    assert workspace_pkg.cmd_clean is workspace_main.cmd_clean
    assert workspace_pkg.cmd_hpc is workspace_main.cmd_hpc
    assert workspace_pkg.fermilink_init_main is workspace_main.fermilink_init_main
    assert workspace_pkg.fermilink_clean_main is workspace_main.fermilink_clean_main
    assert workspace_main.initialize_workspace is workspace_init.initialize_workspace
    assert workspace_main.cmd_init is workspace_init.cmd_init
    assert workspace_main.fermilink_init_main is workspace_init.fermilink_init_main
    assert workspace_main.clean_workspace is workspace_clean.clean_workspace
    assert workspace_main.cmd_clean is workspace_clean.cmd_clean
    assert workspace_main.fermilink_clean_main is workspace_clean.fermilink_clean_main
    assert workspace_main.cmd_hpc is workspace_hpc.cmd_hpc
