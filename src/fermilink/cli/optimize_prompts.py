from __future__ import annotations

import json
import re


EXPERIMENT_DESCRIPTION_TAG = "experiment_description"
DECISION_TAG = "decision"
CONTROLLER_SUMMARY_TAG = "controller_summary"
EXPERIMENT_DESCRIPTION_TOKEN_RE = re.compile(
    rf"<{EXPERIMENT_DESCRIPTION_TAG}>\s*(.*?)\s*</{EXPERIMENT_DESCRIPTION_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
DECISION_TOKEN_RE = re.compile(
    rf"<{DECISION_TAG}>\s*(.*?)\s*</{DECISION_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
CONTROLLER_SUMMARY_TOKEN_RE = re.compile(
    rf"<{CONTROLLER_SUMMARY_TAG}>\s*(.*?)\s*</{CONTROLLER_SUMMARY_TAG}>",
    re.IGNORECASE | re.DOTALL,
)


def default_program_markdown(*, package_id: str, benchmark_id: str) -> str:
    """Return the default optimize-program playbook."""

    return (
        "# FermiLink Optimize Program\n"
        "\n"
        f"- package_id: {package_id}\n"
        f"- benchmark_id: {benchmark_id}\n"
        "\n"
        "## Purpose\n"
        "- Search the code implementation space for better performance while "
        "preserving benchmark correctness.\n"
        "- Use one experiment at a time.\n"
        "- Keep accepted changes simple, reproducible, and benchmark-backed.\n"
        "\n"
        "## Workflow\n"
        "1. Read the benchmark contract, skills, memory, and recent results.\n"
        "2. Propose exactly one candidate change.\n"
        "3. Edit only benchmark-approved source files.\n"
        "4. Optionally run quick local checks, but do not run the authoritative "
        "benchmark command.\n"
        "5. Stop after the candidate change is ready.\n"
        "\n"
        "## Heuristics\n"
        "- Prefer simpler accepted changes when gains are marginal.\n"
        "- Avoid broad refactors that blur the causal source of improvement.\n"
        "- When stuck, change one dominant hypothesis instead of many knobs at once.\n"
        "- Never weaken tolerances or special-case benchmark inputs.\n"
    )


def build_optimize_agents_md(
    *,
    benchmark_rel: str,
    program_rel: str,
    memory_rel: str,
    results_rel: str,
    editable_paths: list[str],
    immutable_paths: list[str],
) -> str:
    """Return temporary AGENTS instructions for optimize-mode turns."""

    editable_block = "\n".join(f"- `{item}`" for item in editable_paths) or "- `(none)`"
    immutable_block = (
        "\n".join(f"- `{item}`" for item in immutable_paths) or "- `(none)`"
    )
    return (
        "# FermiLink Optimize Mode\n"
        "\n"
        "You are running under a dedicated optimization controller.\n"
        "\n"
        "Read these first:\n"
        f"- `{benchmark_rel}`\n"
        f"- `{program_rel}`\n"
        f"- `{memory_rel}`\n"
        f"- `{results_rel}`\n"
        "- `skills/`\n"
        "\n"
        "Edit scope:\n"
        f"{editable_block}\n"
        "\n"
        "Never edit:\n"
        f"{immutable_block}\n"
        "- `skills/`\n"
        "- `.fermilink-optimize/`\n"
        "\n"
        "Rules:\n"
        "- Make exactly one candidate experiment per turn.\n"
        "- Do not weaken tolerances or special-case benchmark cases.\n"
        "- Do not add dependencies.\n"
        "- Do not run the authoritative benchmark command from the benchmark contract.\n"
        "- Quick local checks are allowed when cheap and directly relevant.\n"
        "\n"
        "When finished, reply with exactly one short tag block and no long summary:\n"
        f"<{EXPERIMENT_DESCRIPTION_TAG}>short description of the experiment</{EXPERIMENT_DESCRIPTION_TAG}>\n"
    )


def build_controller_agents_md(
    *,
    benchmark_rel: str,
    program_rel: str,
    memory_rel: str,
    results_rel: str,
    run_rel: str,
) -> str:
    """Return temporary AGENTS instructions for controller-review turns."""

    return (
        "# FermiLink Optimize Controller Mode\n"
        "\n"
        "You are the benchmark review controller for one completed optimization iteration.\n"
        "\n"
        "Read these first:\n"
        f"- `{benchmark_rel}`\n"
        f"- `{program_rel}`\n"
        f"- `{memory_rel}`\n"
        f"- `{results_rel}`\n"
        f"- `{run_rel}`\n"
        "\n"
        "Edit scope:\n"
        f"- `{memory_rel}`\n"
        "\n"
        "Never edit:\n"
        "- source code files\n"
        "- benchmark files\n"
        "- `results.tsv`\n"
        "- `state.json`\n"
        "\n"
        "Rules:\n"
        "- Update only the optimize memory with a thoughtful postmortem of this iteration.\n"
        "- Record what changed, what happened in benchmarking, what was learned, and what to try next.\n"
        "- Decide whether this candidate should become the new incumbent.\n"
        "- Your decision must respect any hard scientific failures described in the prompt.\n"
        "\n"
        "When finished, reply with exactly these tags and no long free-form summary:\n"
        f"<{DECISION_TAG}>ACCEPTED or REJECTED</{DECISION_TAG}>\n"
        f"<{CONTROLLER_SUMMARY_TAG}>one-line reason</{CONTROLLER_SUMMARY_TAG}>\n"
    )


def build_optimize_prompt(
    *,
    benchmark_payload: dict[str, object],
    benchmark_rel: str,
    program_rel: str,
    memory_rel: str,
    results_rel: str,
    recent_results_text: str,
    state_payload: dict[str, object],
    editable_paths: list[str],
) -> str:
    """Build the one-shot optimize prompt."""

    benchmark_id = str(benchmark_payload.get("benchmark_id") or "benchmark")
    objective = (
        benchmark_payload.get("controller", {})
        if isinstance(benchmark_payload.get("controller"), dict)
        else {}
    )
    primary = (
        objective.get("objective", {})
        if isinstance(objective.get("objective"), dict)
        else {}
    )
    primary_metric = str(primary.get("primary_metric") or "primary_metric")
    direction = str(primary.get("direction") or "minimize")
    latest_commit = str(state_payload.get("incumbent_commit") or "unknown")
    latest_metric = state_payload.get("incumbent_primary_metric")
    latest_metric_text = (
        f"{latest_metric:.12g}"
        if isinstance(latest_metric, (int, float))
        else "unknown"
    )
    editable_json = json.dumps(editable_paths, indent=2)
    return (
        "You are running in **FermiLink optimize mode**.\n"
        "\n"
        f"Benchmark contract: `{benchmark_rel}`\n"
        f"Optimize program: `{program_rel}`\n"
        f"Persistent memory: `{memory_rel}`\n"
        f"Results ledger: `{results_rel}`\n"
        "\n"
        f"Benchmark id: {benchmark_id}\n"
        f"Current incumbent commit: {latest_commit}\n"
        f"Current incumbent {primary_metric}: {latest_metric_text}\n"
        f"Objective direction: {direction}\n"
        "\n"
        "You must propose exactly one candidate experiment.\n"
        "Only edit benchmark-approved source files.\n"
        "Do not edit `skills/`, `.fermilink-optimize/`, or the benchmark files.\n"
        "Do not run the authoritative benchmark command; the controller will do that.\n"
        "\n"
        "Editable path globs:\n"
        f"{editable_json}\n"
        "\n"
        "Recent results:\n"
        "<<<RESULTS\n"
        f"{recent_results_text.strip() or '(no prior results)'}\n"
        "RESULTS>>>\n"
        "\n"
        "Read the optimize program and skills first, then make one concrete code change.\n"
        "When done, reply with exactly one tag block:\n"
        f"<{EXPERIMENT_DESCRIPTION_TAG}>short description</{EXPERIMENT_DESCRIPTION_TAG}>\n"
    )


def build_controller_prompt(
    *,
    benchmark_payload: dict[str, object],
    benchmark_rel: str,
    program_rel: str,
    memory_rel: str,
    results_rel: str,
    run_rel: str,
    recent_results_text: str,
    iteration: int,
    incumbent_commit: str,
    candidate_commit: str | None,
    worker_description: str,
    changed_paths: list[str],
    evaluation_context: dict[str, object],
) -> str:
    """Build the controller-review prompt after one candidate attempt."""

    benchmark_id = str(benchmark_payload.get("benchmark_id") or "benchmark")
    changed_json = json.dumps(changed_paths, indent=2)
    evaluation_json = json.dumps(evaluation_context, indent=2, sort_keys=True)
    return (
        "You are the controller for a completed FermiLink optimize iteration.\n"
        "\n"
        f"Benchmark contract: `{benchmark_rel}`\n"
        f"Optimize program: `{program_rel}`\n"
        f"Persistent memory to update: `{memory_rel}`\n"
        f"Results ledger: `{results_rel}`\n"
        f"Run artifacts directory: `{run_rel}`\n"
        "\n"
        f"Benchmark id: {benchmark_id}\n"
        f"Iteration: {iteration}\n"
        f"Incumbent commit before review: {incumbent_commit or 'unknown'}\n"
        f"Candidate commit: {candidate_commit or 'none'}\n"
        f"Worker experiment description: {worker_description}\n"
        "\n"
        "Candidate changed paths:\n"
        f"{changed_json}\n"
        "\n"
        "Evaluation context (authoritative):\n"
        f"{evaluation_json}\n"
        "\n"
        "Recent results:\n"
        "<<<RESULTS\n"
        f"{recent_results_text.strip() or '(no prior results)'}\n"
        "RESULTS>>>\n"
        "\n"
        "Your tasks:\n"
        "1. Update `memory.md` with a reflective entry for this iteration.\n"
        "2. Include: hypothesis, changed files, benchmark outcome, lesson learned, and next hypothesis.\n"
        "3. Decide whether this candidate should be ACCEPTED or REJECTED.\n"
        "4. If the evaluation context says `hard_reject=true`, you must output REJECTED.\n"
        "\n"
        "When done, reply with exactly:\n"
        f"<{DECISION_TAG}>ACCEPTED or REJECTED</{DECISION_TAG}>\n"
        f"<{CONTROLLER_SUMMARY_TAG}>one-line reason</{CONTROLLER_SUMMARY_TAG}>\n"
    )


def extract_experiment_description(text: str) -> str | None:
    """Extract a short experiment description tag from assistant text."""

    match = EXPERIMENT_DESCRIPTION_TOKEN_RE.search(str(text or ""))
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip()
    return value or None


def extract_decision(text: str) -> str | None:
    """Extract an ACCEPTED/REJECTED decision tag from controller text."""

    match = DECISION_TOKEN_RE.search(str(text or ""))
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip().upper()
    if value in {"ACCEPTED", "REJECTED"}:
        return value
    return None


def extract_controller_summary(text: str) -> str | None:
    """Extract a one-line controller summary tag."""

    match = CONTROLLER_SUMMARY_TOKEN_RE.search(str(text or ""))
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip()
    return value or None
