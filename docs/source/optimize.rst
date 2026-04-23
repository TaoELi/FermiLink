Tutorial: Code Optimization 
==============================

This tutorial walks a beginner end-to-end through running
**FermiLink**'s benchmark-gated code optimizer on a scientific package
(Python, C, C++, or Fortran). It is **self-contained**: you can follow
it from a fresh shell without reading other pages.

``fermilink optimize`` iteratively proposes source-level changes to a
target package, benchmarks each candidate, validates correctness, and
**accepts only changes that improve measured performance while
preserving results**. You describe your intent in a short structured
markdown file (``goal.md``); **FermiLink** will perform the optimization and return a branch with the accepted commits.

For general-purpose HPC simulation workflows (``exec``, ``loop``,
``research``, ``reproduce``), see :doc:`tutorial_hpc`.


Prerequisites
~~~~~~~~~~~~~

You need the following available locally (all can be user-local, no
sudo required):

- Python ``>= 3.11``
- ``git`` on ``PATH`` (optimize campaigns run inside a git worktree)
- Node.js + ``npm`` (for the Codex agent CLI)
- A C / C++ / Fortran toolchain **if** the package you want to optimize
  is compiled (e.g. ``gcc``, ``g++``, ``gfortran``, ``cmake``, ``make``)

This tutorial assumes you will use the OpenAI **Codex** CLI as the
agent backend, since it currently gives the strongest optimization
results with ``gpt-5.4`` at ``xhigh`` reasoning effort.


Step 1. Install the Codex CLI and authenticate
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Install the Codex CLI globally (or user-locally via ``npm --prefix``)
and log in once:

.. code-block:: bash

   npm i -g @openai/codex
   codex login

After ``codex login`` completes you should be able to run ``codex``
interactively without being prompted for credentials.

.. note::

   **FermiLink** also supports Claude and Gemini agents. For
   optimization we recommend Codex because of its superior code-editing
   and profiling behavior at high reasoning effort as well as its relative affordability. If you prefer a
   different provider, see :doc:`choosing_agent`.


Step 2. Install **FermiLink**
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Install **FermiLink** into the Python environment you want to drive the
optimizer from. Note that the Python launcher in Step 4 creates a **separate** per-campaign venv for the
target package, so this environment only needs **FermiLink** itself.

.. code-block:: bash

   pip install fermilink


This installs the ``fermilink`` CLI and the two opinionated optimize
launchers (``fermilink-optimize-python`` and
``fermilink-optimize-cpp``) onto your ``PATH``.


Step 3. Set the agent runtime policy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Optimization agents need to compile code, run benchmarks, and profile
hot paths, so the default restricted sandbox is too tight. Switch the
Codex agent to bypass-sandbox mode with the most capable model and
highest reasoning effort:

.. code-block:: bash

   fermilink agent codex --bypass-sandbox \
     --model gpt-5.4 \
     --reasoning-effort xhigh

   # verify what is active
   fermilink agent --json

This mirrors the recommended HPC configuration in :doc:`tutorial_hpc`.
``xhigh`` reasoning effort is important for optimization: the agent
needs deep deliberation to reason about correctness constraints while
restructuring hot paths.

.. warning::

   Bypassing the sandbox lets the agent execute arbitrary shell
   commands in your user account. **Never run this as root.** Use a
   dedicated non-root account and keep regular backups. Also run
   campaigns on a **clean clone** of the target package so the
   original checkout stays untouched (the launchers in Step 4 enforce
   this via a sibling git worktree).


Step 4. Write a ``goal.md`` file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``goal.md`` file is the only input you author by hand. It is a
short structured markdown file describing **what** to optimize and
**what invariants must hold**. **FermiLink** converts it into a
deterministic benchmark contract automatically.

For the full authoring guide, see :ref:`Goal files for code optimization <goal-files-for-code-optimization>`.

Also view the  `example goal files at Project Optimization <http://project-optimization.fermilink.org/index.html#po-packages>`_ for inspiration -- these are all real goals that have driven successful campaigns on open-source scientific packages.

A minimal goal file looks like this:

