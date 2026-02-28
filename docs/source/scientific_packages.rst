``install``: Built-In Scientific Package Management
====================================================

One central idea of FermiLink is to provide the full source code tree for agent reasoning, instead of only providing API-level documentation. This allows agents to have more context and flexibility in using the scientific packages, which is crucial for complex scientific simulations.

However, because the source code trees of scientific packages can be very large, FermiLink builds an **Agent Skills** layer on top of the source code tree, which provides an entry point for agent reasoning. This design allows agents to efficiently locate and access the most relevant documentation, tutorials, and source code functions in the source code tree for reasoning. Common pitfalls for using the package are also highlighted in **Agent Skills**.

FermiLink package management controls which scientific knowledge base is available
at execution time and how that context is overlaid into workspaces.

Built-in package catalog
------------------------

FermiLink has a built-in curated channel (Github: ``skilled-scipkg``) containing more than 150 scientific packages across multiple domains. Each package in this channel has an associated knowledge base, including the full source code tree and the **Agent Skills** layer. This built-in channel allows users to quickly access a wide range of scientific packages.

For the live list of built-in curated packages (``package_id``, title, and repo),
see :doc:`built_in_scientific_packages`.

Check curated package availability
----------------------------------

Use ``avail`` to check whether a package exists in curated channels.

.. code-block:: bash

   fermilink avail ase
   fermilink avail quantum

Install package knowledge base
-------------------------------

Use ``install`` to download the curated package knowledge base (source code tree + agent skills) to the local machine. This command does not really install the package for execution, but it makes the package knowledge available for agents to use.

.. code-block:: bash

   fermilink install ase --activate

Install multiple curated packages:

.. code-block:: bash

   fermilink install ase meep qutip
   fermilink activate ase

``--activate`` sets this package as the default option for new sessions (if the user's prompt cannot fit any installed package). You can switch later using ``fermilink activate``.

Package lifecycle commands
--------------------------

.. code-block:: bash

   # check the list of locally installed packages
   fermilink list
   # switch active package, the default package for new sessions
   fermilink activate maxwelllink
   # delete the package knowledge base from local machine (but keep the files if --keep-files is provided)
   fermilink delete maxwelllink
   fermilink delete maxwelllink --keep-files
   
In some cases, you want to **expose multiple packages simutaneously for agents** to reasoning.
For example, when doing LAMMPS MD simulations (the main package for agent reasoning), the agent might also need to use Packmol (a common package for preparing molecular initial geometry) for the pre-processing step. In this situation, use:

.. code-block:: bash

   fermilink dependencies lammps --package packmol

This will allow the agent, when lammps is loaded as the main package, to also use packmol for reasoning and execution. 


Where are the package files stored?
-------------------------------------

Once installed, these package data are stored within FermiLink under ``FERMILINK_SCIPKG_ROOT`` or ``~/.fermilink/scientific_packages/``:

- ``packages/<package_id>/...`` installed package trees.
- ``registry.json`` package metadata and active package.
- ``router_rules.json`` keyword router configuration for each package.



Advanced: ``auto-compile`` for curated channel
-----------------------------------------------------

The curated channel (Github: ``skilled-scipkg``) is maintained by FermiLink developers. This channel is created automatically using the ``auto-compile`` workflow.

.. note:: This workflow requires GitHub authentication and the ``gh`` CLI tool, and will **create a forked repo under the user's Github account**.

.. note:: Use ``fermilink compile`` (:doc:`usage_configure_your_package`) instead if you simply want to compile your own local packages.

.. code-block:: bash

   fermilink auto-compile qutip https://github.com/qutip/qutip \
     --fermilink-repo /absolute/path/to/FermiLink/source/code

``--fermilink-repo`` is required to specify the local path to the FermiLink repository, as ``auto-compile`` can update curated channel metadata within the FermiLink source code.


See also:

- :doc:`usage_configure_your_package` for compiling your own packages.
- :doc:`usage_advanced_configuration` for adding package skills using publised paper or group secrets.
- :doc:`usage` for broader command-line workflows.