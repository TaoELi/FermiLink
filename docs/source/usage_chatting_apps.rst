Chatting Apps
=============

FermiLink supports remote control via chatting apps through the Telegram
gateway. This gives you an "agent in your pocket" workflow: you chat on your
phone, but the actual computation happens on the machine running
``fermilink gateway`` (your laptop/workstation/HPC login node).

Unlike the Web UI (which streams per-turn runs), the gateway is optimized for
remote control: it acks queued requests quickly and sends a final completion
message (plus files/figures) when a run finishes.

Telegram gateway quick start
----------------------------

.. code-block:: bash

   export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<bot-token>"
   export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="123456789"  # optional allowlist
   fermilink gateway

Step-by-step setup (phone + computer)
-------------------------------------

1. Create a Telegram bot (phone): open ``@BotFather`` in Telegram, run
   ``/newbot``, and copy the bot token.
2. Get your numeric Telegram user id (phone): message ``@userinfobot`` and copy
   the returned ``Id`` value.
3. Prepare the computer where work will run:

   .. code-block:: bash

      pip install .
      npm i -g @openai/codex
      codex login
      export FERMILINK_CODEX_AUTH_MODE=login
      fermilink install meep --activate

4. Export gateway environment variables on the computer:

   .. code-block:: bash

      export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token-from-botfather>"
      export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<numeric-id-from-userinfobot>"

5. Start the gateway:

   .. code-block:: bash

      fermilink gateway

6. From Telegram (phone), open a chat with your bot and run ``/help``.

Using the bot
-------------

The gateway keeps each Telegram chat bound to a sticky workspace repo and
supports multiple run modes.

Core commands:

- ``/help``: show the command list.
- ``/mode <exec|loop|research|reproduce>``: switch the default run mode for
  normal messages in this chat.
- ``/status``: show current gateway/chat state (mode, workspace, run status).
- ``/stop``: stop the active run and clear queued runs for this chat.
- ``/loopcfg``: show or update loop controls (max iterations + wait caps).
- ``/reply <summary|agent|both>``: control the completion message style.
- ``/new [name]``: create and switch to a new workspace.
- ``/use <name-or-id>``, ``/where``, ``/list``: manage and inspect workspaces.

Practical starting pattern:

1. Start in ``exec`` mode for quick one-turn runs.
2. Switch to ``loop`` mode when you need autonomous iteration and waiting for
   local PID or HPC SLURM jobs.
3. Use ``research`` / ``reproduce`` when you want a planner + auditor workflow
   that produces a final report artifact.

File uploads
------------

Send a Telegram **document** or **photo** to the bot:

- the file is downloaded into the active workspace under ``telegram_uploads/``;
- optional caption text is treated as the run message and automatically includes
  uploaded file paths in the prompt context;
- if you send an upload with no text/caption, the gateway confirms the upload
  only.

Workspace model and where files live
------------------------------------

Each Telegram chat maps to one or more workspaces under
``$FERMILINK_WORKSPACES_ROOT``. Each workspace contains a ``repo/`` directory
with ``projects/memory.md``, ``outputs/``, and any uploaded inputs.

Gateway session mappings are stored at:

- ``$FERMILINK_RUNTIME_ROOT/chat_sessions.json``

Troubleshooting
---------------

- ``Access denied.``: your account is not in the allowlist; update
  ``FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM`` and restart the gateway.
- Missing outputs/figures: ask the agent to write files under ``outputs/`` or
  ``projects/`` so they can be detected and attached.
- Runs fail immediately: verify Codex authentication (``codex login`` or API
  key) on the machine running the gateway.

Behavior summary
----------------

- each chat is bound to a sticky workspace repo;
- default run mode is ``exec`` (switch via ``/mode``);
- supports ``exec``, ``loop``, ``research``, and ``reproduce`` workflows;
- supports inbound file uploads into workspace-local ``telegram_uploads/``;
- supports per-chat run control (``/stop``, ``/loopcfg``, ``/status``).

For the full Telegram command/reference details, see
:ref:`usage-cli-telegram` in :doc:`usage`.
