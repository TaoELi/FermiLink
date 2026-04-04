``compile``: Configure Your Package
===================================

Apart from using the built-in curated scientific packages, you can also configure your own local package for FermiLink. This is particularly useful when you have custom or closed-source code that you want to integrate with FermiLink in your local machines.

Quick ``compile`` flow
-----------------------

``fermilink compile`` provides a quick way to onboard your local package into FermiLink. 

It generates the necessary **Agent Skills** for your package and installs it into the **local** scientific package storage. This allows you to use your package in FermiLink agent runs just like the built-in packages.


.. code-block:: bash

   fermilink compile <package_id> <path/to/source/code/tree> \
     --max-skills 30 \
     --core-skill-count 6

Common path choices:

- ``<path/to/source/code/tree>`` as ``.`` for current directory.
- Absolute path for external local projects.

Useful compile flags:

- ``--max-skills``: cap generated skill count including index skill (default 30).
- ``--core-skill-count``: number of topic skills to enrich with high-signal playbooks (default 6).
- ``--docs-only`` for docs-first generation when source trees are unavailable.
- ``--strict-compile-validation`` to fail when validation findings exist.
- ``--install-off`` to refresh ``skills/`` in the local path only, which is useful for iterative skill development without installing the package to local FermiLink storage.
- ``--keep-compile-artifacts`` to keep temporary ``sci-skills-generator/`` folder after compile.


A **suggested advanced workflow** for configuring your local package:

.. code-block:: bash

    # 1. compile with --install-off 
    fermilink compile <package_id> <path/to/source/code/tree> --install-off
    # 2. (optional) inspect and modify the generated skills in <path/to/source/code/tree>/skills/ 

    # 3. install the modified package to local FermiLink storage
    fermilink install <package_id> --local-path <path/to/source/code/tree> --activate

.. note::

    If you prefer a guided flow, run bare ``fermilink`` in an interactive terminal
    and choose ``Advanced: Compile a local package for FermiLink``. 
    
    The assistant asks for the package id and local project path, then dispatches the
    real ``fermilink compile`` command for you.

See also:

- :doc:`usage_advanced_configuration` for adding package skills using published papers or group secrets.
- :doc:`scientific_packages` for curated channel install.
- :doc:`usage` for broader command-line workflows.
