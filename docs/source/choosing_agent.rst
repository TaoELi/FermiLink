Choosing an AI Agent
====================

**FermiLink** delegates reasoning to an external AI agent provider. The global 
setting of the default agent for **FermiLink** is done with
``fermilink agent <provider>``.

Supported providers
-------------------

.. list-table::
   :header-rows: 1
   :widths: 15 20 35 30

   * - Provider
     - CLI tool
     - Install
     - Notes
   * - **Codex**
     - ``codex``
     - ``npm i -g @openai/codex`` or ``brew install codex``
     - OpenAI's coding agent. 
   * - **Claude**
     - ``claude``
     - See `Claude CLI docs <https://docs.anthropic.com/en/docs/claude-code>`_
     - Anthropic's Claude Code. 
   * - **Gemini**
     - ``gemini``
     - See `Gemini CLI docs <https://github.com/google-gemini/gemini-cli>`_
     - Google's Gemini CLI. 
   * - **OpenCode**
     - ``opencode``
     - See `OpenCode docs <https://opencode.ai/docs/>`_
     - Model-agnostic OpenCode CLI. Authenticate providers with
       ``opencode auth login`` and use model names in ``provider/model`` form.


Setting up your agent
---------------------

.. code-block:: bash

   # 1. Install the provider CLI (example: Codex)
   npm i -g @openai/codex

   # 2. Authenticate
   codex login

   # 3. Register with FermiLink
   fermilink agent codex

   # Switch providers later
   fermilink agent claude


How to choose
-------------

All providers work with all **FermiLink** workflows. The best choice depends on
your priorities:

**If you want the broadest compatibility:** Codex and Claude are the most tested providers, which give the richest interactive experience.

**If you want flexible model/provider routing:** OpenCode lets **FermiLink** use
any provider/model profile configured in OpenCode.

**If you use Google Cloud:** Gemini is a natural fit if you're already in the
Google ecosystem.


.. note::

   We are actively adding support for more providers. If you have a preferred agent provider that you want to see integrated, please let us know in the `GitHub discussions <https://github.com/TaoELi/FermiLink/issues>`_.


Advanced configuration
--------------------------

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

By default, **FermiLink** normalizes the reasoning effort setting for agents to the ``codex`` provider's reasoning effort levels (``low``, ``medium``, ``high``, ``xhigh``). 
