FermiLink
======================================

.. image:: _static/img/icon.png
   :alt: FermiLink icon
   :align: center
   :scale: 18

**FermiLink** is a unified AI execution layer for scientific workflows.
It combines a web UI, CLI automation modes, and package-aware runtime routing
so one prompt can run against the right scientific package context.

This documentation is written for both operators and advanced users who want
predictable runtime behavior across ``web``, ``exec``, ``chat``, and autonomous
workflow modes.

Where to start
--------------

- New operator: read :doc:`installation`, then :doc:`configuration`.
- Daily user: read :doc:`usage` and :doc:`scientific_packages`.
- Contributor: read :doc:`contributing` and :doc:`test_plan`.

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
