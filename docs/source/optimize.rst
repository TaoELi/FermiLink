:orphan:

Optimize Mode (Preview)
========================

.. note::

   Optimize mode is under active development and is not yet publicly listed
   in the documentation navigation.  This page is available only via its
   direct URL.

``fermilink optimize`` is a benchmark-gated scientific code optimization
system.  It iteratively proposes source-level changes to a target scientific
package, benchmarks each candidate, validates correctness, and accepts only
changes that improve measured performance.

Two entry points are provided:

- **Goal mode** -- describe your optimization intent in a short structured
  markdown file and let FermiLink auto-generate benchmarks.
- **Expert mode** -- supply a hand-crafted benchmark YAML contract and runner
  script for full control.

A third subcommand, ``fermilink optimize status``, reports campaign progress.


Goal mode
---------

Goal mode is the recommended starting point.  You write a ``goal.md`` file
(~30 lines) instead of a full benchmark YAML contract (~1000 lines), and
FermiLink analyses the target source code to generate everything else.

.. code-block:: bash

   fermilink optimize goal.md

Goal mode is auto-detected when the input markdown contains at least two of the
following section headings:

- ``# Optimization Goal``
- ``## Package``
- ``## Target``
- ``## Editable Scope``
- ``## Performance Metric``
- ``## Correctness Constraints`` / ``## Correctness``
- ``## Representative Workloads`` / ``## Workloads``

You can also force goal mode with the ``--goal`` flag.


Goal file structure
~~~~~~~~~~~~~~~~~~~

A goal file is structured markdown with the following sections.  Only
``## Package`` and ``## Target`` are strictly required; all other sections
improve the quality of the generated benchmark.

