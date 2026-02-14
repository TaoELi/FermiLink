from __future__ import annotations

import re


LOOP_MEMORY_DIRNAME = "projects"
LOOP_MEMORY_FILENAME = "memory.md"
LOOP_DONE_TOKEN = "<promise>DONE</promise>"

LOOP_WAIT_TOKEN_RE = re.compile(
    r"^\s*<wait_seconds>\s*([0-9]+(?:\.[0-9]+)?)\s*</wait_seconds>\s*$",
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

LOOP_PROMPT_PREFIX = (
    "You are running in **FermiLink loop mode**.\n"
    "\n"
    "Persistent memory lives at `projects/memory.md` (relative to the repo root).\n"
    "\n"
    "Long-running jobs (SLURM or similar): it is OK to submit a job, record job ids/paths\n"
    "in `projects/memory.md`, and end the iteration without waiting. A later iteration can\n"
    "check status and continue.\n"
    "\n"
    "At the start of this iteration:\n"
    "1) Read `projects/memory.md`.\n"
    "2) If it does not contain a clear checklist plan, create one (5-15 small steps).\n"
    "3) Execute exactly ONE next unchecked step.\n"
    "4) Update `projects/memory.md`:\n"
    "   - Check off the completed step.\n"
    "   - Append a short progress log entry (what changed + files touched).\n"
    "   - Append a short pending simulation log entry if you have submitted a long-running job, including job id and expected duration.\n"
    "   - Append a short additional notes log entry for the pitfalls you have avoided or key problems encountered.\n"
    "\n"
    "If the task is not complete, provide one machine-readable wait hint on its own line:\n"
    "<wait_seconds>NUMBER</wait_seconds>\n"
    "where NUMBER is a non-negative number of seconds (no units, no extra text).\n"
    "Do not include this wait tag once you are done.\n"
    "\n"
    f"When (and only when) ALL steps are complete and the request is satisfied, output exactly:\n"
    f"{LOOP_DONE_TOKEN}\n"
    "on its own line.\n"
    "\n"
    "Original request:\n"
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
    "- Ensure each task prompt is self-contained, providing all necessary context/background and actionable steps (future agent will only see this prompt).\n"
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
    "- Ensure `prompt_markdown` is self-contained, providing all necessary context/background and actionable steps (future agent will only see this prompt).\n"
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
