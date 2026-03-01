Laptop Tutorial
===============

This tutorial shows how to run FermiLink locally on a laptop/workstation
(macOS/Linux) so you can quickly test autonomous scientific workflows. It is
designed to be **self-contained**, so you can follow it end-to-end without
reading other pages.

This tutorial uses the default FermiLink runtime location:

- ``~/.fermilink`` (no FermiLink environment variables required)

What you will set up:

- A local Python environment
- Codex CLI authentication
- FermiLink installation
- One scientific package knowledge base (example: ``qutip``)
- Example runs with ``exec``, ``chat``, and the Web UI (``start``)


Prerequisites
~~~~~~~~~~~~~~~~

You need:

- Python ``>= 3.11``
- ``git`` on ``PATH`` (workspaces are git repos)
- Node.js + ``npm`` (or Homebrew on macOS) for the Codex CLI

.. note::

   If you want a different runtime location later, see :doc:`configuration`. This tutorial intentionally sticks to the
   defaults for a quick laptop use.


Step 1. Create a clean Python environment (recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use conda environment so your laptop test does not modify your system Python:

.. code-block:: bash

   conda create -n fermilink-laptop python=3.11 -y
   conda activate fermilink-laptop


Step 2. Install Codex CLI and authenticate
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

FermiLink runs agents through the Codex CLI. Install it once and login:

.. code-block:: bash

   npm i -g @openai/codex
   codex login

.. note::

   On macOS, you can also install the Codex CLI via Homebrew (if preferred)::

     brew install codex

If ``codex`` is not found after install, ensure your local ``npm`` bin directory
is on ``PATH``.

Within the ``codex`` terminal, choose the default model you want to use for
FermiLink (for example ``gpt-5.3-codex``).


Step 3. Install FermiLink
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Clone the repo and install the CLI into your active Python environment:

.. code-block:: bash

   mkdir -p ~/fermilink_laptop_demo
   cd ~/fermilink_laptop_demo
   git clone https://github.com/TaoELi/FermiLink.git
   cd FermiLink
   pip install .

Quick check:

.. code-block:: bash

   fermilink --help


Step 4. Install a scientific package knowledge base
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

FermiLink routes each user request to an installed scientific package knowledge base, so
**install at least one package** before you run anything.

This laptop tutorial uses ``qutip`` because it runs well locally and is a good
fit for small "hello world" quantum simulations:

.. code-block:: bash

   # discover packages in the default curated channel
   fermilink avail qutip

   # install and set a default package for new sessions
   fermilink install qutip --activate

   # verify installed packages
   fermilink list

.. note::

   ``fermilink install`` downloads the **knowledge base** (source + skills) into
   ``~/.fermilink/scientific_packages``. It does not necessarily install the
   underlying runtime library used for execution.

Install the runtime Python package too:

.. code-block:: bash

   pip install qutip matplotlib

If you want to use a different scientific package, see the built-in catalog:
:doc:`built_in_scientific_packages`.


Step 5. Set agent runtime policy (sandbox)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, FermiLink runs in a restricted sandbox. For a first laptop test,
the default is recommended.

.. code-block:: bash

   # show current policy
   fermilink agent --json

   # enforce sandbox mode (default)
   fermilink agent codex --sandbox --model gpt-5.3-codex --reasoning-effort xhigh

.. warning::

   If you bypass the sandbox (i.e., replacing ``--sandbox`` with ``--bypass-sandbox``), **never** run as root. Use a dedicated non-root
   account and keep regular backups of your data.


Step 6. Hello world on a laptop (``exec``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run a single prompt in a clean project directory.

.. code-block:: bash

   mkdir -p ~/fermilink_laptop_demo/run_qutip_demo
   cd ~/fermilink_laptop_demo/run_qutip_demo

   fermilink exec "Use qutip to simulate the Jaynes-Cummings model (two-level system + single cavity mode). Plot the excited-state population vs time." 

What ``exec`` does:

- overlays the selected package knowledge base into the current repo
- initializes or updates ``projects/memory.md``
- runs the agent locally (no SLURM / no ``--hpc-profile``)

If ``--init-git`` is provided, FermiLink will skipping prompt you to initialize a git repo for better memory management. You can also use ``--no-init-git`` to skip this step and run without git.


Step 7. Interactive terminal chat (``chat``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``chat`` for multi-turn interaction in the terminal:

.. code-block:: bash

   cd ~/fermilink_laptop_demo/run_qutip_demo
   fermilink chat

Then ask follow-ups like:

.. code-block:: text

   For the previous simulations, refine the plot styling with Nature publication quality and save the figure as jc_population_refined.png.

As this is run in the same workspace as the previous ``exec`` run, the agent can refer to the previous context and files in the repo using the shared memory at ``projects/memory.md``.


Step 8. Web UI on a laptop (``start``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you prefer a ChatGPT-style interface, start the local runner + web UI:

.. code-block:: bash

   fermilink start

Then open ``http://localhost:7860``.


Step 9. Longer local runs (optional)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

On a laptop, you can still use longer-running modes (they run locally by
default):

.. code-block:: bash

   cd ~/fermilink_laptop_demo/run_qutip_demo
   fermilink loop goal.md --max-iterations 5 --max-wait-seconds 3600

.. note::

   ``research`` and ``reproduce`` are expensive. Prefer ``exec``/``loop`` first
   to debug your prompt and environment.


Where your data lives
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, FermiLink stores runtime data under ``~/.fermilink``:

- ``scientific_packages/``: installed package knowledge bases
- ``workspaces/``: per-session workspaces (web UI, workflows, some CLI runs)
- ``runtime/logs/``: service logs (runner/web/gateway)


Troubleshooting quick checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Codex not found**: confirm ``npm`` install and PATH, then run ``codex login``.
- **No packages installed**: run ``fermilink install <package_id> --activate``,
  then verify with ``fermilink list``.
- **Runtime package missing**: install the underlying package (for example
  ``pip install qutip``); FermiLink only installs the knowledge base.


Further reading (optional)
----------------------------

- :doc:`installation` for installation details.
- :doc:`usage` for CLI modes and flags.
- :doc:`scientific_packages` for package management and dependencies.
- :doc:`tutorial_hpc` for SLURM-based HPC workflows.

