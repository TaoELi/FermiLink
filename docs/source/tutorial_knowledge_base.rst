Tutorial: FermiLink as a Knowledge Base for Any Agent
========================================================

This tutorial shows how to use **FermiLink** purely as a **scientific package
knowledge base** under your favorite coding agent --- whether you use the
**Codex CLI**, **Claude Code CLI**, **Claude Desktop**, the **Claude Code VS
Code extension**, **Cursor**, **Gemini CLI**, **OpenCode CLI**, or any other
agent that reads ``AGENTS.md`` / provider-specific instruction aliases in the
current working directory.

The overall workflow is very straightforward:

1. ``fermilink install`` --- bring a scientific package knowledge base into
   your machine.
2. ``fermilink init <pkg-id>`` --- overlay that knowledge base into your
   project directory so the agent of your choice can read it.
3. Open the directory with your agent and solve the scientific problem
   normally.
4. ``fermilink clean`` --- remove the overlay when you are done, leaving your
   project directory exactly as it was before.

You do **not** need to use ``fermilink exec``/``chat``/``loop`` for this
workflow. **FermiLink** is acting only as a knowledge-base provisioner; your
agent does the reasoning.

This tutorial is **self-contained**: you can follow it from a fresh shell
without reading any other page.

Prerequisites
~~~~~~~~~~~~~~~

You need:

- Python ``>= 3.11``
- ``git`` on ``PATH``
- One coding agent already installed and authenticated (Codex, Claude, Gemini,
  OpenCode, Cursor, etc.)

This tutorial assumes the default **FermiLink** runtime location at
``~/.fermilink``. If you want a different location later, see
:doc:`configuration`.


Step 1. Install **FermiLink**
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A clean conda environment is recommended so the install does not touch your
system Python:

.. code-block:: bash

   conda create -n fermilink python=3.11 -y
   conda activate fermilink

   pip install fermilink

Quick check:

.. code-block:: bash

   fermilink --help


Step 2. Install a scientific package knowledge base
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A **package knowledge base** in **FermiLink** is the full source code tree of a
scientific package plus an **Agent Skills** layer that points the agent to the
high-signal documentation, tutorials, source functions, and known pitfalls
for that package. There are three ways to install one **package knowledge base**.

Option A. From the curated ``skilled-scipkg`` channel (150+ packages)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The default channel hosts more than 150 curated scientific packages (see
:doc:`built_in_scientific_packages` for the live catalog). Search and install
by package id:

.. code-block:: bash

   # search the curated channel
   fermilink avail qutip

   # install one or several curated packages
   fermilink install qutip
   fermilink install lammps pyscf meep

Option B. From any GitHub repository URL (public or private)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Pass any GitHub URL as the install key. **FermiLink** clones the repository
over SSH (``git@github.com:<owner>/<repo>.git``) and registers it as a local
package knowledge base. This works for both public and private repositories,
as long as your SSH key has access to the repository.

.. code-block:: bash

   # public repo
   fermilink install https://github.com/skilled-scipkg/qutip

   # private repo (your SSH key must have access)
   fermilink install https://github.com/your-org/internal-solver

.. note::

   The package id is derived from the repository name (for example,
   ``internal-solver`` for ``your-org/internal-solver``). If you have not yet
   set up an SSH key for GitHub, follow GitHub's
   `SSH key setup guide <https://docs.github.com/en/authentication/connecting-to-github-with-ssh>`_
   first.

Option C. From any local directory
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If the package lives on your local disk (e.g., an in-house code, a fork you
are iterating on, or a cloned repo with hand-written ``skills/``), point
``fermilink install`` at the directory:

.. code-block:: bash

   # current directory
   fermilink install .

   # explicit path
   fermilink install ~/code/my-solver

   # equivalent explicit form (also accepts a custom package id)
   fermilink install my-solver --local-path ~/code/my-solver

.. tip::

   If your local package does not yet have an ``Agent Skills`` layer
   (``skills/`` directory), you can optionally run ``fermilink compile <package_id> <path>``
   first to generate it. See :doc:`usage_configure_your_package` for details.

Confirm the install (whichever method you used)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   fermilink list

Each installed knowledge base is stored under
``~/.fermilink/scientific_packages/packages/<package_id>/``.


Step 3. Overlay the knowledge base into your project directory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create (or move into) the working directory where you want the agent to do
its work, then run ``fermilink init <pkg-id>``:

.. code-block:: bash

   mkdir -p ~/projects/jc_demo
   cd ~/projects/jc_demo

   fermilink init qutip

What this does:

- Symlinks the installed package source tree entries into the current
  directory (so the agent can read every file).
- Copies the ``skills/`` directory as a managed local copy (the agent's
  primary entry point for package-specific guidance).
