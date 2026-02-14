Introduction
============

**FermiLink** is a unified Codex + Chainlit platform for scientific simulation
workflows. It keeps package routing, workspace overlay, and runtime policy
consistent across web and CLI execution.

Why FermiLink
-------------

Many scientific-agent systems fail at consistency: web behavior and CLI behavior
diverge, package context drifts, and outputs become hard to reproduce.
FermiLink addresses this with a shared execution model:

- one package-selection stack for web and terminal modes;
- one workspace overlay model with manifest tracking;
- one policy plane for provider and sandbox behavior.

Core execution modes
--------------------

- ``web``: ``fermilink start`` launches runner + Chainlit UI.
- ``exec``: ``fermilink exec`` runs one prompt in the current repository.
- ``chat``: ``fermilink chat`` runs interactive multi-turn terminal sessions.
- ``loop``: autonomous iterative runs with persistent memory.
- ``reproduce``: planner + auditor + task-loop orchestration for paper workflows.
- ``research``: planner + auditor + task-loop orchestration from an idea prompt.

What stays consistent across modes
----------------------------------

- Package routing from rules + session state.
- Overlay into a repo workspace with ownership manifest.
- Runtime policy resolution (provider + sandbox).
- Structured streaming of execution output.

How a prompt is executed
------------------------

1. Resolve package intent from request and session state.
2. Provision or reuse workspace repo for the active session.
3. Overlay selected package entries and dependency links.
4. Execute provider CLI with resolved runtime policy.
5. Stream outputs and artifacts back to UI or terminal.

See :doc:`architecture` for the full runtime flow and contracts.
