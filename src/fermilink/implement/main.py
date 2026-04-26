from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .campaign import read_campaign_status, run_goal_campaign
from .state import clear_run_lock, run_lock_path


def build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m fermilink.implement.main")
    _add_run_arguments(parser)
    return parser


def build_status_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m fermilink.implement.main status")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--tail", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("goal", nargs="?", help="implementation goal.md path")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--branch")
    parser.add_argument("--sandbox")
    parser.add_argument("--max-iterations", type=int)
    parser.add_argument("--stop-on-consecutive-rejections", type=int)
    parser.add_argument("--worker-max-iterations", type=int)
    parser.add_argument("--worker-wait-seconds", type=float)
    parser.add_argument("--worker-max-wait-seconds", type=float)
    parser.add_argument("--worker-pid-stall-seconds", type=float)
    parser.add_argument("--worker-provider")
    parser.add_argument("--worker-model")
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--json", action="store_true")


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv or [])
    if raw_argv and raw_argv[0] == "status":
        parser = build_status_parser()
        args = parser.parse_args(raw_argv[1:])
        payload = read_campaign_status(args)
    else:
        if raw_argv and raw_argv[0] == "run":
            raw_argv = raw_argv[1:]
        parser = build_run_parser()
        args = parser.parse_args(raw_argv)
        if not args.goal:
            parser.error("goal markdown path is required")
        lock_path = run_lock_path(
            Path(str(args.project_root or ".")).expanduser().resolve()
        )
        try:
            payload = run_goal_campaign(args)
        finally:
            clear_run_lock(lock_path)
    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
