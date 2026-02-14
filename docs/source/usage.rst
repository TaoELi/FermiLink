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
   fermilink reproduce paper.tex --report-only

Key artifacts are written under ``projects/reproduce/<run-id>/`` (for example
``plan.json``, ``state.json``, prompts, logs, archive, summaries) plus final
report output.

Research workflows
------------------

Use ``research`` when starting from an idea prompt instead of an existing
paper.

.. code-block:: bash

   fermilink research "Design and validate a cavity QED protocol"
   fermilink research idea.md --plan-only
   fermilink research idea.md --report-only

Key artifacts are written under ``projects/research/<run-id>/`` and support
resume from edited plan state.

Web package controls
--------------------

In Chainlit, manage package state with ``/package`` commands:

- list installed packages;
- pin a package for a session;
- toggle automatic routing behavior.
