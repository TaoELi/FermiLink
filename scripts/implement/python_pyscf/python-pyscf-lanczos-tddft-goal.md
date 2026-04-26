# Implementation Goal

## Package
pyscf

## Language
python

## Target
Implement a new Lanczos-chain TDDFT response algorithm in PySCF, inspired by
Rocca, Gebauer, Saad, and Baroni, "Turbo charging time-dependent
density-functional theory with Lanczos chains", arXiv:0801.1393.

The implementation should add a usable alternative to the existing
Davidson/Casida-style TDDFT root solver for closed-shell molecular calculations.
The first production target is a finite-basis PySCF implementation that reuses
existing RHF/RKS TD response matrix-vector products where possible, avoids
materializing the full TDDFT matrix, and evaluates frequency-dependent response
functions through a non-Hermitian Lanczos or bi-Lanczos projection of the TDDFT
Liouvillian.

Target implementation direction:
- represent the linearized TDDFT problem as repeated applications of a
  Liouvillian/super-operator to trial response vectors
- build right/left Lanczos chains or an equivalent stable non-Hermitian Lanczos
  recurrence suitable for molecular finite-basis TDDFT
- compute dynamical polarizability or an absorption spectrum from the projected
  resolvent/continued-fraction representation
- reuse existing PySCF TDDFT response builders instead of duplicating exchange
  and XC-kernel logic
- keep the algorithm deterministic, restart-free, and independent of
  molecule-specific shortcuts

Do not replace PySCF's existing TDDFT/TDA APIs or change their default
behavior. This is a new algorithm/API that can be selected explicitly.

## Editable Scope
- pyscf/tdscf/lanczos.py
- pyscf/tdscf/__init__.py
- pyscf/tdscf/rhf.py
- pyscf/tdscf/rks.py
- pyscf/tdscf/_lr_eig.py
- pyscf/tdscf/test/test_lanczos_tddft.py
- examples/tddft/*lanczos*

## Input API
Expose a small public API that feels native to PySCF. A preferred shape is:

```python
from pyscf import gto, dft, tdscf
import numpy as np

mol = gto.M(atom="O 0 0 0; H 0 0 0.958; H 0 0.927 -0.239", basis="sto-3g")
mf = dft.RKS(mol)
mf.xc = "b3lyp"
mf.kernel()

td = tdscf.LanczosTDDFT(mf)
td.nsteps = 300
td.damping = 0.005
freq = np.linspace(0.0, 0.8, 200)
result = td.kernel(freq=freq, polarizations="xyz")

omega = result.freq
alpha = result.polarizability
strength = result.strength
```

Acceptable API refinements:
- `tdscf.lanczos.LanczosTDDFT(mf)` as the implementation class
- `tdscf.LanczosTDDFT(mf)` as the public constructor
- `mf.LanczosTDDFT()` only if it matches established PySCF TDDFT factory style
- an optional `kernel(freq=None, nstates=None)` mode that returns approximate
  peak locations/strengths for comparison with existing TDDFT roots

## Desired Outputs
- A frequency grid and complex dynamical polarizability tensor or vector.
- A nonnegative scalar absorption/strength spectrum for isotropic closed-shell
  cases.
- Optional approximate peak positions and oscillator strengths extracted from
  the spectrum.
- Diagnostic metadata: Lanczos steps used, loss-of-biorthogonality indicator,
  residual/stability notes, and number of TDDFT response matrix-vector calls.

## Representative Workloads
- train-rhf-water-sto3g: RHF water / STO-3G / singlet TDHF response / frequency
  grid 0.0-1.0 Hartree / compare the first few spectral peaks to existing
  `tdscf.TDHF` or `tdscf.TDDFT` roots
- train-rks-water-b3lyp-631g: RKS water / 6-31G / `xc="b3lyp"` / singlet TDDFT
  response / compare low-energy peak positions to existing PySCF TDDFT roots
- train-rks-formaldehyde-pbe-sto3g: RKS formaldehyde / STO-3G / `xc="pbe"` /
  verify stable spectrum and reproducible integrated strength
- test-rhf-ammonia-sto3g: RHF ammonia / STO-3G / held-out molecule for TDHF
  spectrum shape and API checks
- test-rks-ethylene-b3lyp-631g: RKS ethylene / 6-31G / `xc="b3lyp"` / held-out
  molecule for TDDFT spectrum and no-hardcoding checks

## Build
```bash
cd pyscf/lib
mkdir -p build
cd build
cmake ..
cmake --build . -j4
cd ../../../
python -m pip install -e .
```

## Non Goals
- Do not replace the existing Davidson/Casida TDDFT/TDA solvers or change their
  default behavior.
- Do not reduce requested accuracy by loosening SCF or TDDFT convergence
  thresholds in existing code paths.
- Do not hard-code spectra, excitation energies, oscillator strengths, molecule
  names, basis names, or workload identifiers.
- Do not require plane waves, real-space grids, or an unoccupied-orbital-free
  density-matrix implementation in the first PySCF version. Those are allowed as
  future extensions after the finite-basis molecular API works.
- Do not add heavyweight new dependencies outside PySCF's existing dependency
  style.

## Done Criteria
- `tdscf.LanczosTDDFT(mf)` or an equivalent documented constructor works for
  closed-shell RHF and RKS mean-field objects.
- The Lanczos implementation applies the TDDFT response operator through
  matrix-vector products and does not build the full TDDFT matrix in normal
  operation.
- Validation tests pass for at least one RHF and one RKS molecular case.
- Low-energy spectral peaks agree with existing PySCF TDDFT/TDHF roots to within
  the tolerance encoded in the new tests.
- Repeated runs on the same input are deterministic to numerical tolerance.
- The public API has docstrings and one minimal example under `examples/tddft/`.

## Notes
- Background paper checked for this goal: arXiv:0801.1393, "Turbo charging
  time-dependent density-functional theory with Lanczos chains" by Dario Rocca,
  Ralph Gebauer, Yousef Saad, and Stefano Baroni.
- The paper frames dynamical polarizability as an off-diagonal matrix element of
  the resolvent of the TDDFT Liouvillian and evaluates that resolvent with a
  non-symmetric Lanczos recursion. This goal asks for the same algorithmic
  direction adapted to PySCF's finite Gaussian-basis molecular TDDFT machinery.
- For the first implementation, favor a robust and reviewable closed-shell
  RHF/RKS path over broad method coverage. UKS, spin-flip, periodic systems,
  and fully unoccupied-orbital-free density-matrix response can be separate
  follow-up tasks.
- If numerical stability requires reorthogonalization or look-ahead handling in
  the bi-Lanczos recursion, implement it explicitly and expose diagnostics rather
  than silently returning unstable spectra.
