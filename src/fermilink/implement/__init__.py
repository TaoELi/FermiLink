"""Implementation-mode orchestration for FermiLink.

The package is intentionally runnable without CLI registration:

``python -m fermilink.implement.main goal.md``
"""

from .campaign import read_campaign_status, run_goal_campaign

__all__ = ["read_campaign_status", "run_goal_campaign"]
