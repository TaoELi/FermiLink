Advanced Local Configuration
============================

This page describes practical patterns for configuring FermiLink with
local-only assets such as private datasets, paper pipelines, and group
configuration repositories.

When to use this page
---------------------

Use this guide when you need any of the following:

- Keep data and operational files on your own machine or lab servers.
- Compile local pipeline knowledge from paper/project repositories.
- Reference sensitive settings without exposing raw secrets in prompts.

Local root setup
----------------

Pin runtime roots before running services or session commands:

.. code-block:: bash

   export FERMILINK_HOME=./.fermilink
   export FERMILINK_CHAINLIT_APP_ROOT=./.fermilink
   export FERMILINK_RUNNER_URL=http://127.0.0.1:8000

This keeps package store, workspaces, and runtime state under one local
directory tree that you can back up or move as needed.

Bring paper pipelines into local package knowledge
--------------------------------------------------

For repositories that include paper methods, scripts, and supplementary files:

.. code-block:: bash

   fermilink recompile <package_id> <path-to-project> \
     --doc ./paper/manuscript.tex \
     --data-dir ./paper/supplementary \
     --comment "focus on simulation + validation steps"

Use ``--doc`` and ``--data-dir`` to anchor generated skills to your manuscript
and local assets while keeping the workflow private to your own environment.

Organize local data and outputs
-------------------------------

FermiLink writes runtime artifacts under your configured roots:

- ``$FERMILINK_HOME/scientific_packages``
- ``$FERMILINK_HOME/workspaces``
- ``$FERMILINK_HOME/runtime``

Recommended practice:

1. Keep large immutable datasets outside workspace repos and reference them by
   stable absolute paths.
2. Use workspace-local folders (for example ``projects/`` and ``outputs/``) for
   run outputs and intermediate artifacts.
3. Version-control only reproducible scripts/config files, not large binaries
   or generated result files.

Handle private configuration and secrets safely
-----------------------------------------------

- Prefer environment variables for credentials and tokens.
- Keep secret files in private directories outside public repositories.
- Do not place raw secrets directly in ``skills/`` markdown content.
- Add local config files to ``.gitignore`` in package/project repositories.

See also
--------

- :doc:`usage_configure_your_package` for compile/recompile workflows.
- :doc:`scientific_packages` for package lifecycle and overlay behavior.
- :doc:`configuration` for full runtime environment-variable reference.
