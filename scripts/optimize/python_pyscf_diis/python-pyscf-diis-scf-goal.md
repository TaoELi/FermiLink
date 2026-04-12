# Optimization Goal

## Package
pyscf

## Language
python

## Target
Optimize the DIIS family used by PySCF SCF, with primary focus on:

- core DIIS subspace storage and extrapolation in `pyscf/lib/diis.py`
- SCF-specific CDIIS/ADIIS/EDIIS logic and error-vector construction in `pyscf/scf/diis.py`
- RHF/UHF/ROHF call sites that invoke DIIS during iterative SCF updates in `pyscf/scf/hf.py`, `pyscf/scf/uhf.py`, and `pyscf/scf/rohf.py`

Target optimization opportunities include:
- lower-cost linear algebra in CDIIS update/extrapolation
- cheaper error-vector construction, including symmetry-aware masking paths
- lower overhead in ADIIS/EDIIS minimization and DIIS history bookkeeping
- fewer transient allocations and better memory locality in tight SCF loops
- fewer SCF cycles or lower per-cycle DIIS cost without relaxing tolerances or changing the scientific fixed point

Do not treat this as only a local Python micro-optimization task. Algorithm-level improvements within the existing DIIS family are in scope, but the benchmark must preserve CDIIS/ADIIS/EDIIS availability, restart/rollback behavior, and symmetry-aware error-vector handling.

## Editable Scope
- pyscf/lib/diis.py
- pyscf/scf/diis.py
- pyscf/scf/hf.py
- pyscf/scf/uhf.py
- pyscf/scf/rohf.py

## Performance Metric
Minimize end-to-end SCF kernel time from `mf.kernel()`.

Primary objective should be weighted median `scf_kernel_seconds` across all benchmark cases. Secondary objective should be lower `scf_cycles` and lower DIIS-phase time such as `diis_update_seconds` when the benchmark runner can expose those metrics.

## Correctness Constraints
- Total SCF energy absolute delta <= 5e-8 Hartree vs incumbent baseline
- Molecular orbital energies RMS delta <= 2e-5 Hartree vs incumbent baseline
- Density matrix RMS delta <= 2e-6 vs incumbent baseline when the benchmark exposes density matrices
- `<S^2>` absolute delta <= 1e-6 for unrestricted and ROHF cases when the benchmark exposes it
- All benchmark cases must converge within incumbent cycle limits and preserve requested spin and symmetry configuration
- Do not loosen `conv_tol`, `conv_tol_grad`, `diis_space`, `diis_start_cycle`, `diis_space_rollback`, `max_cycle`, damping, level shift, initial guess, `irrep_nelec`, or symmetry settings
- Do not disable or bypass CDIIS, ADIIS, EDIIS, DIIS restart-from-file behavior, or symmetry-projected error-vector handling to gain speed
- No case-specific shortcuts keyed on molecule identity, charge, spin, basis, DIIS family, or whether the case is train vs test

## Representative Workloads
- train-rhf-benzene-631gss: benzene geometry from `examples/2-benchmark/bz.py` / RHF / 6-31g** / `diis_space=12` / `init_guess='minao'`
- train-rhf-glycine-631gs: glycine geometry from `examples/scf/glycine.xyz` / RHF / 6-31g* / `diis_space=12` / `init_guess='minao'`
- train-uhf-allyl-def2tzvp: allyl radical geometry from `examples/mp/12-dfump2-natorbs.py` / UHF / spin=1 / def2-TZVP / `diis_space=12` / `init_guess='minao'`
- test-rhf-c3h7oh-631gss: C3H7OH geometry from `examples/local_orb/08-cholesky.py` / RHF / 6-31g** / `diis_space=12` / `init_guess='minao'`
- test-uhf-o2-dimer-def2tzvp: separated O2 + O2 geometry from `examples/mcscf/23-local_spin.py` / UHF / spin=4 / `symmetry=True` / def2-TZVP / `diis_space=12` / `init_guess='minao'`
- test-rohf-o2-dimer-def2tzvp-symmetry: separated O2 + O2 geometry from `examples/mcscf/23-local_spin.py` / ROHF / spin=4 / `symmetry=True` / def2-TZVP / `diis_space=12` / fixed `irrep_nelec`

## Build
```bash
export SOURCE_REPO_ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
export VENV="$SOURCE_REPO_ROOT/../venvs/fermilink-optimize/pyscf-diis"
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
- Base the benchmark setups on the DIIS-focused upstream tests in `pyscf/scf/test/test_diis.py` and `pyscf/lib/test/test_diis.py`, plus the larger local SCF examples in `examples/2-benchmark/` and `examples/scf/`.
- Keep held-out test cases on different molecules than the train set so benchmark improvements generalize beyond benzene, glycine, and allyl-specific behavior.
- Keep held-out test cases in roughly the same AO-count regime as the train set so controller-side performance remains representative; for example C3H7OH / 6-31g** is close to the benzene/glycine RHF workloads, and the separated O2 + O2 / def2-TZVP UHF/ROHF cases stay near the allyl / def2-TZVP orbital dimension instead of dropping to tiny diatomics.
- Prefer a smaller number of materially larger RHF/UHF cases plus one behavior-protection ROHF symmetry case, so the benchmark is dominated by iterative SCF/DIIS behavior rather than tiny-system timing noise.
- Use `init_guess='minao'` unless a case explicitly specifies otherwise.
- Keep benchmark behavior deterministic across repeated runs, with thread counts pinned explicitly in benchmark runtime config.
- In the generated benchmark YAML, include `runtime.pre_commands` derived from the `## Build` section so authoritative benchmark runs use the current PySCF checkout deterministically.
- For the symmetry-constrained ROHF O2-dimer case, it is acceptable to seed `irrep_nelec` from a converged symmetry-adapted reference; one valid pattern is `{'Ag': (4,4), 'B1g': (1,1), 'B2g': (3,2), 'B3g': (1,0), 'Au': (1,0), 'B1u': (3,2), 'B2u': (1,1), 'B3u': (4,4)}`.
- If the benchmark runner can expose them, record per-case `scf_cycles`, `diis_update_seconds`, `get_fock_seconds`, `eig_seconds`, and total `scf_kernel_seconds`.
- In the generated benchmark YAML, include a top-level split block:
  ```yaml
  split:
    train_case_ids:
      - train-rhf-benzene-631gss
      - train-rhf-glycine-631gs
      - train-uhf-allyl-def2tzvp
  ```
