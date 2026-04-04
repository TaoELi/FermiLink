# Optimization Goal

## Package
pyscf

## Language
python

## Target
Optimize DIIS (Direct Inversion in the Iterative Subspace) behavior for SCF in
PySCF, with primary focus on `pyscf/lib/diis.py` and SCF call sites that
invoke DIIS during iterative convergence.

Target optimization opportunities include:
- reduced overhead in DIIS history management and error-vector assembly
- lower-cost linear algebra in DIIS extrapolation updates
- fewer transient allocations and better memory locality in tight SCF loops
- faster convergence-path handling without relaxing tolerances

## Editable Scope
- pyscf/lib/diis.py
- pyscf/scf/**

## Performance Metric
Minimize end-to-end SCF convergence time.

Primary objective should be weighted median total wall-clock time across all
benchmark cases (including both setup and kernel phases).

## Correctness Constraints
- Total SCF energy absolute delta <= 5e-8 Hartree vs incumbent baseline
- Molecular orbital energies RMS delta <= 2e-5 vs incumbent baseline
- Open-shell `<S^2>` absolute delta <= 1e-3 vs incumbent baseline
- All benchmark cases must converge within configured cycle limits
- Do not loosen `conv_tol`, `conv_tol_grad`, DIIS start criteria, or max-cycle defaults
- No case-specific shortcuts keyed on molecule identity

## Representative Workloads
- H2O / cc-pVDZ / RHF / DIIS space=12
- NH3 / cc-pVDZ / RHF / DIIS space=12
- O2 / cc-pVDZ / UHF (spin=2) / DIIS space=12
- NO / cc-pVDZ / UHF (spin=1) / DIIS space=12

## Build
```bash
python -m pip install -U pip
python -m pip install -e .
python -m pip install PyYAML
```

## Notes
- Use MINAO initial guess unless a case explicitly specifies otherwise.
- Keep benchmark behavior deterministic across repeated runs.
- If multithreading is used, keep thread counts explicit in benchmark runtime config.
