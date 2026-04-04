# Optimization Goal

## Package
pyscf

## Language
python

## Target
Optimize the DIIS (Direct Inversion in the Iterative Subspace) extrapolation
implementation in `pyscf/lib/diis.py` and surrounding SCF driver code in
`pyscf/scf/` for faster Hartree-Fock and DFT self-consistent field
convergence on small-to-medium molecules.

The DIIS algorithm is the primary convergence accelerator in PySCF's SCF loop.
Optimization opportunities include: linear-algebra operations in the DIIS
update step (Fock matrix extrapolation, error vector management), memory
layout and allocation patterns for the DIIS history, numerical
short-circuits when the error vector is small, and any overhead in the
SCF iteration driver that calls into DIIS.

## Editable Scope
- pyscf/scf/**
- pyscf/lib/diis.py

## Performance Metric
End-to-end wall-clock time for full SCF convergence (minimize).

The primary metric should be the weighted median total time across all
benchmark cases, capturing both setup and kernel execution phases so that
moving work between phases cannot game the metric.

## Correctness Constraints
- Total SCF energy must match reference within 5e-8 Hartree (absolute)
- Molecular orbital energies must match reference within 2e-5 RMS
- Spin expectation value S^2 must match within 1e-3 (for open-shell cases)
- All cases must converge within the allowed cycle limit
- No relaxation of convergence tolerances is permitted
- No special-casing of benchmark input molecules by name

## Representative Workloads
- H2O / cc-pVDZ / RHF: small closed-shell baseline
- NH3 / cc-pVDZ / RHF: small closed-shell with pyramidal geometry
- O2 / cc-pVDZ / UHF (spin=2): small open-shell triplet
- NO / cc-pVDZ / UHF (spin=1): small open-shell doublet, harder convergence

## Notes
These cases cover both restricted (RHF) and unrestricted (UHF) Hartree-Fock.
The DIIS subspace size should be 12 vectors. Convergence tolerance is 1e-10
for energy and 1e-6 for gradient.  Initial guess should use MINAO.

For larger-scale optimization campaigns, consider adding cc-pVTZ basis set
cases or DFT (RKS/UKS with B3LYP) workloads in a separate goal file.
