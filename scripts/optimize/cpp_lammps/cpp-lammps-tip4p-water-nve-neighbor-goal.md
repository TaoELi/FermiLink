# Optimization Goal

## Package
lammps

## Language
cpp

## Target
Optimize the neighbor-list construction hot path of the TIP4P long-range water NVE workflow in LAMMPS, with primary focus on rebuild-time binning and pair-list generation for the `neighbor 2.0 bin` configuration used by the benchmark inputs.

In `src/verlet.cpp`, the TIP4P NVE loop calls `neighbor->decide()` every step and `neighbor->build(1)` whenever reneighboring is required. For this input deck, the relevant hot paths are `src/neighbor.cpp` and `src/npair_bin.cpp`: `Neighbor::build()` stores the displacement reference state, bins all local and ghost atoms, builds the perpetual pair lists, and rebuilds topology lists, while `NPairBin::build()` loops over owned atoms and bin stencils, evaluates cutoffs and exclusions, and emits the neighbor lists consumed by the pair style.

Primary optimization interest is reducing neighbor rebuild cost for the fixed-size water systems while preserving the same list completeness, rebuild safety, special-neighbor handling, and stable NVE behavior.

This goal assumes benchmark generation will use the attached input artifacts by filename (`water_216_data.lmp`, `in.tip4p_nve`, and `in.tip4p_nve_long`) and resolve them from the staged goal input root.

## Editable Scope
- src/neighbor.cpp
- src/neighbor.h
- src/npair_bin.cpp
- src/npair_bin.h

## Performance Metric
Minimize weighted median `neigh_seconds` across all benchmark cases.

Benchmark should also record `loop_seconds`, `neigh_seconds`, `pair_seconds`, `kspace_seconds`, `comm_seconds`, `bond_seconds`, and normalized throughput (for example, steps/second or ns/day). Secondary objective should be lower `loop_seconds` without winning by skipping required rebuilds or weakening list safety.

## Correctness Constraints
- Preserve NVE energy behavior: total energy drift per atom per step over the longer runs must stay within benchmark tolerance versus incumbent baseline.
- Preserve sampled thermo observables at matched output steps: `etotal`, `pe`, `ke`, `temp`, `press`, and `density` must stay within benchmark tolerance.
- Preserve sampled force consistency for representative frames: RMS and max absolute force-component deltas must stay within benchmark tolerance.
- Preserve neighbor semantics exactly: same pair-list completeness, same special-neighbor encoding and exclusion behavior, same topology-list correctness for bonds and angles, and no dangerous builds or out-of-range atoms introduced by the optimization.
- Do not change physical model semantics or runtime controls to gain speed: keep `pair_style lj/cut/tip4p/long`, `kspace_style pppm/tip4p 0.0001`, `neighbor 2.0 bin`, default rebuild-safety behavior, `timestep 0.5`, units, and TIP4P geometry assumptions unchanged.
- Do not weaken distance checks, ghost coverage, or rebuild cadence safety to gain speed.
- All benchmark cases must complete successfully with deterministic runner settings.

## Representative Workloads
- train-16r-long: `in.tip4p_nve_long` + `water_216_data.lmp` on 16 MPI ranks, giving a longer run with repeated neighbor rebuild opportunities and lower communication noise.
- train-32r-long: `in.tip4p_nve_long` + `water_216_data.lmp` on 32 MPI ranks to keep the optimization useful across a second domain decomposition.
- train-32r-short: `in.tip4p_nve` + `water_216_data.lmp` on 32 MPI ranks as a shorter-turnaround rebuild case.
- test-16r-short: `in.tip4p_nve` + `water_216_data.lmp` on 16 MPI ranks as a held-out lower-rank case.
- test-64r-short: `in.tip4p_nve` + `water_216_data.lmp` on 64 MPI ranks as a held-out scaling-sensitive case where neighbor and communication interact more strongly.

## Build
```bash
mkdir -p build
cd build
cmake -C ../cmake/presets/most.cmake -C ../cmake/presets/nolib.cmake -D PKG_GPU=off ../cmake
cmake --build . -j4
```

## Notes
- Treat the attached LAMMPS input file(s) as the source of truth for runtime settings and any include-chain files.
- This campaign is intended to find algorithm-level improvements inside `src/neighbor.*` and `src/npair_bin.*`, not generic pair, PPPM, communication, or bonded-kernel tuning.
- Keep benchmark execution deterministic: fixed thread settings, fixed random seeds (if any), and explicit launch command.
- Run LAMMPS with full timer output so the benchmark runner can parse `Neigh`, `Pair`, `Kspace`, `Comm`, `Bond`, and total loop timings from the standard timing table.
- In generated benchmark YAML, include `runtime.pre_commands` derived from the build section so authoritative runs rebuild the edited LAMMPS binary before benchmarking.
- In generated benchmark runtime command, invoke LAMMPS via MPI launcher with the case-specific rank count (16, 32, or 64), not one fixed rank count for every case.
- Set `OMP_NUM_THREADS=1` unless a case explicitly requires hybrid MPI+OpenMP, and keep this setting identical across baseline/candidate runs.
- In generated benchmark YAML, include a split block so worker sees the train cases only:
  ```yaml
  split:
    train_case_ids:
      - train-16r-long
      - train-32r-long
      - train-32r-short
  ```
