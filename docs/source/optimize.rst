Code Optimization
==================

``fermilink optimize`` is a benchmark-gated scientific code optimization
system.  It iteratively proposes source-level changes to a target scientific
package, benchmarks each candidate, validates correctness, and accepts only
changes that improve measured performance.

You describe your optimization intent in a short structured markdown file
(``goal.md``), and **FermiLink** analyses the target source code to
auto-generate benchmarks and drive the campaign:

.. code-block:: bash

   fermilink optimize goal.md


Recommended launchers (``bin/``)
--------------------------------

For most repositories, the shipped launcher scripts under ``bin/`` are the
easiest way to start a campaign.  They are opinionated wrappers around
``fermilink optimize <goal.md>`` that:

- create (or reuse) a sibling **git worktree** next to the source repo so the
  original checkout stays untouched;
- default the worktree branch to ``fermilink-optimize/<repo>-<task>`` derived
  from the goal file name;
- excludes ``.fermilink-optimize/`` and ``.fermilink-home/`` from git via
  ``.git/info/exclude``;
- forward ``--hpc-profile``, ``--worker-provider``, ``--worker-model`` to the
  underlying ``fermilink optimize`` invocation.

Two launchers are provided, one per ecosystem:

- ``bin/fermilink-optimize-python`` -- for Python packages.  Additionally
  creates a per-branch venv under ``<repo-parent>/venvs/`` and ``pip install
  fermilink`` into it before launching.
- ``bin/fermilink-optimize-cpp`` -- for C / C++ / Fortran packages.  No venv
  is created; use your repo's existing build toolchain.

Run with ``--help`` for the full option list.  Anything after ``--`` is
forwarded verbatim to ``fermilink optimize``.


Example: Python (PySCF)
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   ./bin/fermilink-optimize-python \
     --project-root /data/pyscf \
     --goal /path/to/python-pyscf-diis-scf-goal.md \
     --branch fermilink-optimize/pyscf-diis \
     -- --max-iterations 40 --worker-max-iterations 8 --resume


Example: C++ (LAMMPS)
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   ./bin/fermilink-optimize-cpp \
     --project-root /data/lammps \
     --goal /path/to/cpp-lammps-tip4p-water-nve-comm-goal.md \
     --branch fermilink-optimize/lammps-tip4p-comm \
     -- --resume --timeout-seconds 6000


Common launcher options
~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--goal PATH``
     - Goal markdown file (required).
   * - ``--project-root PATH``
     - Clean git repo to optimize.  Defaults to the current working repo.
   * - ``--branch NAME``
     - Worktree branch name.  Default ``fermilink-optimize/<repo>-<task>``.
   * - ``--base-ref REF``
     - Base ref used when creating a new branch.  Default ``origin/HEAD``.
   * - ``--worktree-root PATH`` / ``--worktree-name NAME``
     - Override where the sibling worktree is created.
   * - ``--hpc-profile PATH``
     - HPC profile forwarded to ``fermilink optimize``.
   * - ``--worker-provider NAME`` / ``--worker-model MODEL``
     - Override the worker-agent provider or model.
   * - ``--isolate-fermilink-home`` / ``--fermilink-home PATH``
     - Run with a campaign-local ``FERMILINK_HOME`` so settings do not bleed
       between campaigns.
   * - ``--allow-dirty-base``
     - Allow uncommitted changes in ``--project-root``.
   * - ``--dry-run``
     - Print the resolved ``fermilink optimize`` command and exit.

Python-launcher-only options:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Flag
     - Description
   * - ``--python-bin BIN``
     - Python executable used to create the venv (default ``python3``).
   * - ``--venv-root PATH`` / ``--venv-name NAME`` / ``--venv-path PATH``
     - Override where the per-branch venv is created.


Goal file structure
-------------------

A goal file is structured markdown.  Only ``## Package`` and ``## Target`` are
strictly required; the remaining sections improve the quality of the generated
benchmark.  See :doc:`writing_goal_md` for the full authoring guide.

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


Goal-mode pipeline
------------------

When a goal file is submitted, **FermiLink** runs a two-phase generation
pipeline before starting the optimization loop:

1. **Source analysis** -- an agent reads the target source code and produces a
   structured JSON analysis of the package, its hot paths, and correctness
   boundaries.
2. **Benchmark generation** -- a second agent writes a ``benchmark.yaml``
   contract and ``benchmark_runner.py`` script, which are validated and
   placed in ``.fermilink-optimize/autogen/``.

After generation the campaign proceeds against the auto-generated benchmark
artifacts.


Campaign lifecycle
------------------

Once benchmarks are ready, the worker--controller loop runs:

1. **Baseline** -- the benchmark suite runs on the unmodified source to
   establish incumbent performance.
2. **Worker turn** -- an AI agent proposes a single candidate source-level
   change inside an isolated git worktree.
3. **Benchmark** -- the candidate is benchmarked (locally via PID or remotely
   via SLURM).
4. **Controller turn** -- a separate agent evaluates correctness and
   performance, then accepts or rejects the candidate.
5. **Iterate** -- repeat from step 2 until the iteration cap, the consecutive
   rejection limit, or ``--forever`` mode termination.

State is persisted under ``.fermilink-optimize/`` (campaign state, results
TSV, controller and worker memory files) so campaigns can be resumed with
``--resume``.


``fermilink optimize`` options
------------------------------

Options commonly passed after ``--`` when using the launchers, or directly to
``fermilink optimize goal.md``:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Flag
     - Description
   * - ``--baseline-only``
     - Run only the baseline benchmark and exit.
   * - ``--plan-only``
     - Initialize state and validate inputs without running the loop.
   * - ``--resume``
     - Resume an existing campaign from local state.
   * - ``--max-iterations <n>``
     - Override the campaign iteration cap.
   * - ``--worker-max-iterations <n>``
     - Override the per-turn worker iteration cap.
   * - ``--stop-on-consecutive-rejections <n>``
     - Override the rejection-based early-stop threshold.
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
