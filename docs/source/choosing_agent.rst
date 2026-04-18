Choosing an AI Agent
====================

FermiLink delegates reasoning to an external AI agent provider. You pick
a provider once with ``fermilink agent <provider>``, and FermiLink handles
all the integration details -- workspace aliases, environment variables,
streaming, and command translation.

Supported providers
-------------------

.. list-table::
   :header-rows: 1
   :widths: 18 20 20 42

   * - Provider
     - CLI tool
     - Install
     - Notes
   * - **Codex**
     - ``codex``
     - ``npm i -g @openai/codex`` or ``brew install codex``
     - OpenAI's coding agent. Uses direct TTY mode for interactive sessions.
   * - **Claude**
     - ``claude``
     - See `Claude CLI docs <https://docs.anthropic.com/en/docs/claude-code>`_
     - Anthropic's Claude. Pipe-backed streaming.
   * - **Gemini**
     - ``gemini``
     - See `Gemini CLI docs <https://github.com/google-gemini/gemini-cli>`_
     - Google's Gemini. Pipe-backed streaming.
   * - **DeepSeek**
     - ``deepseek``
     - See `DeepSeek docs <https://www.deepseek.com/>`_
     - DeepSeek models. Pipe-backed streaming.


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

All providers work with all FermiLink workflows. The best choice depends on
your priorities:

**If you want the broadest compatibility:** Codex is the most tested provider
and uses direct TTY mode, which gives the richest interactive experience.

**If you prefer Anthropic models:** Claude integrates well and supports
advanced reasoning. It uses pipe-backed streaming rather than direct TTY.

**If you use Google Cloud:** Gemini is a natural fit if you're already in the
Google ecosystem.

**For the optimize workflow:** Any provider works, but you can also set a
different provider for just the worker turns using
``--worker-provider`` / ``--worker-model``:

.. code-block:: bash

   # Use Claude for controller, Codex for worker
   fermilink optimize goal.md --worker-provider codex

Runtime policy
--------------

You can override the default provider, model, sandbox, and reasoning behavior
at runtime:

.. code-block:: bash

   # Override model
   FERMILINK_AGENT_MODEL=o3 fermilink loop goal.md

   # Override sandbox policy
   FERMILINK_AGENT_SANDBOX_POLICY=relaxed fermilink exec "..."

See :doc:`configuration` for the full list of runtime policy environment
variables.
