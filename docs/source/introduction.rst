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


Three major FermiLink workflows are supported in FermiLink:

- ``exec``: one prompt, one run, suitable for short computing.
- ``loop``: autonomous iteration with memory updates, job-aware waiting
  (local PID and HPC SLURM jobs), and long-running task support.
- ``research`` / ``reproduce``: planner + auditor + task-loop workflows for
  idea-to-results and paper-reproduction workflows, suitable for simulations at scale of a research paper.


Package management workflow
---------------------------

.. figure:: _static/img/package_management_workflow.svg
   :alt: FermiLink package management workflow.
   :align: center
   :width: 95%

FermiLink uses **Agent Skills** to compress knowledge for agent reasoning. It has a built-in **Curated Computational Packages** channel 
which stores the full source-code tree + **Agent Skills** containing the entry-level tutorials and the file map of this source-code tree. This GitHub channel
now supports more than 150 scientific packages across multiple domains.

Users can also easily use the command line tools in FermiLink to add their own local packages. Additionally, users can add pipelines in research papers, database, or unpublished screts 
as additional **Agent Skills** for one specific package. Then, this package knowledge base is used across all jobs in FermiLink.

How to choose your starting point
---------------------------------

1. Use ``fermilink exec`` when you need a direct result quickly.
2. Use ``fermilink loop`` when a task needs iterative refinement or long simulation waits.
3. Use ``fermilink reproduce`` or ``fermilink research`` when you need
   publication-scale workflows.
4. Use ``fermilink compile`` / ``fermilink recompile`` when your package
   knowledge should be created and reusable.

See :doc:`architecture` for the full runtime flow and contracts.
