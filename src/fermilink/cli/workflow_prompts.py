from __future__ import annotations

import re


LOOP_MEMORY_DIRNAME = "projects"
LOOP_MEMORY_FILENAME = "memory.md"
LOOP_DONE_TOKEN = "<promise>DONE</promise>"

LOOP_WAIT_TOKEN_RE = re.compile(
    r"^\s*<wait_seconds>\s*([0-9]+(?:\.[0-9]+)?)\s*</wait_seconds>\s*$",
    re.MULTILINE,
)
LOOP_PID_TOKEN_RE = re.compile(
    r"^\s*<pid_number>\s*([0-9]+)\s*</pid_number>\s*$",
    re.MULTILINE,
)
LOOP_SLURM_JOB_TOKEN_RE = re.compile(
    r"^\s*<slurm_job_number>\s*([0-9]+)\s*</slurm_job_number>\s*$",
    re.MULTILINE,
)

REPRODUCE_PLAN_TAG = "reproduce_plan"
RESEARCH_PLAN_TAG = "research_plan"
REPRODUCE_PLAN_TOKEN_RE = re.compile(
    r"<reproduce_plan>\s*(\{.*?\})\s*</reproduce_plan>",
    re.DOTALL,
)
RESEARCH_PLAN_TOKEN_RE = re.compile(
    r"<research_plan>\s*(\{.*?\})\s*</research_plan>",
    re.DOTALL,
)

REPRODUCE_STATE_FILENAME = "state.json"
REPRODUCE_PLAN_FILENAME = "plan.json"
REPRODUCE_PROMPTS_DIRNAME = "prompts"
REPRODUCE_LOGS_DIRNAME = "logs"
REPRODUCE_ARCHIVE_DIRNAME = "archive"
REPRODUCE_RUNS_DIR = "reproduce"
RESEARCH_RUNS_DIR = "research"
REPRODUCE_LATEST_RUN_FILENAME = "latest_run.txt"
WORKFLOW_SUMMARIES_DIRNAME = "summaries"
WORKFLOW_REPORT_FILENAME = "report.md"
WORKFLOW_DATA_DIRNAME = "data"
WORKFLOW_DATA_MANIFEST_FULL_FILENAME = "data_manifest_full.json"
WORKFLOW_DATA_MANIFEST_FILENAME = "data_manifest.json"
WORKFLOW_DATA_SUMMARY_FILENAME = "data_summary.md"
WORKFLOW_TASK_DATA_MAP_FILENAME = "task_data_map.json"
WORKFLOW_TASK_DATA_MAP_TAG = "task_data_map"
WORKFLOW_TASK_DATA_MAP_TOKEN_RE = re.compile(
    r"<task_data_map>\s*(\{.*?\})\s*</task_data_map>",
    re.DOTALL,
)