.. code-block:: markdown

   # Optimization Goal

   ## Package
   pyscf

   ## Language
   python

   ## Target
   Optimize DIIS behavior for SCF convergence in PySCF,
   focusing on reduced overhead and faster convergence paths.

   ## Editable Scope
   - pyscf/lib/diis.py
   - pyscf/scf/**

   ## Performance Metric
   Minimize end-to-end SCF convergence wall-clock time.

   ## Correctness Constraints
   - Total SCF energy absolute delta <= 5e-8 Hartree vs baseline
   - All benchmark cases must converge within configured cycle limits

   ## Representative Workloads
   - train-o2: O2 / 6-31g / UHF (spin=2) / DIIS space=12
   - train-h2o: H2O / 6-31g / RHF / DIIS space=12
   - test-h2o: H2O / cc-pVDZ / RHF / DIIS space=12

   ## Build
   ```bash
   pip install -e .
   ```

   ## Notes
   Keep benchmark behavior deterministic across repeated runs.


Goal mode pipeline
~~~~~~~~~~~~~~~~~~

When a goal file is submitted, FermiLink runs a two-phase pipeline:

1. **Source analysis** -- an agent reads the target source code and produces a
   structured JSON analysis of the package, its hot paths, and correctness
   boundaries.
2. **Benchmark generation** -- a second agent writes a ``benchmark.yaml``
   contract and ``benchmark_runner.py`` script, which are validated and placed
   in ``.fermilink-optimize/autogen/``.

After generation the campaign continues as an expert-mode campaign using the
auto-generated benchmark artifacts.


Expert mode
-----------

Expert mode gives full control over the benchmark contract.  You supply a
package identifier, the project source path, and a ``--benchmark`` YAML file.

.. code-block:: bash

   fermilink optimize <package_id> <project_path> --benchmark benchmark.yaml

The benchmark YAML contract defines:

- Benchmark cases (workloads), each with commands, expected outputs, and
  tolerances.
- Correctness validation mode (runner exit-status or field-level tolerances).
- Runtime mode (synchronous or submit-poll for SLURM/PID-based execution).
- Optional train/test split via ``split.train_case_ids`` to prevent
  overfitting.

FermiLink ships reference benchmark templates for Python, C++, and Fortran
packages under ``scripts/``.


Campaign lifecycle
------------------

Both modes share the same worker-controller optimization loop:

1. **Baseline** -- the benchmark suite runs on the unmodified source to
   establish incumbent performance.
2. **Worker turn** -- an AI agent proposes a single candidate source-level
   change inside an isolated git worktree.
3. **Benchmark** -- the candidate is benchmarked (locally via PID or remotely
   via SLURM).
4. **Controller turn** -- a separate agent evaluates correctness and
   performance, then accepts or rejects the candidate.
5. **Iterate** -- repeat from step 2 until the iteration cap, consecutive
   rejection limit, or ``--forever`` mode termination.

State is persisted in ``.fermilink-optimize/`` (campaign state, results TSV,
controller and worker memory files) so campaigns can be resumed with
``--resume``.


Common CLI options
------------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--goal``
     - Force goal-mode detection for the input markdown.
   * - ``--benchmark <path>``
     - Benchmark YAML contract path (expert mode).
   * - ``--program <path>``
     - Custom optimize-program markdown path.
   * - ``--skills-source <mode>``
     - How to prepare skills: ``auto``, ``existing``, ``channel``, or
       ``compile``.
   * - ``--baseline-only``
     - Run only the baseline benchmark and exit.
   * - ``--plan-only``
     - Initialize state and validate inputs without running the loop.
   * - ``--resume``
     - Resume an existing campaign from local state.
   * - ``--max-iterations <n>``
     - Override the campaign iteration cap.
   * - ``--stop-on-consecutive-rejections <n>``
     - Override the rejection-based early stop threshold.
   * - ``--timeout-seconds <n>``
     - Override the per-run benchmark timeout.
   * - ``--hpc-profile <json>``
     - HPC profile for SLURM job submission.
   * - ``--forever``
     - Run indefinitely until interrupted.
   * - ``--allow-dirty``
     - Allow startup from a dirty git working tree.
   * - ``--sandbox <mode>``
     - Provider sandbox override for optimize agent turns.

Check campaign progress at any time:

.. code-block:: bash

   fermilink optimize status


Tutorial: optimizing PySCF with the sample goal file
-----------------------------------------------------

This walkthrough uses the sample goal file shipped with FermiLink
(``scripts/python-pyscf-diis-scf-goal.md``) to run a goal-mode campaign
against a local PySCF clone.  The controller operates inside a **git
worktree** so the original clone stays untouched.

Prerequisites
~~~~~~~~~~~~~

- FermiLink is installed and an agent provider CLI is authenticated.
- A ``skills/`` folder for PySCF is available either in your main clone or a
  FermiLink-managed package location.
- Git and Python >= 3.11 are available on ``PATH``.


Step 1. Clone or update PySCF on ``master``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   git clone git@github.com:skilled-scipkg/pyscf.git ~/pyscf
   cd ~/pyscf
   git fetch origin
   git checkout master
   git pull --ff-only origin master

If you already have a local clone, skip this step.


Step 2. Create a controller worktree
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a dedicated worktree from the original ``master`` branch so the
optimization campaign does not modify the main checkout:

.. code-block:: bash

   cd ~/pyscf
   git worktree add -b fermilink-optimize/pyscf-diis ../pyscf-optimize-diis master

This places the worktree at ``~/pyscf-optimize-diis`` on branch
``fermilink-optimize/pyscf-diis``.  Change into it for the remaining steps:

.. code-block:: bash

   cd ../pyscf-optimize-diis


Step 3. Build in the worktree
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   python -m venv .venv
   ./.venv/bin/python -m pip install -U pip
   ./.venv/bin/python -m pip install -e .
   ./.venv/bin/python -m pip install PyYAML
   ./.venv/bin/python -m pip install -e /path/to/fermilink


Step 4. Set the goal file path
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Point an environment variable at the sample goal file shipped with
FermiLink.  This avoids copying the file into the worktree (which would
create an untracked file in the git tree):

.. code-block:: bash

   export GOAL=/path/to/fermilink/scripts/python-pyscf-diis-scf-goal.md
   test -f "$GOAL"

Replace ``/path/to/fermilink`` with the actual location of your FermiLink
source checkout.  Review the file and, if needed, create a modified copy
**outside** the worktree and point ``GOAL`` at that copy instead.


Step 5. Launch the campaign
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run optimize in goal mode from inside the worktree, referencing the goal
file by its absolute path:

.. code-block:: bash

   cd ~/pyscf-optimize-diis
   ./.venv/bin/fermilink optimize "$GOAL" \
     --max-iterations 30 \
     --stop-on-consecutive-rejections 8 \
     --timeout-seconds 900

FermiLink will:

1. Parse the goal file and analyse the PySCF source.
2. Auto-generate ``benchmark.yaml`` and ``benchmark_runner.py`` under
   ``.fermilink-optimize/autogen/``.
3. Run the baseline benchmark on the unmodified source.
4. Enter the worker-controller optimization loop.

The worker operates in its own nested git worktree inside the campaign
directory (``fermilink-optimize-worktrees/``); the controller worktree you
created in Step 2 is the authoritative checkout.


Step 6. Monitor progress
~~~~~~~~~~~~~~~~~~~~~~~~~

In a separate terminal, ``cd`` into the worktree and run:

.. code-block:: bash

   cd ~/pyscf-optimize-diis
   ./.venv/bin/fermilink optimize status

This prints the current iteration count, accepted/rejected totals, incumbent
commit, and the most recent results from ``results.tsv``.


Step 7. Resume if interrupted
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If the campaign is interrupted (e.g. by ``Ctrl-C`` or a timeout), resume
from the last checkpoint:

.. code-block:: bash

   cd ~/pyscf-optimize-diis
   ./.venv/bin/fermilink optimize "$GOAL" --resume


Step 8. Review accepted commits and clean up
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   cd ~/pyscf-optimize-diis
   git log --oneline

   cd ~/pyscf
   git worktree remove ../pyscf-optimize-diis
   # git branch -D fermilink-optimize/pyscf-diis  # if you no longer need the branch
