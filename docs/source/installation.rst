Installation
============

This page covers all installation options. For the shortest path from zero to
running, see the :doc:`quickstart`.

- **Laptop / workstation users:** see also the :doc:`tutorial_laptop` for a
  hands-on walkthrough.
- **HPC users:** see also the :doc:`tutorial_hpc` for SLURM-specific setup.

Fast path 
------------

.. code-block:: bash

   # 1. Install FermiLink 
   pip install fermilink

   # 2. Start the guided beginner entrypoint
   fermilink

   # 3. Or, follow the manual setup path below:
   # Install and authenticate one supported agent provider CLI (Codex/Claude/Gemini)
   # For example, Codex option:
   npm i -g @openai/codex   # Use ``brew install codex`` for Mac
   codex login

   # 4. Install at least one scientific package
   fermilink install meep

   # 5.1. Command line execution (most powerful)
   fermilink exec/loop/research/reproduce "..."

   # 5.2. Start the web UI for chatgpt-like experience (laptops and workstations)
   fermilink start

   # 5.3. Connect to Telegram chatbot (suitable for HPC with no sudo access)
   export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token-from-@BotFather>"
   export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<numeric-id-from-@get_telegram_id_smppcenter_bot>"
   fermilink gateway

See also :doc:`usage`, :doc:`usage_web_ui`, and :doc:`usage_chatting_apps` for
details on each interface, or :doc:`choosing_agent` for provider comparisons.


Complete installation guide
-----------------------------

Below are **more detailed instructions for each step**, as well as some optional configurations for users with specific needs.

Prerequisites
~~~~~~~~~~~~~~~~

FermiLink assumes a standard local developer environment:

- Python ``>= 3.11``
- ``git`` on ``PATH`` (workspaces are git repos)
- Node.js + ``npm`` (commonly used for local agent provider CLIs) or ``homebrew`` installed for Mac
- Supported provider CLI on ``PATH``: Codex (``codex``) or Claude (``claude``) or Gemini (``gemini``)

.. note::

   For HPC users without sudo access, you need to install Node.js and ``npm`` locally first.

Install provider CLI (Codex or Claude or Gemini)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Codex option
   npm i -g @openai/codex  # Use ``brew install codex`` for Mac
   # install Claude / Gemini CLI from its official distribution

Provider authentication
~~~~~~~~~~~~~~~~~~~~~~~~~

Authenticate the provider you selected **before** starting web UI services or running
``fermilink exec/chat/loop/research/reproduce``.

Example login commands:

.. code-block:: bash

   # Codex
   codex login
   # Claude
   claude
   # Gemini
   gemini

Install FermiLink
~~~~~~~~~~~~~~~~~~

You can install FermiLink with pip:

.. code-block:: bash

   pip install fermilink



Install your first scientific package knowledge base
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

FermiLink routes each request to a scientific package knowledge base.
Install at least one package before your first run.

.. code-block:: bash

   # discover packages in the default curated channel (https://github.com/orgs/skilled-scipkg/repositories)
   fermilink avail meep

   # install one or more packages
   fermilink install meep --activate
   fermilink install lammps

   # verify locally installed packages
   fermilink list

``--activate`` sets the default package for new sessions. You can switch later
with ``fermilink activate <package_id>``. 

``fermilink install`` downloads the package *knowledge base* (source code tree
+ agent skills) -- it does **not** install the package for execution.
It is recommended that users have the actual software already installed on
their machines, but the agent can install packages on its own if needed.

Agent runtime policy
~~~~~~~~~~~~~~~~~~~~~~~

By default, agents run in a sandbox that restricts file-system and network
access. Some scientific simulations (e.g., MPI jobs, access to external data)
require bypassing the sandbox. You can do this per-provider:

.. code-block:: bash

   # show current policy
   fermilink agent --json

   # enforce sandbox mode (default), codex provider
   fermilink agent codex --sandbox --model gpt-5.3-codex --reasoning-effort xhigh

   # bypass sandbox (which might be needed for local MPI jobs)
   fermilink agent codex --bypass-sandbox --model gpt-5.3-codex --reasoning-effort xhigh

   # bypass sandbox for claude
   fermilink agent claude --bypass-sandbox --model sonnet --reasoning-effort high

.. warning::

   When ``fermilink agent --bypass-sandbox`` is needed for maximal functionality, **NEVER run it as a root user.** 


Developer mode
-------------------

Install from source if you want to modify FermiLink or contribute:

.. code-block:: bash

   git clone git@github.com:TaoELi/FermiLink.git
   cd FermiLink
   pip install -e ".[dev]"
