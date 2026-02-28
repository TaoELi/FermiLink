HPC Tutorial
===============

This tutorial shows how to run FermiLink smoothly on a typical SLURM-based HPC
cluster without sudo access. It is designed to be **self-contained**, so you can
follow it end-to-end without reading other pages. At the end, we also point to
more detailed references if you want them.

What you will set up:

- A user-local Python + Node.js environment
- FermiLink + Codex CLI authentication
- One or more scientific packages (knowledge bases)
- An ``hpc_profile.json`` so FermiLink submits and monitors SLURM jobs
- Example runs with ``exec``, ``loop``, ``research``, and ``reproduce``
- Optional Telegram remote control


Prerequisites
~~~~~~~~~~~~~~~~

You need the following available on the cluster (all can be user-local):

- Python ``>= 3.11``
- ``git`` on ``PATH`` (workspaces are git repos)
- Node.js + ``npm`` (for the Codex CLI)
- SLURM client tools (``sbatch``, ``squeue``, ``sacct``) if you plan to submit jobs

.. note::

   If your cluster does not provide Node.js, install it locally (user space) or
   use your site’s module system. No sudo access is required.


Step 1. Choose working and runtime locations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

FermiLink stores packages, workspaces, and logs under ``FERMILINK_HOME``
(default ``~/.fermilink``). On HPC, it is often better to use a scratch or
project filesystem to avoid home-quota issues.

The most significant storage is for workspaces, which might generate large runtime simulation data, so it is better to place this workspaces directory at scratch. You can set these environment variables in your ``.bashrc``:

.. code-block:: bash

   # ~/.bashrc
   # Example: keep FermiLink runtime in a project filesystem
   export FERMILINK_HOME="$PROJECT/.fermilink/"
   # Example: also keep the simulation workspaces in scratch 
   export FERMILINK_WORKSPACES_ROOT="$SCRATCH/fermilink/workspaces"

Step 2. Install Codex CLI and authenticate
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

FermiLink runs agents through the Codex CLI. Install it once and login:

.. code-block:: bash

   npm i -g @openai/codex
   codex login

If ``codex`` is not found after install, ensure your local ``npm`` bin directory
is on ``PATH``.

Within the ``codex`` terminal, choose the default model (e.g., gpt-5.3-codex) you want to use for FermiLink.


Step 3. Install FermiLink
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   git clone https:://github.com/TaoELi/FermiLink.git
   cd FermiLink/
   pip install .

If you are working from a cloned repo, this installs the CLI and all runtime
dependencies into your current Python environment.


Step 4. Install at least one scientific package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

FermiLink routes each run to an installed package knowledge base. Install at
least one package before you run anything:

.. code-block:: bash

   # discover packages in the default curated channel
   fermilink avail maxwelllink

   # install and set a default package for new sessions
   fermilink install maxwelllink --activate

   # verify installed packages
   fermilink list

   # also really install this package for simulation
   pip install maxwelllink

.. note::

   ``fermilink install`` downloads **knowledge bases** (source + skills) into
   ``$FERMILINK_HOME/scientific_packages``. It does not install the underlying
   simulator. Make sure the actual solver (e.g., Meep, LAMMPS) is installed in
   your environment or available via modules. 

   Of course, agent can install this package for you if it finds it is not installed, but it is better to have it ready beforehand for smoother runs.


Step 5. Set agent runtime policy (sandbox)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, FermiLink runs in a restricted sandbox. Some simulations or local
MPI workflows may need to bypass it. Check and set the policy if needed:

.. code-block:: bash

   # show current policy
   fermilink agent --json

   # enforce sandbox (default)
   fermilink agent --sandbox

   # bypass sandbox for local MPI jobs (not needed for SLURM MPI jobs)
   fermilink agent --bypass-sandbox

.. warning::

   If you bypass the sandbox, **never** run as root. Use a dedicated non-root
   account and keep regular backups of your data.


If you need to force one model for all FermiLink runs, set a global override:

.. code-block:: bash

   fermilink agent --model gpt-5.3-codex

Clear it later to return to Codex default model selection:

.. code-block:: bash

   fermilink agent --clear-model

If you need to force Codex reasoning effort globally across
``exec/chat/loop/research/reproduce/web``, set:

.. code-block:: bash

   fermilink agent --reasoning-effort high

Clear it later to return to Codex default reasoning effort:

.. code-block:: bash

   fermilink agent --clear-reasoning-effort




Step 6. Create an ``hpc_profile.json``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The HPC profile tells FermiLink how to request SLURM resources and what
resource policy to follow. Start with this minimal template:

