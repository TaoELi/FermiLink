Installation
============

This page gets you from a clean environment to a working local FermiLink
deployment.

Fast path (recommended)
-----------------------

.. code-block:: bash

   pip install .
   codex login
   fermilink install maxwelllink --activate
   fermilink agent --sandbox
   fermilink start

Then open ``http://localhost:7860``.

Prerequisites
-------------

- Python ``>= 3.11``
- ``git`` on ``PATH``
- Codex CLI on ``PATH`` (``codex``)

Install Codex CLI example:

.. code-block:: bash

   npm i -g @openai/codex

Recommended runtime roots
-------------------------

For local development, isolate runtime state inside your repo or a dedicated
folder. This keeps generated files out of unrelated projects.

.. code-block:: bash

   export FERMILINK_HOME=./.fermilink
   export FERMILINK_SCIPKG_ROOT=./.fermilink/scientific_packages
   export FERMILINK_WORKSPACES_ROOT=./.fermilink/workspaces
   export FERMILINK_RUNTIME_ROOT=./.fermilink/runtime
   export FERMILINK_CHAINLIT_APP_ROOT=./.fermilink

Agent runtime policy
--------------------

Policy is shared across web, runner, ``exec``, ``chat``, and compile commands.

.. code-block:: bash

   # show current policy
   fermilink agent --json

   # enforce sandbox mode
   fermilink agent --sandbox

   # bypass codex sandbox (host/container restrictions still apply)
   fermilink agent --bypass-sandbox

   # set provider
   fermilink agent codex

Manual service startup (optional)
---------------------------------

Use this when you want to run web and runner directly without the service
manager wrapper.

.. code-block:: bash

   uvicorn fermilink.runner.app:app --host 0.0.0.0 --port 8000
   FERMILINK_RUNNER_URL=http://127.0.0.1:8000 \
     chainlit run src/fermilink/web/app.py --host 0.0.0.0 --port 7860

Smoke check
-----------

After startup:

1. Open the UI and sign in (if auth is enabled).
2. Run ``/package list`` to confirm package registry visibility.
3. Send one prompt and verify artifacts/outputs appear.

Build documentation
-------------------

.. code-block:: bash

   pip install ".[docs]"
   make doc html
