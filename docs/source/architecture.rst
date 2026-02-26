Architecture
============

This page describes how FermiLink executes one request end-to-end and how key
subsystems coordinate. It also consolidates the maintained repository map that
was previously split into a separate page.

System snapshot
---------------

FermiLink has two runtime services:

- Runner API (``src/fermilink/runner/app.py``): admission, workspace setup,
  package overlay, provider execution, SSE streaming.
- Chainlit web app (``src/fermilink/web/app.py``): auth/quota, package routing,
  second-guess preflight, stream rendering, and artifact handling.

Shared components:

- Package registry and overlay engine:
  ``src/fermilink/packages/package_core.py`` and
  ``src/fermilink/packages/package_registry.py``.
- Router sync logic: ``src/fermilink/router_rules.py``.
- Runtime policy and provider abstraction:
  ``src/fermilink/agent_runtime.py`` and ``src/fermilink/providers.py``.

Request flow
------------

1. User sends message in Chainlit or CLI mode.
2. Package is resolved (manual pin, router rules, optional second-guess).
3. Runner admission checks global/per-user limits.
4. Workspace repo is provisioned and normalized.
5. Package entries and dependencies are overlaid into repo.
6. Effective runtime policy is resolved (provider + sandbox).
7. Provider command runs and emits structured stream events.
8. Web/CLI/loop modes read/update unified shared memory at
   ``projects/memory.md`` (short-term + long-term sections).
9. Web/CLI renders output and persists session metadata.

Workflow planning (`reproduce`/`research`)
------------------------------------------

Workflow command order is explicit:

1. Planner generates draft tasks from source scientific intent.
2. Optional data pass (`--data-dir`) writes both deterministic full inventory
   (``data_manifest_full.json``) and compact LLM-facing inventory
   (``data_manifest.json``) under ``projects/<mode>/<run-id>/data/`` after
   deterministic filtering/family-collapse compaction.
3. Data mapping adapts by compact-manifest size: small inventories use one
   global mapping call, large inventories use an internal per-task mapping loop
   with per-task fallback/resume-safe artifact writes.
4. Auditor receives both draft plan and data map, then emits corrected tasks.
5. Loop executes each task with task-scoped data context (`task_XXX.md`) in the
   preamble and read-only data-dir guard by default.

Workspace and overlay model
---------------------------

Per session:

- workspace root: ``FERMILINK_WORKSPACES_ROOT/<session_id>/``
- repo root: ``.../repo/``
- overlay manifest: ``.../.package_manifest.json``

Overlay behavior:

- exportable package entries are symlinked when possible, copied as fallback;
- reserved entries (for example package ``AGENTS.md``) are not exported to repo root;
- dependencies are linked under ``repo/external_packages/``;
- ownership metadata is persisted for deterministic cleanup/reuse.

Streaming contract
------------------

Runner SSE event types:

- ``meta``: session/package/policy metadata.
- ``codex``: provider stream records.
- ``log``: runner log lines.
- ``runner.exit``: final status and return code.

Admission and backpressure
--------------------------

Admission controller enforces:

- global active run limits;
- per-user active run limits;
- pending queue limits (global and per user).

Operational visibility is exposed through the ``/ops/*`` endpoints.

Web app internal layout
-----------------------

``src/fermilink/web/app.py`` remains the Chainlit entrypoint, while helper
logic is split into focused modules under ``src/fermilink/web/``:

- ``package_router_helpers.py``: package routing/scoring and second-guess prompts.
- ``package_session_helpers.py``: session package state and ``/package`` handling.
- ``chat_helpers.py``: stream text extraction and prompt history construction.
- ``artifact_helpers.py``: artifact discovery/attachment and transparency rendering.
- ``runner_helpers.py``: runner stream/admission probe and log filtering helpers.
- ``storage_helpers.py``: local storage provider and public-root resolution.
- ``sqlite_helpers.py``: sqlite URL parsing and schema/bootstrap helpers.
- ``auth_helpers.py``: auth/signup/quota/account helpers.
- ``activity_helpers.py``: active-run ownership and reconnect-safe rebinding.
- ``status_helpers.py``: transient status-label rendering helpers.

Integrated repository map
-------------------------

This quick map helps contributors locate where behavior lives.

Root
^^^^

- ``README.md``: project overview and quick start.
- ``pyproject.toml``: package metadata, dependencies, and ``fermilink`` entrypoint.
- ``Makefile``: lint/format/docs targets.
- ``project_history.md``: architecture and implementation history.

Core package (``src/fermilink``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- ``agent_runtime.py``: persisted runtime provider/sandbox policy.
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
