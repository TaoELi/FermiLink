Web UI
======

Use the web interface for ChatGPT-style interaction while preserving the same
routing, overlay, and policy behavior used by CLI modes.

In Web UI mode, each message is executed inside a session workspace repo under
``$FERMILINK_WORKSPACES_ROOT/<session_id>/repo``. That workspace contains the
same on-disk artifacts you would see from CLI runs (for example
``projects/memory.md`` and ``outputs/``).

Quick start
-----------

.. code-block:: bash

   # Install at least one scientific package for routing (once).
   fermilink install meep --activate

   # Start runner + Web UI.
   fermilink start

Then open ``http://localhost:7860``, sign up / sign in, and run:

.. code-block:: text

   /package list

Start and stop services
-----------------------

.. code-block:: bash

   fermilink start
   fermilink status
   fermilink restart
   fermilink stop

By default:

- ``runner`` (execution backend): ``http://127.0.0.1:8000``
- ``web`` (Chainlit UI): ``http://127.0.0.1:7860``

Service logs are written under ``$FERMILINK_RUNTIME_ROOT/logs/``.

Manual startup (optional)
-------------------------

Use this when you want runner and Chainlit processes managed separately.

.. code-block:: bash

   uvicorn fermilink.runner.app:app --host 127.0.0.1 --port 8000
   export FERMILINK_RUNNER_URL=http://127.0.0.1:8000
   chainlit run src/fermilink/web/app.py --host 127.0.0.1 --port 7860

Accounts and signup
-------------------

The Web UI uses password authentication (sqlite-backed local user store).

Common controls:

- ``FERMILINK_AUTH_SIGNUP_ENABLED``: allow self-signup (default: true)
- ``FERMILINK_AUTH_AUTO_REGISTER``: auto-create a user record on first login
  (default: false; convenient for single-user/local setups)
- ``FERMILINK_AUTH_MAX_USERS``: cap the number of accounts (``0`` means unlimited)
- ``FERMILINK_AUTH_MIN_PASSWORD_LEN``: minimum password length (default: 8)

To keep sessions stable across restarts, set a persistent secret:

.. code-block:: bash

   export FERMILINK_CHAINLIT_AUTH_SECRET="<random-secret>"

Package selection inside the UI
-------------------------------

FermiLink routes each message to an installed package (keyword router + optional
second-guess preflight). You can also pin a package for the current chat.

Use ``/package help`` in the UI for the built-in command list. The most common
commands are:

.. code-block:: text

   /package list
   /package current
   /package use meep
   /package auto on
   /package auto off
   /package clear

Notes:

- Auto routing is per-chat; it can switch packages between turns when enabled.
- Manual ``/package use ...`` pins always take precedence until you clear them.
- If you see "no packages", install one with ``fermilink install <id> --activate``.

Where your work lives
---------------------

Each chat thread is backed by one workspace repo:

- ``projects/memory.md``: unified short-term/long-term memory used across turns
- ``outputs/``: recommended default place for scripts, plots, and results
- ``projects/``: recommended place for multi-day research folders and reports

To find recent workspaces on disk:

.. code-block:: bash

   ls -t "$FERMILINK_WORKSPACES_ROOT" | head

Then inspect:

.. code-block:: bash

   ls "$FERMILINK_WORKSPACES_ROOT/<session_id>/repo"

Artifacts and transparency
--------------------------

Artifacts referenced in assistant output are auto-attached when they live under
common prefixes (for example ``outputs/`` and ``projects/``). Images are shown
inline; multiple files may be zipped when needed.

For a deterministic post-run disclosure (tool calls, commands, and file
changes), enable the optional transparency report:

.. code-block:: bash

   export FERMILINK_CHAINLIT_TRANSPARENCY_ENABLED=true

Web UI validation checklist
---------------------------

1. Open ``http://localhost:7860`` and sign up / sign in.
2. Run ``/package list`` to verify package visibility.
3. Send one prompt and confirm outputs/artifacts are returned.
4. If transparency is enabled, confirm the ``Transparency`` report appears.

Troubleshooting
---------------

- Runner fails to start: ensure ``codex`` is on ``PATH`` and authenticated.
- Web UI cannot reach runner in manual mode: verify ``FERMILINK_RUNNER_URL``.
- ``/package list`` is empty: install packages and confirm ``FERMILINK_SCIPKG_ROOT``.

See also:

- :doc:`installation` for full setup.
- :doc:`configuration` for runtime variables.
- :doc:`scientific_packages` for package install/compile/recompile workflows.
- :doc:`architecture` for request flow and streaming contracts.
