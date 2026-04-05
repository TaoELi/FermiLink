# Optimization Goal

## Package
lammps

## Language
cpp

## Target
Optimize TIP4P water NVE simulation performance in LAMMPS, with focus on
pair-force and neighbor-list hot paths used by the TIP4P water workflow.

Primary optimization interest is reducing per-step wall time while preserving
physical trajectory quality and thermodynamic consistency for the same initial
condition and timestep configuration.

This goal assumes benchmark generation will use attached input artifacts by
filename (for example, `water_216_data.lmp`, `in.tip4p_nve`,
`in.tip4p_nve_medium`, `in.tip4p_nve_large`, and `in.tip4p_nve_long`,
plus any include-chain files referenced by those LAMMPS inputs).

## Editable Scope
- src/**/pair_lj_cut_tip4p*.cpp
- src/**/pair_lj_cut_tip4p*.h
- src/**/neighbor*.cpp
- src/**/verlet*.cpp
- src/**/integrate*.cpp
- src/**/atom*.cpp
- src/**/comm*.cpp
- src/**/domain*.cpp

## Performance Metric
Minimize weighted median wall-clock seconds per fixed step block for a
fixed-size MPI run with exactly 16 ranks.

Benchmark should record both end-to-end runtime and normalized throughput
(for example, ns/day or steps/second) for the same simulation length, with
the primary objective set to runtime minimization.

## Correctness Constraints
- Preserve NVE energy behavior: total energy drift per atom per step must stay within benchmark tolerance versus incumbent baseline.
- Preserve force consistency for representative sampled frames: max absolute force-component delta must stay within benchmark tolerance.
- Preserve trajectory invariants for identical seed/initial state (same atom count, no NaN/Inf, stable integration, no lost atoms).
- Do not change physical model semantics (TIP4P geometry assumptions, long-range setup choices, or units) unless explicitly documented and accepted.
- Do not relax numerical stability controls to gain speed (for example, no unsafe timestep increases or reduced neighbor rebuild safety).
- All benchmark cases must complete successfully with deterministic runner settings.

## Representative Workloads
- train-small: `in.tip4p_nve` + `water_216_data.lmp` (216 waters; no replication) for short warm-cache profiling.
- train-medium: `in.tip4p_nve_medium` + `water_216_data.lmp` (`replicate 2 1 1`; ~2x molecules) for main optimization loop.
- test-large: `in.tip4p_nve_large` + `water_216_data.lmp` (`replicate 2 2 1`; ~4x molecules) for generalization check.
- test-long: `in.tip4p_nve_long` + `water_216_data.lmp` (`replicate 2 1 1`; longer run) for longer-horizon NVE drift validation.

## Build
```bash
cmake -S . -B build -D CMAKE_BUILD_TYPE=Release
cmake --build build -j
```

## Notes
- Treat the attached LAMMPS input file(s) as the source of truth for runtime settings and any include-chain files.
- Prefer localized C++ optimizations over broad architecture rewrites.
- Keep benchmark execution deterministic: fixed thread settings, fixed random seeds (if any), and explicit launch command.
- Enforce MPI benchmarking with exactly 16 ranks for both baseline and candidate runs.
- In generated benchmark runtime command, invoke LAMMPS via MPI launcher with 16 ranks (for example `mpirun -np 16 ...` or `mpiexec -n 16 ...`).
- Set `OMP_NUM_THREADS=1` unless a case explicitly requires hybrid MPI+OpenMP, and keep this setting identical across baseline/candidate runs.
- In generated benchmark YAML, include a split block so worker sees train cases only:
  ```yaml
  split:
    train_case_ids:
      - train-small
      - train-medium
  ```
