from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_service_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    add_json_option: Callable[[argparse.ArgumentParser], None],
    cmd_start: CommandHandler,
    cmd_stop: CommandHandler,
    cmd_restart: CommandHandler,
    cmd_status: CommandHandler,
) -> None:
    start_parser = subparsers.add_parser(
        "start",
        help="Start one or more services: runner, web. Default starts both.",
    )
    add_json_option(start_parser)
    start_parser.add_argument("components", nargs="*", help="runner and/or web")
    start_parser.set_defaults(func=cmd_start)

    stop_parser = subparsers.add_parser(
        "stop",
        help="Stop one or more services: runner, web. Default stops both.",
    )
    add_json_option(stop_parser)
    stop_parser.add_argument("components", nargs="*", help="runner and/or web")
    stop_parser.set_defaults(func=cmd_stop)

    restart_parser = subparsers.add_parser(
        "restart",
        help="Restart one or more services: runner, web. Default restarts both.",
    )
    add_json_option(restart_parser)
    restart_parser.add_argument("components", nargs="*", help="runner and/or web")
    restart_parser.set_defaults(func=cmd_restart)

    status_parser = subparsers.add_parser(
        "status",
        help="Show service status for runner/web. Default checks both.",
    )
    add_json_option(status_parser)
    status_parser.add_argument("components", nargs="*", help="runner and/or web")
    status_parser.set_defaults(func=cmd_status)