LOOP_PROMPT_PREFIX = (
    "You are running in **FermiLink loop mode**.\n"
    "\n"
    "Persistent memory lives at `projects/memory.md` (relative to the repo root).\n"
    "This file uses a unified schema with short-term and long-term sections.\n"
    "\n"
    "Long-running jobs (SLURM or similar spending more than ~20 minutes): it is OK to submit a job, record job ids/paths\n"
    "in `projects/memory.md`, and end the iteration without waiting. A later iteration can\n"
    "check status and continue.\n"
    "\n"
    "At the start of this iteration:\n"
    "1) Read `projects/memory.md`.\n"
    "2) Maintain short-term memory sections:\n"
    "   - `## Short-Term Memory (Operational) -> ### Plan`\n"
    "   - `## Short-Term Memory (Operational) -> ### Progress log`\n"
    "3) If `### Plan` does not contain a clear checklist, create one (5-15 small steps).\n"
    "4) If `### Plan` contains a fully finished checklist, override it with a new checklist (5-15 small steps).\n"
    "5) Execute unchecked step(s) sequentially.\n"
    "6) Update `projects/memory.md`:\n"
    "   - Check off the completed step(s) in `### Plan`.\n"
    "   - Append one short entry to `### Progress log` (what changed + files touched).\n"
    "   - Update relevant long-term sections only when there is durable information:\n"
    "     - `### File map` for stable file/purpose mapping changes.\n"
    "     - `### Simulation history` for run/job milestones and outcomes.\n"
    "     - `### Key results` for validated, reproducible outcomes (include artifact paths).\n"
    "     - `### Suggested skills updates` for recurring failure patterns and concrete fixes.\n"
    "\n"
    "If the task is not complete and you started local and/or SLURM jobs, emit machine-readable tags:\n"
    "- local background job pid:\n"
    "  <pid_number>NUMBER</pid_number>\n"
    "- slurm job id:\n"
    "  <slurm_job_number>NUMBER</slurm_job_number>\n"
    "Use one line per job id/pid (integer only, no extra text).\n"
    "Do not include these tags once those jobs are finished or when no waiting is needed.\n"
    "\n"
    f"When (and only when) ALL steps are complete and the request is satisfied, output exactly:\n"
    f"{LOOP_DONE_TOKEN}\n"
    "on its own line.\n"
    "\n"
    "Original request:\n"
)

UNIFIED_MEMORY_PROMPT_PREFIX = (
    "You are running in **FermiLink unified-memory mode**.\n"
    "\n"
    "Persistent shared memory lives at `projects/memory.md` (relative to the repo root).\n"
    "Before acting, read `projects/memory.md`.\n"
    "\n"
    "When you finish this turn, update memory with concise, factual entries:\n"
    "- Short-term memory:\n"
    "  - `## Short-Term Memory (Operational) -> ### Plan`\n"
    "  - `## Short-Term Memory (Operational) -> ### Progress log`\n"
    "- Long-term memory (only when relevant):\n"
    "  - `### File map` for stable file/purpose mapping changes.\n"
    "  - `### Simulation history` for run/job milestones and outcomes.\n"
    "  - `### Key results` for validated, reproducible outcomes (include artifact paths).\n"
    "  - `### Suggested skills updates` for recurring failure patterns and concrete fixes.\n"
    "\n"
    "Always include touched file paths in progress entries when applicable.\n"
    "\n"
    "Current request/context:\n"
)

REPRODUCE_PLANNER_PROMPT_PREFIX = (
    "You are running in **FermiLink reproduce planner mode**.\n"
    "\n"
    "Goal: split a paper-level reproduction request into medium, executable tasks.\n"
    "Each task should map to a full figure or a coherent fraction of one figure.\n"
    "\n"
    "Output exactly one XML-like block:\n"
    f"<{REPRODUCE_PLAN_TAG}>{{JSON}}</{REPRODUCE_PLAN_TAG}>\n"
    "\n"
    "JSON schema:\n"
    "{\n"
    '  "version": 1,\n'
    '  "paper_source": "short source description",\n'
    '  "assumptions": ["..."],\n'
    '  "tasks": [\n'
    "    {\n"
    '      "id": "task_001",\n'
    '      "title": "short title",\n'
    '      "figure_targets": ["Figure 1a"],\n'
    '      "objective": "what to reproduce",\n'
    '      "simulation_requirements": ["what to simulate"],\n'
    '      "parameter_constraints": ["parameters/conditions"],\n'
    '      "plot_requirements": ["axes/style/colors"],\n'
    '      "acceptance_checks": ["completion criteria"],\n'
    '      "prompt_markdown": "prompt text for one fermilink loop task"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "Rules:\n"
    "- Return valid JSON only inside the tag (no markdown fences).\n"
    "- Keep task count practical (3-12 tasks unless source is tiny).\n"
    "- Include concrete parameters/plot details when available; otherwise add assumptions.\n"
    "- Ensure each task prompt is self-contained, providing all necessary context/background and actionable steps, including what package(s) are used in the manuscript for calculations  (future agent will only see this prompt).\n"
)

