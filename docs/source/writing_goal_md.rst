Writing a ``goal.md``
=====================

The ``goal.md`` file is **FermiLink**'s primary user interface. It is a plain
markdown file that tells the AI agent *what* you want to accomplish. **FermiLink**
reads it and figures out how to get there.

This page explains how to write effective ``goal.md`` files for both
**simulations** and **code optimization**.


Goal files for simulations
--------------------------

When you run ``fermilink loop goal.md``, the agent reads your goal and
autonomously plans and executes the simulation.

Simulation goals are free-form markdown. The agent interprets your intent from the structure and content.

.. note::

   ``fermilink research goal.md`` also accepts a free-form markdown goal, but it
   treats it as an **open-ended research idea** rather than a fixed simulation
   spec: it first drafts a research *charter* (question, candidate approaches,
   risks, success/kill criteria), then runs an explore → reflect → re-plan loop
   that may change approach between phases, and finally writes a
   submission-ready paper. Use ``loop``/``reproduce`` when you already know
   exactly what to compute; use ``research`` when the approach itself is part of
   what you want to discover.

Recommended structure
~~~~~~~~~~~~~~~~~~~~~

A good simulation goal answers three questions:

1. **What to compute**: the physical system to simulate and the package to use (if you know).
2. **How to judge success**: convergence criteria or accuracy targets.
3. **What to produce**: output figures or a LaTeX summary report.

.. code-block:: markdown

   # Goal: Photonic crystal band structure

   Simulate the band structure of a 2D triangular lattice of dielectric
   rods (r=0.2a, epsilon=12) in air using MEEP/MPB.

   ## What to compute
   - TM and TE band diagrams along Gamma->M->K->Gamma
   - Extract the band gap ratio (Delta_omega/omega_mid) for the first gap

   ## Success criteria
   - At least 8 bands converged with resolution >= 32
   - Band gap ratio within 5% of published values

   ## Deliverables
   - Save final band diagram as `bands.png`

How the agent uses each part:

