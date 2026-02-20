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
- initializes/upgrades shared memory at ``projects/memory.md``;
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
- initializes/upgrades shared memory at ``projects/memory.md``;
- streams provider stdout/stderr live;
- appends assistant reply to session history.

Useful flags:

- ``--package <id>`` pin package for whole session.
- ``--sandbox <mode>`` enforce sandbox mode for this session.
- ``--init-git`` initialize git repo if missing.
- ``--no-init-git`` fail if git repo is missing.

Telegram gateway (iPhone chat)
------------------------------

Use ``gateway`` to bind Telegram chat sessions to sticky workspace repos and
run each message through ``fermilink loop``.

.. code-block:: bash

   export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<bot-token>"
   export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="123456789"
   fermilink gateway

Step-by-step setup (iPhone + computer):

1. Create a Telegram bot on iPhone:
   open ``@BotFather`` in Telegram, run ``/newbot``, and copy the bot token.
2. Find your numeric Telegram user id on iPhone:
   message ``@get_telegram_id_smppcenter_bot`` and copy the ``Id`` value.
3. Run the local FermiLink code on your computer:

   .. code-block:: bash

      cd /Users/taoli/Documents/Github/FermiLink_development
      pip install .

4. Export gateway variables on the computer or add it to ``.bashrc/zshrc``:

   .. code-block:: bash

      export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token-from-botfather>"
      export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<numeric-id-from-userinfobot>"

5. Start the gateway:

   .. code-block:: bash

      fermilink gateway

6. From iPhone Telegram, open the chat with your bot and test commands:
   ``/help``, then a normal simulation request, then ``/mode exec``,
   ``/mode loop``, ``/status``, ``/new test2``, ``/use main``, ``/where``,
   and ``/list``.
7. Verify mapping/runtime state on computer:

   .. code-block:: bash

      cat ~/.fermilink/runtime/chat_sessions.json
      ls ~/.fermilink/workspaces

If you receive ``Access denied.``, the allowlist id/username does not match the
sender account. Update ``FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM`` and restart.

Gateway behavior:

- each Telegram chat gets one active workspace under
  ``$FERMILINK_WORKSPACES_ROOT/<workspace_id>/repo``;
- normal messages run in the active workspace so follow-up requests reuse
  ``projects/memory.md`` history;
- run replies are rendered as a human-friendly summary from memory sections
  (completed plan items + key findings), instead of raw status payloads;
- generated figures/documents are auto-attached back to Telegram when available
  so plots can be viewed directly on mobile clients;
- ``/mode <loop|exec>`` switches normal-message execution between
  ``fermilink loop`` (multi-iteration autonomous mode) and
  ``fermilink exec`` (single-turn mode) per chat session;
- ``/status`` returns a quick health snapshot for the current chat (gateway
  online response timestamp, current mode, active workspace, live agent state
  ``idle/queued/running`` (with current-run details while active), and last
  run status/reason/timestamps;
- ``/new [name]`` creates and switches to a new workspace;
- ``/use <name-or-id>`` switches back to an existing workspace;
- ``/where`` prints the active workspace and ``/list`` shows all chat
  workspaces;
- for run messages, Telegram immediately sends a queued/accepted ack, then
  sends only final completion updates when the run finishes (no token
  streaming during execution).

Useful flags:

- ``--telegram-token <token>`` set bot token from CLI instead of env.
- ``--allow-from <id-or-username>`` sender allowlist (repeatable).
- ``--session-store <path>`` custom persistent chat-session store JSON path.
- loop forwarding flags such as ``--package``, ``--sandbox``,
  ``--max-iterations``, ``--wait-seconds``, ``--max-wait-seconds``,
  ``--pid-stall-seconds``.

Autonomous iterative loop
-------------------------

Use ``loop`` for iterative autonomous work with persistent memory.

.. code-block:: bash

   fermilink loop prompt.md
   fermilink loop "refactor router and add tests"
   fermilink loop --max-iterations 50 prompt.md
   fermilink loop --wait-seconds 30 --max-wait-seconds 300 prompt.md
   fermilink loop --pid-stall-seconds 900 prompt.md

Loop behavior:

- defaults to iterative execution until done token or iteration cap;
- persists unified memory to ``projects/memory.md`` with:
  ``Short-Term Memory`` (``Plan``, ``Progress log``) and
  ``Long-Term Memory`` (``File map``, ``Simulation history``,
  ``Key results``, ``Suggested skills updates``);
- stops early when output includes ``<promise>DONE</promise>``;
- supports job-based waiting via ``<pid_number>...</pid_number>`` (local
  processes) and ``<slurm_job_number>...</slurm_job_number>`` (HPC jobs) tags;
  when present, loop polls those jobs until completion or until
  ``--max-wait-seconds`` is reached;
- detects local pid failures/stalls and repeated unqueryable slurm-job states
  during polling, then immediately advances to the next iteration for
  debug/resubmit handoff (pid stall detection controlled by
  ``--pid-stall-seconds``; set ``0`` to disable);
- emits a polling heartbeat roughly every 10 minutes during active waits,
  including UTC timestamp and currently tracked wait targets;
- keeps backward-compatible ``<wait_seconds>...</wait_seconds>`` wait hints when
  no pid/slurm wait tags are provided.

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
In HPC mode, workflow report finalization retries script/report generation with
explicit validator feedback when SLURM contract checks fail (up to a bounded
attempt limit). Validation diagnostics are written to
``hpc_contract_errors.json`` under the run directory.
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

Memory-driven recompile planning
--------------------------------

Use ``recompile --memory`` to convert unified-memory suggestions into an
plan-and-apply append-only skill refresh flow.

.. code-block:: bash

   fermilink recompile <package_id> <path> --memory ./projects/memory.md
   fermilink recompile <package_id> <path> --memory ./projects

When ``--memory`` points to a directory, FermiLink recursively scans all
``memory.md`` files, extracts ``### Suggested skills updates`` entries for the
requested package id, classifies machine-specific issues into
``skills/user-specific-settings/SKILL.md`` targets, writes plan JSON to
``skills/.evidence/memory_update_plan.json``, and appends accepted updates into
target ``skills/*/SKILL.md`` files. This mode cannot be combined with ``--doc``,
``--data-dir``, or ``--comment``.

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
