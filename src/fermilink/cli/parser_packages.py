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
    cmd_recompile: CommandHandler,
    cmd_auto_compile: CommandHandler,
    default_max_zip_bytes: int,
) -> None:
    """
    Register parser arguments for package install/compile/recompile.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction[argparse.ArgumentParser]
        Subparser collection created from the root parser.
    add_json_option : Callable[[argparse.ArgumentParser], None]
        Callback that adds shared `--json` output flags.
    cmd_install : CommandHandler
        Command handler for `install` subcommands.
    cmd_compile : CommandHandler
        Command handler for `compile` subcommands.
    cmd_recompile : CommandHandler
        Command handler for `recompile` subcommands.
    cmd_auto_compile : CommandHandler
        Command handler for `auto-compile` subcommands.
    default_max_zip_bytes : int
        Default maximum zip size (bytes) for package install validation.

    Returns
    -------
    None
        No return value; parser objects are mutated in place.
    """
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
        default="skilled-scipkg",
        help="Curated source channel (default: skilled-scipkg).",
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
            "three Codex passes with a deterministic skills generation+validation "
            "pipeline, then install locally."
        ),
    )
    add_json_option(compile_parser)
    compile_parser.add_argument("package_id", help="Target package id to register.")
    compile_parser.add_argument(
        "project_path",
        help="Project root path to compile.",
    )
    compile_parser.add_argument(
        "--title",
        help="Optional display title for installed package metadata.",
    )
    compile_parser.add_argument(
        "--max-skills",
        type=int,
        default=30,
        help=(
            "Maximum number of generated skills (including index) during deterministic "
            "generation (default: 30)."
        ),
    )
    compile_parser.add_argument(
        "--core-skill-count",
        type=int,
        default=6,
        help=(
            "How many top topic skills to enrich with compact high-signal playbooks "
            "(default: 6)."
        ),
    )
    compile_parser.add_argument(
        "--docs-only",
        action="store_true",
        help="Force docs-only generation mode (skip source discovery in generator).",
    )
    compile_parser.add_argument(
        "--keep-compile-artifacts",
        action="store_true",
        help="Keep temporary `sci-skills-generator/` folder after compile completes.",
    )
    compile_parser.add_argument(
        "--strict-compile-validation",
        action="store_true",
        help=(
            "Fail compile when validation detects broken links or missing playbook "
            "requirements. By default, validation findings are reported but do not "
            "block install."
        ),
    )
    compile_parser.add_argument(
        "--install-off",
        action="store_true",
        help=(
            "Skip install/registry/router steps and only update `skills/` artifacts "
            "inside the target project."
        ),
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

    recompile_parser = subparsers.add_parser(
        "recompile",
        help=(
            "Re-audit/update an existing skills folder during package development "
            "using three Codex passes, then install locally."
        ),
    )
    add_json_option(recompile_parser)
    recompile_parser.add_argument("package_id", help="Target package id to register.")
    recompile_parser.add_argument(
        "project_path",
        nargs="?",
        default=None,
        help=(
            "Project root path to recompile. When omitted, defaults to installed "
            "package path under scientific packages storage."
        ),
    )
    recompile_parser.add_argument(
        "--memory",
        default=None,
        help=(
            "Optional memory.md file or directory used to extract "
            "`### Suggested skills updates` and generate append-only "
            "skills update plans."
        ),
    )
    recompile_parser.add_argument(
        "--doc",
        default=None,
        help=(
            "Optional manuscript/paper path used to drive paper-focused skill "
            "updates during recompile."
        ),
    )
    recompile_parser.add_argument(
        "--data-dir",
        default=None,
        help=(
            "Optional supplementary data directory (inputs/scripts/results) used "
            "for paper-focused recompile context."
        ),
    )
    recompile_parser.add_argument(
        "--comment",
        default=None,
        help=(
            "Optional note describing which paper skills to learn; requires --doc. "
            "When omitted, recompile targets broad paper-result reproducibility."
        ),
    )
    recompile_parser.add_argument(
        "--title",
        help="Optional display title for installed package metadata.",
    )
    recompile_parser.add_argument(
        "--core-skill-count",
        type=int,
        default=6,
        help=(
            "How many top topic skills to enrich with compact high-signal playbooks "
            "(default: 6)."
        ),
    )
    recompile_parser.add_argument(
        "--docs-only",
        action="store_true",
        help="Force docs-only coverage behavior for source-link validation.",
    )
    recompile_parser.add_argument(
        "--keep-compile-artifacts",
        action="store_true",
        help="Keep temporary `sci-skills-generator/` folder after recompile completes.",
    )
    recompile_parser.add_argument(
        "--strict-compile-validation",
        action="store_true",
        help=(
            "Fail recompile when validation detects broken links or missing playbook "
            "requirements. By default, validation findings are reported but do not "
            "block install."
        ),
    )
    recompile_parser.add_argument(
        "--install-off",
        action="store_true",
        help=(
            "Skip install/registry/router steps and only refresh `skills/` artifacts "
            "inside the target project."
        ),
    )
    recompile_parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Compatibility flag (no-op): recompile already replaces existing "
            "installed package files/metadata for this package id."
        ),
    )
    recompile_parser.add_argument(
        "--activate",
        "--active",
        action="store_true",
        help="Activate package after recompile+install.",
    )
    recompile_parser.add_argument(
        "--no-router-sync",
        action="store_true",
        help="Skip automatic router_rules.json synchronization.",
    )
    recompile_parser.set_defaults(func=cmd_recompile)

    auto_compile_parser = subparsers.add_parser(
        "auto-compile",
        help=(
            "Fork an upstream GitHub repo to your account (or an organization), "
            "compile skills if needed, push to your fork, then append vetted "
            "metadata entries into local skilled-scipkg and family hints data files."
        ),
    )
    add_json_option(auto_compile_parser)
    auto_compile_parser.add_argument(
        "package_id",
        nargs="?",
        help="Target package id (required unless --spec-file is used).",
    )
    auto_compile_parser.add_argument(
        "upstream_repo_url",
        nargs="?",
        help="Upstream GitHub repo URL (required unless --spec-file is used).",
    )
    auto_compile_parser.add_argument(
        "--spec-file",
        help=(
            'JSON file with `{ "packages": [{"package_id": ..., '
            '"upstream_repo_url": ...}, ...] }` for batch processing.'
        ),
    )
    auto_compile_parser.add_argument(
        "--fermilink-repo",
        required=True,
        help=(
            "Local FermiLink repository path where "
            "src/fermilink/data/curated_channels/skilled-scipkg.json and "
            "src/fermilink/data/router/family_hints.json will be updated."
        ),
    )
    auto_compile_parser.add_argument(
        "--workspace-root",
        default="./.fermilink-auto-compile",
        help=(
            "Workspace directory for cloned fork repositories "
            "(default: ./.fermilink-auto-compile)."
        ),
    )
    auto_compile_parser.add_argument(
        "--organization",
        help=(
            "GitHub organization that should own created forks "
            "(uses `gh repo fork --org <organization>`)."
        ),
    )
    auto_compile_parser.add_argument(
        "--channel",
        default="skilled-scipkg",
        help="Curated channel file to update (default: skilled-scipkg).",
    )
    auto_compile_parser.add_argument(
        "--max-skills",
        type=int,
        default=30,
        help=("Maximum generated skills when compile is needed " "(default: 30)."),
    )
    auto_compile_parser.add_argument(
        "--core-skill-count",
        type=int,
        default=6,
        help=("Top topic skills to enrich during compile " "(default: 6)."),
    )
    auto_compile_parser.add_argument(
        "--docs-only",
        action="store_true",
        help="Force docs-only mode if compile is needed.",
    )
    auto_compile_parser.add_argument(
        "--keep-compile-artifacts",
        action="store_true",
        help="Keep temporary sci-skills-generator folder after compile.",
    )
    auto_compile_parser.add_argument(
        "--strict-compile-validation",
        action="store_true",
        help=("Fail compile when generated skills validation has findings."),
    )
    auto_compile_parser.add_argument(
        "--update-existing",
        action="store_true",
        help=(
            "Allow replacing existing package entries in curated channel/family hints."
        ),
    )
    auto_compile_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run fork/clone/compile/push and metadata validation, but do not write "
            "curated channel or family hints files."
        ),
    )
    auto_compile_parser.add_argument(
        "--cleanup-clone",
        action="store_true",
        help=(
            "Remove cloned repository directories after each package completes. "
            "By default, cloned forks are kept under --workspace-root."
        ),
    )
    auto_compile_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop batch processing after the first package failure.",
    )
    auto_compile_parser.set_defaults(func=cmd_auto_compile)


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
    """
    Register parser arguments for package management.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction[argparse.ArgumentParser]
        Subparser collection created from the root parser.
    add_json_option : Callable[[argparse.ArgumentParser], None]
        Callback that adds shared `--json` output flags.
    cmd_list : CommandHandler
        Command handler for `list` subcommands.
    cmd_avail : CommandHandler
        Command handler for `avail` subcommands.
    cmd_activate : CommandHandler
        Command handler for `activate` subcommands.
    cmd_overlay : CommandHandler
        Command handler for `overlay` subcommands.
    cmd_dependencies : CommandHandler
        Command handler for `dependencies` subcommands.
    cmd_delete : CommandHandler
        Command handler for `delete` subcommands.

    Returns
    -------
    None
        No return value; parser objects are mutated in place.
    """
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
        default="skilled-scipkg",
        help="Curated source channel to query (default: skilled-scipkg).",
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
