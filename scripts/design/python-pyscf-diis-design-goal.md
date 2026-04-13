# Design Goal

## Package
pyscf

## Language
python

## Scientific Problem
Search for scientifically meaningful algorithm-hypothesis candidates that can
improve the DIIS family used in PySCF SCF, especially in regimes where the
current CDIIS/ADIIS/EDIIS implementation becomes expensive because the AO space
is large, the DIIS subspace/history is nontrivial, symmetry constraints are
active, or many SCF iterations are required.

The goal is not to produce a patch. The goal is to extract the current baseline
algorithm, identify the dominant cost centers inside the DIIS workflow, and
search for mechanistically distinct candidate algorithms that could provide a
clear advantage in a stated limit. Deliverables should be algorithm sketches /
IR plus human-readable pseudocode and claimed advantage regimes.

## Target Kernel
Target the DIIS-related portion of the molecular SCF workflow, with primary
focus on:

- generic DIIS history storage, error-vector management, and extrapolation in
  `pyscf/lib/diis.py`
- SCF-specific CDIIS/ADIIS/EDIIS logic, error-vector construction, damping, and
  rollback behavior in `pyscf/scf/diis.py`
- RHF/UHF/ROHF call paths that invoke DIIS during iterative SCF updates in
  `pyscf/scf/hf.py`, `pyscf/scf/uhf.py`, and `pyscf/scf/rohf.py`

Important baseline details to preserve during analysis:

- CDIIS currently builds error vectors from `SDF - FDS`, with an orthonormalized
  symmetry-aware variant available through `Corth`
- ADIIS and EDIIS maintain history buffers and solve a small optimization
  problem over prior density/Fock states
- the generic DIIS layer stores vectors either in-core or via an HDF5-backed
  temporary file depending on size/history behavior
- restart/rollback semantics and symmetry-projected error-vector handling are
  part of the intended scientific behavior

## Target Regime
Favor algorithm hypotheses that are plausibly better in one or more of these
limits:

- large AO basis and larger Fock/density/error-vector dimensions
- many requested SCF iterations before convergence, so DIIS bookkeeping cost is
  repeatedly paid
- larger DIIS spaces or adaptive DIIS histories where history management and
  Gram/Hessian-like updates become noticeable
- open-shell or symmetry-constrained problems where error-vector construction
  and masking are nontrivial
- workloads where the current dense history treatment, dense error vectors, or
  repeated small optimization solves create overhead that is negligible for tiny
  systems but material for larger or harder cases

Prefer candidates with explicit crossover claims such as:
- worse for tiny molecules but better once AO dimension or DIIS space is large
- better when repeated history updates dominate
- better when symmetry structure or block structure can be exploited

## Current Baseline
The current baseline appears to be a family of dense-history Pulay-style
methods:

- `pyscf/lib/diis.py` implements generic DIIS vector/error storage, rotation
  over a bounded history, and extrapolation using stored vectors and error
  vectors
- `pyscf/scf/diis.py` implements CDIIS, ADIIS, and EDIIS on top of that layer
- CDIIS forms dense error vectors from commutator-like quantities and performs
  dense extrapolation over history states
- ADIIS and EDIIS optimize mixing coefficients over stored density/Fock history
  using dense objective construction plus `scipy.optimize.minimize`
- the baseline is scientifically mature and likely strong for conventional
  small-to-moderate systems, so candidate algorithms should target limits where
  fundamentally different structure could matter

The design analysis should reconstruct this baseline as explicit pseudocode and
comment on which parts are likely most time-consuming in the target regimes.

## Editable Scope
- pyscf/lib/diis.py
- pyscf/scf/diis.py
- pyscf/scf/hf.py
- pyscf/scf/uhf.py
- pyscf/scf/rohf.py

## Required Invariants
- Preserve the converged SCF fixed point to the extent allowed by the stated
  accuracy/error budget
- Preserve CDIIS, ADIIS, and EDIIS availability as scientific modes unless a
  candidate explicitly proposes a principled replacement family with clear
  compatibility discussion
- Preserve restart/rollback semantics or explicitly describe how an alternative
  would maintain equivalent robustness
