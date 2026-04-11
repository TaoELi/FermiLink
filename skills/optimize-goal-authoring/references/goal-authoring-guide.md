# Goal Authoring Guide

## Contents

1. Parser Contract
2. Section-by-Section Guidance
3. Workload Design
4. Scientific Correctness
5. Build and Runtime Guidance
6. Workload File Staging Pattern
7. Common Failure Modes
8. Example Assets

## Parser Contract

FermiLink goal mode recognizes structured markdown, not arbitrary prose. Keep
the standard headings so the parser can extract the intended fields reliably.

Recognized high-value headings:

- `# Optimization Goal`
- `## Package`
- `## Language`
- `## Target`
- `## Editable Scope`
- `## Performance Metric`
- `## Correctness Constraints`
- `## Representative Workloads`
- `## Build`
- `## Notes`

Only `## Package` and `## Target` are strictly required by the parser, but a
strong goal file should include all of the sections above.

The parser extracts:

- package id
- language hint
- target text
- editable-scope bullet list
- performance-metric text
- correctness-constraint bullet list
- representative-workload bullet list
- build fenced code blocks
- notes text

## Section-by-Section Guidance

### `## Target`

Write the target as a hot-path statement, not a vague desire for speed. Say:

- what subsystem is being optimized
- which files own the behavior
- whether the intent is implementation-level or algorithm-level
- what must remain scientifically or physically unchanged

Good target text narrows the search space before any benchmark is generated.

### `## Editable Scope`

Keep scope narrow. Use explicit paths whenever possible.

Good:

- `pyscf/lib/diis.py`
- `pyscf/scf/diis.py`
- `src/**/pair_lj_cut_tip4p*.cpp`

Weak:

- `src/**`
- `pyscf/**`

Broad scope makes it easy for the optimizer to win in unrelated code rather
than the intended hot path.

### `## Performance Metric`

State the primary objective in benchmark terms.

Strong metrics usually name:

- the targeted timed region
- the aggregation style you want later
- secondary counters that help keep the campaign honest

Examples:

- weighted median `scf_kernel_seconds`
- weighted median TDDFT kernel time
- wall-clock seconds per fixed step block
- steps/second or ns/day as a secondary throughput metric

If the true target is a subphase, ask the generated runner to emit subphase
timings and counters such as iterations, matvecs, DIIS update time, eigensolve
time, neighbor-list time, and so on.

### `## Correctness Constraints`

Prefer numeric scientific outputs that can be validated with field tolerances.

Good examples:

- total energy
- excitation energies
- oscillator strengths
- forces
- density matrices
- MO energies
- `<S^2>`
- convergence flags and root counts

Also state what cannot be weakened to gain speed:

- tolerances
- `max_cycle`
- solver family
- timestep
- basis set
- number of states or roots
- symmetry restrictions
- restart/rollback semantics
- model physics

### `## Representative Workloads`

Write 3-6 bullets. Each bullet should describe one case clearly enough that a
benchmark generator can turn it into a case dict.

A strong workload set:

- includes both train and held-out test cases
- stresses the intended hot path
- covers meaningful scientific diversity
- avoids toy systems if algorithm-level speedups are desired

Use concise case ids like `train-rhf-benzene-631gss`.

### `## Build`

Include the exact commands needed to produce a runnable target checkout. Use a
fenced code block. For native or mixed-language projects, include rebuild and
install steps, not just a high-level statement.

### `## Notes`

Use this section to force important benchmark-generation behavior that is not
captured cleanly elsewhere:

- deterministic thread settings
- MPI rank counts
- fixed seeds
- required runtime launcher behavior
- request for `runtime.pre_commands`
- request for train/test split
- workload-file handling details

## Workload Design

Choose workloads to match the optimization level.

For algorithm-level optimization:

- use larger single-machine or fixed-size MPI cases
- make the target computation dominate runtime
- keep one or two behavior-protection cases if they guard special logic

For implementation-level optimization:

- smaller cases may be acceptable if they still exercise the exact hot path

Avoid these mistakes:

- only easy toy systems
- only one scientific family
- no open-shell or symmetry cases when those branches are in scope
- whole-program timing when the target is a tiny subroutine

## Scientific Correctness

The goal file should bias benchmark generation toward `field_tolerances`, not
`runner_only`.

Write constraints that naturally map to emitted runner fields:

- scalar values with absolute delta
- arrays with RMS or max-absolute tolerance
- exact integer counts like converged roots

If you cannot name a scientific output, the goal is probably too vague.

## Build and Runtime Guidance

If the project needs compilation or installation before authoritative
benchmarking, say so in `## Build` and reinforce it in `## Notes`.

For native or mixed-language packages, explicitly ask the generated benchmark to
include `runtime.pre_commands` derived from the build steps.

For deterministic benchmarking, specify:

- thread counts
- MPI rank counts
- seeds
- launch mode assumptions

## Workload File Staging Pattern

Goal mode can stage workload files referenced from `## Representative Workloads`
into a controlled input root exposed to the benchmark runner as
`FERMILINK_GOAL_INPUT_ROOT`.

Practical rules:

- Mention input files by filename or relative path directly in workload bullets.
- Keep those files next to the goal file or in a nearby sibling folder.
- If multiple cases share the same files, shared references are fine.
- The generated runner should resolve workload files relative to
  `FERMILINK_GOAL_INPUT_ROOT`, not by guessing parent directories.

This pattern is especially useful for C/C++ or Fortran packages driven by input
files, such as LAMMPS.

## Common Failure Modes

- Target too broad: optimizer wins in unrelated code.
- Scope too broad: benchmark no longer measures the named hot path.
- Workloads too small: timing noise overwhelms the intended signal.
- Correctness too weak: candidates can change physics or numerics.
- Missing build steps: benchmark runs stale binaries or extensions.
- No staged file guidance: generated runner guesses incorrect input paths.
- No held-out cases: campaign overfits train workloads.

## Example Assets

Use these bundled examples as patterns:

- `assets/examples/python-pyscf-diis-scf-goal.md`
  DIIS-family SCF goal with explicit CDIIS/ADIIS/EDIIS scope and larger RHF/UHF workloads.
- `assets/examples/python-pyscf-tddft-davidson-goal.md`
  algorithm-level eigensolver goal with strong secondary metrics and held-out cases.
- `assets/examples/lammps-tip4p/cpp-lammps-tip4p-water-nve-goal.md`
  native-code example with workload-file staging and deterministic MPI/runtime notes.

Start from `assets/goal-template.md` when writing a new goal file.

