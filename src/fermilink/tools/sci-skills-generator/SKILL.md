---
name: sci-skills-generator
description: This skill should be used when users ask to bootstrap or regenerate a `skills/` folder for a scientific software package (for example LAMMPS, PSI4, GROMACS, Q-Chem docs-only, MEEP, or similar) from docs/source/tests/tutorial layouts, with documentation-first routing and an abstract skill count capped around 30.
---

# Scientific Skills Folder Generator

## Goal
- Generate a reusable, documentation-first `skills/` folder for a scientific codebase.
- Keep generated skills high-level and routing-oriented for large packages instead of creating function-by-function skills.
- Keep total generated skills at or below `--max-skills` (default `30`, including index skill).

## Run the generator
- Command:
  - `python sci-skills-generator/scripts/generate_skills_folder.py --package-root <repo> --package-name <name> --output-dir <repo>/skills --max-skills 30`
- For closed-source or docs-only packages (for example Q-Chem docs distributions):
  - `python sci-skills-generator/scripts/generate_skills_folder.py --package-root <repo> --package-name qchem --docs-only --max-skills 30 --overwrite`
- If docs are not under default names:
  - `python sci-skills-generator/scripts/generate_skills_folder.py --package-root <repo> --package-name <name> --docs-dirs docs,manual --source-dirs src,python`
- For package-specific command examples:
  - `references/example-invocations.md`

## Validate generated output
- Ensure one index skill exists (`<package>-index`) and it routes to topic skills.
- Ensure each topic skill has:
  - `SKILL.md`
  - `references/doc_map.md`
  - `references/source_map.md` (unless `--docs-only`, where the file should clearly note source is unavailable)
- Ensure topic skills reference docs first and only escalate to source inspection when docs are insufficient.
- Ensure total skill count does not exceed the configured cap.

## Enrich core skills (high-signal playbooks)
The generator intentionally produces a docs-first router skeleton. For best agent performance, enrich a small number of core skills with compressed, high-signal content distilled from the docs/examples.

### Pick the core skills to enrich
- Always keep the index skill as the router: `<package>-index`.
- Pick ~5–8 topic skills that cover the first-week questions for the package. Common cores across EM, electronic-structure, and MD codes:
  - Getting started / quickstart
  - Build and install
  - Inputs & modeling / methods / force fields
  - Run workflows (run/restart/checkpoint/output)
  - API and scripting (Python bindings/CLI)
  - Parallel/HPC (MPI/OpenMP/GPU, schedulers)
  - Analysis/post-processing (outputs, formats, plotting)
  - Troubleshooting/FAQ

### What to add to each enriched SKILL.md
Add a `## High-Signal Playbook` section near the top (right after the title) with:
- **Route the request**: when to use other skills (avoid answering everything in one place).
- **Triage questions** (4–8): the minimum questions that determine the right solution path.
- **Canonical workflow** (5–10 steps): the docs’ recommended path, not an exhaustive survey.
- **Minimal working example** (1–2 snippets): a short CLI + minimal input/script extracted from docs/examples/tests; keep each snippet <= ~30 lines.
- **Pitfalls** (5–10 bullets): doc-backed “gotchas”, common errors, and their fixes.
- **Convergence/validation checklist** (3–6 bullets): the few knobs that dominate correctness vs. cost, and how to validate.
  - EM/FDTD: resolution/mesh, time step/CFL, PML thickness/padding, domain size, source bandwidth, run-time/decay criteria.
  - Electronic structure: basis/k-points/grids, SCF thresholds/mixing, pseudopotentials, smearing, geometry optimization tolerances.
  - MD: time step, cutoff/neighbor settings, thermostat/barostat parameters, equilibration length, finite-size effects, validation observables.

### How to source the compressed content (keep it doc-anchored)
- Start from each topic skill’s “Primary documentation references”.
- Use examples/tests as “golden” minimal reproductions when available.
- Prefer extracting/rewriting:
  - the smallest runnable example,
  - the docs’ recommended default flags/parameters,
  - explicit warnings/notes and troubleshooting tips.
- Keep claims defensible by citing doc file paths in the playbook (don’t rely on memory).
- Do not paste long narrative sections; keep long/complete lists in `references/doc_map.md`.

## Collapse low-signal one-doc topics into `<package>-advanced-topics`
If the generator produces many narrow topic skills where `references/doc_map.md` shows “Total docs grouped in this topic: 1”, reduce clutter:
- Create a single `<package>-advanced-topics` skill.
- Move the one-doc topics’ content into that skill:
  - Keep a short routing list in `<package>-advanced-topics/SKILL.md`.
  - Combine their doc inventories into `<package>-advanced-topics/references/doc_map.md`.
  - Add `<package>-advanced-topics/references/source_map.md` with a few targeted entry points and search tokens.
- Update `<package>-index` to route to `<package>-advanced-topics`.
- Delete the old one-doc topic directories.

## Template asset
- Use `assets/maxwelllink-skills-template/skills/` as the style/template baseline derived from MaxwellLink.
- Use the template for manual refinement when auto-grouping needs package-specific adjustments.

## Review loop
- Start with a dry run:
  - `python sci-skills-generator/scripts/generate_skills_folder.py --package-root <repo> --dry-run`
- Generate skills.
- Manually merge or rename topics if needed for domain clarity.
- Re-run with `--overwrite` after adjustments.
- After the final generation pass, enrich core skills and consolidate one-doc topics (so you don’t overwrite manual playbook edits).
- Use `references/generation-rubric.md` to decide whether topics should be merged or renamed before finalizing.
