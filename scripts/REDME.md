# scripts/ directory scope

This directory stores maintenance scripts and case-specific optimize benchmark examples.

## Naming convention

Benchmark examples follow `language-pkg-goal` naming:

- `<language>-<package>-<goal>-benchmark.yaml`
- `<language>-<package>-<goal>-bench.<py|sh>`

## Files and roles

- `validate_data.py`: validates curated channel/package metadata used by FermiLink.
- `hpc_profile_anvil.json`: sample HPC profile for Purdue Anvil.

- `python-pyscf-scf-benchmark.yaml`: Python/PySCF SCF optimize benchmark contract example with composite single+SMP and HF+DFT objectives plus incumbent no-regression guardrails.
- `python-pyscf-scf-bench.py`: Python benchmark runner matching the PySCF contract, including per-case thread-profile execution and composite summary metrics.

- `cpp-lammps-tip4p-force-eval-benchmark.yaml`: C++/LAMMPS TIP4P force-evaluation optimize contract example.
- `cpp-lammps-tip4p-force-eval-bench.sh`: Bash benchmark runner matching the LAMMPS contract.

- `fortran-quantum-espresso-scf-benchmark.yaml`: Fortran/Quantum ESPRESSO SCF optimize contract example.
- `fortran-quantum-espresso-scf-bench.sh`: Bash benchmark runner matching the QE contract.

## How optimize quick mode uses these

`fermilink optimize prompt.md` can use these case examples as language-specific reference templates when generating `.fermilink-optimize/autogen/benchmark.yaml` and related quick-mode metadata.

Template resolution order:

1. `<target_repo>/scripts/` (project-local templates)
2. FermiLink built-in `scripts/` templates (fallback)

This lets quick mode start from proven benchmark structure and thresholds instead of purely generic defaults.
