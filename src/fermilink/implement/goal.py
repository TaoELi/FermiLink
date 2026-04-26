"""Parse implementation goal markdown into a structured specification."""

from __future__ import annotations

import re
from typing import Any


_GOAL_SECTION_MARKERS = [
    "## package",
    "## target",
    "## editable scope",
    "## input api",
    "## desired outputs",
    "## baseline",
    "## reference",
    "## representative workloads",
    "## build",
    "## validation",
    "## done criteria",
    "# implementation goal",
]

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")


def is_goal_markdown(text: str) -> bool:
    """Return True when markdown looks like an implementation goal."""

    lowered = str(text or "").lower()
    return sum(1 for marker in _GOAL_SECTION_MARKERS if marker in lowered) >= 2


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
            continue
        current_lines.append(line)
    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()
    return sections


def _text_section(sections: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(sections.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _first_line(sections: dict[str, str], *keys: str) -> str:
    text = _text_section(sections, *keys)
    return text.splitlines()[0].strip() if text else ""


def _list_section(sections: dict[str, str], *keys: str) -> list[str]:
    for key in keys:
        text = str(sections.get(key, "") or "").strip()
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


def _code_blocks(sections: dict[str, str], *keys: str) -> list[str]:
    blocks: list[str] = []
    for key in keys:
        text = str(sections.get(key, "") or "")
        current: list[str] | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                if current is None:
                    current = []
                else:
                    blocks.append("\n".join(current).strip())
                    current = None
                continue
            if current is not None:
                current.append(line)
    return [block for block in blocks if block]


def _replace_or_append_section(
    text: str,
    *,
    headings: set[str],
    replacement_heading: str,
    replacement_body: str,
) -> str:
    lines = str(text or "").splitlines()
    output: list[str] = []
    index = 0
    replaced = False
    while index < len(lines):
        line = lines[index]
        match = _HEADING_RE.match(line)
        if not match or match.group(2).strip().lower() not in headings:
            output.append(line)
            index += 1
            continue
        replaced = True
        level = len(match.group(1))
        output.append(f"{'#' * level} {replacement_heading}")
        if replacement_body.strip():
            output.append(replacement_body.strip())
        index += 1
        while index < len(lines):
            next_match = _HEADING_RE.match(lines[index])
            if next_match and len(next_match.group(1)) <= level:
                break
            index += 1
    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(f"## {replacement_heading}")
        if replacement_body.strip():
            output.append(replacement_body.strip())
    return "\n".join(output).rstrip() + "\n"


def render_worker_visible_goal(
    text: str,
    *,
    worker_workloads: list[str],
    split_enabled: bool,
) -> str:
    """Render a worker copy of goal.md with controller-only details redacted."""

    workload_body = "\n".join(
        f"- {item}" for item in worker_workloads if str(item).strip()
    )
    if not workload_body:
        workload_body = (
            "- No worker-visible representative workloads are specified. "
            "Controller-only holdouts are hidden."
        )
    rendered = _replace_or_append_section(
        text,
        headings={"representative workloads", "workloads", "cases", "test cases"},
        replacement_heading="Representative Workloads",
        replacement_body=workload_body,
    )
    if split_enabled:
        rendered = _replace_or_append_section(
            rendered,
            headings={"validation", "checks"},
            replacement_heading="Validation",
            replacement_body=(
                "Implementation-mode validation is generated in the YAML "
                "contract. Controller-only validation commands and held-out "
                "workloads may be hidden from the worker."
            ),
        )
    return rendered


def parse_goal(text: str) -> dict[str, Any]:
    """Parse a goal.md file for implementation mode.

    Missing sections are represented as empty strings or lists so callers can
    decide whether to infer a contract or require explicit user input.
    """

    sections = _extract_sections(text)
    baseline = _text_section(
        sections,
        "baseline",
        "reference",
        "baseline / reference",
        "baseline/reference",
    )
    build_commands = _code_blocks(sections, "build", "setup", "install")
    legacy_pre_commands = _code_blocks(sections, "pre commands", "precommands")
    return {
        "raw_text": str(text or ""),
        "package": _first_line(sections, "package"),
        "target": _text_section(sections, "target"),
        "editable_scope": _list_section(
            sections,
            "editable scope",
            "scope",
            "editable paths",
        ),
        "input_api": _text_section(sections, "input api", "api", "input interface"),
        "desired_outputs": _list_section(
            sections,
            "desired outputs",
            "outputs",
            "expected outputs",
        ),
        "baseline_reference": baseline,
        "baseline_optional": not bool(baseline.strip()),
        "workloads": _list_section(
            sections,
            "representative workloads",
            "workloads",
            "cases",
            "test cases",
        ),
        "validation": _text_section(sections, "validation", "checks"),
        "validation_commands": _code_blocks(sections, "validation", "checks"),
        "build_commands": build_commands,
        "pre_commands": legacy_pre_commands,
        "done_criteria": _list_section(
            sections,
            "done criteria",
            "completion criteria",
            "acceptance criteria",
        ),
        "non_goals": _list_section(sections, "non goals", "non-goals"),
        "language": _first_line(sections, "language"),
        "notes": _text_section(sections, "notes"),
    }
