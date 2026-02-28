from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_agent_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    add_json_option: Callable[[argparse.ArgumentParser], None],
    cmd_agent: CommandHandler,
    supported_providers: tuple[str, ...],
) -> None:
    """
    Register parser arguments for agent.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction[argparse.ArgumentParser]
        Subparser collection created from the root parser.
    add_json_option : Callable[[argparse.ArgumentParser], None]
        Callback that adds shared `--json` output flags.
    cmd_agent : CommandHandler
        Command handler for `agent` subcommands.
    supported_providers : tuple[str, ...]
        Provider names exposed by the runtime policy layer.

    Returns
    -------
    None
        No return value; parser objects are mutated in place.
    """
    agent_parser = subparsers.add_parser(
        "agent",
        help=(
            "Manage global agent runtime policy for provider and sandbox behavior "
            "used by runner/web/exec/chat/loop/research/reproduce."
        ),
    )
    add_json_option(agent_parser)
    agent_parser.add_argument(
        "provider",
        nargs="?",
        choices=supported_providers,
        help="Agent provider selection (codex, claude, gemini).",
    )
    sandbox_group = agent_parser.add_mutually_exclusive_group(required=False)
    sandbox_group.add_argument(
        "--sandbox",
        action="store_true",
        help="Enforce sandbox mode.",
    )
    sandbox_group.add_argument(
        "--bypass-sandbox",
        action="store_true",
        help="Bypass sandbox mode.",
    )
    model_group = agent_parser.add_mutually_exclusive_group(required=False)
    model_group.add_argument(
        "--model",
        default=None,
        help=(
            "Override provider default model (for example "
            "`gpt-5.3-codex-xhigh`)."
        ),
    )
    model_group.add_argument(
        "--clear-model",
        action="store_true",
        help="Clear model override and use provider default model selection.",
    )
    agent_parser.set_defaults(func=cmd_agent)
