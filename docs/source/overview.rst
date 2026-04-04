FermiLink
======================================

.. image:: _static/img/icon.png
   :alt: FermiLink icon
   :align: center
   :scale: 18

**FermiLink** is a unified AI agent framework for autonomous scientific computing on laptops, workstations, HPC clusters, and mobile phones.
It supports a set of powerful **command line tools** for autonomous computation at different scales: from quick one-off runs (``fermilink exec``),
to iterative jobs that wait on long local or HPC SLURM processes (``fermilink loop``), to full research-paper-scale workflows (``fermilink reproduce/research``).

.. figure:: _static/img/major_modes_workflow.svg
   :alt: Three major FermiLink workflows: exec for single runs, loop for iterative runs involving long SLURM or PID jobs, and research/reproduce for full research-paper-level calculations.
   :align: center
   :width: 95%

It ships with the built-in support of many popular scientific packages (``fermilink install``), and users can easily compile their local scientific packages, research pipelines described in 
papers, or group-owned secrets to the knowledge database of **FermiLink** locally (``fermilink compile/recompile``) for efficient agent-operated scientific simulations.

.. figure:: _static/img/package_management_workflow.svg
   :alt: FermiLink package management workflow.
   :align: center
   :width: 95%

Apart from the command line tools, it includes a **web UI** for a ChatGPT-style chat interface, and a **Telegram bot** for remote control from your phone.
Users can run autonomous scientific computing at any time, from any place.

.. figure:: _static/img/web_ui_telegram.png
   :alt: FermiLink web UI and Telegram bot.
   :align: center
   :width: 95%


.. toctree::
   :maxdepth: 1
   :caption: Get Started

   introduction
   installation

.. toctree::
   :maxdepth: 1
   :caption: Practical Tutorial on machines

   tutorial_laptop
   tutorial_hpc

.. toctree::
   :maxdepth: 2
   :caption: Usage Guide

   Usage Guide <usage_guide>

.. toctree::
   :maxdepth: 1
   :caption: Advanced Topics

   Advanced Configuration <configuration>
   architecture
   contributing
   API Reference <api/modules>
