How FermiLink Works
===================

You write a ``goal.md`` -- a plain markdown file describing what you want to
compute. FermiLink reads it, selects the right scientific tools, generates
input files, submits jobs, monitors progress, checks results, and iterates
until the goal is met. The same workflow runs on your laptop, your lab's
workstation, or an HPC cluster.

This page explains the key ideas behind FermiLink. If you just want to start
using it, see the :doc:`quickstart`.


The goal.md interface
---------------------

Every FermiLink session starts with a goal -- either typed at the command line
or written in a ``goal.md`` file. The goal describes *what* to compute, not
*how*. FermiLink figures out the how:

.. code-block:: bash

   # Pass a goal file
   fermilink loop goal.md

   # Or type directly
   fermilink exec "Compute the phonon dispersion of silicon using Quantum ESPRESSO"

For longer or multi-step work, writing a ``goal.md`` file is recommended.
See :doc:`writing_goal_md` for the full format guide.


Three autonomous workflows
---------------------------

.. figure:: _static/img/major_modes_workflow.svg
   :alt: Three major FermiLink workflows: exec for single runs, loop for iterative runs involving long SLURM or PID jobs, and research/reproduce for full research-paper-level calculations.
   :align: center
   :width: 95%

FermiLink provides three workflows matched to different task scales:

- **exec** -- one prompt, one agent run. Best for quick tasks that finish in
  under 30 minutes (e.g., plotting data, short calculations).

- **loop** -- autonomous iteration with persistent memory, local PID polling,
  and SLURM job monitoring. Best for tasks that run for hours or days, where
  the agent needs to wait for jobs, check outputs, and iterate
  (e.g., convergence studies, multi-step simulations).

- **research / reproduce** -- planner + auditor + task-loop orchestration for
  publication-scale campaigns with multiple interdependent tasks
  (e.g., reproducing all figures from a paper, running a full parameter sweep).

Choose based on timescale and complexity:

1. Can it finish in one agent turn? Use ``exec``.
2. Does it need iteration or long waits? Use ``loop``.
3. Does it span multiple independent tasks? Use ``research`` or ``reproduce``.


Scientific package knowledge bases
-----------------------------------

.. figure:: _static/img/package_management_workflow.svg
   :alt: FermiLink package management workflow.
   :align: center
   :width: 95%

FermiLink ships with 150+ built-in scientific package knowledge bases spanning
computational chemistry, materials science, photonics, fluid dynamics, and
more. Each knowledge base includes:

- The full source-code tree of the package
- **Agent Skills** -- curated entry-level tutorials, file maps, and usage
  patterns that help the AI agent efficiently navigate the package

When you install a package (``fermilink install meep``), FermiLink makes this
knowledge available to the agent during all sessions. The agent uses it to
write correct input files, choose appropriate methods, and avoid common
pitfalls.

You can also create knowledge bases for your own code:

.. code-block:: bash

   fermilink compile /path/to/my-code          # create from source
   fermilink recompile my-code                  # update after changes

See :doc:`scientific_packages` for the full package management guide.


Three ways to interact
----------------------

FermiLink is not just a CLI tool. Pick the interface that fits your workflow:

- **Command line** -- ``fermilink exec/loop/research goal.md`` for headless,
  scriptable autonomy. The most powerful interface.
- **Web UI** -- ``fermilink start`` launches a ChatGPT-style browser interface
  for interactive sessions.
- **Telegram bot** -- ``fermilink gateway`` connects to Telegram so you can
  run and monitor jobs from your phone.

See :doc:`usage_web_ui` and :doc:`usage_chatting_apps` for setup guides.


Unified memory
--------------

FermiLink maintains a ``projects/memory.md`` file in each workspace that
persists across iterations and sessions. The memory includes:

- **Short-term state** -- current plan, progress log, active file map
- **Long-term knowledge** -- simulation history, key results, known pitfalls

This memory allows the agent to resume work after interruptions, learn from
failed attempts, and build on prior results within the same project.


What's next
-----------

- :doc:`quickstart` -- install and run your first goal in 5 minutes
- :doc:`writing_goal_md` -- how to write effective goal files
- :doc:`optimize` -- autonomous code optimization
- :doc:`architecture` -- full runtime flow and module contracts
