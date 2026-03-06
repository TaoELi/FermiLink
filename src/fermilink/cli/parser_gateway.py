from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_gateway_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    cmd_gateway: CommandHandler,
) -> None:
    """
    Register parser arguments for gateway.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction[argparse.ArgumentParser]
        Subparser collection created from the root parser.
    cmd_gateway : CommandHandler
        Command handler for `gateway`.
    Returns
    -------
    None
        No return value; parser objects are mutated in place.
    """

    gateway_parser = subparsers.add_parser(
        "gateway",
        help=(
            "Run Telegram gateway that maps chat sessions to sticky workspaces "
            "and executes requests via fermilink exec by default."
        ),
    )
    gateway_parser.add_argument(
        "--telegram-token",
        default=None,
        help=(
            "Telegram bot token. When omitted, reads "
            "FERMILINK_GATEWAY_TELEGRAM_TOKEN."
        ),
    )
    gateway_parser.add_argument(
        "--allow-from",
        action="append",
        default=None,
        help=(
            "Allowlist sender ids/usernames (repeatable, supports comma-separated "
            "entries). Empty allowlist permits all senders."
        ),
    )
    gateway_parser.add_argument(
        "--poll-timeout-seconds",
        type=int,
        default=30,
        help="Telegram getUpdates long-poll timeout in seconds (default: 30).",
    )
    gateway_parser.add_argument(
        "--session-store",
        default=None,
        help=(
            "Optional path for gateway session-state JSON. Defaults to "
            "$FERMILINK_RUNTIME_ROOT/chat_sessions.json."
        ),
    )
    gateway_parser.add_argument(
        "--package",
        dest="package_id",
        default=None,
        help="Pin one installed package id and skip auto routing.",
    )
    gateway_parser.add_argument(
        "--sandbox",
        default=None,
        help=(
            "Override sandbox mode for loop/workflow runs triggered by gateway messages. "
            "When omitted, uses `fermilink agent` policy."
        ),
    )
    gateway_parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Forwarded to loop: max iterations per message (default: 10).",
    )
    gateway_parser.add_argument(
        "--wait-seconds",
        type=float,
        default=1.0,
        help="Forwarded to loop polling interval/wait fallback (default: 1).",
    )
    gateway_parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=6000.0,
        help="Forwarded to loop max per-iteration wait cap (default: 6000).",
    )
    gateway_parser.add_argument(
        "--pid-stall-seconds",
        type=float,
        default=900.0,
        help=(
            "Forwarded to loop local-pid stall timeout in seconds "
            "(default: 900; set 0 to disable)."
        ),
    )
    gateway_parser.add_argument(
        "--hpc-profile",
        default=None,
        help=(
            "Optional JSON file forwarded to gateway-triggered exec/loop/workflow "
            "runs (`fermilink exec`, `fermilink loop`, "
            "`fermilink research ...`, `fermilink reproduce ...`)."
        ),
    )
    init_git_group = gateway_parser.add_mutually_exclusive_group(required=False)
    init_git_group.add_argument(
        "--init-git",
        dest="init_git",
        action="store_true",
        default=True,
        help="Auto-run git init in gateway-created workspace repos (default).",
    )
    init_git_group.add_argument(
        "--no-init-git",
        dest="init_git",
        action="store_false",
        help="Fail when workspace repo lacks git metadata.",
    )
    gateway_parser.set_defaults(func=cmd_gateway)
