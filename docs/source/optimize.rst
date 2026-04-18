Code Optimization
==================

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
