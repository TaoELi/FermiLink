Scientific Package Management
=============================

FermiLink package management controls which scientific context is available
at execution time and how that context is overlaid into workspaces.

Common workflow
---------------

.. code-block:: bash

   fermilink install maxwelllink --activate
   fermilink list
   fermilink overlay maxwelllink --entry skills --entry docs --entry src
   fermilink dependencies maxwelllink --package meep

Storage model
-------------

Package data under ``FERMILINK_SCIPKG_ROOT``:

- ``packages/<package_id>/...`` installed package trees.
- ``registry.json`` package metadata and active package.
- ``router_rules.json`` keyword router configuration.

Session workspace data under ``FERMILINK_WORKSPACES_ROOT/<session_id>/``:

- ``repo/`` active execution repository.
- ``.package_manifest.json`` overlay/dependency ownership manifest.

Install sources
---------------

Curated channel install:

.. code-block:: bash

   fermilink install ase --activate

Multiple curated packages:

.. code-block:: bash

   fermilink install ase meep qutip
   fermilink activate ase

Custom zip install:

.. code-block:: bash

   fermilink install mypkg \
     --zip-url https://github.com/<org>/<repo>/archive/refs/heads/main.zip \
     --activate

Local path install:

.. code-block:: bash

   fermilink install mypkg --local-path /absolute/path/to/package --activate

Compile local project into a package
------------------------------------

.. code-block:: bash

   fermilink compile <package_id> <path> \
     --max-skills 30 \
     --core-skill-count 6

Compile uses a three-pass Codex workflow plus deterministic generation and
validation around ``sci-skills-generator`` to create/refine package
``skills/`` and then installs into scientific package storage.

Typical compile path:

1. Validate package id does not already exist in registry (unless ``--install-off``).
2. Copy ``sci-skills-generator`` tool into project root.
3. Pass 1 discovers project structure and writes ``skills/.compile_profile.json``.
4. Run deterministic ``generate_skills_folder.py`` using the discovered profile.
5. Build ``skills/.evidence/`` bundle for core topic skills.
6. Pass 2 enriches compact high-signal playbooks in core skills.
7. Pass 3 audits/fixes path consistency and source-link quality.
8. Validate required files, links, source entry points, and playbook sections.
9. Write ``skills/.compile_report.json`` and install the package (validation findings are reported by default).

Useful compile options:

- ``--max-skills``: cap generated skill count including index skill.
- ``--core-skill-count``: number of topic skills to enrich with high-signal playbooks.
- ``--docs-only``: force docs-only generation mode when source trees are unavailable.
- ``--keep-compile-artifacts``: keep temporary ``sci-skills-generator/`` folder after compile.
- ``--strict-compile-validation``: fail compile when validation findings exist.
- ``--install-off``: skip package install/registry/router updates; only refresh local ``skills/`` outputs.

Recompile existing skills during package development
----------------------------------------------------

Use ``recompile`` when a package already has ``skills/`` and you want to refresh
link consistency and source coverage after code changes (for example after PRs).

.. code-block:: bash

   fermilink recompile <package_id> <path> \
     --core-skill-count 6

Typical recompile path:

1. Validate ``skills/`` exists in the target project.
2. Run pass 1 to rediscover layout and refresh ``skills/.compile_profile.json``.
3. Build ``skills/.evidence/`` bundle plus ``skills/.evidence/recompile_coverage.md``
   highlighting potential uncovered source files/functions.
4. Run pass 2 to update ``skills/`` coverage and source links.
5. Run pass 3 to audit/finalize link consistency and simulation-readiness.
6. Validate skills and write ``skills/.compile_report.json``.
7. Install updated package into scientific package storage (skipped with ``--install-off``).

Useful recompile options:

- ``--strict-compile-validation``: fail recompile when validation findings exist.
- ``--keep-compile-artifacts``: keep temporary ``sci-skills-generator/`` folder after recompile.
- ``--docs-only``: force docs-only coverage behavior (skip source-link requirements).
- ``--install-off``: skip package install/registry/router updates; only refresh local ``skills/`` outputs.

Recompile always updates/replaces the installed package for the same ``package_id``.

Check curated package availability
----------------------------------

Use ``avail`` to check whether a package exists in curated channels.

.. code-block:: bash

   fermilink avail ase
   fermilink avail quantum

Package lifecycle commands
--------------------------

.. code-block:: bash

   fermilink list
   fermilink activate maxwelllink
   fermilink delete maxwelllink
   fermilink delete maxwelllink --keep-files

Overlay and dependency controls
-------------------------------

Restrict exposed top-level entries:

.. code-block:: bash

   fermilink overlay maxwelllink --entry skills --entry docs --entry src

Clear overlay restriction:

.. code-block:: bash

   fermilink overlay maxwelllink --clear

Configure dependency package links under ``repo/external_packages``:

.. code-block:: bash

   fermilink dependencies maxwelllink --package meep --package qutip

Runtime package resolution order
--------------------------------

Runner resolves package by:

1. Explicit request package id.
2. Workspace manifest pin.
3. ``FERMILINK_SCIPKG_ACTIVE`` environment override.
4. Registry ``active_package``.

Practical guidance
------------------

- Use ``--activate`` for the package most users should get by default.
- Keep overlay scope small for better prompt focus and lower collision risk.
- Use dependencies for shared helper packages instead of duplicating files.
- Re-sync router rules when registry content changes significantly.