- Drops an ``AGENTS.md`` policy file in the current directory and creates
  ``CLAUDE.md`` / ``GEMINI.md`` aliases so agents that follow the standard
  ``AGENTS.md`` convention or provider-specific aliases pick up the same
  context.
- Records a ``.package_manifest.json`` so ``fermilink clean`` can later
  reverse the overlay safely.

After this step, your project directory looks like a normal working folder
with the package knowledge laid alongside any files you already had.

.. note::

   You can also pass an explicit destination:
   ``fermilink init qutip ~/projects/jc_demo``. If a file would be
   overwritten, ``fermilink init`` errors out unless you pass ``--force``.


Step 4. Use any agent to solve the problem
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Now hand the directory over to whichever agent you prefer. Each agent picks
up the overlaid ``AGENTS.md`` / provider-specific aliases and the package
``skills/`` automatically.

**Codex CLI**

.. code-block:: bash

   cd ~/projects/jc_demo
   codex

Then ask in the Codex prompt:

.. code-block:: text

   Use qutip to simulate the Jaynes-Cummings model (two-level system + single
   cavity mode). Plot the excited-state population vs time and save the figure
   as jc_population.png.

**Claude Code CLI**

.. code-block:: bash

   cd ~/projects/jc_demo
   claude

Claude Code reads ``CLAUDE.md`` (created by ``fermilink init`` as an alias of
``AGENTS.md``) and the package ``skills/`` automatically.

**Claude Desktop**

In Claude Desktop, add ``~/projects/jc_demo`` as a project / working folder
and start a new chat. The same ``CLAUDE.md`` and ``skills/`` files are
visible to the desktop client.

**Claude Code VS Code extension / Cursor / other IDE agents**

Open the folder (``code ~/projects/jc_demo`` or ``cursor ~/projects/jc_demo``)
and start the agent panel. The agent reads ``AGENTS.md`` / ``CLAUDE.md`` from
the workspace root the same way the CLI does.

**Gemini CLI**

.. code-block:: bash

   cd ~/projects/jc_demo
   gemini

Gemini CLI reads ``GEMINI.md`` (also aliased to ``AGENTS.md`` by
``fermilink init``).

**OpenCode CLI**

.. code-block:: bash

   cd ~/projects/jc_demo
   opencode

OpenCode reads the workspace instructions and can use any provider/model
profile configured in OpenCode.


Step 5. Clean up the overlay with ``fermilink clean``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When you are finished the whole process, restore the directory to its pre-overlay state:

.. code-block:: bash

   cd ~/projects/jc_demo
   fermilink clean

What this does:

- Removes the symlinks pointed at the installed package tree.
- Removes the managed ``skills/`` directory copy.
- Removes the ``AGENTS.md`` policy file and its ``CLAUDE.md`` / ``GEMINI.md``
  aliases.
- Removes ``.package_manifest.json``.
- Leaves all of **your** files (scripts, figures, results) untouched.

If you have modified a managed file by hand, ``fermilink clean`` refuses to
delete it. Pass ``--force`` only when you are sure those local changes can
be discarded:

.. code-block:: bash

   fermilink clean --force

The installed knowledge base itself stays under
``~/.fermilink/scientific_packages/`` so you can re-overlay it later with
another ``fermilink init <pkg-id>`` --- no need to download or clone again.

To also delete the installed knowledge base from disk, use
``fermilink delete <pkg-id>`` (see :doc:`scientific_packages`).


Troubleshooting quick checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **"Package id is required" on install**: pass a curated id, GitHub URL, or
  local path as the install key.
- **GitHub clone fails for a private repo**: confirm your SSH key is added to
  your GitHub account and that you have read access to the repository; the
  installer clones via ``git@github.com:<owner>/<repo>.git``.
- **"Conflict at <path>" during** ``fermilink init``: the destination already
  contains a file or directory with the same name. Move/rename it, or re-run
  with ``--force`` to overwrite.
- **Agent does not seem to use the package skills**: confirm ``AGENTS.md`` /
  ``CLAUDE.md`` / ``GEMINI.md`` and a ``skills/`` directory exist at the
  workspace root after ``fermilink init`` (``ls -la`` should show them).


Further reading (optional)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- :doc:`scientific_packages` --- full reference for ``install`` /
  ``activate`` / ``overlay`` / ``delete`` and the curated channel layout.
- :doc:`usage_configure_your_package` --- generate the ``skills/`` layer for
  a local package via ``fermilink compile``.
- :doc:`built_in_scientific_packages` --- the live list of curated packages
  in the default ``skilled-scipkg`` channel.
- :doc:`choosing_agent` --- comparison of supported agent providers.
- :doc:`tutorial_laptop` --- end-to-end laptop walkthrough using
  **FermiLink**'s own ``exec``/``chat``/``loop`` runners (if you prefer that
  flow over bring-your-own-agent).
