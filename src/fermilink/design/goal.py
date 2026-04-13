"""Parse design goal markdown into a structured specification."""

from __future__ import annotations

import re
from typing import Any


_GOAL_SECTION_MARKERS = [
    "## package",
    "## scientific problem",
    "## target kernel",
    "## target regime",
    "## current baseline",
    "## required invariants",
    "## accuracy/error budget",
    "## representative workloads",
    "# design goal",
]

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")


def is_design_goal_markdown(text: str) -> bool:
    lowered = str(text or "").lower()
    count = sum(1 for marker in _GOAL_SECTION_MARKERS if marker in lowered)
    return count >= 2


def _extract_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in str(text or "").splitlines():
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
    for key in keys:
        value = sections.get(key, "").strip()
        if value:
            return value
    return ""


def _first_line(sections: dict[str, str], *keys: str) -> str:
    value = _text_section(sections, *keys)
    return value.splitlines()[0].strip() if value else ""


def _list_section(sections: dict[str, str], *keys: str) -> list[str]:
    for key in keys:
        value = sections.get(key, "").strip()
        if not value:
            continue
        items: list[str] = []
        for line in value.splitlines():
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


def _code_blocks(sections: dict[str, str], *keys: str) -> list[str]:
    for key in keys:
        text = sections.get(key, "")
        if not text:
            continue
        blocks: list[str] = []
        current: list[str] | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                if current is None:
                    current = []
                else:
                    blocks.append("\n".join(current))
                    current = None
            elif current is not None:
                current.append(line)
        if blocks:
            return blocks
    return []


def parse_goal(text: str) -> dict[str, Any]:
    sections = _extract_sections(text)
    return {
        "raw_text": str(text or ""),
        "package": _first_line(sections, "package"),
        "scientific_problem": _text_section(
            sections,
            "scientific problem",
            "problem",
            "target",
        ),
        "target_kernel": _text_section(sections, "target kernel", "kernel"),
        "target_regime": _text_section(sections, "target regime", "regime"),
        "current_baseline": _text_section(
            sections,
            "current baseline",
            "baseline",
        ),
        "editable_scope": _list_section(
            sections,
            "editable scope",
            "scope",
            "editable paths",
        ),
        "required_invariants": _list_section(
            sections,
            "required invariants",
            "invariants",
        ),
        "accuracy_error_budget": _list_section(
            sections,
            "accuracy/error budget",
            "accuracy budget",
            "error budget",
        ),
        "workloads": _list_section(
            sections,
            "representative workloads",
            "workloads",
            "cases",
        ),
        "prior_art_hints": _list_section(
            sections,
            "prior-art hints",
            "prior art hints",
            "prior art",
        ),
        "excluded_directions": _list_section(
            sections,
            "excluded directions",
            "avoid",
            "do not propose",
        ),
        "local_evidence_paths": _list_section(
            sections,
            "local evidence",
            "evidence sources",
            "papers and docs",
        ),
        "deliverable_preferences": _list_section(
            sections,
            "deliverable preferences",
            "deliverables",
        ),
        "language": _first_line(sections, "language"),
        "notes": _text_section(sections, "notes"),
        "build_commands": _code_blocks(sections, "build", "setup", "install"),
    }
