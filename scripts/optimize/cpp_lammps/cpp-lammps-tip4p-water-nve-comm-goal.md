# Optimization Goal

## Package
lammps

## Language
cpp

## Target
Optimize the MPI communication hot path of the TIP4P long-range water NVE workflow in LAMMPS, with primary focus on the brick-domain communication routines used throughout the Verlet step.

In `src/verlet.cpp`, the TIP4P NVE loop calls `comm->forward_comm()` on non-reneighboring steps, calls `comm->exchange()` plus `comm->borders()` on reneighboring steps, and calls `comm->reverse_comm()` after force evaluation because `pppm/tip4p` requires Newton communication of forces on ghost atoms. For the benchmarked input, the relevant hot path is `src/comm_brick.cpp`: repeated work includes packing and unpacking ghost coordinates, migrating atoms that crossed sub-domain boundaries, constructing border ghost slabs, and returning force contributions from ghost atoms to their owners.

Primary optimization interest is reducing communication overhead for the fixed-size water system across 16/32/64-rank decompositions while preserving the same atom migration, halo coverage, and Newton communication semantics.

This goal assumes benchmark generation will use the attached input artifacts by filename (`water_216_data.lmp`, `in.tip4p_nve`, and `in.tip4p_nve_long`) and resolve them from the staged goal input root.

## Editable Scope
- src/comm_brick.cpp
- src/comm_brick.h

## Performance Metric
Minimize weighted median `comm_seconds` across all benchmark cases.

Benchmark should also record `loop_seconds`, `comm_seconds`, `pair_seconds`, `kspace_seconds`, `neigh_seconds`, `bond_seconds`, and normalized throughput (for example, steps/second or ns/day). Secondary objective should be lower `loop_seconds` without winning by changing decomposition, ghost coverage, or force-return semantics.

## Correctness Constraints
- Preserve NVE energy behavior: total energy drift per atom per step over the longer runs must stay within benchmark tolerance versus incumbent baseline.
- Preserve sampled thermo observables at matched output steps: `etotal`, `pe`, `ke`, `temp`, `press`, and `density` must stay within benchmark tolerance.
- Preserve sampled force consistency for representative frames: RMS and max absolute force-component deltas must stay within benchmark tolerance.
- Preserve communication semantics exactly: same atom ownership after migration, same ghost coverage needed by TIP4P pair/PPPM and bonded kernels, same reverse force accumulation, and no lost atoms or duplicate ownership.
- Do not change physical model semantics or runtime controls to gain speed: keep `pair_style lj/cut/tip4p/long`, `kspace_style pppm/tip4p 0.0001`, `neighbor 2.0 bin`, `timestep 0.5`, units, and TIP4P geometry assumptions unchanged.
- Do not change MPI rank counts, communication style, ghost-cutoff safety, or Newton/halo semantics to gain speed.
- All benchmark cases must complete successfully with deterministic runner settings.

## Representative Workloads
- train-32r-short: `in.tip4p_nve` + `water_216_data.lmp` on 32 MPI ranks, where communication is a larger fraction of the full loop than on 16 ranks.
- train-64r-short: `in.tip4p_nve` + `water_216_data.lmp` on 64 MPI ranks to amplify forward/reverse communication and border exchange costs.
- train-32r-long: `in.tip4p_nve_long` + `water_216_data.lmp` on 32 MPI ranks for a longer timer-stability case with repeated atom migration and ghost refresh.
- test-16r-short: `in.tip4p_nve` + `water_216_data.lmp` on 16 MPI ranks as a held-out lower-communication case.
- test-16r-long: `in.tip4p_nve_long` + `water_216_data.lmp` on 16 MPI ranks as a held-out lower-communication drift case.

## Build
```bash
mkdir -p build
cd build
cmake -C ../cmake/presets/most.cmake -C ../cmake/presets/nolib.cmake -D PKG_GPU=off ../cmake
cmake --build . -j4
```

## Notes
- Treat the attached LAMMPS input file(s) as the source of truth for runtime settings and any include-chain files.
- This campaign is intended to find algorithm-level improvements inside `src/comm_brick.*`, not generic pair, PPPM, neighbor, or bonded-kernel tuning.
- Keep benchmark execution deterministic: fixed thread settings, fixed random seeds (if any), and explicit launch command.
- Run LAMMPS with full timer output so the benchmark runner can parse `Comm`, `Pair`, `Kspace`, `Neigh`, `Bond`, and total loop timings from the standard timing table.
- In generated benchmark YAML, include `runtime.pre_commands` derived from the build section so authoritative runs rebuild the edited LAMMPS binary before benchmarking.
- In generated benchmark runtime command, invoke LAMMPS via MPI launcher with the case-specific rank count (16, 32, or 64), not one fixed rank count for every case.
- Set `OMP_NUM_THREADS=1` unless a case explicitly requires hybrid MPI+OpenMP, and keep this setting identical across baseline/candidate runs.
- In generated benchmark YAML, include a split block so worker sees the train cases only:
  ```yaml
  split:
    train_case_ids:
      - train-32r-short
      - train-64r-short
      - train-32r-long
  ```
