# Optimization Goal

## Package
pyscf

## Language
python

## Target
Optimize PySCF molecular DFT numerical integration, with primary focus on the Python-controlled grid-block batching, density-evaluation path selection, and AO screening logic in `pyscf/dft/numint.py`, plus the direct RKS/UKS call sites in `pyscf/dft/rks.py` and `pyscf/dft/uks.py`.

Target optimization opportunities include:
- lower overhead in `NumInt.block_loop` grid blocking, buffer reuse, and repeated per-block setup
- cheaper `make_rho` dispatch and better general-purpose `eval_rho1` / `eval_rho2` path selection
- lower-cost sparse/dense AO screening and pair-mask reuse
- fewer transient allocations across `nr_rks` / `nr_uks` block loops and better reuse of scaled-AO work arrays
- faster GGA, meta-GGA, hybrid, and UKS numerical integration without changing the grid definition, XC model, or SCF fixed point

Do not treat this as only a low-level C-kernel problem.
The goal is materially faster Python-level numerical integration while preserving the general RKS/UKS screening semantics and the sparse/dense plus `eval_rho1` / `eval_rho2` dispatch behavior across held-out systems.

## Editable Scope
- pyscf/dft/numint.py
- pyscf/dft/rks.py
- pyscf/dft/uks.py

## Performance Metric
Minimize cumulative time spent in DFT numerical integration during `mf.kernel()`.

Primary objective should be weighted median `numint_seconds` across all benchmark cases, where `numint_seconds` is the accumulated time spent inside `NumInt.nr_rks` / `NumInt.nr_uks` or an equivalent directly timed numerical integration region.
Secondary objective should be lower `scf_kernel_seconds`, `make_rho_seconds`, `eval_xc_seconds`, and `block_loop_seconds` when the benchmark runner can expose those metrics.

## Correctness Constraints
- Total SCF energy absolute delta <= 5e-8 Hartree vs incumbent baseline
- Numerical electron count absolute delta <= 5e-7 vs incumbent baseline for every case
- Molecular orbital energies RMS delta <= 2e-5 Hartree vs incumbent baseline
- Density matrix RMS delta <= 2e-6 vs incumbent baseline when the benchmark exposes density matrices
- `<S^2>` absolute delta <= 1e-6 for UKS cases when the benchmark exposes it
- All benchmark cases must converge within incumbent cycle limits and preserve requested spin, symmetry, XC family, and grid settings
- Do not loosen `conv_tol`, `conv_tol_grad`, `max_cycle`, `level_shift`, damping, `init_guess`, `grids.level`, `grids.prune`, `grids.atom_grid`, `radi.ATOM_SPECIFIC_TREUTLER_GRIDS`, `omega`, or symmetry settings
- Do not change the XC functional, skip meta-GGA tau terms, bypass screening entirely, or force a case-specific sparse/dense or `eval_rho1` / `eval_rho2` choice keyed on workload identity
- No case-specific shortcuts keyed on molecule identity, charge, spin, basis, functional, grid scheme, or whether the case is train vs test

## Representative Workloads
- train-rks-b3lyp-benzene-631gss-grid4: benzene geometry from `examples/2-benchmark/bz.py` / RKS / `xc='b3lyp5'` / 6-31g** / `grids.level=4` / `grids.prune=None` / `init_guess='minao'`
- train-rks-m06l-glycine-631gss-grid4: glycine geometry from `examples/scf/glycine.xyz` / RKS / `xc='m06l'` / 6-31g** / `grids.level=4` / `grids.prune=None` / `init_guess='minao'`
- train-uks-bp86-allyl-def2tzvp-grid4: allyl radical geometry from `examples/mp/12-dfump2-natorbs.py` / UKS / `xc='b88,p86'` / spin=1 / def2-TZVP / `grids.level=4` / `grids.prune=None` / `init_guess='minao'`
- test-rks-camb3lyp-c3h7oh-631gss-grid4: C3H7OH geometry from `examples/local_orb/08-cholesky.py` / RKS / `xc='camb3lyp'` / 6-31g** / `grids.level=4` / `grids.prune=None` / `init_guess='minao'`
- test-uks-b3lyp-o2-dimer-def2tzvp-grid4: separated O2 + O2 geometry from `examples/mcscf/23-local_spin.py` / UKS / `xc='b3lyp5'` / spin=4 / `symmetry=True` / def2-TZVP / `grids.level=4` / `grids.prune=None` / `init_guess='minao'`

## Build
```bash
export SOURCE_REPO_ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
export VENV="$SOURCE_REPO_ROOT/../venvs/fermilink-optimize/pyscf-numint"
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
- Base the benchmark construction on the DFT driver patterns in `examples/dft/00-simple_dft.py`, `examples/dft/11-grid_scheme.py`, and `examples/dft/13-rsh_dft.py`, but use the larger local geometries above so the timed region is dominated by `numint` rather than tiny-system noise.
- Mirror upstream DFT and TDDFT test practice with `dft.radi.ATOM_SPECIFIC_TREUTLER_GRIDS = False` and `grids.prune = None` so grid construction and screening behavior stay deterministic across runs.
- Keep held-out test cases on different molecules than the train set while staying in roughly the same AO-count regime; C3H7OH / 6-31g** stays close to the benzene and glycine RKS cases, and the separated O2-dimer / def2-TZVP UKS case stays close to the allyl-radical / def2-TZVP case.
- The workload mix should span common and advanced molecular DFT branches through one standard hybrid case, one meta-GGA case, one open-shell UKS GGA case, and one range-separated hybrid held-out case, all through the same `numint` hot path.
- Prefer timing the cumulative `nr_rks` / `nr_uks` region directly, not only end-to-end SCF time.
- If the runner can expose them, record per-case `numint_seconds`, `scf_kernel_seconds`, `make_rho_seconds`, `eval_xc_seconds`, `block_loop_seconds`, `eval_rho1_calls`, `eval_rho2_calls`, `sparse_block_count`, and `dense_block_count`.
- Keep benchmark behavior deterministic across repeated runs, with BLAS thread counts pinned explicitly to 1 in the generated benchmark runtime config.
- In the generated benchmark YAML, include `runtime.pre_commands` derived from the `## Build` section so authoritative runs rebuild and reinstall the local PySCF checkout deterministically.
- In the generated benchmark YAML, include a top-level split block:
  ```yaml
  split:
    train_case_ids:
      - train-rks-b3lyp-benzene-631gss-grid4
      - train-rks-m06l-glycine-631gss-grid4
      - train-uks-bp86-allyl-def2tzvp-grid4
  ```
