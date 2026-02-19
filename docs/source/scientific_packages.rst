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
2. Auto-initialize ``git`` in target project when ``.git`` is missing.
3. Initialize/upgrade compile memory at ``skills/.evidence/memory.md``.
4. Copy ``sci-skills-generator`` tool into project root.
5. Pass 1 discovers project structure and writes ``skills/.compile_profile.json``
   plus ``skills/.evidence/skill_plan.json``.
6. Run deterministic ``generate_skills_folder.py`` using the discovered profile.
7. Build ``skills/.evidence/`` bundle for core topic skills.
8. Pass 2 enriches compact high-signal playbooks in plan-priority skills.
9. Pass 3 audits/fixes path consistency and source-link quality.
10. Validate required files, links, source entry points, playbook sections, and
   plan-target coverage.
11. Write ``skills/.compile_report.json``, update compile memory history, and install
    the package (validation findings are reported by default).
12. Ensure ``skills/.gitignore`` ignores ``.evidence/`` so compile-only evidence stays local.

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

Paper-focused recompile (manuscript + supplementary data):

.. code-block:: bash

   fermilink recompile <package_id> <path> \
     --doc ./paper/manuscript.tex \
     --data-dir ./paper/supplementary \
     --comment "focus on the cavity spectra and validation workflow"

Memory-focused recompile planning (extract from one memory file or a directory tree):

.. code-block:: bash

   fermilink recompile <package_id> <path> \
     --memory ./projects/memory.md

   fermilink recompile <package_id> <path> \
     --memory ./projects

Typical recompile path (standard mode):

1. Validate ``skills/`` exists in the target project.
2. Auto-initialize ``git`` in target project when ``.git`` is missing.
3. Initialize/upgrade compile memory at ``skills/.evidence/memory.md``.
4. Run pass 1 to rediscover layout and refresh ``skills/.compile_profile.json``
   plus ``skills/.evidence/skill_plan.json``.
5. Build ``skills/.evidence/`` bundle plus ``skills/.evidence/recompile_coverage.md``
   highlighting potential uncovered source files/functions.
6. Run pass 2 to update plan-priority skills, coverage, and source links.
7. Run pass 3 to audit/finalize link consistency and simulation-readiness.
8. Validate skills (including plan coverage and source-coverage trend warnings),
   write ``skills/.compile_report.json``, and update compile memory history.
9. Install updated package into scientific package storage (skipped with ``--install-off``).
10. Ensure ``skills/.gitignore`` ignores ``.evidence/`` so evidence artifacts are not committed by default.

Paper-mode pass flow (``--doc ...``):

1. Pass 1 (plan): load manuscript text directly into the prompt and generate both
   ``skills/.compile_profile.json`` and
   ``skills/.evidence/paper_context/paper_plan.json`` (figure-by-figure
   simulation configs, required packages, parameters, and acceptance checks).
   If ``--doc`` is very large, the run may fail before Codex starts with
   ``Argument list too long`` because prompt text is passed via command
   arguments; trim manuscript size (for example, appendices/references) and retry.
2. Prepare deterministic paper artifacts under
   ``skills/.evidence/paper_context/``:
   ``paper_context.json``, optional data manifests/summaries (from ``--data-dir``),
   and ``staged_assets_manifest.json`` + ``staged_assets/``.
3. Pass 2 (tutorial synthesis): use ``paper_plan.json`` plus data manifests to
   build a new skill ``skills/paper_tutorial_<slug>/`` and fill sidecar files
   ``figure_data_map.json`` and ``paper_skill_manifest.json``.
   The ``<slug>`` is derived from manuscript scope content (comment/plan
   summaries) so it stays descriptive and avoids generic names such as
   ``manuscript_revised``.
   The original manuscript text is not injected in this pass.
   Runtime execution should be instructed under
   ``projects/YYYY-MM-DD-<scope>/`` (copy from tutorial ``assets/``), not
   under ``skills/.../workspace/``.
   Under ``## Figure Routing``, each figure entry should include a brief
   scope summary (scientific aim/condition) plus its playbook path.
   The generated tutorial must be self-contained (copy lightweight inputs,
   postprocess/plot scripts, and references into local ``assets/``) and must
   not depend on ``skills/.evidence/`` paths or external ``--data-dir`` paths.