- **Title** (``# Goal: ...``): gives the agent a concise summary of the task.
- **Description paragraph**: provides physical context. Be specific about the
  simulation system and parameters.
- **What to compute**: the agent translates these into concrete simulation steps
  and output extraction commands.
- **Success criteria**: the agent checks these after each iteration and decides
  whether to continue or report completion.
- **Deliverables**: the agent knows what files to produce and verify at the end.

You can of course write ``goal.md`` in your own style. The agent is quite flexible. 

More simulation examples
~~~~~~~~~~~~~~~~~~~~~~~~

**DFT surface relaxation (Quantum ESPRESSO)**

.. code-block:: markdown

   # Goal: Si(100) surface relaxation

   Relax the Si(100) 2x1 reconstructed surface using Quantum ESPRESSO
   with PBE functional.

   ## What to compute
   - Fully relaxed surface geometry (forces < 0.01 eV/Ang)
   - Surface energy relative to bulk Si
   - Layer-resolved atomic displacements

   ## Setup
   - 6-layer slab with 15 Ang vacuum
   - Bottom 2 layers fixed
   - Kinetic energy cutoff: 40 Ry, charge density cutoff: 320 Ry
   - k-point mesh: 4x4x1 Monkhorst-Pack

   ## Success criteria
   - All forces below 0.01 eV/Ang
   - Total energy converged to 1e-6 Ry between last two ionic steps

   ## Deliverables
   - Save relaxed structure as `si100_relaxed.xyz`


**Molecular dynamics (LAMMPS)**

.. code-block:: markdown

   # Goal: Water viscosity via Green-Kubo

   Compute the shear viscosity of TIP4P water at 300 K and 1 atm
   using the Green-Kubo method in LAMMPS.

   ## What to compute
   - Equilibrate NPT at 300 K / 1 atm for 500 ps
   - Production NVE run for 2 ns, sampling pressure tensor every 10 fs
   - Compute viscosity from the autocorrelation of off-diagonal
     pressure tensor components

   ## Success criteria
   - Viscosity within 20% of experimental value (~0.89 mPa*s)
   - Autocorrelation function decays to near zero

   ## Deliverables
   - Save viscosity vs correlation time plot as `viscosity.png`


Tips for effective simulation goals
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Be specific about physical parameters.** Instead of "simulate a water box,"
write "simulate 1000 TIP4P water molecules at 300 K and 1 atm in a cubic box."

**Name the software when you know it.** "Using MEEP/MPB" or "with Quantum
ESPRESSO" helps the agent pick the right package and avoid guessing.

**Set quantitative success criteria.** "Converged results" is vague;
"forces below 0.01 eV/Ang" is actionable.

**Specify output files.** Telling the agent to "save the band diagram as
``bands.png``" gives it a concrete deliverable to verify.

**Include setup constraints.** If you need a specific k-point mesh, basis set,
or pseudopotential, say so. The agent respects explicit constraints.


.. _goal-files-for-code-optimization:

Goal files for code optimization
--------------------------------

When you run ``fermilink optimize goal.md``, **FermiLink** enters a different
mode: it iteratively modifies source code to improve performance while
preserving correctness.

Different from regular free-style simulation goals, optimization goals use a **structured format** with
fixed section headings. This is because optimizing code is a more delicate task that requires precise requirements to prevent the agent from breaking the science. 

Ask **FermiLink** to write an optimization goal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For users new to optimization, feel free to use the skills available in **FermiLink** to write a well-structured optimization goal. Just run:

.. code-block:: bash

   mkdir myproject
   cd myproject/
   fermilink init
   # start a coding agent
   codex        # or claude, gemini, opencode

Then ask the agent:

.. code-block:: text

   I want to optimize the performance of <routine> in <pkg-id> at <location>. Use optimize-goal-authoring skill to write a well-structured optimization goal in markdown format.


Optimization goal example
~~~~~~~~~~~~~~~~~~~~~~~~~~

Here is a complete optimization goal for PySCF's DIIS SCF solver:

.. code-block:: markdown

   # Optimization Goal

   ## Package
   pyscf

   ## Language
   python

   ## Target
   Optimize DIIS behavior for SCF convergence in PySCF, focusing on
   reduced overhead and faster convergence paths in:
   - `pyscf/lib/diis.py` -- core DIIS subspace storage and extrapolation
   - `pyscf/scf/diis.py` -- SCF-specific CDIIS/ADIIS/EDIIS logic
   - `pyscf/scf/hf.py`, `pyscf/scf/uhf.py` -- RHF/UHF call sites

   ## Editable Scope
   - pyscf/lib/diis.py
   - pyscf/scf/diis.py
   - pyscf/scf/hf.py
   - pyscf/scf/uhf.py
   - pyscf/scf/rohf.py

   ## Performance Metric
   Minimize end-to-end SCF kernel time from `mf.kernel()`.

   ## Correctness Constraints
   - Total SCF energy absolute delta <= 5e-8 Hartree vs baseline
   - Molecular orbital energies RMS delta <= 2e-5 Hartree vs baseline
   - All benchmark cases must converge within baseline cycle limits
   - Do not loosen conv_tol, diis_space, or max_cycle settings

   ## Representative Workloads
   - train-rhf-benzene: benzene / RHF / 6-31g** / diis_space=12
   - train-uhf-allyl: allyl radical / UHF / spin=1 / def2-TZVP
   - test-rhf-c3h7oh: C3H7OH / RHF / 6-31g**
   - test-uhf-o2-dimer: O2+O2 / UHF / spin=4 / def2-TZVP

   ## Build
   ```bash
   cd pyscf/lib && mkdir -p build && cd build
   cmake .. && cmake --build . -j4
   cd ../../../ && pip install -e .
   ```

   ## Notes
   Keep benchmark behavior deterministic with pinned thread counts.


Required sections
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Section
     - What to write
   * - ``# Optimization Goal``
     - Title line. Optional but recommended.
   * - ``## Package``
     - The scientific package identifier (e.g., ``pyscf``, ``lammps``).
   * - ``## Language``
     - ``python``, ``c``, ``cpp``, or ``fortran``. Helps the analysis agent.
   * - ``## Target``
     - Plain-language description of what to optimize and where the hot paths are.
       Be specific about files and functions.
   * - ``## Editable Scope``
     - List of files/directories the agent is allowed to modify. Glob patterns
       like ``pyscf/scf/**`` are supported.
   * - ``## Performance Metric``
     - What to minimize or maximize (e.g., "wall-clock time for ``mf.kernel()``").
   * - ``## Correctness Constraints``
     - Numerical tolerances and behavioral invariants that must hold. These
       are the guardrails that prevent the optimizer from breaking the science.
   * - ``## Representative Workloads``
     - Train and test cases with specific parameters. Use ``train-`` and
       ``test-`` prefixes. The optimizer only sees train cases; test cases are
       used for final validation.
   * - ``## Build``
     - Shell commands to rebuild the package after source changes. **Ensure the commands can be run properly in your machine.**
   * - ``## Notes``
     - Hints for the benchmark generator (determinism, thread pinning, etc.).


How the optimization pipeline uses your goal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When you submit an optimization goal, **FermiLink** runs a two-phase pipeline:

1. **Source analysis**: an agent reads the target source code and produces a
   structured analysis of the package and its hot paths.

2. **Benchmark generation**: a second agent writes a deterministic benchmark YAML contract
   and runner script, informed by your workloads and correctness constraints.
   These are placed in ``.fermilink-optimize/autogen/``.

The generated deterministic benchmark then is used in the optimization loop.

The ``## Representative Workloads`` section is particularly important. Use
``train-`` prefixed cases for the workloads the optimizer sees during iteration,
and ``test-`` prefixed cases for held-out validation. Choose test cases that
are in the same computational regime as train cases but use different molecules
or parameters, so improvements generalize.


Common mistakes
~~~~~~~~~~~~~~~~~~~

**Vague goals.** "Make my code faster" gives the agent nothing to work with.
Specify which functions to optimize and what metric to improve under which constraints.

**Overly tight tolerances.** Setting energy tolerance to
``1e-15`` will cause every candidate to be rejected. Use physically meaningful
tolerances (e.g., ``5e-8 Hartree`` for SCF energies).

**Train/test overlap.** If your test workloads use the same
molecules as training, the optimizer may overfit to those specific systems.
Use different molecules in the same size regime if you want more generalized code.

**No build instructions .** If the package needs compilation
after source changes, omit the ``## Build`` section and the optimizer
cannot test its modifications. Always include build commands for compiled
packages.

**Ambiguous scope.** For optimization, explicitly listing editable files
prevents the agent from making changes in unrelated parts of the codebase.
