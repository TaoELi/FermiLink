Test Plan
=========

This plan validates runtime policy control, provider wiring, and no-regression
behavior across CLI, runner, and web paths.

Core regression commands
------------------------

Focused suites:

.. code-block:: bash

   pytest -q \
     tests/test_agent_runtime.py \
     tests/test_providers.py \
     tests/test_cli_agent.py \
     tests/test_cli_chat.py \
     tests/test_runner_policy.py \
     tests/test_services.py \
     tests/test_cli_exec.py

Full regression:

.. code-block:: bash

   pytest -q

Behavioral checks
-----------------

Policy controls:

1. ``fermilink agent --json``
2. ``fermilink agent --bypass-sandbox --json``
3. ``fermilink agent --sandbox --json``
4. ``fermilink agent codex --json``

Expected:

- policy persists between commands;
- sandbox policy toggles correctly;
- sandbox mode remains stable unless explicitly changed.

Exec/chat checks:

- verify bypass behavior and per-run/session sandbox overrides;
- verify package selection output and overlay cleanup;
- verify ``exec`` and ``chat`` do not seed web ``public/`` assets into arbitrary repos.

Runner/web propagation checks:

1. ``fermilink agent --sandbox``
2. ``fermilink start``
3. Run one web prompt and inspect runner ``meta`` payload.

Expected ``meta.agent`` fields:

- ``provider``
- ``sandbox_policy``
- ``sandbox_mode``
