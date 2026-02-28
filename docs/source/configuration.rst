Advanced Configuration
======================

This page summarizes the highest-impact settings for reliable local and shared
FermiLink deployments.

Agent runtime policy 
------------------------

FermiLink resolves provider and sandbox behavior in this order:

1. Environment overrides:
   ``FERMILINK_AGENT_PROVIDER``,
   ``FERMILINK_AGENT_SANDBOX_POLICY``,
   ``FERMILINK_AGENT_SANDBOX_MODE``.
2. Persisted policy file:
   ``FERMILINK_HOME/agent_runtime.json``.
3. Built-in defaults.

Set policy via CLI:

.. code-block:: bash

   fermilink agent --sandbox
   fermilink agent --bypass-sandbox
   fermilink agent codex

Core path variables
-------------------

.. list-table::
   :header-rows: 1

   * - Variable
     - Default
     - Purpose
   * - ``FERMILINK_HOME``
     - ``~/.fermilink``
     - Base root for packages, workspaces, and runtime state.
   * - ``FERMILINK_SCIPKG_ROOT``
     - ``$FERMILINK_HOME/scientific_packages``
     - Scientific package root.
   * - ``FERMILINK_WORKSPACES_ROOT``
     - ``$FERMILINK_HOME/workspaces``
     - Session workspace root.
   * - ``FERMILINK_RUNTIME_ROOT``
     - ``$FERMILINK_HOME/runtime``
     - Service state/log root.
   * - ``FERMILINK_CHAINLIT_APP_ROOT``
     - ``$FERMILINK_HOME``
     - Chainlit app root for DBs and public assets.

Common runner/web controls
--------------------------

.. list-table::
   :header-rows: 1

   * - Variable
     - Default
     - Purpose
   * - ``FERMILINK_RUNNER_URL``
     - ``http://runner:8000`` (web default)
     - Runner base URL used by web app.
   * - ``FERMILINK_CODEX_BIN``
     - ``codex``
     - Provider binary path for codex runs.
   * - ``FERMILINK_RUNNER_MAX_RUNTIME_SECONDS``
     - ``600``
     - Per-run hard timeout in runner.
   * - ``FERMILINK_CHAINLIT_PACKAGE_ROUTER_ENABLED``
     - ``true``
     - Enable keyword router in web layer.
   * - ``FERMILINK_PACKAGE_SECOND_GUESS_ENABLED``
     - ``true``
     - Enable model-based package second-guess preflight.

Gateway controls
----------------

``fermilink gateway`` reads these Telegram-specific variables:

.. list-table::
   :header-rows: 1

   * - Variable
     - Default
     - Purpose
   * - ``FERMILINK_GATEWAY_TELEGRAM_TOKEN``
     - unset
     - Telegram bot token used by ``gateway`` long polling.
   * - ``FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM``
     - unset
     - Optional comma/space-separated sender allowlist (ids/usernames).


Core runtime paths (when not overridden)
----------------------------------------

By default, FermiLink stores runtime data under:

- ``~/.fermilink/scientific_packages``
- ``~/.fermilink/workspaces``
- ``~/.fermilink/runtime``
- ``~/.fermilink/public`` (Chainlit static assets + local artifact storage)
- ``~/.fermilink/.chainlit`` (Chainlit sqlite DBs and internal state)

Operational metrics endpoints
-----------------------------

Runner exposes:

- ``GET /ops/concurrency``
- ``GET /ops/concurrency.prom``
- ``GET /ops/admission``

