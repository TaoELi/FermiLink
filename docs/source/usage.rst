Usage Guide
===========

This page focuses on practical command flows. For policy and architecture
details, see :doc:`configuration` and :doc:`architecture`.

Service lifecycle
-----------------

.. code-block:: bash

   fermilink start
   fermilink status
   fermilink stop

One-shot execution in current repo
----------------------------------

Use ``exec`` when you want web-like package routing in a local repository.

.. code-block:: bash

   fermilink exec "run a single-mode cavity coupled to a weakly excited two-level system"

   # provide prompt from a file
   fermilink exec prompt.md

What ``exec`` does:

- routes prompts to the best installed package (keyword router + second guess);
- overlays selected package files into current repository;
- syncs baseline ``AGENTS.md`` workspace instructions;
- avoids seeding web-only ``public/`` assets into your repo;
- runs provider execution and streams output.

Useful flags:

- ``--package <id>`` pin package id.
- ``--sandbox <mode>`` apply per-run sandbox override.
- ``--init-git`` initialize git repo non-interactively if missing.
- ``--no-init-git`` fail when git repo is missing.

Interactive terminal chat
-------------------------

Use ``chat`` for multi-turn conversation in terminal while keeping package
selection and overlay behavior aligned with web mode.

.. code-block:: bash

   fermilink chat

Per turn, ``chat``:

- rebuilds transcript-style prompt context;
- re-runs package routing and can switch package when needed;
- overlays package content into current repository;
- streams provider stdout/stderr live;
- appends assistant reply to session history.

Useful flags:

- ``--package <id>`` pin package for whole session.
- ``--sandbox <mode>`` enforce sandbox mode for this session.
- ``--init-git`` initialize git repo if missing.
- ``--no-init-git`` fail if git repo is missing.

Autonomous iterative loop
-------------------------

Use ``loop`` for iterative autonomous work with persistent memory.

.. code-block:: bash

   fermilink loop prompt.md
   fermilink loop "refactor router and add tests"
   fermilink loop --max-iterations 50 prompt.md
   fermilink loop --wait-seconds 30 --max-wait-seconds 300 prompt.md

Loop behavior:

- defaults to iterative execution until done token or iteration cap;
- persists long-term memory to ``projects/memory.md``;
- stops early when output includes ``<promise>DONE</promise>``;
- supports dynamic wait control via ``<wait_seconds>...</wait_seconds>`` tags.

Reproduce workflows
-------------------

Use ``reproduce`` to orchestrate planner + auditor + multi-task loop runs for
publication-scale requests.

.. code-block:: bash

   fermilink reproduce paper.tex
   fermilink reproduce "reproduce Figures 1-4 from this paper ..."
   fermilink reproduce paper.tex --plan-only
   fermilink reproduce paper.tex --data-dir ./data
   fermilink reproduce paper.tex --dry-run
   fermilink reproduce paper.tex --enforce-simulation
   fermilink reproduce paper.tex --hpc-profile scripts/hpc_profile_anvil.json
   fermilink reproduce paper.tex --report-only

Key artifacts are written under ``projects/reproduce/<run-id>/`` (for example
``plan.json``, ``state.json``, prompts, logs, archive, summaries, and
``report.md``).
After successful report finalization, four orchestration scripts are generated
at run root:

- ``00_run_all.sh``: per-task orchestration that runs simulation, then
  post-processing, then plotting for each task in order; when stage scripts
  emit SLURM job ids, it waits for successful completion before advancing to
  downstream stages or the next task;

- ``01_run_simulations.sh``: executes all task-level simulation scripts while
  continuing across per-task failures;
- ``02_run_postprocess.sh``: executes all task-level post-processing scripts
  with the same failure-tolerant behavior;
- ``03_run_plots.sh``: executes all task-level plotting scripts with the same
  failure-tolerant behavior.
For HPC/SLURM workflows, these stage drivers also propagate inter-stage job
dependencies via run-scoped map files:

- ``simulation_job_ids.tsv``: task-to-job-id mapping emitted by
  ``01_run_simulations.sh`` from ``FERMILINK_FINAL_JOB_ID=<job_id>`` markers;
