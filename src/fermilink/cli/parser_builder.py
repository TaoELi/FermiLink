from __future__ import annotations

import argparse


def _cli():
    from fermilink import cli

    return cli


def _build_parser() -> argparse.ArgumentParser:
    cli = _cli()
    parser = argparse.ArgumentParser(
        prog="fermilink",
        description="Unified FermiLink CLI for package management and service control.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_json_option(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--json",
            action="store_true",
            help="Print full JSON output instead of concise human-readable lines.",
        )

    cli.register_package_install_compile_parsers(
        subparsers,
        add_json_option=_add_json_option,
        cmd_install=cli._cmd_install,
        cmd_compile=cli._cmd_compile,
        cmd_recompile=cli._cmd_recompile,
        cmd_auto_compile=cli._cmd_auto_compile,
        default_max_zip_bytes=cli.DEFAULT_MAX_ZIP_BYTES,
    )
    cli.register_optimize_parser(
        subparsers,
        add_json_option=_add_json_option,
        cmd_optimize=cli._cmd_optimize,
        supported_providers=cli.SUPPORTED_PROVIDERS,
    )
    cli.register_implement_parser(
        subparsers,
        add_json_option=_add_json_option,
        cmd_implement=cli._cmd_implement,
        supported_providers=cli.SUPPORTED_PROVIDERS,
    )
    cli.register_exec_loop_parsers(
        subparsers,
        cmd_exec=cli._cmd_exec,
        cmd_loop=cli._cmd_loop,
    )
    cli.register_exploop_parser(
        subparsers,
        cmd_exploop=cli._cmd_exploop,
    )
    cli.register_drvloop_parser(
        subparsers,
        cmd_drvloop=cli._cmd_drvloop,
    )
    cli.register_workspace_parsers(
        subparsers,
        cmd_init=cli._cmd_init,
        cmd_clean=cli._cmd_clean,
        cmd_hpc=cli._cmd_hpc,
    )
    cli.register_gateway_parser(
        subparsers,
        cmd_gateway=cli._cmd_gateway,
    )
    cli.register_workflow_parsers(
        subparsers,
        cmd_reproduce=cli._cmd_reproduce,
        cmd_research=cli._cmd_research,
    )
    cli.register_chat_parser(
        subparsers,
        cmd_chat=cli._cmd_chat,
    )
    cli.register_agent_parser(
        subparsers,
        add_json_option=_add_json_option,
        cmd_agent=cli._cmd_agent,
        supported_providers=cli.SUPPORTED_PROVIDERS,
        supported_reasoning_efforts=cli.SUPPORTED_REASONING_EFFORTS,
    )
    cli.register_package_management_parsers(
        subparsers,
        add_json_option=_add_json_option,
        cmd_list=cli._cmd_list,
        cmd_avail=cli._cmd_avail,
        cmd_activate=cli._cmd_activate,
        cmd_overlay=cli._cmd_overlay,
        cmd_dependencies=cli._cmd_dependencies,
        cmd_delete=cli._cmd_delete,
    )
    cli.register_service_parsers(
        subparsers,
        add_json_option=_add_json_option,
        cmd_start=cli._cmd_start,
        cmd_stop=cli._cmd_stop,
        cmd_restart=cli._cmd_restart,
        cmd_status=cli._cmd_status,
    )

    return parser
