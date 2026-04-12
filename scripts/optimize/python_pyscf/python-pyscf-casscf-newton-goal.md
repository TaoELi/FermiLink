# Optimization Goal

## Package
pyscf

## Language
python

## Target
Optimize PySCF orbital-optimized MCSCF drivers, with primary focus on the Python-controlled macro/micro iteration, restart, and augmented-Hessian logic in `pyscf/mcscf/mc1step.py` and `pyscf/mcscf/newton_casscf.py`.

Target optimization opportunities include:
- lower overhead in macro/micro iteration bookkeeping, scheduler callbacks, and accepted/rejected orbital-rotation updates
- better reuse of AO2MO/CASCI/density-matrix intermediates across micro iterations, trust-region restarts, and state-average branches
- lower cost in augmented-Hessian and JK-update paths plus fewer full-size transient allocations or copies
- smarter micro-cycle, max-step, and trust-region heuristics that reduce macro iterations or inner AH/KF steps without changing the scientific target
- better handling of one-step, two-step, Newton, state-averaged, and density-fitted CASSCF workloads through the same solver stack

Do not treat this as only a low-level integral-kernel task.
The goal is materially faster Python-level CASSCF/Newton-CASSCF control flow while preserving the existing solver families, restart behavior, state-average semantics, and DF/symmetry branches.

## Editable Scope
- pyscf/mcscf/mc1step.py
- pyscf/mcscf/newton_casscf.py
- pyscf/mcscf/mc1step_symm.py
- pyscf/mcscf/newton_casscf_symm.py
- pyscf/mcscf/casci.py
- pyscf/mcscf/mc_ao2mo.py
- pyscf/mcscf/df.py

## Performance Metric
Minimize end-to-end CASSCF wall-clock time across `mc.mc1step()`, `mc.mc2step()`, and `mc.newton().kernel()` benchmark cases.

Primary objective should be weighted median `casscf_kernel_seconds` across all benchmark cases.
Secondary objective should be lower `macro_cycles`, `micro_cycles`, `casci_seconds`, `ao2mo_seconds`, `update_orb_ci_seconds`, `ah_seconds`, `jk_seconds`, `kf_steps`, and `hx_steps` when the benchmark runner can expose those metrics.

## Correctness Constraints
- Total CASSCF energy absolute delta <= 5e-8 Hartree vs incumbent baseline for single-state cases
- State-averaged total energy absolute delta <= 1e-7 Hartree and per-state energy absolute delta <= 5e-6 Hartree vs incumbent baseline when the benchmark exposes `e_states`
- Molecular-orbital coefficient RMS delta <= 2e-5 and CAS natural-occupation RMS delta <= 2e-4 vs incumbent baseline when the benchmark exposes them
- All benchmark cases must converge while preserving the same active-space definition, spin, symmetry, state weights, and requested number of roots
- Do not loosen `conv_tol`, `conv_tol_grad`, `max_cycle_macro`, `max_cycle_micro`, `max_stepsize`, `ah_conv_tol`, `ah_lindep`, `ah_level_shift`, `ah_start_tol`, `ah_start_cycle`, `ah_max_cycle`, `nroots`, state weights, `fix_spin_`, `wfnsym`, `internal_rotation`, or DF `auxbasis`
- Do not replace Newton-CASSCF with approximate-Hessian CASSCF, replace CASSCF with CASCI/DMRG/selected-CI, reduce roots, alter active-orbital selection, change initial MO sorting/projected-guess rules, remove scheduler hooks, or disable DF/symmetry/state-average branches to gain speed
- No case-specific shortcuts keyed on molecule identity, bond length, basis, active space, solver family, or whether the case is train vs test

## Representative Workloads
- train-mc1step-o2-ccpvdz-cas68: O2 geometry from `examples/mcscf/00-simple_casscf.py` / RHF / spin=2 / cc-pVDZ / CASSCF / CAS(6o,8e) / `mc1step()`
- train-mc2step-n2-ccpvdz-cas66-scheduler: N2 geometry from `examples/mcscf/70-casscf_optimize_scheduler.py` with the higher-cost CAS(6o,6e) pattern from `pyscf/mcscf/test/test_n2.py` / RHF / cc-pVDZ / CASSCF / `mc2step()` / keep the custom `micro_cycle_scheduler`
- train-newton-sa4-h2o-631g-cas44: H2O geometry from `examples/mcscf/15-state_average.py` and `pyscf/mcscf/test/test_h2o.py` / RHF / 6-31g / four-root state-averaged CASSCF / sorted active orbitals `[4,5,6,10]` / `.newton().kernel()`
- test-newton-c2-ccpvdz-cas64-triplet: C2 geometry from `examples/mcscf/12-c2_triplet_from_singlet_hf.py` / RHF / cc-pVDZ / CASSCF / CAS(6o,(4,2)e) / `.newton().kernel()`
- test-df-hf-ccpvdz-cas66: HF geometry from `examples/mcscf/30-hf_scan/hf-scan.py` / RHF / cc-pVDZ / DFCASSCF following the decoration pattern in `examples/mcscf/16-density_fitting.py` / CAS(6o,6e) / `sort_mo([3,4,5,7,9,10])` / `mc1step()`

## Build
```bash
export SOURCE_REPO_ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
export VENV="$SOURCE_REPO_ROOT/../venvs/fermilink-optimize/pyscf-casscf"
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
- Base the benchmark construction on `examples/mcscf/00-simple_casscf.py`, `15-state_average.py`, `16-density_fitting.py`, `70-casscf_optimize_scheduler.py`, `12-c2_triplet_from_singlet_hf.py`, `30-hf_scan/hf-scan.py`, plus `pyscf/mcscf/test/test_h2o.py` and `pyscf/mcscf/test/test_n2.py` for `.newton()` and higher-cost MCSCF setup details.
- Keep the actual benchmark dominated by solver work rather than a whole geometry scan.
- For the HF example, use one fixed bond length and a fixed initial active-space guess; do not benchmark a projected-initial-guess scan over many geometries.
- Use the density-fitting decoration pattern from `examples/mcscf/16-density_fitting.py` on a moderate-size molecule such as HF rather than the heavier benzene/cc-pVTZ demo, so the DF case stays in the same rough cost regime as the other train/test cases.
- If the four-root H2O state-averaged Newton case is too small on the target machine, scale that branch with the larger CAS(6o,8e) state-average pattern in `examples/mcscf/15-state_average.py` or a symmetry-enabled variant from `pyscf/mcscf/test/test_h2o.py`, while keeping held-out test molecules distinct from the train set.
- Keep benchmark behavior deterministic across repeated runs.
- It is acceptable to use 2-4 OpenMP or BLAS threads if single-thread timings are too short, but thread counts should be pinned explicitly and kept identical for baseline and candidate runs.
- If the benchmark runner can expose them, record per-case `macro_cycles`, `micro_cycles`, `ao2mo_seconds`, `casci_seconds`, `fcisolver_seconds`, `update_orb_ci_seconds`, `ah_seconds`, `jk_seconds`, `kf_steps`, `hx_steps`, and total `casscf_kernel_seconds`.
- In the generated benchmark YAML, include `runtime.pre_commands` derived from the `## Build` section so authoritative benchmark runs rebuild and reinstall the local PySCF checkout deterministically.
- In the generated benchmark YAML, include a top-level split block:
  ```yaml
  split:
    train_case_ids:
      - train-mc1step-o2-ccpvdz-cas68
      - train-mc2step-n2-ccpvdz-cas66-scheduler
      - train-newton-sa4-h2o-631g-cas44
  ```
