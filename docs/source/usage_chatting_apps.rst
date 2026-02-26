Chatting Apps
=============

FermiLink supports remote control via chatting apps through the Telegram
gateway.

Telegram gateway quick start
----------------------------

.. code-block:: bash

   export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<bot-token>"
   export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="123456789"
   fermilink gateway

Behavior summary
----------------

- each chat is bound to a sticky workspace repo;
- default run mode is ``exec`` (switch via ``/mode``);
- supports ``exec``, ``loop``, ``research``, and ``reproduce`` workflows;
- supports inbound file uploads into workspace-local ``telegram_uploads/``;
- supports per-chat run control (``/stop``, ``/loopcfg``, ``/status``).

For the full Telegram command/reference details, see
:ref:`usage-cli-telegram` in :doc:`usage`.
