# Optimization Goal

## Package
lammps

## Language
cpp

## Target
Optimize the TIP4P long-range water NVE workflow in LAMMPS, with primary focus on algorithm-level improvements inside `lj/cut/tip4p/long` and `pppm/tip4p`.

The relevant hot paths in the local LAMMPS source tree are `src/KSPACE/pair_lj_cut_tip4p_long.cpp` and `src/KSPACE/pppm_tip4p.cpp`. Important repeated work includes TIP4P M-site construction during pair traversal, TIP4P-specific charge mapping, rho accumulation, and electric-field interpolation inside the PPPM solve.

Primary optimization interest is reducing the combined pair-plus-kspace cost for fixed-size water systems while preserving the same physical model, long-range accuracy, timestep, and stable NVE behavior.

This goal assumes benchmark generation will use the attached input artifacts by filename (`water_216_data.lmp`, `in.tip4p_nve`, and `in.tip4p_nve_long`) and resolve them from the staged goal input root.

## Editable Scope
- src/KSPACE/pair_lj_cut_tip4p_long.cpp
- src/KSPACE/pair_lj_cut_tip4p_long.h
- src/KSPACE/pppm_tip4p.cpp
- src/KSPACE/pppm_tip4p.h

## Performance Metric
Minimize weighted median `pair_seconds + kspace_seconds` across all benchmark cases.

Benchmark should also record `loop_seconds`, `pair_seconds`, `kspace_seconds`, `neigh_seconds`, `comm_seconds`, and normalized throughput (for example, steps/second or ns/day). Secondary objective should be lower `loop_seconds` without winning only through communication-side artifacts.

## Correctness Constraints
- Preserve NVE energy behavior: total energy drift per atom per step over the longer runs must stay within benchmark tolerance versus incumbent baseline.
- Preserve sampled thermo observables at matched output steps: `etotal`, `pe`, `ke`, `temp`, `press`, and `density` must stay within benchmark tolerance.
- Preserve sampled force consistency for representative frames: RMS and max absolute force-component deltas must stay within benchmark tolerance.
- Preserve trajectory invariants for identical initial state and deterministic seed: same atom count, stable completion, no lost atoms, and no NaN/Inf.
- Do not change physical model semantics or runtime controls to gain speed: keep `pair_style lj/cut/tip4p/long`, `kspace_style pppm/tip4p 0.0001`, `neighbor 2.0 bin`, `timestep 0.5`, units, bonded terms, and TIP4P geometry assumptions unchanged.
- Do not weaken long-range accuracy, neighbor rebuild safety, or Newton/communication semantics to gain speed.
- All benchmark cases must complete successfully with deterministic runner settings.

## Representative Workloads
- train-16r-short: `in.tip4p_nve` + `water_216_data.lmp` on 16 MPI ranks (216 waters replicated by `4x4x4` inside the input; short run) so pair plus PPPM work dominates more strongly than communication.
- train-32r-short: `in.tip4p_nve` + `water_216_data.lmp` on 32 MPI ranks to keep the optimization useful across a second domain decomposition.
- train-16r-long: `in.tip4p_nve_long` + `water_216_data.lmp` on 16 MPI ranks for a longer-horizon NVE drift and timer-stability case.
- test-32r-long: `in.tip4p_nve_long` + `water_216_data.lmp` on 32 MPI ranks as a held-out moderate-decomposition case.
- test-64r-short: `in.tip4p_nve` + `water_216_data.lmp` on 64 MPI ranks as a held-out scaling-sensitive case; this case should not define the primary optimization direction.

## Build
```bash
mkdir -p build
cd build
cmake -C ../cmake/presets/most.cmake -C ../cmake/presets/nolib.cmake -D PKG_GPU=off ../cmake
cmake --build . -j4
```

## Notes
- Treat the attached LAMMPS input file(s) as the source of truth for runtime settings and any include-chain files.
- This campaign is intended to find algorithm-level improvements inside the named TIP4P pair and PPPM kernels, not generic communication or integrator tuning.
- Keep benchmark execution deterministic: fixed thread settings, fixed random seeds (if any), and explicit launch command.
- Run LAMMPS with full timer output so the benchmark runner can parse `Pair`, `Kspace`, `Neigh`, `Comm`, and total loop timings from the standard timing table.
- In generated benchmark YAML, include `runtime.pre_commands` derived from the build section so authoritative runs rebuild the edited LAMMPS binary before benchmarking.
- In generated benchmark runtime command, invoke LAMMPS via MPI launcher with the case-specific rank count (16, 32, or 64), not one fixed rank count for every case.
- Set `OMP_NUM_THREADS=1` unless a case explicitly requires hybrid MPI+OpenMP, and keep this setting identical across baseline/candidate runs.
- In generated benchmark YAML, include a split block so worker sees the train cases only:
  ```yaml
  split:
    train_case_ids:
      - train-16r-short
      - train-32r-short
      - train-16r-long
  ```
