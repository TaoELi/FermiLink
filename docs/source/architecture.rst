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
- ``providers.py``: provider binary resolution and command assembly.
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

- ``commands/*``: implementations for package/service/session/workflow commands.
- ``parser_*`` modules: parser registration by command family.
- ``exec_runtime.py`` and helpers: subprocess execution and shared CLI behavior.

Runner and web
^^^^^^^^^^^^^^

- ``src/fermilink/runner/app.py``: FastAPI backend and SSE run execution.
- ``src/fermilink/runner/admission.py``: admission queue and limits.
- ``src/fermilink/web/app.py``: Chainlit entrypoint and orchestration.
- ``src/fermilink/web/*_helpers.py``: routing, auth, storage, runner, and activity helpers.

Tests and docs
^^^^^^^^^^^^^^

- ``tests/``: coverage for CLI, runner, web, packages, policy, and routing.
- ``docs/source``: Sphinx source for this documentation site.
