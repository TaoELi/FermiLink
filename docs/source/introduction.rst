Introduction
============

**FermiLink** is a unified AI agent framework for autonomous scientific
computing on **laptops**, **workstations**, **HPC clusters**, and **cellphones**.
It combines scientific package management, reliable execution workflows, and
multiple interaction surfaces (web UI, command line, and chatting apps) in one
consistent system.

What makes FermiLink practical
------------------------------

Many scientific-agent systems break when you switch computation tools or scale up task
complexity. FermiLink is designed to avoid that drift:

- **one package-selection layer** across web and terminal runs supporting a wide range of scientific packages;
- **one unified short-term/long-term memory model** (``projects/memory.md``) for iterative and
  long-running work in the same workspace;
- **one runtime policy plane** (provider and sandbox) shared by web, CLI, and
  chatting apps;
- **three distinct workflows** for computational tasks at different scales.


You can start quickly with built-in scientific packages
(``fermilink install``), and you can also turn your own local projects or paper
pipelines into reusable package knowledge with
``fermilink compile`` / ``fermilink recompile``.


Three major autonomous workflows
--------------------------------

.. figure:: _static/img/major_modes_workflow.svg
   :alt: Three major FermiLink workflows: exec for single runs, loop for iterative runs involving long SLURM or PID jobs, and research/reproduce for full research-paper-level calculations.
   :align: center
   :width: 95%

   Three major FermiLink workflows: ``exec`` for single-run tasks, ``loop`` for
   iterative autonomous work involving long SLURM or PID jobs, and ``research``/``reproduce`` for paper-scale
   calculations.

- ``exec``: one prompt, one run, fast turn-around in the current repo.
- ``loop``: autonomous iteration with memory updates, job-aware waiting
  (local PID and HPC SLURM jobs), and long-running task support.
- ``research`` / ``reproduce``: planner + auditor + task-loop workflows for
  idea-to-results and paper-reproduction workflows, with structured run
  artifacts and report finalization.


Package management workflow
---------------------------

.. figure:: _static/img/package_management_workflow.svg
   :alt: FermiLink package management workflow.
   :align: center
   :width: 95%

This workflow lets you keep domain knowledge close to your execution runtime:

1. Add package knowledge base through curated install or local compile/recompile.
2. Keep package metadata and router rules in deterministic storage.
3. Reuse the same package context across all jobs in FermiLink.

How to choose your starting point
---------------------------------

1. Use ``fermilink exec`` when you need a direct result quickly.
2. Use ``fermilink loop`` when a task needs iterative refinement or long simulation waits.
3. Use ``fermilink reproduce`` or ``fermilink research`` when you need
   publication-scale workflows.
4. Use ``fermilink compile`` / ``fermilink recompile`` when your package
   knowledge should be created and reusable.

See :doc:`architecture` for the full runtime flow and contracts.
