FermiLink
======================================

.. image:: _static/img/icon.png
   :alt: FermiLink icon
   :align: center
   :scale: 18

**FermiLink** is a unified AI agent framework for autonomous scientific computing in laptops, workstations, and HPC clusters.
It supports a set of powerful **command line tools** for efficient calculations at different scopes, ranging from simple tutorial-level calculations (``fermilink exec``),
reproducing a figure of a scientific paper involving long-term calculations (``fermilink loop``), to independent research at the scale of a whole research paper (``fermilink reproduce/research``).

It ships with the built-in support of many popular scientific packages (``fermilink install``), and users can easily compile their local scientific packages, research pipelines described in 
papers, or group-owned screts to the knowledge database of **FermiLink** locally (``fermilink compile/recompile``) for efficient agent-induced scientific simulations.

Apart from the command line tools, it contains a user-friendly **web UI** interface (for ChatGPT-like experience) and also supports the remote control using **chatting apps** (for OpenClaw-like experience).
Users can enjoy autonomous scientific computing at any time, any place.

.. toctree::
   :maxdepth: 1
   :caption: Get Started

   introduction
   installation
   usage

.. toctree::
   :maxdepth: 1
   :caption: Platform Operations

   scientific_packages
   built_in_scientific_packages
   configuration
   architecture

.. toctree::
   :maxdepth: 1
   :caption: Engineering

   contributing
   test_plan
   repository_map

.. toctree::
   :maxdepth: 1
   :caption: Reference

   API reference <api/modules>