- Preserve symmetry-aware behavior in the orthonormalized error-vector path
- Preserve RHF, UHF, and ROHF applicability
- Preserve deterministic behavior under fixed thread counts and deterministic
  linear algebra
- Do not rely on loosening `conv_tol`, `conv_tol_grad`, `diis_space`,
  `diis_start_cycle`, `diis_space_rollback`, `max_cycle`, damping, level shift,
  initial guess, or symmetry settings to claim algorithmic improvement

## Accuracy/Error Budget
- Any candidate hypothesis that introduces approximation, compression,
  randomized sketching, reduced precision, or screening must state an explicit
  error-control mechanism
- Candidate claims should preserve total SCF energy to within the same order of
  accuracy normally expected from the current DIIS family for the same SCF
  settings
- Candidate claims should preserve qualitative convergence behavior and not
  silently change the solved problem

## Representative Workloads
- RHF benzene / 6-31g** using geometry patterns from `examples/2-benchmark/bz.py`
- RHF glycine / 6-31g* using `examples/scf/glycine.xyz`
- UHF allyl radical / def2-TZVP using geometry patterns from
  `examples/mp/12-dfump2-natorbs.py`

These workloads are intended only to define the scientific regime and the
desired crossover region for candidate algorithms. Phase 1 design mode does not
need to generate or run authoritative benchmarks.

## Prior-Art Hints
- Pulay DIIS / CDIIS
- C2DIIS
- EDIIS
- ADIIS
- GEDIIS
- block-structured or spin-separated extrapolation ideas
- low-rank or compressed history representations
- incremental Gram / overlap updates for history-based extrapolation
- symmetry-aware compressed residual representations
- hybrid strategies that switch algorithm families based on error geometry or
  conditioning, if the switching rule is scientifically principled

## Excluded Directions
- Pure Python micro-optimizations with no meaningful algorithmic change
- Claims that depend mainly on turning off CDIIS/ADIIS/EDIIS modes, symmetry
  handling, rollback, or robustness features
- Case-specific heuristics keyed to molecule identity, basis name, spin, or
  train/test labeling
- Hardware-only proposals whose main idea is GPU offload rather than an
  algorithmic change in the DIIS procedure itself
- Trivial rebranding of already standard Pulay-family variants without a clear
  new mechanism or regime-specific advantage

## Local Evidence
- README.md
- pyscf/lib/diis.py
- pyscf/scf/diis.py
- pyscf/scf/hf.py
- pyscf/scf/uhf.py
- pyscf/scf/rohf.py
- pyscf/lib/test/test_diis.py
- pyscf/scf/test/test_diis.py
- examples/2-benchmark/bz.py
- examples/scf/glycine.xyz
- examples/mp/12-dfump2-natorbs.py
- examples/mcscf/23-local_spin.py

## Deliverable Preferences
- Reconstruct the current DIIS baseline as a typed algorithm sketch plus
  rendered pseudocode
- In the rendered baseline pseudocode, add comments identifying what is likely
  most time-consuming in the target regimes
- Propose multiple candidate algorithm hypotheses, but shortlist only those
  that are mechanistically distinct from the current dense-history Pulay-family
  baseline
- For each shortlisted candidate, provide:
  - rendered pseudocode
  - dominant mechanism
  - expected advantage regime
  - likely tradeoffs or failure modes
  - complexity or overhead discussion
  - novelty-risk label rather than a hard novelty claim
- Prefer candidate mechanisms such as:
  - low-rank / compressed DIIS history
  - block-structured or symmetry-structured error-vector representations
  - incremental updates that avoid repeated dense history recomputation
  - adaptive or hierarchical history selection if scientifically principled
  - solver reformulations that change the coefficient-determination step in a
    meaningful, controlled way

## Notes
- This goal is intended for Phase 1 `fermilink design` mode only. The desired
  outcome is a scientifically interesting shortlist of candidate DIIS-family
  algorithm hypotheses, not source-code modifications.
- The local PySCF checkout that should be analyzed is expected to be passed as
  the project root when invoking design mode.
- Be conservative about novelty. If a proposal resembles a known Pulay-family
  variant, compressed-mixing method, or existing SCF extrapolation strategy,
  label it accordingly instead of overstating novelty.
