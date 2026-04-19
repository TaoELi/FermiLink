Quickstart
==========

Get **FermiLink** running in 5 minutes. By the end of this page you will have launched your first
autonomous simulation with **FermiLink**.

.. note::

   Prefer a guided walkthrough? Just run ``fermilink`` with no arguments
   after installing. An interactive setup wizard walks you through everything.

1. Install **FermiLink**
--------------------

.. code-block:: bash

   pip install fermilink

Requirements: Python >= 3.11, ``git`` on PATH.

2. Set up an AI agent
---------------------

**FermiLink** needs an AI agent provider to do the reasoning. Pick one and
authenticate:

.. code-block:: bash

   # Pick one provider:
   fermilink agent codex       # OpenAI Codex
   fermilink agent claude      # Anthropic Claude
   fermilink agent gemini      # Google Gemini

See :doc:`choosing_agent` for a comparison of providers.

3. Install a scientific package knowledge base
----------------------------------------------------

**FermiLink** ships with 150+ built-in scientific package knowledge bases.
Install one relevant to your work:

.. code-block:: bash

   fermilink install meep           # photonics
   fermilink install lammps         # molecular dynamics
   fermilink install pyscf          # quantum chemistry
   fermilink install openfoam       # CFD

Browse the `full package list <built_in_scientific_packages.html>`_ for all available package knowledge bases.

4. Write your goal.md
---------------------

Create a file called ``goal.md`` in your working directory. This is
**FermiLink**'s primary interface -- a plain markdown file describing what
you want to compute:

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
   - Save final band diagram as `bands.png`

The ``goal.md`` tells **FermiLink** *what* you want. **FermiLink** figures out *how*.

For detailed guidance on writing effective goals, see :doc:`writing_goal_md`.


5. Run
------

.. code-block:: bash

   fermilink loop goal.md

**FermiLink** then reads your goal, picks the right tools, generates
input files, runs simulations, checks results, and iterates until the goal
is met.

Choosing the right command
--------------------------

**FermiLink** offers several commands depending on your workflow:

.. list-table::
   :header-rows: 1
   :widths: 18 42 20 20

   * - Command
     - Best for
     - Duration
     - Environment
   * - ``exec``
     - Quick one-off simulations
     - Minutes
     - Laptop / workstation
   * - ``chat``
     - Interactive conversation with the agent
     - Hours
     - Laptop / workstation
   * - ``loop``
     - Iterative jobs with PID/SLURM monitoring
     - Hours to days
     - Workstation / HPC
   * - ``reproduce``
     - Multi-task paper-scale reproduction
     - Days to weeks
     - HPC clusters
   * - ``research``
     - Multi-task paper-scale research
     - Days to weeks
     - HPC clusters
   * - ``optimize``
     - Code performance tuning *(beta)*
     - Hours
     - Any

If you are not sure how to use **FermiLink** for your specific needs, simply ask a coding agent:

.. code-block:: bash

   cp myproject/
   fermilink init
   codex

Then ask your coding agent what you want to do. `fermilink init` will provide 
all context of **FermiLink** for agent reasoning.

Next steps
----------

- :doc:`tutorial_laptop` -- end-to-end laptop walkthrough with screenshots
- :doc:`tutorial_hpc` -- running on HPC clusters with SLURM
- :doc:`writing_goal_md` -- how to write effective ``goal.md`` files
- :doc:`optimize` -- autonomous code optimization
