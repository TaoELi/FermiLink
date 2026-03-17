# scripts/ directory scope

This directory stores maintenance scripts and case-specific optimize benchmark examples.

## Naming convention

Benchmark examples follow `language-pkg-goal` naming:

- `<language>-<package>-<goal>-benchmark.yaml`
- `<language>-<package>-<goal>-bench.<py|sh>`

## Files and roles

- `validate_data.py`: validates curated channel/package metadata used by FermiLink.
- `hpc_profile_anvil.json`: sample HPC profile for Purdue Anvil.
- `fermilink-optimize-worktree.sh`: helper launcher for isolated optimize campaigns using `git worktree` + per-worktree venv. It accepts benchmark/runner selection, branch/worktree naming, optional `--hpc-profile`, and forwards additional `fermilink optimize` flags after `--`.

- `python-pyscf-scf-benchmark.yaml`: Python/PySCF SCF optimize benchmark contract example with composite single+SMP and HF+DFT objectives plus incumbent no-regression guardrails; correctness now uses `mode: field_tolerances` for energy/MO-energy/`s2` checks (no density-matrix correctness gate).
- `python-pyscf-scf-bench.py`: Python benchmark runner matching the PySCF contract, including per-case thread-profile execution, composite summary metrics, per-case `s2` output for correctness checks, and incumbent-normalized wall-time ratio metrics (`mean_wall_ratio_vs_incumbent`, `geomean_wall_ratio_vs_incumbent`) for average speedup scoring across cases.
- `python-pyscf-hf-small-diis-benchmark.yaml`: DIIS-focused RHF/UHF small-molecule benchmark set (4 cases) with `weighted_median_wall_seconds` primary objective and a strict 2% minimum relative speedup gate (`min_relative_improvement: 0.02`), executed through `python-pyscf-scf-bench.py`.
- `python-pyscf-hf-large-diis-benchmark.yaml`: DIIS-focused RHF/UHF larger single-node benchmark set (4 cases) with the same 2% acceptance threshold, executed through `python-pyscf-scf-bench.py`.
- `python-pyscf-dft-small-diis-benchmark.yaml`: DIIS-focused RKS/UKS small-molecule benchmark set (4 cases) with the same `weighted_median_wall_seconds` + 2% acceptance gate, executed through `python-pyscf-scf-bench.py`.
- `python-pyscf-dft-large-diis-benchmark.yaml`: DIIS-focused RKS/UKS larger single-node benchmark set (4 cases) with the same 2% acceptance threshold, executed through `python-pyscf-scf-bench.py`.
- All bundled PySCF templates now default SMP throughput to 4 threads (`FERMILINK_PYSCF_SMP_THREADS=4` and `thread_profiles.smp_node.threads=4`) to reduce out-of-the-box resource pressure.

- `cpp-lammps-tip4p-force-eval-benchmark.yaml`: C++/LAMMPS TIP4P force-evaluation optimize contract example using `correctness.mode: field_tolerances`.
- `cpp-lammps-tip4p-force-eval-bench.sh`: Bash benchmark runner matching the LAMMPS contract, emitting per-case energy/temperature values used by field-tolerance checks.

- `fortran-quantum-espresso-scf-benchmark.yaml`: Fortran/Quantum ESPRESSO SCF optimize contract example using `correctness.mode: field_tolerances`.
- `fortran-quantum-espresso-scf-bench.sh`: Bash benchmark runner matching the QE contract, emitting per-case total energy values in Ry for field-tolerance checks.

## How optimize quick mode uses these

`fermilink optimize prompt.md` can use these case examples as language-specific reference templates when generating `.fermilink-optimize/autogen/benchmark.yaml` and related quick-mode metadata.

Template resolution order:

1. `<target_repo>/scripts/` (project-local templates)
2. FermiLink built-in `scripts/` templates (fallback)

This lets quick mode start from proven benchmark structure and thresholds instead of purely generic defaults.