.. code-block:: markdown

   # Optimization Goal

   ## Package
   pyscf

   ## Language
   python

   ## Target
   Optimize DIIS behavior for SCF convergence in PySCF, focusing on
   reduced overhead and faster convergence paths.

   ## Editable Scope
   - pyscf/lib/diis.py
   - pyscf/scf/**

   ## Performance Metric
   Minimize end-to-end SCF kernel time from `mf.kernel()`.

   ## Correctness Constraints
   - Total SCF energy absolute delta <= 5e-8 Hartree vs baseline
   - All benchmark cases must converge within baseline cycle limits

   ## Representative Workloads
   - train-rhf-benzene: benzene / RHF / 6-31g** / diis_space=12
   - train-uhf-allyl: allyl radical / UHF / spin=1 / def2-TZVP
   - test-rhf-c3h7oh: C3H7OH / RHF / 6-31g**

   ## Build
   ```bash
   pip install -e .
   ```

   ## Notes
   Keep benchmark behavior deterministic with pinned thread counts.

Save this file anywhere -- e.g. ``~/goals/pyscf-diis.md``.

.. tip::

   If you are new to writing optimization goals, ask **FermiLink**
   itself. From any directory, run ``fermilink init`` then ``codex``
   and tell the agent: *"Use the optimize-goal-authoring skill to
   write a well-structured optimization goal for <routine> in
   <package>."* See :ref:`Goal files for code optimization <goal-files-for-code-optimization>` for the full prompt.


Step 5. Launch your first campaign
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The easiest way to start a campaign is the shipped launcher matching
your language. The launchers handle all the plumbing, including creating a
sibling git worktree and excluding ``fermilink optimize`` runtime data from git tracking. 

**Python packages** (PySCF, ASE, PyTorch-based solvers, ...):

.. code-block:: bash

   cd /path/to/pyscf       # your clean clone of the target package
   fermilink-optimize-python ~/goals/pyscf-diis.md --branch fermilink-optimize/pyscf-diis

**C / C++ / Fortran packages** (LAMMPS, Quantum ESPRESSO, GROMACS, ...):

.. code-block:: bash

   cd /path/to/lammps
   fermilink-optimize-cpp ~/goals/lammps-tip4p.md --branch fermilink-optimize/lammps-tip4p

That is all. The launcher:

1. Creates a sibling git worktree next to the source repo so your
   original checkout stays untouched.
2. Names the worktree branch ``fermilink-optimize/<repo>-<task>``
   (derived from the goal file name).
3. (Python launcher only) Creates a per-branch venv under
   ``<repo-parent>/venvs/`` and ``pip install fermilink`` into it.
4. Calls ``fermilink optimize <goal.md>`` inside the worktree.

Progress streams live to your terminal. You can check the current optimization status at any time
from another shell:

.. code-block:: bash

   fermilink optimize status

.. note::

   If you are not already inside a git repo, pass
   ``--project-root /path/to/clean/clone`` instead of ``cd``\ ing
   first. See the :ref:`detailed launcher options
   <optimize-launcher-options>` section below.


Step 6. What happens during a run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Understanding the two internal stages helps you read the log stream
and intervene if something looks off.

**Goal-mode pipeline (one-time setup)**

When you submit a ``goal.md`` for the first time, **FermiLink** runs a
two-phase generation pipeline **before** starting the optimization
loop:

1. **Source analysis**: an agent reads your target source code and
   produces a structured JSON analysis of the package
   and correctness boundaries.
2. **Benchmark generation**: a second agent writes a
   ``benchmark.yaml`` deterministic contract and a
   ``benchmark_runner.py`` driver script, which are validated and
   placed under ``.fermilink-optimize/autogen/``. A copy of your input
   ``goal.md`` is preserved alongside them.

Once generation is done the campaign switches to the standard
optimization loop.

**Campaign lifecycle (repeated until convergence or iteration cap)**

1. **Baseline**: the generated benchmark suite runs on the
   unmodified source to establish incumbent performance.
2. **Worker turn**: an AI worker agent proposes a single
   candidate source-level change inside an isolated git worktree.
3. **Benchmark**: the candidate is benchmarked.
4. **Controller turn**: a separate controller agent evaluates
   correctness (against your ``## Correctness Constraints``) and
   performance, then **accepts or rejects** the candidate.
5. **Iterate**: repeat from step 2 until the iteration cap, the
   consecutive-rejection limit, or ``--forever`` termination.

Runtime data for ``fermilink optimize`` is persisted under ``.fermilink-optimize/``. 


Step 7. Resume from an interrupted run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Long campaigns occasionally get interrupted. You can
resume exactly where you left off by passing ``--resume`` through to
the launcher:

.. code-block:: bash

   # Python
   fermilink-optimize-python --goal ~/goals/pyscf-diis.md --branch fermilink-optimize/pyscf-diis \
     -- --resume

   # C/C++/Fortran
   fermilink-optimize-cpp --goal ~/goals/lammps-tip4p.md --branch fermilink-optimize/lammps-tip4p \
     -- --resume

Anything after ``--`` is forwarded verbatim to the underlying
``fermilink optimize`` call, so ``-- --resume`` means "add
``--resume`` to ``fermilink optimize``".

What ``--resume`` does:

- Reuses the existing sibling worktree and branch (does not recreate
  them).
- Reuses the already-generated ``benchmark.yaml`` and runner under
  ``.fermilink-optimize/autogen/`` -- the expensive two-phase goal
  pipeline is **skipped**.
- Reloads controller and worker memories and continues the iteration
  loop from the last persisted state.
- Preserves accepted commits already in the branch history.

You can combine ``--resume`` with other flags, e.g. extending the
iteration cap or the per-run benchmark timeout:

.. code-block:: bash

   fermilink-optimize-cpp --goal ~/goals/lammps-tip4p.md --branch fermilink-optimize/lammps-tip4p \
     -- --resume --max-iterations 60 --timeout-seconds 6000


Step 8. Power user: tune the generated ``benchmark.yaml``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

After the first goal-mode run, **FermiLink** has written a fully
deterministic benchmark contract under:

.. code-block:: text

   .fermilink-optimize/autogen/
   ├── goal.md              # copy of your input
   ├── benchmark.yaml       # generated deterministic contract
   └── benchmark_runner.py  # generated driver script

This ``benchmark.yaml`` is what actually drives the optimization loop.
For more deterministic behavior, **FermiLink does not re-read ``goal.md`` during iterations**. 

That means once you are happy with the auto-generated contract (or want to
hand-tune it), you can skip goal mode entirely and drive the optimizer
directly in **expert mode**:

.. code-block:: bash

   fermilink optimize <package_id> <project_path> \
     --benchmark .fermilink-optimize/autogen/benchmark.yaml \
     --resume

Why you might want to tune the YAML by hand:

- **Target section**: add or remove editable file globs to narrow
  the agent's scope.
- **Correctness tolerances**: tighten or loosen numerical
  tolerances in ``field_tolerances`` based on what you observed in
  the baseline run.
- **Runtime hooks**: edit ``runtime.pre_commands`` to add a custom
  rebuild recipe of the package.
- **Per-run timeout**: change ``runtime.timeout_seconds`` if some
  cases are outliers.

The rule of thumb: use ``goal.md`` to bootstrap the contract, then
treat ``benchmark.yaml`` as the source of truth for further
iterations. 


Step 9. Check campaign progress
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

At any time, inside the modified repo, inspect the
campaign state:

.. code-block:: bash

   fermilink optimize status



Troubleshooting quick checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Dirty working tree refused**: commit or stash your changes, or
  pass ``--allow-dirty-base`` to the launcher if you know what you
  are doing.
- **Every candidate rejected on correctness**: your
  ``## Correctness Constraints`` are likely too tight. See
  :doc:`writing_goal_md` for physically meaningful tolerance
  guidance.
- **Benchmark always times out**: bump ``--timeout-seconds``, or
  pick smaller ``train-`` workloads. The optimizer never sees the
  ``test-`` cases during iteration.
- **Resume does nothing**: make sure you are in the same worktree
  (the sibling one created by the launcher), not the original
  source checkout.


----

Detailed reference: launcher and ``fermilink optimize`` options
---------------------------------------------------------------

The Step 5 and Step 7 commands above cover 95% of day-to-day usage.
The remaining sections document every flag for the moments you need
fine-grained control.

.. _optimize-launcher-options:

Common launcher options
~~~~~~~~~~~~~~~~~~~~~~~

Shared between ``fermilink-optimize-python`` and
``fermilink-optimize-cpp``.

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Flag
     - Description
   * - ``--goal PATH``
     - Goal markdown file (**required**).
   * - ``--project-root PATH`` / ``--repo PATH``
     - Clean git repo to optimize. Defaults to the current working
       git repo root.
   * - ``--branch NAME``
     - Worktree branch name. Default ``fermilink-optimize/<repo>-<task>``.
   * - ``--base-ref REF``
     - Base ref used when creating a new branch. Defaults to
       ``origin/HEAD`` when available, otherwise the current branch
       or ``HEAD``.
   * - ``--worktree-root PATH``
     - Parent dir for generated worktrees. Default
       ``<repo-parent>``.
   * - ``--worktree-name NAME``
     - Explicit worktree directory name. Default
       ``<repo>-<task>``.
   * - ``--hpc-profile PATH``
     - HPC profile JSON forwarded to ``fermilink optimize`` for
       SLURM submission. See :doc:`tutorial_hpc`.
   * - ``--worker-provider NAME``
     - Override the worker-agent provider (e.g. ``claude``,
       ``gemini``).
   * - ``--worker-model MODEL``
     - Override the worker-agent model. Inherits sandbox/reasoning
       from the active agent policy.
   * - ``--isolate-fermilink-home``
     - Run with a campaign-local ``FERMILINK_HOME`` so settings do
       not bleed between campaigns.
   * - ``--fermilink-home PATH``
     - Explicit ``FERMILINK_HOME`` path (implies isolation).
   * - ``--fermilink-bin BIN``
     - Which ``fermilink`` executable to run. Default ``fermilink``.
   * - ``--allow-dirty-base``
     - Allow uncommitted changes in ``--project-root``.
   * - ``--dry-run``
     - Print the resolved ``fermilink optimize`` command and exit.
   * - ``-h`` / ``--help``
     - Show the launcher help.


Python-launcher-only options
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``fermilink-optimize-python`` additionally provisions a per-branch
virtualenv with ``pip install fermilink`` inside it:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Flag
     - Description
   * - ``--python-bin BIN``
     - Python executable used to create the venv. Default
       ``python3``.
   * - ``--venv-root PATH``
     - Parent dir for per-branch venvs. Default
       ``<repo-parent>/venvs``.
   * - ``--venv-name NAME``
     - Override the default venv directory name.
   * - ``--venv-path PATH``
     - Explicit venv path (overrides ``--venv-root``/``--venv-name``).


Extra optimize arguments (after ``--``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Everything after ``--`` on the launcher command line is forwarded
verbatim to ``fermilink optimize``. These are the flags you most
often want to pass through.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Flag
     - Description
   * - ``--baseline-only``
     - Run only the baseline benchmark and exit. Useful for sanity
       checks.
   * - ``--plan-only``
     - Initialize state and validate inputs without running the
       loop.
   * - ``--resume``
     - Resume an existing campaign from local state (see Step 7).
   * - ``--max-iterations <n>``
     - Override the campaign iteration cap.
   * - ``--worker-max-iterations <n>``
     - Override the per-turn worker iteration cap.
   * - ``--stop-on-consecutive-rejections <n>``
     - Override the rejection-based early-stop threshold.
   * - ``--timeout-seconds <n>``
     - Override the per-run benchmark timeout.
   * - ``--hpc-profile <path>``
     - HPC profile for SLURM job submission.
   * - ``--forever``
     - Run indefinitely until interrupted.
   * - ``--allow-dirty``
     - Allow startup from a dirty git working tree.
   * - ``--sandbox <mode>``
     - Provider sandbox override for optimize agent turns.


Expert mode: run directly from ``benchmark.yaml``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Once you have a ``benchmark.yaml`` (either hand-written or generated
by goal mode as shown in Step 8), you can skip the launchers and the
goal pipeline entirely:

.. code-block:: bash

   fermilink optimize <package_id> <project_path> \
     --benchmark <path/to/benchmark.yaml> \
     [--branch NAME] \
     [--resume] \
     [--max-iterations N] \
     [--timeout-seconds N] \
     [--hpc-profile PATH]


Further reading
---------------

- :doc:`writing_goal_md` -- full authoring guide for ``goal.md``
  files, including every required section, tolerance conventions,
  and domain-specific examples.
- :doc:`tutorial_hpc` -- how to wire ``--hpc-profile`` for SLURM so
  optimize benchmarks run on a cluster instead of your laptop.
- :doc:`choosing_agent` -- picking between Codex, Claude, and
  Gemini as the worker or controller agent.
- :doc:`configuration` -- ``FERMILINK_HOME`` and related
  environment variables.
- :doc:`usage_advanced_configuration` -- reusable pipelines and
  memory-driven skill updates.
