# Generation rubric for scientific packages

## Primary design constraints
- Prioritize docs-first navigation in every generated skill.
- Keep generated skill count <= 30 for very large packages.
- Prefer abstract topic grouping over low-level API partitioning.
- Preserve explicit file-path references so the agent can drill down when needed.
- Add a small amount of compressed, high-signal content to core skills so the agent can answer common questions without opening multiple docs.

## Suggested expectations by package scale
- Small docs set (< 80 docs files): usually 6-12 topic skills + 1 index.
- Medium docs set (80-300 docs files): usually 10-20 topic skills + 1 index.
- Large docs set (> 300 docs files): usually 15-29 topic skills + 1 index; overflow docs should be merged into an advanced topic.

## Recommended topic coverage
- Getting started
- Build and install
- Inputs and modeling
- Simulation workflows
- Parallel and HPC
- API and scripting
- Examples and tutorials
- Analysis and output
- Developer guide
- Troubleshooting
- Theory and methods

## Manual review checklist
- Does each topic skill meaningfully group a coherent docs theme?
- Are primary docs in each skill representative and not overly narrow?
- Does the index skill list all topic skills and their scope clearly?
- Does each skill avoid exhaustive per-function instructions unless explicitly required?
- For docs-only packages, does each skill avoid source-code assumptions?

## High-signal playbook checklist (core skills only)
Core skills should not be “just links”. Enrich ~5–8 core skills with a short `## High-Signal Playbook` section (see `sci-skills-generator/SKILL.md`) and verify:
- The playbook includes triage questions, canonical workflow steps, and at least one minimal working example.
- Pitfalls are doc-backed (notes/warnings/FAQ items) and written as actionable fixes.
- Convergence/validation guidance names the few dominant accuracy knobs for the domain (EM, electronic structure, or MD).
- The playbook stays compact (prefer bullets; avoid long pasted tutorials).
- The full doc inventory remains in `references/doc_map.md` for deep dives.

## Advanced-topics consolidation (optional, but recommended when noisy)
If many generated topic skills contain only one doc file:
- Consolidate them into a single `<package>-advanced-topics` skill.
- Update `<package>-index` routing accordingly.
- Ensure advanced-topics keeps a combined `references/doc_map.md` and a small `references/source_map.md` for targeted source entry points.