.. code-block:: json

   {
      "slurm_default_partition": "shared",
      "slurm_defaults": "--nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=1 --time=24:00:00",
      "slurm_resource_policy": "Use serial/single-node defaults unless the method explicitly requires MPI or multi-node scaling"
   }

You can also copy the sample at FermiLink repo ``scripts/hpc_profile_anvil.json`` and edit it
for your site. Update the partition name and any defaults your cluster requires
(e.g., account, QoS, time limits).

It is safe to create this file in your home directory (``$HOME/hpc_profile.json``).


Step 7. Hello world on HPC (``exec``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run a single prompt in a clean project directory.

.. code-block:: bash

   mkdir -p run_em_demo
   cd run_em_demo

   # one-shot execution with an HPC profile
   fermilink exec "run a single two-level system coupled to a single-mode cavity" \
     --hpc-profile "$HOME/hpc_profile.json" \
     --init-git

What ``exec`` does:

- routes your prompt to the best installed package
- overlays the package knowledge base into the current repo
- initializes or updates ``projects/memory.md``
- submits and monitors SLURM jobs when ``--hpc-profile`` is provided

.. note::

   The interactive ``fermilink chat`` mode does **not** support ``--hpc-profile``.
   For SLURM runs, use ``exec``, ``loop``, ``research``, or ``reproduce``.


Step 8. Long-running jobs with ``loop``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``loop`` for iterative workflows with long SLURM jobs. It waits for job
completion and can run multiple iterations until the goal is reached.

.. code-block:: bash

   fermilink loop goal.md \
     --hpc-profile "$HOME/hpc_profile.json" \
     --max-iterations 10 \
     --max-wait-seconds 7200 \
     --init-git

``loop`` recognizes ``<slurm_job_number>...</slurm_job_number>`` tags and polls
SLURM until completion, up to the ``--max-wait-seconds`` limit.


Step 9. Full workflows (``research`` and ``reproduce``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use these when you want a complete research-style workflow with planning,
execution, and a final report. Note that these two modes are very expensive to run. **Always try 
with ``exec`` or ``loop`` first** to debug your prompt and HPC settings before you run these full workflows.

.. code-block:: bash

   # start from an idea
   fermilink research idea.md --hpc-profile "$HOME/hpc_profile.json" --init-git

   # reproduce a paper
   fermilink reproduce paper.tex --hpc-profile "$HOME/hpc_profile.json" --init-git

Artifacts are written under:

- ``projects/research/<run-id>/``
- ``projects/reproduce/<run-id>/``

Each workflow also writes helper scripts (for example ``00_run_all.sh``) inside
the run directory for staged or re-run execution.


Step 10. Submit FermiLink as a SLURM job
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If your site discourages long runs on login nodes, submit FermiLink itself as a
batch job. Create ``fermilink_job.sh``:

.. code-block:: bash

   #!/bin/bash
   #SBATCH --job-name=fermilink
   #SBATCH --partition=shared
   #SBATCH --time=24:00:00
   #SBATCH --nodes=1
   #SBATCH --ntasks=1
   #SBATCH --cpus-per-task=1
   #SBATCH --output=fermilink-%j.out

   fermilink exec "run a single two-level system coupled to a single-mode cavity" \
     --hpc-profile "$HOME/hpc_profile.json" \
     --init-git

Submit it with:

.. code-block:: bash

   sbatch fermilink_job.sh

Adjust the ``#SBATCH`` lines to match your site’s partition, account, or QoS
requirements.


Step 11. Compile / recompile your own package
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

At this early stage, it is likely that you want to add your own package or pipeline to FermiLink. Use ``fermilink compile`` to turn a local project into a package knowledge base, and use ``fermilink recompile`` to update it after you add more skills or files.

See :doc:`usage_configure_your_package` and :doc:`usage_advanced_configuration` for details on how to compile/recompile your package and convert research pipelines or memory suggestions into package knowledge.

Alternatively, you can also send an email to the FermiLink team (taoeli@udel.edu) with your open-source package or pipeline, and we can help compile it into the curated channel for easy installation and use by the community.


Step 12. Optional: Telegram remote control for HPC
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The Telegram gateway is a convenient remote control when you want to queue jobs
from your phone while the cluster runs them.

You can start the gateway on the cluster with in the login node for testing:

.. code-block:: bash

   export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token-from-@BotFather>"
   export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<numeric-id-from-@get_telegram_id_smppcenter_bot>"

   fermilink gateway --max-wait-seconds 6000 --max-iterations 10 \
     --hpc-profile "$HOME/hpc_profile.json"

Once the gateway is running, chat with your bot and use ``/list`` or ``/mode`` to
start sending jobs. 

.. note::

   Read :doc:`usage_chatting_apps` for the full Telegram gateway guide and more details about flags and usage tips.

Then, if everything works, you can submit the gateway itself as a long-running SLRUM job (1 CPU) so it can accept commands whenever you need it.

.. code-block:: bash

   #!/bin/bash
   #SBATCH --job-name=fermilink_gateway
   #SBATCH --partition=shared
   #SBATCH --time=4-00:00:00
   #SBATCH --nodes=1
   #SBATCH --ntasks=1
   #SBATCH --cpus-per-task=1
   #SBATCH --output=fermilink-%j.out

   export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token-from-@BotFather>"
   export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<numeric-id-from-@get_telegram_id_smppcenter_bot>"

   fermilink gateway --max-wait-seconds 6000 --max-iterations 10 \
     --hpc-profile "$HOME/hpc_profile.json"

Even better, if you want **multiple bots working for you simultaneously for different tasks**, you can create multiple gateway jobs with different bot tokens and user restrictions.

.. code-block:: bash

   #!/bin/bash
   #SBATCH --job-name=gateway_lammps
   #SBATCH --partition=shared
   #SBATCH --time=4-00:00:00
   #SBATCH --nodes=1
   #SBATCH --ntasks=1
   #SBATCH --cpus-per-task=1
   #SBATCH --output=fermilink-%j.out

    FERMILINK_WORKSPACES_ROOT=$SCRATCH/fermilink/workspaces_lammps \
    fermilink gateway --telegram-token "xxxx" \
        --session-store $FERMILINK_HOME/runtime/chat_sessions_lammps.json \
        --max-iterations 30 --max-wait-seconds 36000 \
        --hpc-profile $HOME/hpc_profile.json

The above SLURM script starts a gateway for LAMMPS-related jobs with a specific Telegram bot token, separate session store, and workspace root. You can create similar scripts for different packages or projects. 

.. note::

    It is suggested for each independent bot, we assign a different workspace (`FERMILINK_WORKSPACES_ROOT`) and session for it.

Where your data lives
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, FermiLink stores runtime data under ``$FERMILINK_HOME``:

- ``scientific_packages/``: installed package knowledge bases
- ``runtime/logs/``: service and gateway logs
- ``projects/memory.md``: unified memory file for each workspace

In this tutorial, we have also set ``FERMILINK_WORKSPACES_ROOT`` to a scratch location for better performance and larger storage, so all session workspaces and project repos will be stored there.


Troubleshooting quick checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Codex not found**: confirm ``npm`` install and PATH, then run ``codex login``.
- **Jobs run locally instead of SLURM**: ensure you passed ``--hpc-profile`` and
  the JSON file path is correct.
- **``sbatch`` not found**: you are not on a SLURM-enabled node or SLURM tools
  are not on ``PATH``.
- **Permission or quota errors**: set ``FERMILINK_HOME`` to scratch or project
  storage.
- **Simulation package missing**: install the solver package or load the
  appropriate module; FermiLink only installs the knowledge base.



Important tips for the prompts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A high-quality prompt is essential for good results. Here are some tips:

- **Be specific about the system, method, goal, and plotting requirement**. For example, instead of
  "simulate a cavity system", say "simulate a weakly excited two-level atom coupled to a
  single-mode cavity and plot the population dynamics".

- If you want to use a **specific package, mention this package in the prompt**.

- If one task should use different HPC resources than the setting in ``$HOME/hpc_profile.json``, **specify the HPC constraints in the prompt**. For example, "simulate a large system with 4 nodes and 16 tasks per node using LAMMPS".

- Prefer using **markdown file as the prompt input file**, which can provide better formatting and readability for complex prompts. For example, you can create a file named ``goal.md`` with the following content:

.. code-block:: markdown

   # Simulation Goal

   Use the maxwelllink package to simulate a weakly excited two-level atom coupled to a classical single-mode cavity and plot the Rabi splitting spectrum using the photonic coordinate.

   ## Deliverables

   - Plot a single panel figure showing the population of the excited state as a function of time with publication quality.

   ## HPC Constraints

   Use 1 node with 1 task for this simulation.

Then run:

.. code-block:: bash

    fermilink exec goal.md --hpc-profile "$HOME/hpc_profile.json" --init-git


Further reading (optional)
----------------------------

If you want more details, these pages go deeper:

- :doc:`installation` for full setup and sandbox policy background.
- :doc:`usage` for the complete CLI reference and mode-specific flags.
- :doc:`configuration` for environment variables and runtime paths.
- :doc:`usage_chatting_apps` for the full Telegram gateway guide.
- :doc:`scientific_packages` and :doc:`usage_configure_your_package` if you want
  to install or customize your own packages.
- :doc:`usage_advanced_configuration` for reusable research pipelines and
  memory-driven skill updates.
