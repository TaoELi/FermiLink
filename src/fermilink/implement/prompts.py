from __future__ import annotations

import json
import re
from typing import Any

from fermilink.cli.workflow_prompts import LOOP_DONE_TOKEN


IMPLEMENTATION_DESCRIPTION_TAG = "implementation_description"
DECISION_TAG = "decision"
CONTROLLER_SUMMARY_TAG = "controller_summary"
CONTROLLER_REVIEW_TAG = "controller_review"

IMPLEMENTATION_DESCRIPTION_RE = re.compile(
    rf"<{IMPLEMENTATION_DESCRIPTION_TAG}>\s*(.*?)\s*</{IMPLEMENTATION_DESCRIPTION_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
DECISION_RE = re.compile(
    rf"<{DECISION_TAG}>\s*(.*?)\s*</{DECISION_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
CONTROLLER_SUMMARY_RE = re.compile(
    rf"<{CONTROLLER_SUMMARY_TAG}>\s*(.*?)\s*</{CONTROLLER_SUMMARY_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
CONTROLLER_REVIEW_RE = re.compile(
    rf"<{CONTROLLER_REVIEW_TAG}>\s*(.*?)\s*</{CONTROLLER_REVIEW_TAG}>",
    re.IGNORECASE | re.DOTALL,
)


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_review_decision(value: object) -> str:
    decision = str(value or "").strip().upper()
    return decision if decision in {"ACCEPTED", "REJECTED"} else ""


def _normalize_controller_review(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    decision = _normalize_review_decision(
        payload.get("decision") or payload.get("verdict")
    )
    if decision:
        normalized["decision"] = decision
    summary = str(
        payload.get("summary")
        or payload.get("controller_summary")
        or payload.get("reason")
        or ""
    ).strip()
    if summary:
        normalized["summary"] = summary
    requirements: list[dict[str, Any]] = []
    raw_requirements = payload.get("requirements")
    if isinstance(raw_requirements, list):
        for index, raw_item in enumerate(raw_requirements, start=1):
            if not isinstance(raw_item, dict):
                continue
            requirement_id = str(
                raw_item.get("id") or raw_item.get("name") or f"requirement_{index}"
            ).strip()
            verdict = str(raw_item.get("verdict") or raw_item.get("status") or "").strip()
            requirements.append(
                {
                    **raw_item,
                    "id": requirement_id or f"requirement_{index}",
                    "verdict": verdict.lower(),
                    "evidence": _string_list(raw_item.get("evidence")),
                    "required": bool(raw_item.get("required", True)),
                }
            )
    if requirements:
        normalized["requirements"] = requirements
    risks = _string_list(payload.get("risks"))
    if risks:
        normalized["risks"] = risks
    return normalized


def default_program_markdown(*, package_id: str, goal_rel: str, contract_rel: str) -> str:
    return (
        "# FermiLink Implement Program\n"
        "\n"
        f"- package_id: {package_id}\n"
        f"- goal_path: {goal_rel}\n"
        f"- contract_path: {contract_rel}\n"
        "\n"
        "## Purpose\n"
        "- Implement the requested scientific feature through focused, validated steps.\n"
        "- Accept partial implementations only when they improve measurable progress.\n"
        "- Finish only when the generated contract's done criteria are satisfied.\n"
        "\n"
        "## Rules\n"
        "- Keep edits inside editable scope.\n"
        "- Do not weaken validation, hard-code workload answers, or bypass real code paths.\n"
        "- Prefer small implementation steps that make review and rollback clear.\n"
    )


def build_worker_agents_md(
    *,
    goal_rel: str,
    contract_rel: str,
    program_rel: str,
    controller_memory_rel: str,
    worker_memory_rel: str,
    results_rel: str,
    editable_paths: list[str],
    immutable_paths: list[str],
) -> str:
    editable_block = "\n".join(f"- `{item}`" for item in editable_paths) or "- `(none)`"
    immutable_block = (
        "\n".join(f"- `{item}`" for item in immutable_paths) or "- `(none)`"
    )
    return (
        "# FermiLink Implement Worker Mode\n"
        "\n"
        "You are implementing one focused step under a controller.\n"
        "\n"
        "Read these first:\n"
        f"- `{goal_rel}`\n"
        f"- `{contract_rel}`\n"
        f"- `{program_rel}`\n"
        f"- `{controller_memory_rel}`\n"
        f"- `{worker_memory_rel}`\n"
        f"- `{results_rel}`\n"
        "- `skills/` if present\n"
        "\n"
        "Editable paths:\n"
        f"{editable_block}\n"
        f"- `{worker_memory_rel}`\n"
        "\n"
        "Never edit:\n"
        f"{immutable_block}\n"
        f"- `{contract_rel}`\n"
        f"- `{controller_memory_rel}`\n"
        f"- `{results_rel}`\n"
        "- `.fermilink-implement/state.json`\n"
        "- `.fermilink-implement/runs/`\n"
        "\n"
        "Rules:\n"
        "- Make exactly one candidate implementation step.\n"
        "- Update worker memory with the plan and factual progress.\n"
        "- Do not weaken tests or validation artifacts.\n"
        "- Do not hard-code answers for representative workloads.\n"
        "- Treat worker-visible workloads as training/public examples; the controller may hold out private workloads and validation commands.\n"
        "- Long jobs may be launched and monitored with loop wait tags.\n"
        "\n"
        "When the candidate is ready for controller validation, reply with:\n"
        f"<{IMPLEMENTATION_DESCRIPTION_TAG}>short description</{IMPLEMENTATION_DESCRIPTION_TAG}>\n"
        f"{LOOP_DONE_TOKEN}\n"
    )


def build_worker_prompt(
    *,
    goal_rel: str,
    contract_rel: str,
    program_rel: str,
    controller_memory_rel: str,
    worker_memory_rel: str,
    results_rel: str,
    recent_results_text: str,
    state_payload: dict[str, object],
    editable_paths: list[str],
) -> str:
    incumbent_commit = str(state_payload.get("incumbent_commit") or "unknown")
    incumbent_validation = (
        state_payload.get("incumbent_validation")
        if isinstance(state_payload.get("incumbent_validation"), dict)
        else {}
    )
    incumbent_score = incumbent_validation.get("score", 0.0)
    api_locked = bool(state_payload.get("api_locked", False))
    locked_api = str(state_payload.get("locked_api") or "").strip()
    return (
        "You are running in FermiLink implement worker-loop mode.\n"
        "\n"
        f"Goal: `{goal_rel}`\n"
        f"Implementation contract: `{contract_rel}`\n"
        f"Program: `{program_rel}`\n"
        f"Controller memory: `{controller_memory_rel}`\n"
        f"Worker memory: `{worker_memory_rel}`\n"
        f"Results ledger: `{results_rel}`\n"
        "\n"
        f"Current incumbent commit: {incumbent_commit}\n"
        f"Current incumbent score: {incumbent_score}\n"
        f"API locked: {str(api_locked).lower()}\n"
        f"Locked API: {locked_api or '(not locked yet)'}\n"
        "\n"
        "Prepare exactly one implementation step. The controller can accept partial "
        "progress if validation score improves, but final completion requires the "
        "done criteria in the contract. Some controller-only workloads or "
        "validation commands may be deliberately hidden from this worker copy; "
        "implement the general target, not the visible examples only.\n"
        "\n"
        "Editable path globs:\n"
        f"{json.dumps(editable_paths, indent=2)}\n"
        "\n"
        "Recent results:\n"
        "<<<RESULTS\n"
        f"{recent_results_text.strip() or '(no prior results)'}\n"
        "RESULTS>>>\n"
        "\n"
        "When ready, reply with exactly:\n"
        f"<{IMPLEMENTATION_DESCRIPTION_TAG}>short description</{IMPLEMENTATION_DESCRIPTION_TAG}>\n"
        f"{LOOP_DONE_TOKEN}\n"
    )


def build_controller_agents_md(
    *,
    goal_rel: str,
    contract_rel: str,
    program_rel: str,
    memory_rel: str,
    results_rel: str,
    run_rel: str,
) -> str:
    return (
        "# FermiLink Implement Controller Mode\n"
        "\n"
        "You are reviewing one implementation candidate.\n"
        "\n"
        "Read these first:\n"
        f"- `{goal_rel}`\n"
        f"- `{contract_rel}`\n"
        f"- `{program_rel}`\n"
        f"- `{memory_rel}`\n"
        f"- `{results_rel}`\n"
        f"- `{run_rel}`\n"
        "\n"
        "Edit scope:\n"
        f"- `{memory_rel}`\n"
        "\n"
        "Never edit source code, validation files, results, or state.\n"
        "\n"
        "Rules:\n"
        "- Reject cheating, hard-coded answers, validation weakening, and unrelated scope changes.\n"
        "- Treat validation results as reference evidence, not as proof by themselves.\n"
        "- Accept only implementations that genuinely advance the goal contract after independent code review.\n"
        "- Partial progress may be accepted if validation score improves and the implementation is honest.\n"
        "- Final completion requires explicit evidence that the implementation satisfies the goal and YAML target.\n"
        "\n"
        "When finished, reply with exactly one structured review plus legacy summary tags:\n"
        f"<{CONTROLLER_REVIEW_TAG}>{{\n"
        '  "decision": "ACCEPTED or REJECTED",\n'
        '  "final_complete": false,\n'
        '  "summary": "one-line reason",\n'
        '  "requirements": [\n'
        '    {"id": "api", "verdict": "pass|partial|fail|not_checked", "required": true, "evidence": ["specific file/diff/test evidence"]}\n'
        "  ],\n"
        '  "validation_interpretation": "how validation supports or fails to support the verdict",\n'
        '  "risks": []\n'
        f"}}</{CONTROLLER_REVIEW_TAG}>\n"
        f"<{DECISION_TAG}>ACCEPTED or REJECTED</{DECISION_TAG}>\n"
        f"<{CONTROLLER_SUMMARY_TAG}>one-line reason</{CONTROLLER_SUMMARY_TAG}>\n"
    )


def build_controller_prompt(
    *,
    goal_rel: str,
    contract_rel: str,
    program_rel: str,
    memory_rel: str,
    results_rel: str,
    run_rel: str,
    iteration: int,
    incumbent_commit: str,
    candidate_commit: str | None,
    worker_description: str,
    changed_paths: list[str],
    validation_context: dict[str, object],
    recent_results_text: str,
) -> str:
    return (
        "You are the controller for a completed FermiLink implement iteration.\n"
        "\n"
        f"Goal: `{goal_rel}`\n"
        f"Implementation contract: `{contract_rel}`\n"
        f"Program: `{program_rel}`\n"
        f"Persistent memory to update: `{memory_rel}`\n"
        f"Results ledger: `{results_rel}`\n"
        f"Run artifacts directory: `{run_rel}`\n"
        "\n"
        f"Iteration: {iteration}\n"
        f"Incumbent commit before review: {incumbent_commit or 'unknown'}\n"
        f"Candidate commit: {candidate_commit or 'none'}\n"
        f"Worker implementation description: {worker_description}\n"
        "\n"
        "Candidate changed paths:\n"
        f"{json.dumps(changed_paths, indent=2)}\n"
        "\n"
        "Validation context:\n"
        f"{json.dumps(validation_context, indent=2, sort_keys=True)}\n"
        "\n"
        "Recent results:\n"
        "<<<RESULTS\n"
        f"{recent_results_text.strip() or '(no prior results)'}\n"
        "RESULTS>>>\n"
        "\n"
        "Your tasks:\n"
        "1. Update memory.md with a concise postmortem.\n"
        "2. Read the full controller goal, YAML target, candidate diff, changed files, validation logs, and run artifacts.\n"
        "3. Independently decide whether the code honestly satisfies the target implementation request.\n"
        "4. Treat validation as useful evidence only; do not accept final completion solely because tests passed.\n"
        "5. Check API fit, algorithm/implementation approach, controller-only holdout workloads, non-goals, backward compatibility, validation weakening, hardcoding, and bypassed code paths.\n"
        "6. If validation_context.hard_reject is true, output REJECTED.\n"
        "\n"
        "When done, reply with exactly:\n"
        f"<{CONTROLLER_REVIEW_TAG}>JSON review object</{CONTROLLER_REVIEW_TAG}>\n"
        f"<{DECISION_TAG}>ACCEPTED or REJECTED</{DECISION_TAG}>\n"
        f"<{CONTROLLER_SUMMARY_TAG}>one-line reason</{CONTROLLER_SUMMARY_TAG}>\n"
    )


def extract_implementation_description(text: str) -> str | None:
    match = IMPLEMENTATION_DESCRIPTION_RE.search(str(text or ""))
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip()
    return value or None


def extract_decision(text: str) -> str | None:
    match = DECISION_RE.search(str(text or ""))
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip().upper()
    return value if value in {"ACCEPTED", "REJECTED"} else None


def extract_controller_summary(text: str) -> str | None:
    match = CONTROLLER_SUMMARY_RE.search(str(text or ""))
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip()
    return value or None


def extract_controller_review(text: str) -> dict[str, Any] | None:
    match = CONTROLLER_REVIEW_RE.search(str(text or ""))
    if not match:
        return None
    try:
        payload = json.loads(match.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return _normalize_controller_review(payload)


def controller_review_decision(review: dict[str, Any] | None) -> str | None:
    if not isinstance(review, dict):
        return None
    decision = _normalize_review_decision(review.get("decision"))
    return decision or None


def controller_review_summary(review: dict[str, Any] | None) -> str | None:
    if not isinstance(review, dict):
        return None
    summary = str(review.get("summary") or "").strip()
    return summary or None
