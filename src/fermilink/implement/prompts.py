from __future__ import annotations

import json
import re

from fermilink.cli.workflow_prompts import LOOP_DONE_TOKEN


IMPLEMENTATION_DESCRIPTION_TAG = "implementation_description"
DECISION_TAG = "decision"
CONTROLLER_SUMMARY_TAG = "controller_summary"

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
        "done criteria in the contract.\n"
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
        "- Accept only implementations that genuinely advance the goal contract.\n"
        "- Partial progress may be accepted if validation score improves and the implementation is honest.\n"
        "\n"
        "When finished, reply with exactly:\n"
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
        "2. Decide whether the candidate is honest and goal-aligned.\n"
        "3. If validation_context.hard_reject is true, output REJECTED.\n"
        "\n"
        "When done, reply with exactly:\n"
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
