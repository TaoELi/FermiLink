from __future__ import annotations

import json
import re
from typing import Any


SOURCE_ANALYSIS_TAG = "source_analysis"
IMPLEMENTATION_CONTRACT_TAG = "implementation_contract"
VALIDATION_RUNNER_TAG = "validation_runner"
ANALYSIS_SUMMARY_TAG = "analysis_summary"
REVIEW_NOTES_TAG = "review_notes"

SOURCE_ANALYSIS_RE = re.compile(
    rf"<{SOURCE_ANALYSIS_TAG}>\s*(.*?)\s*</{SOURCE_ANALYSIS_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
IMPLEMENTATION_CONTRACT_RE = re.compile(
    rf"<{IMPLEMENTATION_CONTRACT_TAG}>\s*(.*?)\s*</{IMPLEMENTATION_CONTRACT_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
VALIDATION_RUNNER_RE = re.compile(
    rf"<{VALIDATION_RUNNER_TAG}>\s*(.*?)\s*</{VALIDATION_RUNNER_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
ANALYSIS_SUMMARY_RE = re.compile(
    rf"<{ANALYSIS_SUMMARY_TAG}>\s*(.*?)\s*</{ANALYSIS_SUMMARY_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
REVIEW_NOTES_RE = re.compile(
    rf"<{REVIEW_NOTES_TAG}>\s*(.*?)\s*</{REVIEW_NOTES_TAG}>",
    re.IGNORECASE | re.DOTALL,
)


def extract_source_analysis(text: str) -> dict[str, Any] | None:
    match = SOURCE_ANALYSIS_RE.search(str(text or ""))
    if not match:
        return None
    try:
        payload = json.loads(match.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def extract_implementation_contract(text: str) -> str | None:
    match = IMPLEMENTATION_CONTRACT_RE.search(str(text or ""))
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def extract_validation_runner(text: str) -> str | None:
    match = VALIDATION_RUNNER_RE.search(str(text or ""))
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def extract_analysis_summary(text: str) -> str | None:
    match = ANALYSIS_SUMMARY_RE.search(str(text or ""))
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def extract_review_notes(text: str) -> str | None:
    match = REVIEW_NOTES_RE.search(str(text or ""))
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def build_source_analysis_agents_md(*, goal_rel: str, autogen_rel: str) -> str:
    return (
        "# FermiLink Implement Source Analysis Mode\n"
        "\n"
        "You are analyzing a codebase for a new scientific implementation task.\n"
        "\n"
        "Read these first:\n"
        f"- `{goal_rel}`\n"
        "- Source files referenced by editable scope\n"
        "- Existing tests/examples/build files\n"
        "\n"
        "You may read any repository file.\n"
        f"You may only write to `{autogen_rel}`.\n"
        "Do not modify source code.\n"
    )


def build_contract_generation_agents_md(
    *,
    goal_rel: str,
    analysis_rel: str,
    autogen_rel: str,
) -> str:
    return (
        "# FermiLink Implement Contract Generation Mode\n"
        "\n"
        "You are generating a progressive implementation contract and optional validation runner.\n"
        "\n"
        "Read these first:\n"
        f"- `{goal_rel}`\n"
        f"- `{analysis_rel}`\n"
        "\n"
        "You may read any repository file.\n"
        f"You may only write to `{autogen_rel}`.\n"
        "Do not modify source code.\n"
    )


def build_source_analysis_prompt(
    *,
    goal_spec: dict[str, Any],
    goal_rel: str,
    tracked_file_summary: str,
) -> str:
    return (
        "Perform source analysis for FermiLink implement mode.\n"
        "\n"
        f"Goal file: `{goal_rel}`\n"
        "Full goal:\n"
        "```\n"
        f"{goal_spec.get('raw_text') or ''}\n"
        "```\n"
        "\n"
        "Repository file summary:\n"
        f"{tracked_file_summary}\n"
        "\n"
        "Identify the target API, natural insertion points, existing tests, "
        "build/runtime commands, representative workloads, useful observables, "
        "and risks for cheating or overfitting.\n"
        "\n"
        "Return exactly one JSON object inside:\n"
        f"<{SOURCE_ANALYSIS_TAG}>...</{SOURCE_ANALYSIS_TAG}>\n"
        "with keys such as package, language, target_files, existing_tests, "
        "proposed_api, validation_strategy, and risks. You may also include:\n"
        f"<{ANALYSIS_SUMMARY_TAG}>one sentence</{ANALYSIS_SUMMARY_TAG}>\n"
        f"<{REVIEW_NOTES_TAG}>notes</{REVIEW_NOTES_TAG}>\n"
    )


def build_contract_generation_prompt(
    *,
    goal_spec: dict[str, Any],
    goal_rel: str,
    analysis: dict[str, Any],
    analysis_rel: str,
    default_contract_yaml: str,
    contract_rel: str,
    runner_rel: str,
) -> str:
    return (
        "Generate a progressive implementation contract for FermiLink implement mode.\n"
        "\n"
        f"Goal: `{goal_rel}`\n"
        f"Analysis: `{analysis_rel}`\n"
        "\n"
        "Goal content:\n"
        "```\n"
        f"{goal_spec.get('raw_text') or ''}\n"
        "```\n"
        "\n"
        "Structured analysis:\n"
        f"{json.dumps(analysis, indent=2, sort_keys=True)}\n"
        "\n"
        "Fallback contract template:\n"
        "```yaml\n"
        f"{default_contract_yaml}\n"
        "```\n"
        "\n"
        "Write or return a contract at:\n"
        f"- `{contract_rel}`\n"
        "Optional validation runner path:\n"
        f"- `{runner_rel}`\n"
        "\n"
        "The contract must keep baseline/reference optional, define editable scope, "
        "input API expectations, desired outputs, progressive validation commands, "
        "score-based partial acceptance, final done criteria, and anti-cheating guardrails. "
        "Derive deterministic worker/controller `pre_commands` from the goal "
        "`## Build` section when present; treat `## Pre Commands` as a legacy alias only.\n"
        "\n"
        "Return corrected YAML inside:\n"
        f"<{IMPLEMENTATION_CONTRACT_TAG}>...</{IMPLEMENTATION_CONTRACT_TAG}>\n"
        "If you provide a runner, put it inside:\n"
        f"<{VALIDATION_RUNNER_TAG}>...</{VALIDATION_RUNNER_TAG}>\n"
        f"<{REVIEW_NOTES_TAG}>notes</{REVIEW_NOTES_TAG}>\n"
    )
