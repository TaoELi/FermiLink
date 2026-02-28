``recompile``: Reusable Research Pipelines and Memory
=======================================================

Here we introduce ``fermilink recompile``, a powerful command for refreshing package agent skills and knowledge based on paper pipelines or memory-driven suggestions. This command is designed to help users keep their package knowledge base up-to-date and relevant to their research and simulations.

When to use ``recompile``
-------------------------

Use this guide when you need any of the following:

- Refresh package skills after code or documentation updates.
- Enrich the **local** package knowledge base with new insights or pipelines from **published papers** or **unpublished research**.
- Convert unified-memory suggestions from workspace runs into permanent skill patches for the package knowledge base.

.. note::
   
   - Use ``compile`` when onboarding a new local package (with no ``skills/`` directory) into FermiLink package storage.

   - Use ``recompile`` when a package already has ``skills/`` and you want to refresh skills due to various reasons.


Update skills with significantly modified source code or documentation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In this case, run one of the following commands:

.. code-block:: bash

   fermilink recompile <package_id> --core-skill-count 6
   fermilink recompile <package_id> <path/to/source/code> --core-skill-count 6

When ``<path/to/source/code>`` is omitted, recompile works on the installed package
path at FermiLink local storage ``~/.fermilink/scientific_packages/packages/<package_id>``.

When ``<path/to/source/code>`` is given, recompile targets the provided path instead of the installed package path, and then further installs the updated package into FermiLink local storage. This allows users to maintain a local copy of the package for development and testing before installing it to FermiLink storage.


Convert research pipelines from published papers or unpublished secrets into package knowledge
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In this situation, run the following commands:

.. code-block:: bash

   fermilink recompile <package_id> \
     --doc ./paper/manuscript.tex \
     --data-dir ./paper/supplementary \
     --comment "focus on the cavity spectra and validation workflow"

Here, ``--data-dir`` and ``--comment`` are optional but highly recommended.

- ``--doc`` can be a research paper manuscript or even a simplified markdown file briefly describing the research pipeline.
- ``--data-dir`` is the directory containing **unstructured** supplementary data files, which can be a few input files or even the whole data directory of a research paper. Agent will automatically search and rank these files for relevance to the workflow.
- ``--comment`` is a free-form text to specify the focus or scope of the generated skills, which is particularly useful when the manuscript covers multiple workflows or systems and users want only one or a subset of them. 


Convert unified-memory suggestions into permanent skill patches
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

During FermiLink simulations, agents will write down key findings for improving the usage of the packages in ``projects/memory.md`` within one workspace. 

Use ``recompile --memory`` to **convert unified-memory suggestions** in the workspace to a **permanent skill patch** to the package knowledge base, so all simulations will learn from the simulations in this workspace.

.. code-block:: bash

   fermilink recompile <package_id> \
     --memory ./projects/memory.md

   fermilink recompile <package_id> \
     --memory ./projects

If ``./projects`` is provided, FermiLink will recursively scan all ``memory.md`` files under this directory and extract all entries with the header format of ``### Suggested skills updates`` matching this package. 



See also
--------

- :doc:`usage_configure_your_package` for compile workflows and local package install details.
- :doc:`scientific_packages` for curated channel install.
- :doc:`configuration` for full runtime environment-variable reference.
