from __future__ import annotations

import re


COMPILE_PROFILE_TAG = "compile_profile"
COMPILE_PROFILE_REL_PATH = "skills/.compile_profile.json"
COMPILE_EVIDENCE_DIR_REL_PATH = "skills/.evidence"
COMPILE_MEMORY_REL_PATH = "skills/.evidence/memory.md"
COMPILE_SKILL_PLAN_TAG = "skill_plan"
COMPILE_SKILL_PLAN_REL_PATH = "skills/.evidence/skill_plan.json"
RECOMPILE_COVERAGE_REL_PATH = "skills/.evidence/recompile_coverage.md"
COMPILE_REPORT_REL_PATH = "skills/.compile_report.json"
RECOMPILE_MEMORY_PLAN_TAG = "memory_update_plan"
RECOMPILE_MEMORY_PLAN_REL_PATH = "skills/.evidence/memory_update_plan.json"
RECOMPILE_PAPER_CONTEXT_DIR_REL_PATH = "skills/.evidence/paper_context"
RECOMPILE_PAPER_CONTEXT_REL_PATH = "skills/.evidence/paper_context/paper_context.json"
RECOMPILE_PAPER_PLAN_TAG = "paper_plan"
RECOMPILE_PAPER_PLAN_REL_PATH = "skills/.evidence/paper_context/paper_plan.json"
RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH = (
    "skills/.evidence/paper_context/figure_data_map.json"
)
RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH = (
    "skills/.evidence/paper_context/paper_skill_manifest.json"
)
RECOMPILE_PAPER_STAGED_ASSETS_DIR_REL_PATH = (
    "skills/.evidence/paper_context/staged_assets"
)
RECOMPILE_PAPER_STAGED_ASSETS_MANIFEST_REL_PATH = (
    "skills/.evidence/paper_context/staged_assets_manifest.json"
)
RECOMPILE_PAPER_PLAN_TOKEN_RE = re.compile(
    rf"<{RECOMPILE_PAPER_PLAN_TAG}>(.*?)</{RECOMPILE_PAPER_PLAN_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
RECOMPILE_MEMORY_PLAN_TOKEN_RE = re.compile(
    rf"<{RECOMPILE_MEMORY_PLAN_TAG}>(.*?)</{RECOMPILE_MEMORY_PLAN_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
COMPILE_SKILL_PLAN_TOKEN_RE = re.compile(
    rf"<{COMPILE_SKILL_PLAN_TAG}>(.*?)</{COMPILE_SKILL_PLAN_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
COMPILE_PROFILE_TOKEN_RE = re.compile(
    rf"<{COMPILE_PROFILE_TAG}>(.*?)</{COMPILE_PROFILE_TAG}>",
    re.IGNORECASE | re.DOTALL,
)

COMPILE_PROMPT_1 = (
    "You are running FermiLink compile pass 1/3 (discovery + generator setup). "
    "Inspect this scientific package and identify where source code, docs, tutorials/"
    "examples, and tests live. Start from the skill instructions at "
    "`sci-skills-generator/SKILL.md` and follow its workflow. Then create or update "
    f"`{COMPILE_PROFILE_REL_PATH}` with JSON fields: `package_name` (string), "
    "`docs_dirs` (list of relative directory paths), `tutorial_dirs` (list), "
    "`test_dirs` (list), `source_dirs` (list), `docs_only` (bool), and optional "
    "`notes` (list of short strings). Prefer relative paths from repo root and keep "
    "only directories that actually exist. If needed, you may minimally edit "
    "`sci-skills-generator/scripts/generate_skills_folder.py` so deterministic "
    "generation can succeed for this package layout. Do not do final skills "
    "enrichment in this pass. Also draft a concise skill-priority execution plan "
    f"for passes 2/3 and write it to `{COMPILE_SKILL_PLAN_REL_PATH}`.\n\n"
    "Return TWO tagged JSON blocks:\n"
    f"1) <{COMPILE_PROFILE_TAG}>{{...}}</{COMPILE_PROFILE_TAG}>\n"
    f"2) <{COMPILE_SKILL_PLAN_TAG}>{{...}}</{COMPILE_SKILL_PLAN_TAG}>\n\n"
    "Skill-plan JSON schema:\n"
    "{\n"
    '  "version": 1,\n'
    '  "mode": "compile",\n'
    '  "goal": "short run goal",\n'
    '  "priority_skills": [\n'
    "    {\n"
    '      "skill_id": "skill id or provisional topic id",\n'
    '      "action": "create | refresh | audit",\n'
    '      "reason": "why this is high impact",\n'
    '      "must_cover": ["key workflows/coverage targets"],\n'
    '      "source_hints": ["relevant source/doc paths"]\n'
    "    }\n"
    "  ],\n"
    '  "deferred_gaps": ["non-blocking gaps to track later"]\n'
    "}\n"
)

COMPILE_PROMPT_2 = (
    "You are running FermiLink compile pass 2/3 (targeted enrichment). A baseline "
    "skills folder has already been generated deterministically. Use the evidence "
    f"files under `{COMPILE_EVIDENCE_DIR_REL_PATH}` and follow the priorities in "
    f"`{COMPILE_SKILL_PLAN_REL_PATH}`. Read and honor compile memory in "
    f"`{COMPILE_MEMORY_REL_PATH}`. Follow "
    "`sci-skills-generator/SKILL.md` and `sci-skills-generator/references/generation-rubric.md` "
    "for enrichment strategy. Enrich only core skill "
    "folders so agents can start realistic simulations directly from skills without "
    "opening many docs first. For each enriched core skill, add a compact "
    "`## High-Signal Playbook` that includes: route conditions, triage questions, "
    "canonical workflow, minimal working example, pitfalls/fixes, and "
    "convergence/validation checks. Keep content compressed and doc-backed. "
    "Most importantly, retain and strengthen source-code entry links so the skill "
    "points to concrete implementation files when behavior details are needed. "
    "If many topic skills have only one doc, collapse them into `<package>-advanced-topics` "
    "as described in the sci-skills-generator skill guidance. Edit only under `skills/`."
)

COMPILE_PROMPT_3 = (
    "You are running FermiLink compile pass 3/3 (audit + repair). Audit the generated "
    "skills folder for path consistency and simulation-readiness. Fix broken file links "
    "in `SKILL.md`, `references/doc_map.md`, and especially `references/source_map.md`. "
    "Ensure each topic skill has source entry points that exist and are useful for "
    "function-level behavior checks. Keep playbooks concise and practical for starting "
    "real simulations, including commands/inputs and validation checkpoints. Edit only "
    "under `skills/`. Further enrich the skills/ folder with your maximal efforts if you find agents cannot start from the skills/ folder to optimally "
    "use this package for advanced scientific simulations or computing. Finally, append a short summary of key fixes to "
    f"`{COMPILE_REPORT_REL_PATH}` under `agent_audit_notes` and summarize durable findings in "
    f"`{COMPILE_MEMORY_REL_PATH}`."
    "Ensure the markdown files in each skill are self-contained: no `skills/.evidence/*` path dependencies; no external "
    "absolute path dependencies."
)


RECOMPILE_PROMPT_1 = (
    "You are running FermiLink recompile pass 1/3 (discovery + delta scoping). "
    "This repository already has a `skills/` folder. Start from "
    "`sci-skills-generator/SKILL.md` and follow its workflow for auditing an "
    "existing skills tree under ongoing package development. Re-check current "
    "source/docs/tutorial/test directory layout and update "
    f"`{COMPILE_PROFILE_REL_PATH}` with JSON fields: `package_name`, `docs_dirs`, "
    "`tutorial_dirs`, `test_dirs`, `source_dirs`, `docs_only`, and optional `notes`. "
    "Focus on what changed since prior compile and where coverage may be stale. "
    "Do not regenerate the whole skills folder from scratch in this pass. Also draft "
    "a concise recompile skill-priority plan for passes 2/3 and write it to "
    f"`{COMPILE_SKILL_PLAN_REL_PATH}`.\n\n"
    "Return TWO tagged JSON blocks:\n"
    f"1) <{COMPILE_PROFILE_TAG}>{{...}}</{COMPILE_PROFILE_TAG}>\n"
    f"2) <{COMPILE_SKILL_PLAN_TAG}>{{...}}</{COMPILE_SKILL_PLAN_TAG}>"
)

RECOMPILE_PROMPT_2 = (
    "You are running FermiLink recompile pass 2/3 (coverage update). Existing "
    "skills are present. Follow `sci-skills-generator/SKILL.md` and "
    "`sci-skills-generator/references/generation-rubric.md`. Use evidence under "
    f"`{COMPILE_EVIDENCE_DIR_REL_PATH}` and especially `{RECOMPILE_COVERAGE_REL_PATH}` "
    f"plus priorities in `{COMPILE_SKILL_PLAN_REL_PATH}`. Read compile memory in "
    f"`{COMPILE_MEMORY_REL_PATH}` before editing. "
    "to find source files/functions not well covered by current skills. Update "
    "`skills/` accordingly: refresh outdated links, add missing source entry points, "
    "expand or add concise `## High-Signal Playbook` sections for impacted core skills, "
    "and merge low-signal one-doc topics into `<package>-advanced-topics` when useful. "
    "Edits must stay under `skills/`."
)

RECOMPILE_PROMPT_3 = (
    "You are running FermiLink recompile pass 3/3 (audit + finalize). Audit the "
    "updated `skills/` folder for link consistency and development freshness. Ensure "
    "newly added source files/functions are represented by actionable source links in "
    "`references/source_map.md` and routed by the right skills. Keep guidance compact "
    "and simulation-oriented, consistent with `sci-skills-generator/SKILL.md`. Edit only "
    f"under `skills/`, then append key refresh notes to `{COMPILE_REPORT_REL_PATH}` "
    f"under `agent_audit_notes`, and add durable run outcomes to `{COMPILE_MEMORY_REL_PATH}`."
    "Ensure the markdown files in each skill are self-contained: no `skills/.evidence/*` path dependencies; no external "
    "absolute path dependencies."
)

RECOMPILE_MEMORY_PROMPT_1_PLAN = (
    "You are running FermiLink recompile pass 1/1 (memory-to-skills update planning). "
    "Build an append-only skills/ folder update from "
    "`### Suggested skills updates` entries extracted from `projects/memory.md` files. "
    "Use only suggestions for the target package id.\n\n"
    "Planning rules:\n"
    "- Convert machine-specific issues (env/import/lib/hpc/path/local machine setup) into "
    "`skills/user-specific-settings/SKILL.md`.\n"
    "- Convert package-specific issues into the most suitable existing package skill "
    "`SKILL.md` files under `skills/`.\n"
    "- Use append-only edits. Do not rewrite or delete existing skill content.\n"
    "- Prefer existing skill files; create a new `skills/user-specific-settings/SKILL.md` (and update the skills index with this new file) "
    "only if needed.\n\n"
    "After editing the skills/ folder according to the above rules, write a JSON plan to "
    "return ONE tagged JSON block:\n"
    f"<{RECOMPILE_MEMORY_PLAN_TAG}>{{...}}</{RECOMPILE_MEMORY_PLAN_TAG}>\n\n"
    "JSON schema:\n"
    "{\n"
    '  "version": 1,\n'
    '  "mode": "recompile_memory_plan",\n'
    '  "package_id": "target package id",\n'
    '  "summary": "short summary",\n'
    '  "operations": [\n'
    "    {\n"
    '      "change_type": "append",\n'
    '      "classification": "machine_specific | package_specific",\n'
    '      "target_skill_id": "skill folder name",\n'
    '      "target_path": "skills/<skill-id>/SKILL.md",\n'
    '      "issue_pattern": "copied/normalized issue pattern",\n'
    '      "proposed_append_markdown": "exact markdown snippet to append",\n'
    '      "evidence": "evidence text/path summary",\n'
    '      "status": "proposed | accepted | deferred",\n'
    '      "rationale": "why this target is suitable"\n'
    "    }\n"
    "  ],\n"
    '  "deferred_items": ["optional deferred suggestion notes"],\n'
    '  "warnings": ["optional warnings"]\n'
    "}\n"
)

RECOMPILE_PAPER_PROMPT_1_PLAN = (
    "You are running FermiLink recompile pass 1/3 (paper plan generation). "
    "First, rediscover package layout and refresh compile profile at "
    f"`{COMPILE_PROFILE_REL_PATH}`. Then generate a detailed per-figure reproduction "
    "plan from the provided manuscript text. The plan must explicitly include figure "
    "targets, simulation configurations, used packages, parameter requirements, and "
    "acceptance checks for each figure. If a scope comment is provided, only include "
    "figures/results covered by that scope; otherwise include all key paper results.\n\n"
    "Return TWO tagged JSON blocks:\n"
    f"1) <{COMPILE_PROFILE_TAG}>{{...}}</{COMPILE_PROFILE_TAG}>\n"
    f"2) <{RECOMPILE_PAPER_PLAN_TAG}>{{...}}</{RECOMPILE_PAPER_PLAN_TAG}>\n\n"
    "Paper-plan JSON schema:\n"
    "{\n"
    '  "version": 1,\n'
    '  "paper_source": "short file/source label",\n'
    '  "scope_mode": "all_results | comment_filtered",\n'
    '  "scope_comment": "optional comment text",\n'
    '  "used_packages": ["package names"],\n'
    '  "global_assumptions": ["assumptions"],\n'
    '  "figures": [\n'
    "    {\n"
    '      "id": "fig_001",\n'
    '      "title": "short figure title",\n'
    '      "targets": ["Figure 1a"],\n'
    '      "objective": "what to reproduce",\n'
    '      "simulation_config": ["config items"],\n'
    '      "parameter_requirements": ["parameter constraints"],\n'
    '      "required_packages": ["packages for this figure"],\n'
    '      "expected_artifacts": ["outputs to produce"],\n'
    '      "acceptance_checks": ["validation criteria"]\n'
    "    }\n"
    "  ]\n"
    "}\n"
)

RECOMPILE_PAPER_PROMPT_2_TUTORIAL = (
    "You are running FermiLink recompile pass 2/3 (paper tutorial skill synthesis). "
    "Use `paper_plan.json` and data manifests to map relevant supplementary files for "
    "each figure and create a NEW paper tutorial skill "
    "`paper_tutorial_<brief_summary_of_manuscript_scope>`. Do not "
    "modify any existing topic skills in this pass.\n\n"
    "Requirements:\n"
    "- Do NOT rely on manuscript text in this pass.\n"
    "- Use only files in provided manifests or staged assets.\n"
    "- Runtime work must happen under `projects/YYYY-MM-DD-<scope>/` (create a dated "
    "run directory and copy inputs/scripts from this tutorial skill `assets/` there).\n"
    "- Do NOT create directories inside this tutorial skill when performing calculations or simulations.\n"
    "- Keep the tutorial self-contained under `skills/<paper_tutorial_id>/`: copy only "
    "lightweight reproducibility assets (inputs, postprocess scripts, plotting scripts, "
    "small references) into `assets/`.\n"
    "- Do NOT reference external `--data-dir` locations, absolute filesystem paths, or "
    "`skills/.evidence/*` paths inside tutorial markdown.\n"
    "- Do NOT package large raw trajectories/results; keep only minimal reproducibility assets.\n"
    f"- Write/update `{RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH}`.\n"
    f"- Write/update `{RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH}`.\n"
    "- Root tutorial `SKILL.md` must include: `## Core Simulation Strategy`, "
    "`## Minimal Execution Recipes`, `## Figure Routing`, and "
    "`## Beyond Manuscript Exploration`.\n"
    "- If root tutorial `SKILL.md` includes YAML frontmatter (`name`/`description`), "
    "make `description` explicitly dual-purpose: one clause for manuscript-result "
    "reproduction and one clause for adapting workflows/parameters to closely related "
    "systems.\n"
    "- In `## Figure Routing`, include one entry per planned figure id with a brief "
    "scope summary (scientific aim/condition) plus playbook path; do not use only "
    "figure label + playbook path.\n"
    "- Build concrete, figure-by-figure reproducibility instructions in the new "
    "paper tutorial skill with assets/references/playbooks under `skills/`.\n"
)

RECOMPILE_PAPER_PROMPT_3_AUDIT = (
    "You are running FermiLink recompile pass 3/3 (paper tutorial audit + finalize). "
    "Audit generated paper tutorial skill against `paper_plan.json` and repair gaps "
    "so users can reproduce the planned paper results directly from the package "
    "`skills/` and local assets. You may optionally cross-check manuscript/data files.\n\n"
    "Requirements:\n"
    "- Ensure each planned figure has coherent workflow + acceptance checks.\n"
    "- Ensure figure-to-data mapping is consistent with `figure_data_map.json`.\n"
    "- Ensure runtime instructions use `projects/YYYY-MM-DD-<scope>/` as the execution folder for simulations and calculations.\n"
    "- Ensure each `## Figure Routing` entry contains a brief scope summary per figure, "
    "not only figure label + playbook path.\n"
    "- Ensure tutorial markdown is self-contained: no `skills/.evidence/*`, no external "
    "`--data-dir`, no absolute path dependencies.\n"
    "- Ensure root tutorial `SKILL.md` contains direct simulation strategy guidance, "
    "minimal execution recipes, and a beyond-manuscript exploration section.\n"
    "- Ensure any YAML frontmatter `description` in root tutorial `SKILL.md` is "
    "dual-purpose: it states both manuscript-result reproduction scope and applicability "
    "to related systems via parameter/procedure adjustments. The detailed wording should depend on the scope of the skills.\n"
    "- Ensure tutorial is concrete, executable, and publication-grade.\n"
    "- Modify the skill folder name and skill name as `paper_tutorial_<scope>, with <scope> being at most two words summarizing the manuscript's scientific scope.\n"
    "- Append paper tutorial routing in the index skill as an advanced topic.\n"
)

# Backward-compatible aliases used by existing command wiring/tests.
RECOMPILE_PAPER_PROMPT_1 = RECOMPILE_PAPER_PROMPT_1_PLAN
RECOMPILE_PAPER_PROMPT_2 = RECOMPILE_PAPER_PROMPT_2_TUTORIAL
RECOMPILE_PAPER_PROMPT_3 = RECOMPILE_PAPER_PROMPT_3_AUDIT