- ``postprocess_job_ids.tsv``: task-to-job-id mapping emitted by
  ``02_run_postprocess.sh`` and used to gate plotting jobs;
- ``plot_job_ids.tsv``: task-to-job-id mapping emitted by ``03_run_plots.sh``.
When task scripts use ``sbatch``, they should emit
``FERMILINK_FINAL_JOB_ID=<job_id>`` and, for post-processing/plot stages,
consume optional ``FERMILINK_UPSTREAM_JOB_ID`` to submit dependent jobs with
``--dependency=afterok:<job_id>``.
When ``--data-dir`` is provided, additional run-scoped artifacts are written to
``projects/reproduce/<run-id>/data/``:

- ``data_manifest_full.json`` deterministic full indexed inventory (traceability);
- ``data_manifest.json`` compact relevance-first manifest used for LLM mapping
  (deterministic noise filtering + family collapse metadata);
- ``data_summary.md`` compact-manifest summary including exclusion/collapse stats;
- ``task_data_map.json`` task-to-file mapping with confidence/rationale
  (single global mapping for small manifests, per-task internal mapping loop for
  large manifests);
- ``task_XXX.md`` per-task data scope context consumed by ``loop``.

By default, ``--data-dir`` is read-only across planner/auditor/loop turns.
Use ``--data-writable`` only when you intentionally allow mutations.
By default, ``reproduce`` runs in dry-run scaffold mode (prepare simulation
inputs, post-processing/plot scripts, and README instructions) without running
full simulations. Use ``--enforce-simulation`` to disable dry-run and run
actual simulations.
By default, workflow execution target is local-machine mode (no SLURM).
Use ``--hpc-profile <json>`` to switch planning/prompts to HPC SLURM-ready
artifacts. ``--hpc-profile`` has highest precedence over package/skill defaults.
When the HPC profile includes ``defaults`` (for example ``nodes``, ``ntasks``,
``ntasks_per_node``) and ``comments``, workflow prompts include a concise
resource-policy hint so task execution prefers the specified resource shape
when scientifically appropriate.
An example profile is available at ``scripts/hpc_profile_anvil.json``.

Research workflows
------------------

Use ``research`` when starting from an idea prompt instead of an existing
paper.

.. code-block:: bash

   fermilink research "Design and validate a cavity QED protocol"
   fermilink research idea.md --plan-only
   fermilink research idea.md --data-dir ./data
   fermilink research idea.md --dry-run
   fermilink research idea.md --enforce-simulation
   fermilink research idea.md --hpc-profile scripts/hpc_profile_anvil.json
   fermilink research idea.md --report-only

Key artifacts are written under ``projects/research/<run-id>/`` (including
``report.md``) and support resume from edited plan state. ``--data-dir`` uses
the same run-scoped ``data/`` artifact contract and read-only defaults as
``reproduce``.
``research`` uses the same local-default / ``--hpc-profile`` override behavior
as ``reproduce`` for execution-target-specific artifact generation.
The same four orchestration scripts (``00_run_all.sh`` plus
``01_run_simulations.sh`` / ``02_run_postprocess.sh`` / ``03_run_plots.sh``)
are also generated under
``projects/research/<run-id>/``.

Automated package onboarding
----------------------------

Use ``auto-compile`` to onboard scientific repositories at scale:

.. code-block:: bash

   fermilink auto-compile qutip https://github.com/qutip/qutip \
     --fermilink-repo /absolute/path/to/FermiLink_development \
     --organization your-org

Batch mode:

.. code-block:: bash

   fermilink auto-compile \
     --spec-file ./packages.json \
     --fermilink-repo /absolute/path/to/FermiLink_development

This workflow automates GitHub fork/clone, conditional ``skills/`` compilation,
push to your fork, Codex-driven metadata drafting, and validated append/update
of curated channel plus router family hints data. Omit ``--organization`` to
target your authenticated personal ``gh`` account.

Web package controls
--------------------

In Chainlit, manage package state with ``/package`` commands:

- list installed packages;
- pin a package for a session;
- toggle automatic routing behavior.
