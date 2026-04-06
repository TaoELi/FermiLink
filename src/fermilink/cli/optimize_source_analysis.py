"""Prompt templates and output extraction for goal-driven source analysis.

Goal mode runs two preparatory agent turns before the optimisation loop:

1. **Source analysis** – the agent reads the target package source code
   guided by the goal specification and outputs a structured JSON analysis
   of the API surface, output quantities, configuration parameters,
   discovered test cases, threading model, and build system.

2. **Benchmark generation** – the agent takes the analysis plus the goal
   and writes ``benchmark.yaml`` and ``benchmark_runner.py`` to the
   autogen directory following FermiLink's benchmark contract.

Both turns use ``temporary_optimize_agents`` for workspace-instruction
scoping and ``_run_exec_chat_turn`` for execution.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fermilink.cli.workflow_prompts import LOOP_DONE_TOKEN


# ---------------------------------------------------------------------------
# XML extraction tags
# ---------------------------------------------------------------------------

SOURCE_ANALYSIS_TAG = "source_analysis"
BENCHMARK_YAML_TAG = "benchmark_yaml"
RUNNER_SCRIPT_TAG = "runner_script"
ANALYSIS_SUMMARY_TAG = "analysis_summary"
REVIEW_NOTES_TAG = "review_notes"

SOURCE_ANALYSIS_RE = re.compile(
    rf"<{SOURCE_ANALYSIS_TAG}>\s*(.*?)\s*</{SOURCE_ANALYSIS_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
BENCHMARK_YAML_RE = re.compile(
    rf"<{BENCHMARK_YAML_TAG}>\s*(.*?)\s*</{BENCHMARK_YAML_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
RUNNER_SCRIPT_RE = re.compile(
    rf"<{RUNNER_SCRIPT_TAG}>\s*(.*?)\s*</{RUNNER_SCRIPT_TAG}>",
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


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def extract_source_analysis(text: str) -> dict[str, Any] | None:
    """Extract structured JSON source-analysis from assistant text."""

    match = SOURCE_ANALYSIS_RE.search(str(text or ""))
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(payload, dict):
        return payload
    return None


def extract_benchmark_yaml(text: str) -> str | None:
    """Extract the generated benchmark YAML from assistant text."""

    match = BENCHMARK_YAML_RE.search(str(text or ""))
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def extract_runner_script(text: str) -> str | None:
    """Extract the generated benchmark runner script from assistant text."""

    match = RUNNER_SCRIPT_RE.search(str(text or ""))
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def extract_analysis_summary(text: str) -> str | None:
    """Extract a human-readable analysis summary from assistant text."""

    match = ANALYSIS_SUMMARY_RE.search(str(text or ""))
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def extract_review_notes(text: str) -> str | None:
    """Extract review-recommended notes from assistant text."""

    match = REVIEW_NOTES_RE.search(str(text or ""))
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


# ---------------------------------------------------------------------------
# AGENTS.md templates
# ---------------------------------------------------------------------------


def build_source_analysis_agents_md(
    *,
    goal_rel: str,
    autogen_rel: str,
) -> str:
    """AGENTS.md for the source-analysis agent turn.

    The agent may read any file in the repo but may only write to the
    autogen directory.
    """

    return (
        "# FermiLink Optimize Goal Analysis Mode\n"
        "\n"
        "You are running a source-analysis turn for goal-driven optimization.\n"
        "\n"
        "Read these first:\n"
        f"- `{goal_rel}` (the user's optimization goal)\n"
        "- Source files referenced in the goal's editable scope\n"
        "- `tests/` directory (if present) for existing test cases\n"
        "- Build files (`setup.py`, `pyproject.toml`, `CMakeLists.txt`, `Makefile`, etc.)\n"
        "\n"
        "You may read any file in this repository.\n"
        f"You may only write to `{autogen_rel}`.\n"
        "\n"
        "Do not modify any source code.\n"
        "Do not run any benchmarks or tests.\n"
    )


def build_benchmark_generation_agents_md(
    *,
    goal_rel: str,
    analysis_rel: str,
    autogen_rel: str,
) -> str:
    """AGENTS.md for the benchmark-generation agent turn.

    The agent reads the goal and analysis, writes benchmark.yaml and
    benchmark_runner.py to the autogen directory.
    """

    return (
        "# FermiLink Optimize Benchmark Generation Mode\n"
        "\n"
        "You are generating benchmark files for goal-driven optimization.\n"
        "\n"
        "Read these first:\n"
        f"- `{goal_rel}` (the user's optimization goal)\n"
        f"- `{analysis_rel}` (source analysis from the previous turn)\n"
        "- Source files referenced in the analysis\n"
        "\n"
        "You may read any file in this repository.\n"
        f"You may only write to `{autogen_rel}`.\n"
        "\n"
        "Do not modify any source code outside the autogen directory.\n"
    )


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


def build_source_analysis_prompt(
    *,
    goal_spec: dict[str, Any],
    goal_rel: str,
    language: str,
    tracked_file_summary: str,
) -> str:
    """Build the prompt for the source-analysis agent turn.

    The agent reads the repo source code and produces a structured JSON
    analysis of the target package suitable for benchmark generation.
    """

    goal_text = str(goal_spec.get("raw_text") or "")
    package = str(goal_spec.get("package") or "unknown")
    target = str(goal_spec.get("target") or "")
    editable_scope = goal_spec.get("editable_scope") or []
    editable_block = (
        "\n".join(f"- `{p}`" for p in editable_scope)
        if editable_scope
        else "- (not specified — infer from source)"
    )
    workloads = goal_spec.get("workloads") or []
    workloads_block = (
        "\n".join(f"- {w}" for w in workloads)
        if workloads
        else "- (not specified — discover from source)"
    )
    correctness = goal_spec.get("correctness_constraints") or []
    correctness_block = (
        "\n".join(f"- {c}" for c in correctness)
        if correctness
        else "- (not specified — infer from source)"
    )
    metric = str(goal_spec.get("performance_metric") or "wall-clock time (minimize)")
    build_commands = goal_spec.get("build_commands") or []
    build_block = (
        "\n".join(f"```\n{cmd}\n```" for cmd in build_commands)
        if build_commands
        else "(none specified)"
    )

    return (
        "You are performing **source analysis** for FermiLink goal-driven optimization.\n"
        "\n"
        f"## Goal\n"
        f"Package: `{package}`\n"
        f"Language: `{language}`\n"
        f"Target: {target}\n"
        f"Performance metric: {metric}\n"
        "\n"
        f"### Goal file\n"
        f"Path: `{goal_rel}`\n"
        f"Full content:\n"
        "```\n"
        f"{goal_text}\n"
        "```\n"
        "\n"
        f"### Editable scope (from goal)\n"
        f"{editable_block}\n"
        "\n"
        f"### User-provided workloads\n"
        f"{workloads_block}\n"
        "\n"
        f"### User-provided correctness constraints\n"
        f"{correctness_block}\n"
        "\n"
        f"### Build commands\n"
        f"{build_block}\n"
        "\n"
        f"### Repository file listing (abbreviated)\n"
        f"{tracked_file_summary}\n"
        "\n"
        "## Your task\n"
        "\n"
        "Analyse the source code to understand the target package's API and produce\n"
        "a structured analysis.  Follow these steps:\n"
        "\n"
        "1. **Read the source code** in the editable scope paths (and surrounding\n"
        "   directories as needed) to understand the package's public API, key\n"
        "   computational entry points, and internal structure.\n"
        "\n"
        "2. **Read existing tests** (`tests/`, `test/`, `examples/`) to discover\n"
        "   representative inputs, expected outputs, and correctness checks that\n"
        "   already exist.\n"
        "\n"
        "3. **Read the build system** (`setup.py`, `pyproject.toml`, `CMakeLists.txt`,\n"
        "   `Makefile`, `configure`, etc.) to understand how the package is built\n"
        "   and what runtime command invokes it.\n"
        "\n"
        "4. **Identify**:\n"
        "   - **Entry points**: Functions, methods, or CLI commands that execute the\n"
        "     target computation (e.g. `mf.kernel()` for PySCF SCF, `lmp.run()` for\n"
        "     LAMMPS, etc.).\n"
        "   - **Output quantities** that are relevant for correctness validation\n"
        "     (e.g. total energy, forces, eigenvalues, convergence status).\n"
        "   - **Configuration parameters** that define a test case (e.g. input files,\n"
        "     molecule geometry, basis set, simulation parameters).\n"
        "   - **Threading / parallelism model** (OpenMP, MPI, internal thread pools).\n"
        "   - **Suggested test cases** (3–6 cases covering the target workload spectrum,\n"
        "     each describable as a dict of parameters).\n"
        "\n"
        "5. **Output** your analysis inside this exact XML tag (JSON body):\n"
        "\n"
        f"<{SOURCE_ANALYSIS_TAG}>\n"
        "{\n"
        '  "package": "...",\n'
        '  "language": "python|cpp|fortran|...",\n'
        '  "entry_points": [\n'
        '    {"name": "...", "module_or_file": "...", "call_signature": "...", "description": "..."}\n'
        "  ],\n"
        '  "output_quantities": [\n'
        '    {"field": "...", "type": "scalar|array|string", "description": "...",\n'
        '     "suggested_tolerance": {"mode": "abs_delta|rms_delta|relative_delta", "value": 1e-8}}\n'
        "  ],\n"
        '  "configuration_parameters": [\n'
        '    {"name": "...", "type": "...", "description": "...", "example": "..."}\n'
        "  ],\n"
        '  "threading_model": {"description": "...", "env_vars": ["OMP_NUM_THREADS"], "default_threads": 1},\n'
        '  "build_system": {"type": "pip|cmake|make|configure|...", "build_command": "...", "install_command": "..."},\n'
        '  "suggested_cases": [\n'
        '    {"id": "...", "description": "...", "parameters": {"...": "..."}, "weight": 1.0}\n'
        "  ],\n"
        '  "runtime_command_template": ["python", "-c", "..."],\n'
        '  "editable_paths": ["src/**", "lib/target.py"],\n'
        '  "immutable_paths": [".fermilink-optimize/**", "skills/**"]\n'
        "}\n"
        f"</{SOURCE_ANALYSIS_TAG}>\n"
        "\n"
        "Then output a human-readable summary of what you found:\n"
        f"<{ANALYSIS_SUMMARY_TAG}>short summary of findings</{ANALYSIS_SUMMARY_TAG}>\n"
        "\n"
        "And flag any areas where the user should review your analysis:\n"
        f"<{REVIEW_NOTES_TAG}>items for user review</{REVIEW_NOTES_TAG}>\n"
        "\n"
        f"{LOOP_DONE_TOKEN}\n"
    )


def build_benchmark_generation_prompt(
    *,
    goal_spec: dict[str, Any],
    goal_rel: str,
    analysis: dict[str, Any],
    analysis_rel: str,
    language: str,
    runner_template: str,
    benchmark_template: str,
    autogen_benchmark_rel: str,
    autogen_runner_rel: str,
) -> str:
    """Build the prompt for the benchmark-generation agent turn.

    The agent uses the source analysis and goal to generate both
    ``benchmark.yaml`` and ``benchmark_runner.py`` that conform to the
    FermiLink benchmark contract.
    """

    goal_text = str(goal_spec.get("raw_text") or "")
    package = str(goal_spec.get("package") or "unknown")
    target = str(goal_spec.get("target") or "")
    metric = str(goal_spec.get("performance_metric") or "wall-clock time (minimize)")
    analysis_json = json.dumps(analysis, indent=2, sort_keys=True)

    return (
        "You are generating benchmark files for FermiLink goal-driven optimization.\n"
        "\n"
        f"## Goal\n"
        f"Package: `{package}`\n"
        f"Language: `{language}`\n"
        f"Target: {target}\n"
        f"Performance metric: {metric}\n"
        "\n"
        f"### Goal file: `{goal_rel}`\n"
        "```\n"
        f"{goal_text}\n"
        "```\n"
        "\n"
        f"### Source analysis: `{analysis_rel}`\n"
        "```json\n"
        f"{analysis_json}\n"
        "```\n"
        "\n"
        "## FermiLink benchmark contract reference\n"
        "\n"
        "### Benchmark YAML template (reference only — adapt to this package)\n"
        "```yaml\n"
        f"{benchmark_template}\n"
        "```\n"
        "\n"
        "### Benchmark runner template (reference only — adapt to this package)\n"
        "```python\n"
        f"{runner_template}\n"
        "```\n"
        "\n"
        "## Your task\n"
        "\n"
        "Generate two files that follow the FermiLink benchmark contract:\n"
        "\n"
        "### 1. Benchmark YAML\n"
        "\n"
        f"Write a complete benchmark YAML to `{autogen_benchmark_rel}`.\n"
        "It must conform to this schema:\n"
        "- `schema_version: 1`\n"
        "- `benchmark_id`: unique identifier\n"
        f"- `package_id`: `{package}`\n"
        "- `repo.editable_paths`: glob list from the source analysis\n"
        "- `repo.immutable_paths`: must include `.fermilink-optimize/**` and `skills/**`\n"
        "- `campaign`: `max_iterations: 120`, `stop_on_consecutive_rejections: 30`\n"
        "- `worker`: `max_iterations: 8`, `wait_seconds: 1`\n"
        "- `controller`:\n"
        "  - `timeout_seconds: 1800`\n"
        "  - `warmup_runs: 1`, `measured_runs: 3`\n"
        "  - `objective.primary_metric`: a concrete metric name that your runner emits\n"
        "  - `objective.direction`: `minimize` or `maximize`\n"
        "  - `objective.min_relative_improvement: 0.02`\n"
        "  - `reject_on`: `[crash, timeout, missing_metrics, correctness_failure]`\n"
        "- `correctness`: prefer `mode: field_tolerances` with tolerance specs derived\n"
        "  from source-analysis output quantities.\n"
        "  If `correctness.mode: field_tolerances`, then\n"
        "  `correctness.field_tolerances` MUST be a non-empty list.\n"
        "  Use `mode: runner_only` ONLY when no numeric/scientific output fields can be\n"
        "  extracted for comparison. If you must use `runner_only`, set\n"
        "  `allow_runner_only: true` and explain why in review notes.\n"
        "- `runtime`:\n"
        "  - `mode: direct`\n"
        f"  - `command`: list that runs the benchmark runner at `{autogen_runner_rel}`\n"
        "    with `--benchmark {benchmark} --emit-json` arguments.\n"
        "    Use the correct interpreter for the language (python/bash).\n"
        "  - `env`: set appropriate thread/parallelism variables\n"
        "- `cases`: 3–6 test cases from the source analysis, each with:\n"
        "  - `id`, `weight`, and any case-specific parameters\n"
        "\n"
        "### 2. Benchmark runner script\n"
        "\n"
        f"Write a complete benchmark runner script to `{autogen_runner_rel}`.\n"
        "\n"
        "The runner MUST:\n"
        "- Accept `--benchmark <yaml_path>` and `--emit-json` CLI arguments\n"
        "- Load the benchmark YAML and iterate over all cases\n"
        "- For each case:\n"
        "  - Set up the computation from case parameters\n"
        "  - Time the target computation\n"
        "  - Extract correctness-relevant output quantities\n"
        "  - Handle errors gracefully (catch exceptions, report as case failure)\n"
        "- Compute summary metrics:\n"
        "  - The primary metric named in the benchmark YAML objective\n"
        "  - `peak_rss_mb` (memory usage)\n"
        "- Print a single JSON object to stdout with this schema:\n"
        "```json\n"
        "{\n"
        '  "benchmark_id": "string",\n'
        '  "correctness_ok": true,\n'
        '  "summary_metrics": {\n'
        '    "<primary_metric>": 1.23,\n'
        '    "peak_rss_mb": 0.0\n'
        "  },\n"
        '  "cases": [\n'
        "    {\n"
        '      "id": "case_id",\n'
        '      "converged": true,\n'
        '      "wall_seconds": 1.23,\n'
        '      "total_seconds": 1.23,\n'
        '      "<output_field>": <value>,\n'
        '      "error": ""\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n"
        "\n"
        "**Language-specific guidance:**\n"
        "\n"
        "- **Python packages**: import the package directly, call API functions,\n"
        "  use `time.perf_counter()` for timing, `resource.getrusage()` for RSS.\n"
        "- **C/C++ packages**: run the compiled binary as a subprocess, parse\n"
        "  output for correctness fields, use subprocess timing + `/usr/bin/time`.\n"
        "- **Fortran packages**: similar to C/C++ — subprocess execution of the\n"
        "  compiled binary with parsed output.\n"
        "\n"
        "After writing both files, output their contents in these XML tags:\n"
        "\n"
        f"<{BENCHMARK_YAML_TAG}>\n"
        "... (the complete YAML you wrote) ...\n"
        f"</{BENCHMARK_YAML_TAG}>\n"
        "\n"
        f"<{RUNNER_SCRIPT_TAG}>\n"
        "... (the complete runner script you wrote) ...\n"
        f"</{RUNNER_SCRIPT_TAG}>\n"
        "\n"
        "And a summary of review notes:\n"
        f"<{REVIEW_NOTES_TAG}>items the user should verify before starting optimization</{REVIEW_NOTES_TAG}>\n"
        "\n"
        f"{LOOP_DONE_TOKEN}\n"
    )
