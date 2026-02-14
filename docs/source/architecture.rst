Architecture
============

This page describes how FermiLink executes one request end-to-end and how key
subsystems coordinate.

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
8. Web/CLI renders output and persists session metadata.

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
