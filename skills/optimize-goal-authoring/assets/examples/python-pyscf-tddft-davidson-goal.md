# Optimization Goal

## Package
pyscf

## Language
python

## Target
Optimize the Davidson-style subspace eigensolver used by PySCF TDDFT/TDA, with primary focus on `pyscf/tdscf/_lr_eig.py` and the TD response call sites in `pyscf/tdscf/rhf.py`, `pyscf/tdscf/rks.py`, `pyscf/tdscf/uhf.py`, and `pyscf/tdscf/uks.py`.

Target optimization opportunities include:
- lower-cost projected-subspace construction and update in `eigh`, `eig`, and `real_eig`
- smarter restart/compression policy for the trial space
- fewer expensive full-space copies, orthogonalization passes, and transient allocations
- better reuse/selection of residual vectors and symmetry-partitioned trial vectors
- fewer matrix-vector applications or faster convergence without changing the underlying TDDFT/TDA equations

Do not treat this as a local Python micro-optimization task. The goal is materially faster TDDFT/TDA eigensolver behavior through better Davidson/subspace algorithm choices.

## Editable Scope
- pyscf/tdscf/_lr_eig.py
- pyscf/tdscf/rhf.py
- pyscf/tdscf/rks.py
- pyscf/tdscf/uhf.py
- pyscf/tdscf/uks.py
- pyscf/lib/linalg_helper.py

## Performance Metric
Minimize end-to-end TDDFT/TDA kernel time.

Primary objective should be weighted median total wall-clock time across all benchmark cases. Secondary objective should be lower Davidson iteration count or fewer matrix-vector applications when the benchmark runner can expose those metrics.

## Correctness Constraints
- Excitation energies absolute delta <= 5e-6 Hartree vs incumbent baseline for every reported root
- Oscillator strengths absolute delta <= 1e-4 for singlet closed-shell cases where the benchmark exposes them
- All requested roots must converge, and root ordering should remain consistent with the incumbent baseline
- Do not loosen SCF `conv_tol`, TD solver `conv_tol`, `lindep`, `max_cycle`, `positive_eig_threshold`, `deg_eia_thresh`, `nstates`, or symmetry filtering
- Do not replace TDDFT with TDA/Casida, reduce the number of roots, change functionals/basis sets, or alter DFT grid settings to gain speed
- No case-specific shortcuts keyed on molecule identity, spin state, functional family, or whether the case is train vs test

## Representative Workloads
- train-rks-bp86-casida-benzene-631gss: benzene geometry from `examples/2-benchmark/bz.py` / 6-31g** / RKS / `xc='b88,p86'` / `CasidaTDDFT` / singlet / `nstates=12`
- train-rks-b3lyp-tddft-benzene-631gss: benzene geometry from `examples/2-benchmark/bz.py` / 6-31g** / RKS / `xc='b3lyp5'` / `TDDFT` / singlet / `nstates=10`
- train-uks-bp86-casida-allyl-def2tzvp: allyl radical geometry from `examples/mp/12-dfump2-natorbs.py` / def2-TZVP / spin=1 / UKS / `xc='b88,p86'` / `CasidaTDDFT` / `nstates=8`
- test-rks-b3lyp-tda-c3h7oh-631gss: C3H7OH geometry from `examples/local_orb/08-cholesky.py` / 6-31g** / RKS / `xc='b3lyp5'` / `TDA` / singlet / `nstates=12`
- test-uks-b3lyp-tddft-o2-dimer-def2tzvp: separated O2 + O2 geometry from `examples/mcscf/23-local_spin.py` / def2-TZVP / spin=4 / `symmetry=True` / UKS / `xc='b3lyp5'` / `TDDFT` / `nstates=6`

## Build
```bash
export VENV=/anvil/projects/x-che250091/taoeli/fermilink_optimize/project_pyscf/.venvs/pyscf-optimize-davidson
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
- Base the benchmark setups on the larger single-machine geometries already shipped in the local PySCF tree:
  - benzene from `examples/2-benchmark/bz.py`
  - allyl radical from `examples/mp/12-dfump2-natorbs.py`
  - C3H7OH from `examples/local_orb/08-cholesky.py`
  - separated O2 + O2 from `examples/mcscf/23-local_spin.py`
- Prefer a smaller number of materially larger cases over many toy test cases, so the benchmark is dominated by Davidson/subspace work rather than Python overhead or SCF startup noise.
- For DFT cases, mirror the upstream test setup with `dft.radi.ATOM_SPECIFIC_TREUTLER_GRIDS = False` and `mf.grids.prune = None` so the benchmark is dominated by TDDFT/TDA solver behavior instead of grid-noise differences.
- Keep held-out test cases on molecules different from the train set while staying in the same AO-count regime; for example C3H7OH / `6-31g**` stays close to the benzene / `6-31g**` train size, and the separated O2-dimer / `def2-TZVP` UKS case stays close to the allyl-radical / `def2-TZVP` train size.
- Keep benchmark behavior deterministic across repeated runs.
- If the benchmark runner can expose them, record per-case Davidson iteration count, matrix-vector application count, and total TD kernel wall time.
- Keep all workloads runnable on a single workstation-class machine with BLAS thread counts pinned to 1; prefer increasing molecular size or `nstates` only until TD kernel time clearly dominates SCF time.
- In the generated benchmark YAML, include a top-level split block:
  ```yaml
  split:
    train_case_ids:
      - train-rks-bp86-casida-benzene-631gss
      - train-rks-b3lyp-tddft-benzene-631gss
      - train-uks-bp86-casida-allyl-def2tzvp
  ```
