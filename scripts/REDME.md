# scripts/ directory scope

This directory stores maintenance scripts and case-specific optimize benchmark examples.

## Naming convention

Benchmark examples follow `language-pkg-goal` naming:

- `<language>-<package>-<goal>-benchmark.yaml`
- `<language>-<package>-<goal>-bench.<py|sh>`

## Files and roles

- `validate_data.py`: validates curated channel/package metadata used by FermiLink.
- `hpc_profile_anvil.json`: sample HPC profile for Purdue Anvil.

- `python-pyscf-scf-benchmark.yaml`: Python/PySCF SCF optimize benchmark contract example with composite single+SMP and HF+DFT objectives plus incumbent no-regression guardrails; correctness now uses `mode: field_tolerances` for energy/MO-energy/`s2` checks (no density-matrix correctness gate).
- `python-pyscf-scf-bench.py`: Python benchmark runner matching the PySCF contract, including per-case thread-profile execution, composite summary metrics, per-case `s2` output for correctness checks, and incumbent-normalized wall-time ratio metrics (`mean_wall_ratio_vs_incumbent`, `geomean_wall_ratio_vs_incumbent`) for average speedup scoring across cases.

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
