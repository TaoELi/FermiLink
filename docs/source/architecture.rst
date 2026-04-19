Architecture
============

This page gives a high-level overview of how **FermiLink** is organized.

Repository layout
-----------------

::

   FermiLink/
   ├── src/fermilink/          # core library
   │   ├── agents/             # AI provider adapters and runtime behavior
   │   ├── cli/                # command-line interface
   │   │   └── commands/       # subcommand implementations
   │   ├── optimize/           # benchmark-gated code optimization mode
   │   ├── packages/           # scientific-package registry and overlays
   │   ├── runner/             # FastAPI backend for run execution
   │   ├── tools/              # skill-generation framework
   │   ├── web/                # Chainlit-based web chat UI
   │   └── workspace/          # fermilink init / clean / hpc bootstrap
   ├── bin/                    # goal-mode optimize launchers
   ├── scripts/                # sample goals, HPC profiles, validators
   ├── skills/                 # repo-local skill bundles
   ├── tests/                  # test suite
   └── docs/                   # Sphinx documentation source

Core (``src/fermilink``)
^^^^^^^^^^^^^^^^^^^^^^^^

- ``agents/`` — pluggable AI-provider adapters (model selection, streaming, environment setup).
- ``providers.py`` — provider-agnostic wrappers used by the CLI and runner.
- ``agent_runtime.py`` — persisted runtime settings (provider, model, sandbox mode).
- ``config.py`` — path resolution for FermiLink's home directory.
- ``services.py`` — process lifecycle helpers for the runner and web server.
- ``router_rules.py`` — package-aware routing rule sync.

Packages (``src/fermilink/packages``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``package_registry.py`` — install, list, and manage scientific packages.
- ``package_core.py`` — overlay engine that injects package knowledge into sessions.
- ``curated_channels.py`` — built-in catalog of curated packages.

CLI (``src/fermilink/cli``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``commands/`` — one module per subcommand (packages, sessions, services, workflows, optimize, workspace, etc.).
- ``parser_*.py`` — argparse definitions for each command surface.
- ``zero_arg.py`` — the guided entry point when you run bare ``fermilink``.
- ``exec_runtime.py`` — subprocess execution helpers.

Workspace (``src/fermilink/workspace``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``init.py`` / ``clean.py`` — payload staging and cleanup for ``fermilink init`` / ``fermilink clean``.
- ``hpc.py`` — persistent default SLURM profile helpers.
- ``filesystem.py``, ``payload.py``, ``common.py`` — shared filesystem and payload utilities.

Runner (``src/fermilink/runner``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``app.py`` — FastAPI server that executes runs and streams results via SSE.
- ``admission.py`` — concurrency and admission-queue management.
- ``scientific_packages.py`` — package-routing integration for runner sessions.

Optimize (``src/fermilink/optimize``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``main.py`` — controller/worker campaign implementation.
- ``campaign.py``, ``benchmark.py``, ``goal_pipeline.py`` — domain facades over ``main.py``.
- ``goal.py``, ``state.py``, ``git.py``, ``source_analysis.py``, ``prompts.py`` — goal parsing, state layout, worktree control, and prompt construction.

Web UI (``src/fermilink/web``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``app.py`` — Chainlit chat application and session orchestration.
- ``*_helpers.py`` — routing, auth, storage, and activity helpers.

Tools (``src/fermilink/tools``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``sci-skills-generator/`` — scientific-package skill-generation framework and templates.
