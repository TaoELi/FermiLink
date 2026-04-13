from __future__ import annotations

import argparse
from collections.abc import Callable


CommandHandler = Callable[[argparse.Namespace], int]


def register_design_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[attr-defined]
    *,
    add_json_option: Callable[[argparse.ArgumentParser], None],
    cmd_design: CommandHandler,
) -> None:
    """Register the standalone design command parser."""

    design_parser = subparsers.add_parser(
        "design",
        help=(
            "Run Phase 1 algorithm-hypothesis search from a structured design "
            "goal markdown file."
        ),
    )
    add_json_option(design_parser)
    design_parser.add_argument(
        "goal_path",
        help="Path to the structured design goal markdown file.",
    )
    design_parser.add_argument(
        "--project-root",
        default=".",
        help=(
            "Local scientific package source tree to analyze. Defaults to the "
            "current directory."
        ),
    )
    design_parser.add_argument(
        "--output-root",
        default=None,
        help="Optional override for the `.fermilink-design/` output directory.",
    )
    design_parser.add_argument(
        "--provider",
        default=None,
        help="Override the agent provider for design turns.",
    )
    design_parser.add_argument(
        "--provider-bin",
        default=None,
        help="Optional provider binary override passed through to the agent runtime.",
    )
    design_parser.add_argument(
        "--sandbox",
        default=None,
        help="Override the provider sandbox mode for design turns.",
    )
    design_parser.add_argument(
        "--sandbox-policy",
        default=None,
        help="Override the provider sandbox policy (`enforce` or `bypass`).",
    )
    design_parser.add_argument(
        "--model",
        default=None,
        help="Optional provider model override.",
    )
    design_parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="Optional provider reasoning-effort override.",
    )
    design_parser.add_argument(
        "--max-candidates",
        type=int,
        default=12,
        help="Maximum number of candidate hypotheses to request.",
    )
    design_parser.add_argument(
        "--shortlist-size",
        type=int,
        default=5,
        help="Number of top-ranked candidates to include in the report.",
    )
    design_parser.add_argument(
        "--search-profile",
        choices=("conservative", "balanced", "novelty-seeking", "moonshot"),
        default="balanced",
        help=(
            "Ranking preset for archive prioritization. `balanced` preserves the "
            "current conservative default."
        ),
    )
    design_parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing baseline and archive artifacts when present.",
    )
    design_parser.set_defaults(func=cmd_design)
