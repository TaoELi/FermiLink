---
name: optimize-goal-authoring
description: Write, review, or strengthen `goal.md` files for `fermilink optimize` across Python, C/C++, and Fortran packages. Use when defining target scope, scientific correctness constraints, representative workloads, build commands, or staged workload-input files for goal mode.
---

# FermiLink Optimize Goal Authoring

Use this skill when asked to create, audit, or tighten a goal markdown file for
`fermilink optimize`.

Read these first:

- `references/goal-authoring-guide.md`
- `assets/goal-template.md`

Then load examples only as needed:

- `assets/examples/python-pyscf-diis-scf-goal.md`
- `assets/examples/python-pyscf-tddft-davidson-goal.md`
- `assets/examples/lammps-tip4p/cpp-lammps-tip4p-water-nve-goal.md`

## Missing Details

Do not silently fill in important missing details by assumption.

If the user has not specified information that materially affects the goal file,
ask for it explicitly before finalizing the draft. Typical examples include:

- the exact hot path or algorithm family in scope
- which files are allowed to be edited
- the scientific outputs that must be preserved
- the representative workloads or system sizes to benchmark
- required build or launcher constraints
- whether workload input files already exist and where they live

Only infer details from local repository evidence when that evidence is direct
and unambiguous. If multiple reasonable interpretations exist, ask the user.

## Workflow

1. Identify the actual hot path and write a narrow `## Editable Scope`.
2. Define a primary metric that isolates the targeted computation, not a vague whole-program goal unless that is truly the target.
3. Write scientific correctness constraints as explicit numeric invariants and forbidden relaxations.
4. Choose 3-6 representative workloads with real train/test diversity. Avoid toy cases for algorithm-level work. 
5. Include deterministic `## Build` commands. If the package needs build/install before benchmarking, say so explicitly in `## Notes`.
6. If workloads need input files, mention their filenames directly in `## Representative Workloads` and keep those files bundled with the goal file. FermiLink stages file references from workload bullets into `FERMILINK_GOAL_INPUT_ROOT`.
7. Keep the recognizable goal headings exactly as shown in `assets/goal-template.md`.

## Authoring Rules

- Prefer explicit file paths over broad `**` globs.
- Prefer `field_tolerances`-friendly outputs: energies, forces, densities, eigenvalues, convergence counts, physically meaningful arrays.
- State what must not be changed to gain speed: tolerances, solver family, number of roots, timestep, basis set, thread count, physical model, restart behavior, symmetry settings, and so on.
- Ask for secondary counters when helpful: iterations, matvecs, phase timings, throughput, memory.
- For native or mixed-language packages, include the exact build steps needed for benchmark execution.
- For HPC or launcher-sensitive cases, make deterministic launch constraints explicit in `## Notes`.

## Audit Checklist

- Is the target specific enough to prevent unrelated speedups?
- Is the editable scope tight enough to preserve causal attribution?
- Are workloads large enough to stress the intended hot path?
- Are held-out cases materially different from train cases?
- Do correctness constraints protect the real scientific outputs?
- Does the goal explain any required workload files and build steps?