4. Pass 3 (audit/finalize): audit the new tutorial skill against
   ``paper_plan.json``, optionally cross-check ``--doc`` and ``--data-dir``
   content, and append an advanced-topic route in ``skills/*-index/SKILL.md``.
5. Validate the paper tutorial contract (plan coverage, map coverage, staged
   assets, index routing, and no external absolute path leaks), then write
   ``skills/.compile_report.json`` and continue to install unless strict
   validation is enabled and findings exist.

Useful recompile options:

- ``--strict-compile-validation``: fail recompile when validation findings exist.
- ``--keep-compile-artifacts``: keep temporary ``sci-skills-generator/`` folder after recompile.
- ``--docs-only``: force docs-only coverage behavior (skip source-link requirements).
- ``--install-off``: skip package install/registry/router updates; only refresh local ``skills/`` outputs.
- ``--doc``: manuscript path for paper-focused skill synthesis and reproducibility audits.
- ``--data-dir``: supplementary data directory used to build compact/full manifests and stage reproducible assets (requires ``--doc``).
- ``--comment``: optional targeted paper objective; when omitted, recompile defaults to broad manuscript-result reproducibility.
- ``--memory``: memory-driven plan-and-apply mode. Accepts a single file path or a directory; when a directory is provided, recursively scans all ``memory.md`` files and extracts ``### Suggested skills updates`` entries for the selected package id. This mode emits append-only plan JSON at ``skills/.evidence/memory_update_plan.json``, appends accepted updates into target ``skills/*/SKILL.md`` files, and does not install package files.

``--memory`` cannot be combined with ``--doc``, ``--data-dir``, or ``--comment``.

Recompile always updates/replaces the installed package for the same ``package_id``.
In paper mode, run-scoped evidence lives under ``skills/.evidence/paper_context/``
including ``paper_context.json``, ``paper_plan.json``,
``figure_data_map.json``, ``paper_skill_manifest.json``,
optional ``data/data_manifest_full.json``, ``data/data_manifest.json``,
``data/data_summary.md``, and ``staged_assets_manifest.json`` + ``staged_assets/``.
Both compile and recompile maintain persistent compile memory at
``skills/.evidence/memory.md`` and a run-plan sidecar at
``skills/.evidence/skill_plan.json`` for future refresh iterations.
Both commands also enforce ``skills/.gitignore`` containing ``.evidence/`` so evidence
artifacts remain local build metadata by default.

Auto-compile + curated metadata onboarding
------------------------------------------

Use ``auto-compile`` to scale scientific package onboarding from upstream
GitHub repositories into your authenticated account (or a specified
organization) plus local curated metadata.

.. code-block:: bash

   fermilink auto-compile qutip https://github.com/qutip/qutip \
     --fermilink-repo /absolute/path/to/FermiLink_development

What ``auto-compile`` does per package:

1. Validates input package id and upstream GitHub URL.
2. Ensures a fork exists under your authenticated ``gh`` account or the
   ``--organization`` owner when provided, and validates fork visibility.
3. Clones/refreshes the fork under ``--workspace-root``.
4. Runs ``fermilink compile <package_id> . --install-off`` only when ``skills/``
   does not already exist.
5. Commits and pushes to your fork default branch (no PR to upstream).
6. Calls Codex to generate one package metadata proposal
   (description/tags/router keywords), using the cloned fork repository as the
   Codex working directory and passing existing curated package ids as
   disambiguation candidates.
7. Builds deterministic curated/family entries from that proposal, validates
   format and cross-file consistency via ``scripts/validate_data.py``, then
   appends them to:

   - ``src/fermilink/data/curated_channels/skilled-scipkg.json``
   - ``src/fermilink/data/router/family_hints.json``

Batch mode from an external JSON spec:

.. code-block:: json

   {
     "packages": [
       {
         "package_id": "qutip",
         "upstream_repo_url": "https://github.com/qutip/qutip"
       },
       {
         "package_id": "meep",
         "upstream_repo_url": "https://github.com/NanoComp/meep"
       }
     ]
   }

.. code-block:: bash

   fermilink auto-compile \
     --spec-file ./packages.json \
     --fermilink-repo /absolute/path/to/FermiLink_development \
     --workspace-root ./.fermilink-auto-compile

Useful auto-compile options:

- ``--update-existing``: replace existing curated/family entries for a package id.
  Without this flag, duplicate package ids fail fast before fork/compile/Codex metadata steps.
- ``--organization``: force forks to a specific GitHub organization
  (uses ``gh repo fork --org <organization>``).
- ``--dry-run``: run fork/clone/compile/push + metadata validation without writing
  curated/family files.
- ``--cleanup-clone``: remove local cloned forks after each package.
- ``--fail-fast``: stop batch processing on first failure.

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
