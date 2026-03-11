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

- ``agent_runtime.py``: persisted runtime policy (provider, sandbox mode, model, reasoning effort).
- ``agents/``: provider-agent base contract, per-provider adapters, and shared provider runtime behavior (stream rendering/extraction, runtime env setup, workspace instruction aliases, command adjustments), plus provider registry.
- ``providers.py``: stable provider wrappers that delegate binary resolution, capability queries, service-env collection, and command assembly to the agent registry, keeping command/runner code provider-generic.
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

- ``commands/*``: implementations for package/service/session/workflow commands; provider-specific execution, metadata-generation, and final-reply capture are delegated to ``agents/`` hooks and provider-wrapper helpers.
- ``parser_*`` modules: parser registration by command family.
- ``optimize_*.py`` plus ``commands/optimize.py``: standalone benchmark-gated code-optimization mode with its own parser, prompts, campaign state, worker-agent turn, controller-agent review turn, and git-control helpers, intentionally separated from package-routing and workflow prompt stacks.
- ``zero_arg.py``: deterministic zero-argument beginner entrypoint, startup welcome-banner rendering, terminal status-table rendering, setup menu flow, guided mode selection for bare ``fermilink``, and thin guided wrappers around ``compile``/``recompile``.
- ``exec_runtime.py`` and helpers: subprocess execution and shared CLI behavior; provider-specific runtime details are delegated to ``agents/``.

Runner and web
^^^^^^^^^^^^^^

- ``src/fermilink/runner/app.py``: FastAPI backend and SSE run execution; workspace/env setup delegates to ``agents/``, and provider stdout streams under a generic ``agent`` event label.
- ``src/fermilink/runner/admission.py``: admission queue and concurrency limits.
- ``src/fermilink/web/app.py``: Chainlit entrypoint and orchestration, including isolated package second-guess preflight sessions so routing probes cannot leak provider conversation state into the visible chat turn.
- ``src/fermilink/web/*_helpers.py``: routing, auth, storage, runner, and activity helpers; runner events are consumed in a provider-agnostic way with legacy ``codex`` compatibility where needed.

Tests and docs
^^^^^^^^^^^^^^

- ``tests/``: coverage for CLI, runner, web, packages, policy, and routing.
- ``docs/source``: Sphinx source for this documentation site.
