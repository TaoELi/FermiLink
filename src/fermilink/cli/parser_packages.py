from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_package_install_compile_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    add_json_option: Callable[[argparse.ArgumentParser], None],
    cmd_install: CommandHandler,
    cmd_compile: CommandHandler,
    default_max_zip_bytes: int,
) -> None:
    install_parser = subparsers.add_parser(
        "install",
        help="Install scientific package from curated channel, zip URL, or local path.",
    )
    add_json_option(install_parser)
    install_parser.add_argument(
        "package_id",
        nargs="+",
        help="One or more package ids to install, e.g. ase meep qutip",
    )
    install_parser.add_argument(
        "--channel",
        default="tel-research-group",
        help="Curated source channel (default: tel-research-group).",
    )
    install_parser.add_argument(
        "--version",
        dest="version_id",
        help=(
            "Curated version id to install (for example: branch-head or a tagged version). "
            "Only valid when source is curated channel."
        ),
    )
    install_parser.add_argument(
        "--require-verified",
        action="store_true",
        help=(
            "Fail if the selected curated version is not marked verified. "
            "Only valid when source is curated channel."
        ),
    )
    source_group = install_parser.add_mutually_exclusive_group(required=False)
    source_group.add_argument("--zip-url", help="Override with custom zip URL.")
    source_group.add_argument(
        "--local-path", help="Install from local package directory."
    )
    install_parser.add_argument("--title", help="Display title for package metadata.")
    install_parser.add_argument(
        "--activate",
        "--active",
        action="store_true",
        help="Activate package for new sessions after install.",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing managed package folder.",
    )
    install_parser.add_argument(
        "--max-zip-bytes",
        type=int,
        default=default_max_zip_bytes,
        help=f"Maximum zip download size in bytes (default: {default_max_zip_bytes}).",
    )
    install_parser.add_argument(
        "--no-router-sync",
        action="store_true",
        help="Skip automatic router_rules.json synchronization.",
    )
    install_parser.set_defaults(func=cmd_install)

    compile_parser = subparsers.add_parser(
        "compile",
        help=(
            "Compile a local scientific project into a fermilink package by running "
            "three codex passes with sci-skills-generator, then install locally."
        ),
    )
    add_json_option(compile_parser)
    compile_parser.add_argument("package_id", help="Target package id to register.")
    compile_parser.add_argument(
        "project_path",
        nargs="?",
        default=".",
        help="Project root path to compile (default: current directory).",
    )
    compile_parser.add_argument(
        "--title",
        help="Optional display title for installed package metadata.",
    )
    compile_parser.add_argument(
        "--activate",
        "--active",
        action="store_true",
        help="Activate package after compile+install.",
    )
    compile_parser.add_argument(
        "--no-router-sync",
        action="store_true",
        help="Skip automatic router_rules.json synchronization.",
    )
    compile_parser.set_defaults(func=cmd_compile)


def register_package_management_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    add_json_option: Callable[[argparse.ArgumentParser], None],
    cmd_list: CommandHandler,
    cmd_avail: CommandHandler,
    cmd_activate: CommandHandler,
    cmd_overlay: CommandHandler,
    cmd_dependencies: CommandHandler,
    cmd_delete: CommandHandler,
) -> None:
    list_parser = subparsers.add_parser(
        "list", help="List installed scientific packages."
    )
    add_json_option(list_parser)
    list_parser.set_defaults(func=cmd_list)

    avail_parser = subparsers.add_parser(
        "avail",
        help="Search curated channel packages available for installation.",
    )
    add_json_option(avail_parser)
    avail_parser.add_argument(
        "query", help="Package id or keyword to search in curated channel."
    )
    avail_parser.add_argument(
        "--channel",
        default="tel-research-group",
        help="Curated source channel to query (default: tel-research-group).",
    )
    avail_parser.set_defaults(func=cmd_avail)

    activate_parser = subparsers.add_parser("activate", help="Set active package.")
    add_json_option(activate_parser)
    activate_parser.add_argument("package_id")
    activate_parser.set_defaults(func=cmd_activate)

    overlay_parser = subparsers.add_parser(
        "overlay",
        help="Set which top-level package entries are exposed in workspace repo.",
    )
    add_json_option(overlay_parser)
    overlay_parser.add_argument("package_id")
    overlay_parser.add_argument(
        "--entry",
        action="append",
        help="Top-level package entry name (repeat for multiple).",
    )
    overlay_parser.add_argument(
        "--entries",
        dest="entries_csv",
        help="Comma-separated top-level package entry names.",
    )
    overlay_parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear overlay restriction and expose all exportable entries.",
    )
    overlay_parser.set_defaults(func=cmd_overlay)

    dependencies_parser = subparsers.add_parser(
        "dependencies",
        help="Set dependency package links under repo/external_packages/.",
    )
    add_json_option(dependencies_parser)
    dependencies_parser.add_argument("package_id")
    dependencies_parser.add_argument(
        "--package",
        action="append",
        help="Dependency package id (repeat for multiple).",
    )
    dependencies_parser.add_argument(
        "--packages",
        dest="packages_csv",
        help="Comma-separated dependency package ids.",
    )
    dependencies_parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear dependency package configuration.",
    )
    dependencies_parser.set_defaults(func=cmd_dependencies)

    delete_parser = subparsers.add_parser(
        "delete", help="Delete installed scientific package."
    )
    add_json_option(delete_parser)
    delete_parser.add_argument("package_id")
    delete_parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Only remove registry entry and keep managed files.",
    )
    delete_parser.add_argument(
        "--no-router-sync",
        action="store_true",
        help="Skip automatic router_rules.json synchronization.",
    )
    delete_parser.set_defaults(func=cmd_delete)