REPRODUCE_AUDITOR_PROMPT_PREFIX = (
    "You are running in **FermiLink reproduce auditor mode**.\n"
    "\n"
    "You will receive an original paper request and a proposed reproduce plan.\n"
    "Audit the plan for completeness, executability, and scientific consistency.\n"
    "Fix missing details, impossible ordering, or vague task prompts.\n"
    "\n"
    "Return exactly one corrected plan block:\n"
    f"<{REPRODUCE_PLAN_TAG}>{{JSON}}</{REPRODUCE_PLAN_TAG}>\n"
    "\n"
    "Rules:\n"
    "- Keep JSON schema identical to reproduce planning and keep task ids stable when possible.\n"
    "- Return valid JSON only inside the tag (no markdown fences).\n"
    "- Ensure `prompt_markdown` is self-contained, providing all necessary context/background and actionable steps, including what package(s) are used in the manuscript for calculations (future agent will only see this prompt).\n"
)

RESEARCH_PLANNER_PROMPT_PREFIX = (
    "You are running in **FermiLink research planner mode**.\n"
    "\n"
    "Goal: turn a short scientific research idea into a concrete, executable plan.\n"
    "Design tasks that can be executed sequentially and that cumulatively produce\n"
    "publication-quality simulation results and figures.\n"
    "\n"
    "Output exactly one XML-like block:\n"
    f"<{RESEARCH_PLAN_TAG}>{{JSON}}</{RESEARCH_PLAN_TAG}>\n"
    "\n"
    "JSON schema:\n"
    "{\n"
    '  "version": 1,\n'
    '  "paper_source": "short source description",\n'
    '  "assumptions": ["..."],\n'
    '  "tasks": [\n'
    "    {\n"
    '      "id": "task_001",\n'
    '      "title": "short title",\n'
    '      "figure_targets": ["Figure 1a"],\n'
    '      "objective": "what to reproduce",\n'
    '      "simulation_requirements": ["what to simulate"],\n'
    '      "parameter_constraints": ["parameters/conditions"],\n'
    '      "plot_requirements": ["axes/style/colors"],\n'
    '      "acceptance_checks": ["completion criteria"],\n'
    '      "prompt_markdown": "prompt text for one fermilink loop task"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "Rules:\n"
    "- Return valid JSON only inside the tag (no markdown fences).\n"
    "- Keep task count practical (3-12 tasks unless source is tiny).\n"
    "- Ensure each task prompt is self-contained, providing all necessary context/background and actionable steps (future agent will only see this prompt).\n"
    "- Include baselines/controls/parameter sweeps and plotting requirements where relevant.\n"
)

RESEARCH_AUDITOR_PROMPT_PREFIX = (
    "You are running in **FermiLink research auditor mode**.\n"
    "\n"
    "You will receive a research request and a candidate task plan.\n"
    "Audit for feasibility, ordering, scientific rigor, and reproducibility.\n"
    "Fix vague tasks, missing acceptance checks, and weak experimental controls.\n"
    "\n"
    "Return exactly one corrected plan block:\n"
    f"<{RESEARCH_PLAN_TAG}>{{JSON}}</{RESEARCH_PLAN_TAG}>\n"
    "\n"
    "Rules:\n"
    "- Keep JSON schema identical to reproduce planning and keep task ids stable when possible.\n"
    "- Return valid JSON only inside the tag (no markdown fences).\n"
    "- Ensure `prompt_markdown` is self-contained, providing all necessary context/background and actionable steps (future agent will only see this prompt).\n"
)

WORKFLOW_REPORT_GENERATOR_PROMPT_PREFIX = (
    "You are running in **FermiLink workflow report generation mode**.\n"
    "\n"
    "You must generate concise per-task summaries and a polished top-level report.\n"
)

