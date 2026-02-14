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

Compile local project into a package:

.. code-block:: bash

   fermilink compile <package_id> <path>

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
