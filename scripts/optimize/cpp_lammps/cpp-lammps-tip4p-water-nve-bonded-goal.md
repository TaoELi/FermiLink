# Optimization Goal

## Package
lammps

## Language
cpp

## Target
Optimize the bonded-force hot path of the TIP4P long-range water NVE workflow in LAMMPS, with primary focus on the intramolecular `bond_style class2` and `angle_style harmonic` kernels that run every timestep.

In `src/verlet.cpp`, the TIP4P NVE loop invokes `force->bond->compute()` and `force->angle->compute()` every step after pair forces and before PPPM. For this input deck, the relevant hot paths are `src/CLASS2/bond_class2.cpp` and `src/MOLECULE/angle_harmonic.cpp`: `BondClass2::compute()` walks `neighbor->bondlist` and evaluates the class2 polynomial for every O-H bond, while `AngleHarmonic::compute()` walks `neighbor->anglelist` and performs the two-bond geometry plus `acos()`-based force evaluation for every H-O-H angle. Because the benchmark replicates 216 waters by `4x4x4`, these bonded kernels execute over tens of thousands of topology elements every step even though pair and kspace dominate the largest buckets.

Primary optimization interest is reducing the bonded contribution to full-loop runtime for fixed-size water systems while preserving the same intramolecular model, timestep, and stable NVE behavior.

This goal assumes benchmark generation will use the attached input artifacts by filename (`water_216_data.lmp`, `in.tip4p_nve`, and `in.tip4p_nve_long`) and resolve them from the staged goal input root.

## Editable Scope
- src/CLASS2/bond_class2.cpp
- src/CLASS2/bond_class2.h
- src/MOLECULE/angle_harmonic.cpp
- src/MOLECULE/angle_harmonic.h

## Performance Metric
Minimize weighted median `bond_seconds` across all benchmark cases.

Benchmark should also record `loop_seconds`, `bond_seconds`, `pair_seconds`, `kspace_seconds`, `neigh_seconds`, `comm_seconds`, and normalized throughput (for example, steps/second or ns/day). Secondary objective should be lower `loop_seconds` without shifting work into another timer bucket or winning only through decomposition artifacts.

## Correctness Constraints
- Preserve NVE energy behavior: total energy drift per atom per step over the longer runs must stay within benchmark tolerance versus incumbent baseline.
- Preserve sampled thermo observables at matched output steps: `etotal`, `pe`, `ke`, `temp`, `press`, and `density` must stay within benchmark tolerance.
- Preserve sampled force consistency for representative frames: RMS and max absolute force-component deltas must stay within benchmark tolerance.
- Preserve bonded-model semantics exactly: keep `bond_style class2`, `angle_style harmonic`, bond/angle coefficients, topology ownership, and Newton bonded-force accumulation behavior unchanged.
- Preserve trajectory invariants for identical initial state and deterministic seed: same atom count, stable completion, no lost atoms, and no NaN/Inf.
- Do not change physical model semantics or runtime controls to gain speed: keep `pair_style lj/cut/tip4p/long`, `kspace_style pppm/tip4p 0.0001`, `neighbor 2.0 bin`, `timestep 0.5`, units, and TIP4P geometry assumptions unchanged.
- All benchmark cases must complete successfully with deterministic runner settings.

## Representative Workloads
- train-16r-short: `in.tip4p_nve` + `water_216_data.lmp` on 16 MPI ranks, where communication is lighter and bonded compute is easier to resolve against the full loop.
- train-32r-short: `in.tip4p_nve` + `water_216_data.lmp` on 32 MPI ranks to keep the bonded optimization useful across a second domain decomposition.
- train-16r-long: `in.tip4p_nve_long` + `water_216_data.lmp` on 16 MPI ranks for a longer-horizon NVE drift and timer-stability case.
- test-32r-long: `in.tip4p_nve_long` + `water_216_data.lmp` on 32 MPI ranks as a held-out moderate-decomposition validation case.
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
- This campaign is intended to find algorithm-level improvements inside the named bonded kernels, not generic pair, PPPM, neighbor, or communication tuning.
- Keep benchmark execution deterministic: fixed thread settings, fixed random seeds (if any), and explicit launch command.
- Run LAMMPS with full timer output so the benchmark runner can parse `Bond`, `Pair`, `Kspace`, `Neigh`, `Comm`, and total loop timings from the standard timing table.
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
