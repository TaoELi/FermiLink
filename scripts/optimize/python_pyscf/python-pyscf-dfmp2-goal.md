# Optimization Goal

## Package
pyscf

## Language
python

## Target
Optimize PySCF molecular density-fitted MP2, with primary focus on the Python-controlled batching, incore/outcore switching, and open-shell UHF implementation split across:

- legacy RHF/UHF DF-MP2 kernels and `ovL` caching heuristics in `pyscf/mp/dfmp2.py` and `pyscf/mp/dfump2.py`
- native DF-MP2 / DF-UMP2 three-center transformation and energy loops in `pyscf/mp/dfmp2_native.py` and `pyscf/mp/dfump2_native.py`

Target optimization opportunities include:
- better occupied-block and auxiliary-block heuristics for incore and outcore `ao2mo` / three-center-integral batching
- lower overhead in repeated denominator setup, `get_occ_blk` access, and transient `Kab` / `Tab` / `Eab` array allocation in the MP2 energy loops
- better reuse of HDF5-backed outcore buffers and lower Python overhead in the legacy RHF/UHF block walkers
- faster native UHF integral transformation and alpha/beta energy contraction without changing the DF-MP2 equations or the public API split between legacy and native implementations

Do not solve this by timing SCF, by silently routing all UHF workloads to a different API family, or by bypassing outcore caching.
The goal is materially faster DF-MP2 correlation-stage execution while preserving the incumbent legacy/native entry points, frozen-orbital handling, and outcore read/write semantics.

## Editable Scope
- pyscf/mp/dfmp2.py
- pyscf/mp/dfump2.py
- pyscf/mp/dfmp2_native.py
- pyscf/mp/dfump2_native.py

## Performance Metric
Minimize wall-clock time for the DF-MP2 correlation stage after the RHF/UHF reference has converged.

Primary objective should be weighted median `dfmp2_stage_seconds` across all benchmark cases, where `dfmp2_stage_seconds` starts immediately before the DF-MP2 object begins its integral-build / `ao2mo` work and ends after the correlation energy is returned.
Secondary objective should be lower `ao2mo_seconds`, `ints3c_seconds`, `emp2_contract_seconds`, `same_spin_seconds`, `opposite_spin_seconds`, and outcore HDF5 read/write time when the benchmark runner can expose those metrics.

## Correctness Constraints
- MP2 correlation energy absolute delta <= 5e-7 Hartree vs incumbent baseline for every case
- Same-spin and opposite-spin correlation-energy component absolute delta <= 5e-7 Hartree vs incumbent baseline when the benchmark exposes them
- Total energy absolute delta <= 5e-7 Hartree vs incumbent baseline when the benchmark exposes `e_tot`
- All benchmark cases must finish successfully and preserve the requested RHF or UHF reference, charge, spin, symmetry, frozen-orbital mask, and intended incore vs outcore execution mode
- Do not loosen SCF `conv_tol`, change the basis set, change the MP2-fit auxiliary basis selection, change `with_t2=False` on the legacy cases, or alter the explicit outcore-memory caps used to trigger HDF5-backed `ovL` storage
- Do not replace a legacy `pyscf.mp.dfmp2.DFMP2` or `pyscf.mp.dfump2.DFUMP2` workload with `dfmp2_native` / `dfump2_native`, or vice versa, solely to gain speed
- Do not bypass frozen-core handling, outcore `ovL` save/read behavior, or the separate alpha/beta integral treatment in the native UHF path
- No case-specific shortcuts keyed on molecule identity, charge, spin, basis, API family, incore/outcore label, or whether the case is train vs test

