from __future__ import annotations

import re


COMPILE_PROFILE_TAG = "compile_profile"
COMPILE_PROFILE_REL_PATH = "skills/.compile_profile.json"
COMPILE_EVIDENCE_DIR_REL_PATH = "skills/.evidence"
COMPILE_REPORT_REL_PATH = "skills/.compile_report.json"
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
    "enrichment in this pass. At the end, echo the same JSON object inside "
    f"<{COMPILE_PROFILE_TAG}>...</{COMPILE_PROFILE_TAG}> tags."
)

COMPILE_PROMPT_2 = (
    "You are running FermiLink compile pass 2/3 (targeted enrichment). A baseline "
    "skills folder has already been generated deterministically. Use the evidence "
    f"files under `{COMPILE_EVIDENCE_DIR_REL_PATH}`. Follow "
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
    "under `skills/`. Further enrich the skills/ folder if you find agents cannot start from the skills/ folder to optimally "
    "use this package for advanced scientific simulations or computing. Finally, append a short summary of key fixes to "
    f"`{COMPILE_REPORT_REL_PATH}` under `agent_audit_notes`."
)
