Installation
============

This page takes you from a clean machine to a working **local FermiLink**
deployment.

If you only want to “get it running”, follow the fast path. More details are introduced in later sections.

Fast path (recommended)
-----------------------

.. code-block:: bash

   # 1. Install FermiLink (from this repo root)
   pip install .

   # 2. Install and authenticate the fundamental model (Codex)
   npm i -g @openai/codex   # Use ``brew install codex`` for Mac
   codex login

   # 3. Install at least one scientific package (knowledge base)
   fermilink install meep --activate

   # 4.1. Start the web UI
   fermilink start

   # 4.2. Command line execution
   fermilink exec/loop/research/reproduce "..."

   # 4.3. Connect to Telegram chatbot
   export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token-from-@BotFather>"
   export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<numeric-id-from-@get_telegram_id_smppcenter_bot>"
   fermilink gateway


Prerequisites
-------------

FermiLink assumes a standard local developer environment:

- Python ``>= 3.11``
- ``git`` on ``PATH`` (workspaces are git repos)
- Node.js + ``npm`` (to install the Codex CLI) or ``homebrew`` installed for Mac
- Codex CLI (``codex``) on ``PATH`` 

Install Codex CLI example:

.. code-block:: bash

   npm i -g @openai/codex  # Use ``brew install codex`` for Mac

Install FermiLink
-----------------

FermiLink is a Python package with a single CLI entrypoint: ``fermilink``.

From a source checkout:

.. code-block:: bash

   pip install .

Provider authentication (Codex)
-------------------------------

FermiLink currently executes agent runs via the Codex CLI provider
(``codex exec``). Authenticate *before* starting web UI services or running 
``fermilink exec/chat/loop/research/reproduce``.

Login Codex using OpenAI credentials:

.. code-block:: bash

   codex login

Install your first scientific package
-------------------------------------

Before an agent run, FermiLink selects a scientific package into
the workspace (default path: ``~/.fermilink/scientific_packages/``) based on user's request. This requires to install at least one package first.

.. code-block:: bash

   # discover packages in the default curated channel (Github: skilled-scipkg)
   fermilink avail meep

   # install one or more packages
   fermilink install meep --activate
   fermilink install lammps

   # verify locally installed packages
   fermilink list

``--activate`` sets the default package for new sessions. You can switch later
with ``fermilink activate <package_id>``. 

Note that ``fermilink install`` only downloads 
the package knowledge base (source code tree + agent skills) to ``FERMILINK_SCIPKG_ROOT`` but 
**does not really install the package for execution**. It is recommended that the users have already installed the packages
in their machines, but agents can install the packages by themselves if they find the relavent package is not installed.

Agent runtime policy
--------------------

Runtime policy is shared across the Web UI, Telegram gateway, and command line commands  ``exec/chat/loop/research/reproduce``. The effective provider and sandbox settings are persisted
under ``$FERMILINK_HOME/agent_runtime.json``.

.. code-block:: bash

   # show current policy
   fermilink agent --json

   # enforce sandbox mode (default)
   fermilink agent --sandbox

   # bypass codex sandbox (which might be needed for local MPI jobs)
   fermilink agent --bypass-sandbox

   # set provider (currently supports only codex)
   fermilink agent codex


Build documentation
-------------------

.. code-block:: bash

   pip install ".[docs]"
   make doc html

Developer mode
-------------------

For local development (editable install):

.. code-block:: bash

   pip install -e ".[dev]"