## Representative Workloads
- train-rhf-benzene-631gss-incore: benzene geometry from `examples/2-benchmark/bz.py` / RHF / 6-31g** / legacy `pyscf.mp.dfmp2.DFMP2` / energy-only `kernel(with_t2=False)` / leave `max_memory` high enough that `ovL` stays incore
- train-rhf-glycine-631gs-outcore: glycine geometry from `examples/scf/glycine.xyz` / RHF / 6-31g* / legacy `pyscf.mp.dfmp2.DFMP2` / energy-only `kernel(with_t2=False)` / set a deterministic low `max_memory` so `ovL` is forced to the outcore HDF5 path
- train-uhf-allyl-def2tzvp-legacy: allyl radical geometry from `examples/mp/12-dfump2-natorbs.py` / UHF / spin=1 / def2-TZVP / legacy `pyscf.mp.dfump2.DFUMP2` / energy-only `kernel(with_t2=False)`
- test-rhf-c3h7oh-631gss-outcore: C3H7OH geometry from `examples/local_orb/08-cholesky.py` / RHF / 6-31g** / legacy `pyscf.mp.dfmp2.DFMP2` / energy-only `kernel(with_t2=False)` / force the same outcore `ovL` path with a deterministic low `max_memory`
- test-uhf-o2-dimer-def2tzvp-native: separated O2 + O2 geometry from `examples/mcscf/23-local_spin.py` / UHF / spin=4 / `symmetry=True` / def2-TZVP / native `pyscf.mp.dfump2_native.DFUMP2` / `kernel()`

## Build
```bash
export SOURCE_REPO_ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
export VENV="$SOURCE_REPO_ROOT/../venvs/fermilink-optimize/pyscf-dfmp2"
source "$VENV/bin/activate"
module remove cmake
cd pyscf/lib
mkdir -p build
cd build
cmake ..
cmake --build . -j4
cd ../../../
python -m pip install -e .
```

## Notes
- Base the benchmark construction on the public DF-MP2 examples `examples/mp/10-dfmp2.py`, `examples/mp/10-dfump2.py`, and `examples/mp/12-dfump2-natorbs.py`, plus the upstream regression coverage in `pyscf/mp/test/test_dfmp2.py`, `pyscf/mp/test/test_dfump2.py`, and `pyscf/mp/test/test_dfump2_native.py`.
- Time only the DF-MP2 stage, not the RHF/UHF SCF build.
- Construct canonical RHF/UHF references separately with deterministic settings such as `conv_tol=1e-10` and `init_guess='minao'`, then start timing immediately before the MP2 object enters `ao2mo` / integral-transformation work.
- Keep one closed-shell incore case, two closed-shell outcore cases, one legacy open-shell UHF case, and one native open-shell UHF case so the campaign cannot win solely on the easiest RHF-incore path.
- Keep held-out test molecules different from the train set while staying in a similar AO-count regime; C3H7OH / 6-31g** is a reasonable held-out partner for the benzene and glycine RHF cases, and the separated O2-dimer / def2-TZVP native UHF case is a reasonable held-out partner for the allyl / def2-TZVP legacy UHF case.
- The labeled outcore cases must actually hit the outcore branch on both incumbent and optimized runs.
- Use explicit per-case `max_memory` caps or an equivalent deterministic trigger, and have the benchmark runner assert which path was taken.
- Use `with_t2=False` for the legacy DF-MP2 and DF-UMP2 workloads so the timed region isolates batching, integral caching, and contraction heuristics rather than four-index amplitude storage.
- Preserve that choice across incumbent and optimized runs.
- Treat `pyscf/mp/dfump2_slow.py` as a behavioral reference for legacy UHF semantics if needed, but benchmark the public legacy and native paths above rather than optimizing only the standalone slow fallback.
- Keep benchmark behavior deterministic across repeated runs.
- Pin BLAS/OpenMP thread counts explicitly; `OMP_NUM_THREADS=4` is acceptable if single-thread timings are too small on the target machine, but the same thread count must be used for every case and every candidate.
- In the generated benchmark YAML, include `runtime.pre_commands` derived from the `## Build` section so authoritative runs rebuild and reinstall the local PySCF checkout deterministically.
- If the benchmark runner can expose them, record per-case `dfmp2_stage_seconds`, `ao2mo_seconds`, `ints3c_seconds`, `emp2_contract_seconds`, `same_spin_seconds`, `opposite_spin_seconds`, `outcore_read_seconds`, `outcore_write_seconds`, and the occupied/auxiliary block sizes chosen at runtime.
- Keep all workloads within a single-workstation regime that would still be reasonable for follow-on multireference calculations such as CASSCF; do not scale beyond the listed benzene, glycine, C3H7OH, allyl, and separated O2-dimer systems just to make the benchmark longer.
- In the generated benchmark YAML, include a top-level split block:
  ```yaml
  split:
    train_case_ids:
      - train-rhf-benzene-631gss-incore
      - train-rhf-glycine-631gs-outcore
      - train-uhf-allyl-def2tzvp-legacy
  ```
