# Optimization Goal

## Package
pyscf

## Language
python

## Target
Optimize the native C backend on the SCF hot path in PySCF, with primary focus
on the `libcvhf` implementation under `pyscf/lib/vhf` (loaded by
`pyscf/scf/_vhf.py` for J/K builds in SCF iterations).

Target optimization opportunities include:
- lower-cost J/K contraction loops in direct and incore VHF kernels
- improved data locality and reduced transient allocations in tight C loops
- better screening-path efficiency without changing screening semantics
- reduced overhead in real/relativistic direct-dot helper kernels used by SCF

## Editable Scope
- pyscf/lib/vhf/fill_nr_s8.c
- pyscf/lib/vhf/nr_incore.c
- pyscf/lib/vhf/nr_direct.c
- pyscf/lib/vhf/optimizer.c
- pyscf/lib/vhf/nr_direct_dot.c
- pyscf/lib/vhf/time_rev.c
- pyscf/lib/vhf/r_direct_o1.c
- pyscf/lib/vhf/rkb_screen.c
- pyscf/lib/vhf/r_direct_dot.c
- pyscf/lib/vhf/rah_direct_dot.c
- pyscf/lib/vhf/rha_direct_dot.c
- pyscf/lib/vhf/hessian_screen.c
- pyscf/lib/vhf/nr_sgx_direct.c
- pyscf/lib/vhf/nr_sr_vhf.c

## Performance Metric
Minimize end-to-end SCF convergence time.

Primary objective should be weighted median total wall-clock time across all
benchmark cases (including both setup and kernel phases).

## Correctness Constraints
- Total SCF energy absolute delta <= 5e-8 Hartree vs incumbent baseline
- Molecular orbital energies RMS delta <= 2e-5 vs incumbent baseline
- All benchmark cases must converge within configured cycle limits
- Do not loosen `conv_tol`, `conv_tol_grad`, DIIS start criteria, or max-cycle defaults
- Do not change functional behavior (including direct-SCF screening logic) to trade correctness for speed
- No case-specific shortcuts keyed on molecule identity

## Representative Workloads
- train-o2: O2 / 6-31g / UHF (spin=2) / DIIS space=12
- train-h2o: H2O / 6-31g / RHF / DIIS space=12
- test-h2o: H2O / cc-pVDZ / RHF / DIIS space=12
- test-nh3: NH3 / cc-pVDZ / RHF / DIIS space=12
- test-o2: O2 / cc-pVDZ / UHF (spin=2) / DIIS space=12
- test-no: NO / cc-pVDZ / UHF (spin=1) / DIIS space=12

## Build
```bash
python -m pip install -U pip setuptools wheel
export CMAKE_CONFIGURE_ARGS="${CMAKE_CONFIGURE_ARGS:-} -DBUILD_MARCH_NATIVE=ON"
python -m pip install -e .
python -m pip install PyYAML
```

```bash
cd pyscf/lib
mkdir -p build
cd build
cmake .. ${CMAKE_CONFIGURE_ARGS:-}
cmake --build . -j
```

## Notes
- Keep Python SCF driver files (`pyscf/scf/**`) read-only in this campaign.
- Keep BLAS/OpenMP thread settings explicit and identical across baseline/candidate runs.
- Keep `CMAKE_CONFIGURE_ARGS` identical for baseline and all candidate evaluations.
- In the generated benchmark YAML, include a top-level split block:
  ```yaml
  split:
    train_case_ids:
      - train-o2
      - train-h2o
  ```
- Always rebuild the C backend of PySCF (via cmake mentioned in ## Build) as we now modify only the C files in PySCF.
