Configure Your Package
======================

This page introduces practical ``fermilink compile`` and
``fermilink recompile`` techniques for building and refreshing package skills.

Choose the right command
------------------------

- Use ``compile`` when onboarding a new local project into FermiLink package
  storage.
- Use ``recompile`` when a package already has ``skills/`` and you want to
  refresh skills after code/doc updates.

Quick compile flow
------------------

``compile`` requires an explicit path argument.

.. code-block:: bash

   fermilink compile <package_id> <path> \
     --max-skills 30 \
     --core-skill-count 6

Common path choices:

- ``<path>`` as ``.`` for current directory.
- Absolute path for external local projects.

Useful compile flags:

- ``--docs-only`` for docs-first generation when source trees are unavailable.
- ``--strict-compile-validation`` to fail when validation findings exist.
- ``--install-off`` to refresh local outputs only (skip install/registry update).
- ``--keep-compile-artifacts`` to keep temporary generator artifacts.

Quick recompile flow
--------------------

When ``<path>`` is omitted, recompile defaults to the managed installed package
path ``<scientific_packages_root>/packages/<package_id>``.

.. code-block:: bash

   fermilink recompile <package_id> --core-skill-count 6
   fermilink recompile <package_id> <path> --core-skill-count 6

Use explicit ``.`` when you want to recompile the current directory instead of
the managed installed package path.

Advanced recompile modes
------------------------

Paper-focused recompile:

.. code-block:: bash

   fermilink recompile <package_id> <path> \
     --doc ./paper/manuscript.tex \
     --data-dir ./paper/supplementary \
     --comment "focus on the cavity spectra and validation workflow"

Memory-focused recompile planning:

.. code-block:: bash

   fermilink recompile <package_id> <path> --memory ./projects/memory.md
   fermilink recompile <package_id> <path> --memory ./projects

``--memory`` mode plans and applies append-only skill updates and does not
install package files.

Technique checklist
-------------------

1. Start with ``compile`` for initial onboarding.
2. Use ``recompile`` after meaningful package changes.
3. Keep ``--strict-compile-validation`` enabled in CI or release workflows.
4. Use ``--install-off`` for local iteration and dry runs.
5. Keep ``skills/.evidence/`` local-only (managed via ``skills/.gitignore``).

See also:

- :doc:`scientific_packages` for full compile/recompile lifecycle details.
- :doc:`usage` for broader command-line workflows.
