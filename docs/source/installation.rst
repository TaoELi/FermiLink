Installation
============

This page gets you from a clean environment to a working local FermiLink
deployment (CLI, Web UI, and Telegram gateway).

Fast path (recommended)
-----------------------

.. code-block:: bash

   # 1) Install FermiLink (from this repo checkout)
   pip install .

   # 2) Install and authenticate the provider CLI (Codex)
   npm i -g @openai/codex
   codex login
   export FERMILINK_CODEX_AUTH_MODE=login

   # 3) Install at least one scientific package (knowledge base)
   fermilink install meep --activate

   # 4) Start runner + web UI
   fermilink start

Then open ``http://localhost:7860``.

Prerequisites
-------------

- Python ``>= 3.11``
- ``git`` on ``PATH`` (workspaces are git repos)
- Node.js + ``npm`` (for installing Codex CLI)
- Codex CLI on ``PATH`` (``codex``)

Install Codex CLI example:

.. code-block:: bash

   npm i -g @openai/codex

Install FermiLink
-----------------

FermiLink is a Python package with a single CLI entrypoint: ``fermilink``.

From a source checkout:

.. code-block:: bash

   pip install .

For local development (editable install):

.. code-block:: bash

   pip install -e ".[dev]"

Provider authentication (Codex)
-------------------------------

FermiLink currently executes agent runs via the Codex CLI provider
(``codex exec``). Make sure ``codex`` is installed and authenticated before you
start services or run ``fermilink exec`` / ``fermilink chat``.

Browser login (recommended):

.. code-block:: bash

   codex login
   export FERMILINK_CODEX_AUTH_MODE=login

API-key auth (headless environments):

.. code-block:: bash

   export FERMILINK_OPENAI_API_KEY="<your-key>"

See :doc:`configuration` for more runtime/auth variables (including
``FERMILINK_CODEX_HOME``).

Recommended runtime roots
-------------------------

By default, FermiLink stores state under ``~/.fermilink``. For local development
or reproducible demos, isolate state inside your repo (or a dedicated folder).
This keeps generated files out of unrelated projects.

.. code-block:: bash

   export FERMILINK_HOME=./.fermilink
   export FERMILINK_SCIPKG_ROOT=$FERMILINK_HOME/scientific_packages
   export FERMILINK_WORKSPACES_ROOT=$FERMILINK_HOME/workspaces
   export FERMILINK_RUNTIME_ROOT=$FERMILINK_HOME/runtime
   export FERMILINK_CHAINLIT_APP_ROOT=$FERMILINK_HOME

Install your first scientific package
-------------------------------------

FermiLink selects and overlays an installed scientific package into each
workspace before executing an agent run. Install at least one package first:

.. code-block:: bash

   # discover packages in the default curated channel
   fermilink package avail meep

   # install one or more packages (default channel: skilled-scipkg)
   fermilink install meep --activate
   fermilink install maxwelllink

   # verify local registry
   fermilink package list

``--activate`` sets the default package for new sessions. You can also switch
later with ``fermilink package activate <package_id>`` or in the Web UI with
``/package use <package_id>``.

Agent runtime policy
--------------------

Policy is shared across web, runner, ``exec``, ``chat``, and compile commands.
The effective provider and sandbox settings are persisted under
``$FERMILINK_HOME/agent_runtime.json``.

.. code-block:: bash

   # show current policy
   fermilink agent --json

   # enforce sandbox mode
   fermilink agent --sandbox

   # bypass codex sandbox (host/container restrictions still apply)
   fermilink agent --bypass-sandbox

   # set provider
   fermilink agent codex

Start services (Web UI + runner)
--------------------------------

``fermilink start`` launches two local processes:

- ``runner``: FastAPI execution backend (default: port ``8000``)
- ``web``: Chainlit Web UI (default: port ``7860``)

.. code-block:: bash

   fermilink start
   fermilink status
   fermilink stop

Logs are written under ``$FERMILINK_RUNTIME_ROOT/logs/``.

To override host/port without editing code, set command overrides:

.. code-block:: bash

   export FERMILINK_RUNNER_CMD="uvicorn fermilink.runner.app:app --host 127.0.0.1 --port 8000"
   export FERMILINK_WEB_CMD="chainlit run src/fermilink/web/app.py --host 127.0.0.1 --port 7860"

Manual service startup (optional)
---------------------------------

Use this when you want to run web and runner directly without the service
manager wrapper.

.. code-block:: bash

   uvicorn fermilink.runner.app:app --host 127.0.0.1 --port 8000
   export FERMILINK_RUNNER_URL=http://127.0.0.1:8000
   chainlit run src/fermilink/web/app.py --host 127.0.0.1 --port 7860

Smoke check
-----------

After startup:

1. Open the UI at ``http://localhost:7860`` and sign in.
2. Run ``/package list`` to confirm package visibility.
3. Send one prompt and verify outputs/artifacts appear.
4. (Optional) confirm the workspace is created under
   ``$FERMILINK_WORKSPACES_ROOT/<session_id>/repo``.

Troubleshooting
---------------

- Web UI says no packages: run ``fermilink install <package_id> --activate``.
- Runner fails to start with a provider error: ensure ``codex`` is on ``PATH``
  and authenticated (``codex login`` or ``FERMILINK_OPENAI_API_KEY``).
- Ports already in use: stop the conflicting process or override
  ``FERMILINK_RUNNER_CMD`` / ``FERMILINK_WEB_CMD``.
- Web UI cannot reach runner in manual mode: verify ``FERMILINK_RUNNER_URL``
  matches the runner bind host/port.

Build documentation
-------------------

.. code-block:: bash

   pip install ".[docs]"
   make doc html
