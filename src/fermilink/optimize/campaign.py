"""Campaign entrypoints for optimize mode."""

from __future__ import annotations

from fermilink.optimize.main import (
    read_campaign_status,
    run_campaign,
    run_goal_campaign,
    run_quick_campaign,
)

__all__ = [
    "read_campaign_status",
    "run_campaign",
    "run_goal_campaign",
    "run_quick_campaign",
]
