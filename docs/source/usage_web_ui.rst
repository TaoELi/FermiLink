Web UI
======

Use the web interface for ChatGPT-style interaction while preserving the same
routing, overlay, and policy behavior used by CLI modes.

Start and stop services
-----------------------

.. code-block:: bash

   fermilink start
   fermilink status
   fermilink stop

Manual startup (optional)
-------------------------

Use this when you want runner and Chainlit processes managed separately.

.. code-block:: bash

   uvicorn fermilink.runner.app:app --host 0.0.0.0 --port 8000
   FERMILINK_RUNNER_URL=http://127.0.0.1:8000 \
     chainlit run src/fermilink/web/app.py --host 0.0.0.0 --port 7860

Web UI validation checklist
---------------------------

1. Open ``http://localhost:7860`` and sign in if auth is enabled.
2. Run ``/package list`` to verify package visibility.
3. Send one prompt and confirm outputs/artifacts are returned.

See also:

- :doc:`installation` for full setup.
- :doc:`configuration` for runtime variables.
- :doc:`architecture` for request flow and streaming contracts.
