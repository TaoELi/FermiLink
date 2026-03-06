Architecture
============

This page describes how FermiLink is structured and how its subsystems coordinate. 

Integrated repository map
-------------------------

Root
^^^^

- ``README.md``: project overview and quick start.
- ``pyproject.toml``: package metadata, dependencies, and ``fermilink`` entrypoint.
- ``Makefile``: lint/format/docs targets.
- ``project_history.md``: architecture and implementation history.

Core package (``src/fermilink``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``agent_runtime.py``: persisted runtime provider/sandbox/model/reasoning-effort policy.
- ``agents/``: provider-agent base contract, per-provider adapters, shared provider runtime behavior (stream rendering/extraction, runtime env tweaks, workspace instruction aliases, command adjustments), and provider registry.
- ``providers.py``: stable provider wrappers that delegate binary resolution, compatibility override selection, capability queries, service-env collection, and command assembly to the agent registry, so command/runner code stays provider-generic without codex-named exec/compile shims.
- ``config.py``: runtime path resolution.
- ``services.py``: runner/web process lifecycle helpers.
- ``router_rules.py``: package router rule synchronization.

Packages subsystem (``src/fermilink/packages``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``package_core.py``: shared registry and overlay engine internals.
- ``package_registry.py``: package-management facade and install flows.
- ``curated_channels.py``: curated package catalog and resolver.

CLI subsystem (``src/fermilink/cli``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``commands/*``: implementations for package/service/session/workflow commands, with provider-specific execution, metadata-generation, and final-reply capture quirks delegated to ``agents/`` hooks and provider-wrapper helpers.
- ``parser_*`` modules: parser registration by command family, without provider-specific binary override flags in command surfaces.
- ``exec_runtime.py`` and helpers: subprocess execution and shared CLI behavior, with provider-specific runtime details delegated to ``agents/``.

Runner and web
^^^^^^^^^^^^^^

- ``src/fermilink/runner/app.py``: FastAPI backend and SSE run execution; workspace alias/env provider specifics delegate to ``agents/``, and provider stdout now streams under a generic ``agent`` event label.
- ``src/fermilink/runner/admission.py``: admission queue and limits.
- ``src/fermilink/web/app.py``: Chainlit entrypoint and orchestration.
- ``src/fermilink/web/*_helpers.py``: routing, auth, storage, runner, and activity helpers, including provider-agnostic consumption of ``agent`` runner events with legacy ``codex`` compatibility where needed.

Tests and docs
^^^^^^^^^^^^^^

- ``tests/``: coverage for CLI, runner, web, packages, policy, and routing.
- ``docs/source``: Sphinx source for this documentation site.
