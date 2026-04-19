Updating Package Skills (``recompile``)
========================================

``fermilink recompile`` refreshes the skills and knowledge of an already-installed
package. Common reasons to recompile:

- The package source code or documentation has changed.
- You want to teach the agent workflows from a research paper.
- You want to promote agent memory from a workspace into permanent package knowledge.

.. note::

   Use ``compile`` to onboard a **new** local package for the first time.
   Use ``recompile`` to **update** a package that already has a ``skills/`` directory.


After source-code changes
-------------------------

.. code-block:: bash

   # recompile from the installed copy
   fermilink recompile <package_id> --core-skill-count 6

   # or point to a local development copy
   fermilink recompile <package_id> <path/to/source> --core-skill-count 6

When a source path is given, **FermiLink** recompiles from that path and then
installs the result into local storage, so you can iterate on a development
copy before publishing.


From a research paper
---------------------

.. code-block:: bash

   fermilink recompile <package_id> \
     --doc ./paper/manuscript.tex \
     --data-dir ./paper/supplementary \
     --comment "focus on the cavity spectra and validation workflow"

.. list-table::
   :widths: 20 80

   * - ``--doc``
     - Paper manuscript (``.tex``, ``.md``, etc.) describing the research pipeline.
   * - ``--data-dir``
     - Directory of supplementary files (input decks, scripts, etc.). The agent ranks them by relevance automatically.
   * - ``--comment``
     - Free-form focus hint — useful when the paper covers multiple workflows and you only need a subset.

``--data-dir`` and ``--comment`` are optional but recommended.


From workspace memory
---------------------

During simulations, agents record suggestions in ``projects/memory.md``.
Recompile with ``--memory`` to promote those suggestions into permanent skills
so that **all** future sessions benefit.

.. code-block:: bash

   # from a single memory file
   fermilink recompile <package_id> --memory ./projects/memory.md

   # or scan an entire directory for matching memory entries
   fermilink recompile <package_id> --memory ./projects

   # keep only machine-independent updates
   fermilink recompile <package_id> --memory ./projects \
     --memory-scope package-specific

When a directory is given, **FermiLink** recursively finds all ``memory.md`` files
and extracts ``### Suggested skills updates`` entries that match the package.

``--memory-scope`` controls which suggestions are applied:

- ``package-specific`` — shareable, machine-independent updates only.
- ``machine-specific`` — local-machine troubleshooting guidance only.
- *(default)* — both.


.. tip::

   Prefer a guided flow? Run bare ``fermilink`` and choose
   **Advanced: Update package skills with research pipelines / memory**.


See also
--------

- :doc:`usage_configure_your_package` — first-time ``compile`` and local package install.
- :doc:`scientific_packages` — installing curated packages.
- :doc:`configuration` — runtime environment-variable reference.
