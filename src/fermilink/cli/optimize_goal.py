"""Parse goal.md into a structured optimization goal specification.

Goal mode lets users describe optimization intent in natural-language
markdown instead of authoring a benchmark YAML contract and runner script
by hand.  This module parses the structured markdown into a dict that
the source-analysis / benchmark-generation pipeline can consume.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Goal detection
# ---------------------------------------------------------------------------

_GOAL_SECTION_MARKERS = [
    "## package",
    "## target",
    "## editable scope",
    "## performance metric",
    "## correctness constraints",
    "## correctness",
    "## representative workloads",
    "## workloads",
    "# optimization goal",
]


def is_goal_markdown(text: str) -> bool:
    """Return *True* when *text* looks like a goal-structured markdown file.

    Detection is based on the presence of at least two known goal-section
    headings.  This intentionally stays loose so that users can omit
    optional sections while still triggering goal mode.
    """

    lowered = text.lower()
    count = sum(1 for marker in _GOAL_SECTION_MARKERS if marker in lowered)
    return count >= 2


# ---------------------------------------------------------------------------
# Section extraction helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")


def _extract_sections(text: str) -> dict[str, str]:
    """Split markdown into ``{lowercased heading: body text}`` pairs."""

    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if current_heading:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = match.group(2).strip().lower()
            current_lines = []
        else:
            current_lines.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()

    return sections


def _text_section(sections: dict[str, str], *keys: str) -> str:
    """Return the body of the first matching section, or empty string."""

    for key in keys:
        text = sections.get(key, "").strip()
        if text:
            return text
    return ""


def _first_line(sections: dict[str, str], key: str) -> str:
    text = _text_section(sections, key)
    return text.splitlines()[0].strip() if text else ""


def _list_section(sections: dict[str, str], *keys: str) -> list[str]:
    """Extract a bullet-list section, trying *keys* in order."""

    for key in keys:
        text = sections.get(key, "").strip()
        if not text:
            continue
        items: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            for prefix in ("- ", "* ", "+ "):
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix) :]
                    break
            stripped = stripped.strip()
            if stripped:
                items.append(stripped)
        if items:
            return items
    return []


def _code_blocks(sections: dict[str, str], key: str) -> list[str]:
    """Extract fenced code blocks from a section body."""

    text = sections.get(key, "")
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if current is not None:
                blocks.append("\n".join(current))
                current = None
            else:
                current = []
        elif current is not None:
            current.append(line)
    return blocks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_goal(text: str) -> dict[str, Any]:
    """Parse a goal markdown document into a structured specification.

    Returns a dict with the following keys (all strings or lists of
    strings; missing sections default to empty):

    * ``raw_text`` – the original markdown
    * ``package`` – package identifier (first line of ``## Package``)
    * ``target`` – free-text optimization target description
    * ``editable_scope`` – list of file-path globs the agent may edit
    * ``performance_metric`` – description of the metric to optimise
    * ``correctness_constraints`` – list of correctness requirements
    * ``workloads`` – list of representative workload descriptions
    * ``language`` – optional language hint (``python``, ``cpp``, ``fortran``, …)
    * ``notes`` – optional free-form notes
    * ``build_commands`` – optional build/install commands from code blocks
    """

    sections = _extract_sections(text)

    return {
        "raw_text": text,
        "package": _first_line(sections, "package"),
        "target": _text_section(sections, "target"),
        "editable_scope": _list_section(
            sections, "editable scope", "scope", "editable paths"
        ),
        "performance_metric": _text_section(
            sections, "performance metric", "metric", "objective"
        ),
        "correctness_constraints": _list_section(
            sections,
            "correctness constraints",
            "correctness",
            "constraints",
        ),
        "workloads": _list_section(
            sections,
            "representative workloads",
            "workloads",
            "cases",
            "test cases",
        ),
        "language": _first_line(sections, "language"),
        "notes": _text_section(sections, "notes"),
        "build_commands": _code_blocks(sections, "build")
        or _code_blocks(sections, "setup")
        or _code_blocks(sections, "install"),
    }
