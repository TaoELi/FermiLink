Command Line Tools
==================

This page focuses on practical command flows for the FermiLink CLI family.
For runtime policy and architecture details, see :doc:`configuration` and
:doc:`architecture`.

One-shot execution in the current repo
--------------------------------------

Use ``exec`` when you want one prompt, and one run followed by package routing in
your current working directory.

.. code-block:: bash

   fermilink exec "run a single-mode cavity coupled to a weakly excited two-level system"

   # provide prompt from a file
   fermilink exec prompt.md

   # run with an HPC profile appended to the prompt context
   fermilink exec "run the benchmark on slurm" --hpc-profile scripts/hpc_profile_anvil.json

What ``exec`` does:

- routes the prompt to the best installed package (keyword router + optional agent second-guess);
- overlays the selected package files into the current repository;
- syncs baseline ``AGENTS.md`` workspace instructions;
- initializes/upgrades shared memory at ``projects/memory.md``;
- runs provider execution and streams output.

Useful flags:

- ``--package <id>``: pin a package id (skip routing).
- ``--sandbox <mode>``: apply a per-run sandbox override.
- ``--hpc-profile <json>``: append workflow-style HPC constraints to the prompt.
- ``--init-git``: initialize a git repo non-interactively if missing.
- ``--no-init-git``: fail if a git repo is missing.

Interactive terminal chat
-------------------------

Use ``chat`` for multi-turn conversation in the terminal while keeping package
selection and overlay behavior aligned with web mode.

.. code-block:: bash

   fermilink chat

Per turn, ``chat``:

- rebuilds transcript-style prompt context;
- re-runs package routing and may switch packages when needed;
- overlays package content into the current repository;
- initializes/upgrades shared memory at ``projects/memory.md``;
- streams provider stdout/stderr live;
- appends the assistant reply to session history.

Useful flags:

- ``--package <id>``: pin a package for the whole session.
- ``--sandbox <mode>``: enforce sandbox mode for this session.
- ``--init-git`` / ``--no-init-git``: same behavior as ``exec``.

Autonomous iterative loop
-------------------------

Use ``loop`` for iterative autonomous work with persistent memory and job-aware
waiting.

.. code-block:: bash

   fermilink loop prompt.md
   fermilink loop "refactor router and add tests"

   # cap iterations
   fermilink loop --max-iterations 50 prompt.md

   # explicit wait hints (when no PID/SLURM wait tags are emitted)
   fermilink loop --wait-seconds 30 --max-wait-seconds 300 prompt.md

   # detect PID stalls during long waits
   fermilink loop --pid-stall-seconds 900 prompt.md

   # append an HPC target profile to the loop prompt context
   fermilink loop --hpc-profile scripts/hpc_profile_anvil.json prompt.md

Loop behavior:

- iterates until done token or iteration cap;
- persists unified memory to ``projects/memory.md`` (short-term plan/progress + long-term durable outcomes);
- stops early when output includes ``<promise>DONE</promise>``;
- supports job-aware waiting via ``<pid_number>...</pid_number>`` and
  ``<slurm_job_number>...</slurm_job_number>`` tags and polls until completion
  (bounded by ``--max-wait-seconds``).

Reproduce workflows
-------------------

Use ``reproduce`` to orchestrate planner + auditor + multi-task loop runs for
publication-scale reproduction requests.

.. code-block:: bash

   fermilink reproduce paper.tex
   fermilink reproduce "reproduce Figures 1-4 from this paper arXiv:..."
   fermilink reproduce paper.tex --plan-only
   fermilink reproduce paper.tex --report-only
   fermilink reproduce paper.tex --hpc-profile scripts/hpc_profile_anvil.json

Key artifacts are written under ``projects/reproduce/<run-id>/`` (for example
``plan.json``, ``state.json``, prompts, logs, and ``report.md``).

Notes:

- At workflow entry (except ``--report-only``), ``reproduce`` resets only the
  short-term memory section while preserving long-term memory content.
- The workflow generates orchestration scripts (for example ``00_run_all.sh``)
  under the run directory to support reruns and staged execution.
- Use ``--hpc-profile <json>`` to enforce an HPC SLURM target profile.

Research workflows
------------------

Use ``research`` when starting from an idea prompt instead of an existing paper.

.. code-block:: bash

   fermilink research "Design and validate a cavity QED protocol"
   fermilink research idea.md --plan-only
   fermilink research idea.md --report-only
   fermilink research idea.md --hpc-profile scripts/hpc_profile_anvil.json

Key artifacts are written under ``projects/research/<run-id>/`` (including
``report.md``) and support resume from edited plan state.

Notes:

- Like ``reproduce``, ``research`` resets only short-term memory at workflow
  entry (except ``--report-only``) and preserves long-term memory.
- ``--report-only`` skips planning/task execution and runs only report
  finalization from the saved run context.
- Use ``--hpc-profile <json>`` to enforce an HPC SLURM target profile.

Memory-driven recompile planning
--------------------------------

During calculations, agents will write down key findings for improving the usage of 
the packages in ``projects/memory.md``.  Use ``recompile --memory`` to **convert unified-memory suggestions**
in the workspace to a **permanent skill patch** to the package knowledge base, so all simulations 
will learn from the simulations in this workspace.

.. code-block:: bash

   fermilink recompile <package_id> --memory ./projects/memory.md
   fermilink recompile <package_id> <path> --memory ./projects/memory.md
   fermilink recompile <package_id> <path> --memory ./projects

When ``<path>`` is omitted, recompile targets the managed installed package path
``<scientific_packages_root>/packages/<package_id>``. Use explicit ``.`` to
target a different package directory.

When ``--memory`` points to a directory, FermiLink scans all ``memory.md``
files, extracts ``### Suggested skills updates`` entries for the requested
package id, writes a plan JSON, and appends accepted updates into
``skills/*/SKILL.md`` targets.

See also
--------

- :doc:`installation` for initial setup (Codex auth, first package install).
- :doc:`configuration` for runtime variables and provider/sandbox policy.
- :doc:`architecture` for the request flow and streaming contracts.
- :doc:`scientific_packages` for install/compile/recompile workflows.
- :doc:`usage` for the full Telegram reference (see :ref:`usage-cli-telegram`).