WORKFLOW_REPORT_AUDITOR_PROMPT_PREFIX = (
    "You are running in **FermiLink workflow report audit mode**.\n"
    "\n"
    "You are an independent reviewer. Read the generated report and improve it for\n"
    "scientific clarity, completeness, and reproducibility.\n"
)

WORKFLOW_DATA_AUDITOR_PROMPT_PREFIX = (
    "You are running in **FermiLink workflow data auditor mode**.\n"
    "\n"
    "Goal: map available data files to each draft task with explicit rationale and confidence.\n"
    "Use data summary and compact manifest metadata only; do not invent files.\n"
    "\n"
    "Return exactly one XML-like block:\n"
    f"<{WORKFLOW_TASK_DATA_MAP_TAG}>{{JSON}}</{WORKFLOW_TASK_DATA_MAP_TAG}>\n"
    "\n"
    "JSON schema:\n"
    "{\n"
    '  "version": 1,\n'
    '  "tasks": [\n'
    "    {\n"
    '      "id": "task_001",\n'
    '      "files": [\n'
    "        {\n"
    '          "path": "relative/path/in/data_dir.ext",\n'
    '          "rationale": "why this file is relevant",\n'
    '          "confidence": 0.0\n'
    "        }\n"
    "      ],\n"
    '      "unknowns": ["missing or uncertain data areas"],\n'
    '      "notes": ["short constraints or caveats"]\n'
    "    }\n"
    "  ],\n"
    '  "global_unknowns": ["cross-task uncertainty notes"]\n'
    "}\n"
    "\n"
    "Rules:\n"
    "- Use only file paths present in the provided manifest excerpt/slice.\n"
    "- Keep confidence in [0.0, 1.0].\n"
    "- Prefer narrow, high-signal file subsets per task.\n"
    "- Explicitly flag unknown/low-confidence regions.\n"
    "- Return valid JSON only inside the tag (no markdown fences).\n"
)

WORKFLOW_DRY_RUN_PLANNER_PROMPT_SUFFIX = (
    "Dry-run planning requirements:\n"
    "- Keep task-level scientific intent unchanged, but do not require executing full simulations now.\n"
    "- Design each task so the agent prepares simulation-ready artifacts only: input/config templates,\n"
    "  post-processing scripts, and plotting scripts.\n"
    "- Require a task-level README.md (or update an existing one) with exact commands for later simulation execution,\n"
    "  expected outputs, and validation steps.\n"
    "- Acceptance checks must focus on artifact completeness, script correctness, and reproducibility instructions,\n"
    "  not numerical results from executed simulations.\n"
)

WORKFLOW_DRY_RUN_AUDITOR_PROMPT_SUFFIX = (
    "Dry-run audit requirements:\n"
    "- Rewrite vague or simulation-execution-heavy tasks into artifact-preparation tasks only.\n"
    "- Each `prompt_markdown` must explicitly prohibit running full simulations in this dry-run workflow.\n"
    "- Ensure each task requests: simulation input/config preparation, post-processing scripts,\n"
    "  plotting scripts, and a README.md for running simulations later.\n"
    "- Ensure acceptance checks verify file outputs, command reproducibility instructions, and static checks,\n"
    "  without claiming simulation-derived figure values.\n"
)

WORKFLOW_DRY_RUN_LOOP_PREAMBLE = (
    "DRY-RUN mode constraints:\n"
    "- Do not execute full simulations, MPI jobs, SLURM jobs, or long-running numerical workloads.\n"
    "- Prepare only simulation inputs/configs, post-processing scripts, and plotting scripts.\n"
    "- If you create or revise the checklist in `projects/memory.md`, use only 1-4 focused steps.\n"
    "- This 1-4 checklist range overrides the default loop guidance of 5-15 steps.\n"
    "- Create or update README.md instructions describing exactly how to run simulations later,\n"
    "  expected outputs, and verification steps.\n"
    "- Use static checks only (file existence, syntax/import checks, CLI dry-run checks where available).\n"
    "- Do not claim simulation-derived numerical results in this mode.\n"
)
