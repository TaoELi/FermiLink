Chat Apps
=========

FermiLink supports remote control via chat apps through the Telegram gateway.
This gives you an "agent in your pocket" workflow: you chat on your phone, but
the actual computation happens on the machine running ``fermilink gateway``
(your laptop/workstation/HPC).

Unlike the Web UI (which streams per-turn runs), the gateway is optimized for
remote control: it acks queued requests quickly and sends a final completion
message (plus files/figures) when a run finishes. It never bothers users with internal thinking.

Step-by-Step Setup
-----------------------

If you have already completed :doc:`installation`, this is the only workflow you
need to get the Telegram bot working.

1. Open your Telegram app, search for ``@BotFather``, and type in ``/start`` and then ``/newbot``.
Follow the instructions to provide the username of the bot and **copy the provided bot token**.

2. In the Telegram app, search for ``@get_telegram_id_smppcenter_bot``, and type in  ``/start``. Copy
the provided **numerical User ID**.

3. Open the terminal of the machine you runs Fermilink,

.. code-block:: bash

   export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token-from-@BotFather>"
   # optional: restrict which Telegram accounts can talk to the bot
   export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<numeric-id-from-@get_telegram_id_smppcenter_bot>"
   fermilink gateway

4. Then open Telegram on your phone, chat with your bot, and run:

.. code-block:: text

   /list

If you see the reply, the gateway is ready.

What ``fermilink gateway`` launches
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``fermilink gateway`` starts one long-running local process that:

- listens for Telegram messages sent to your bot;
- queues requests quickly (so you can disconnect from the network and come back);
- runs the requested workflow (``exec``/``loop``/``research``/``reproduce``) on
  the machine where the gateway is running;
- replies with the final completion message and attaches generated artifacts.

Logs are written under ``$FERMILINK_RUNTIME_ROOT/logs/``.


Using the bot
-------------

The gateway keeps each Telegram chat bound to a sticky workspace repo and
supports multiple run modes.

Core commands:

- ``/new [name]``: create and switch to a new workspace.
- ``/mode <exec|loop|research|reproduce>``: set the default run mode for normal
  messages in this chat.
- ``/status``: show current gateway/chat state (mode, workspace, run status).
- ``/stop``: stop the active run and clear queued runs for this chat.
- ``/loopcfg``: show or update loop controls (max iterations + wait caps).
- ``/reply <summary|agent|both>``: control the completion message style.
- ``/use <name-or-id>``, ``/where``, ``/list``: manage and inspect workspaces.

Practical starting pattern:

1. Start in ``exec`` mode (default) for quick one-turn runs.
2. Switch to ``loop`` mode when you need autonomous iteration and waiting for
   local PID or HPC SLURM jobs.
3. Use ``research`` / ``reproduce`` when you want a planner + auditor workflow
   that produces a final report artifact at a research paper scale.

Where your work lives
~~~~~~~~~~~~~~~~~~~~~

Each Telegram chat maps to one workspace repo under::

  $FERMILINK_WORKSPACES_ROOT/<session_id>/repo

The default ``$FERMILINK_WORKSPACES_ROOT`` path is ``~/.fermilink/``.

Inside that repo you will typically see:

- ``projects/memory.md``: unified short-term/long-term memory for this chat
- ``projects/``: recommended place for research folders and reports
- ``telegram_uploads/``: files you send to the bot (see below)

To find recent workspaces on disk:

.. code-block:: bash

   FERMILINK_WORKSPACES_ROOT=$HOME/.fermilink/workspaces
   ls -t "$FERMILINK_WORKSPACES_ROOT" | head

Then inspect one session:

.. code-block:: bash

   ls "$FERMILINK_WORKSPACES_ROOT/<session_id>/repo"

File uploads
------------

Send a Telegram **document** or **photo** to the bot:

- the file is downloaded into the active workspace under ``telegram_uploads/``;
- optional caption text is treated as the run message;
- if you send an upload with no text/caption, the gateway confirms the upload
  only.

Troubleshooting (common first-run issues)
-----------------------------------------

- **Access denied.**: your account is not in the allowlist; update
  ``FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM`` and restart the gateway.
- **Runs fail immediately**: verify Codex authentication (``codex login`` or API
  key) on the machine running the gateway.
- **Missing outputs/figures**: ask the agent to write files under ``outputs/`` or
  ``projects/`` so they can be detected and attached.

See also
--------

- :doc:`installation` for full setup.
- :doc:`configuration` for runtime variables.
- :doc:`architecture` for request flow and workspace contracts.
- For the full Telegram command reference, see :ref:`usage-cli-telegram` in
  :doc:`usage`.