Tutorial: Experimental Workflows with Exploop
=============================================

This tutorial shows how to use **FermiLink exploop mode** for iterative
laboratory or instrument-driven experimental workflows.

The key idea is that ``exploop`` connects an agent workflow with **PID
polling**. The agent can start a long measurement as a detached process,
print ``<pid_number>PID</pid_number>``, and return control immediately.
**FermiLink** then polls that PID, waits until the measurement finishes,
records new artifacts, refreshes ``projects/memory.md``, and only then starts
the next agent reasoning turn. This lets very long iterative agent workflows
run without keeping the agent blocked inside one tool call.

The complete flow is:

1. Install **FermiLink** with ``pip``.
2. Configure the Codex agent policy (tested under both Windows and MacOS environments).
3. Install remote experimental skills from a GitHub repository.
4. Initialize a local workspace from that installed repository.
5. Run ``fermilink exploop goal.md`` until the experiment is complete.

This tutorial is self-contained. You can follow it from a fresh shell.


Prerequisites
~~~~~~~~~~~~~

You need:

- Python ``>= 3.11``
- ``git`` on ``PATH``
- OpenAI Codex CLI installed and authenticated
- Access to a GitHub repository that contains your experimental skills
- A lab workstation or control computer that can safely run the instrument
  scripts referenced by those skills

``exploop`` is designed for Windows-oriented lab workflows and uses native
Windows PID polling through ``tasklist``, as most experimental devices are Windows-based. It also works for
development and testing on macOS/Linux.

.. warning::

   Experimental workflows can interact with real hardware. Do not let the agent modify hardcoded hardware safety limits
   unless you explicitly ask for that change.


Step 1. Install **FermiLink**
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A clean Python environment is recommended:

.. code-block:: bash

   conda create -n fermilink-exploop python=3.11 -y
   conda activate fermilink-exploop

   pip install fermilink

Quick check:

.. code-block:: bash

   fermilink --help


Step 2. Configure the Codex agent policy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Install and authenticate Codex CLI if you have not done that already:

.. code-block:: bash

   npm i -g @openai/codex
   codex login

Then set **FermiLink** to use Codex with a high-reasoning model and  optionally relaxed
sandboxing (``--bypass-sandbox`` or enforcing sanboxing ``--sandbox``) for direct command execution:

.. code-block:: bash

   fermilink agent codex --model gpt-5.5 --reasoning-effort xhigh --bypass-sandbox

Confirm the active policy:

.. code-block:: bash

   fermilink agent --json

.. warning::

   ``--bypass-sandbox`` allows the agent to run local measurement commands and
   instrument-control scripts. Use it only on a trusted experimental control
   machine. For dry runs or mock hardware, prefer ``--sandbox``.


Step 3. Install remote experimental skills from GitHub or local path
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An experimental skills repository can be any GitHub repository that contains a
``skills/`` directory with one or more ``SKILL.md`` files. The repository can
also contain scripts, notebooks, instrument documentation, calibration notes,
or templates that the skills reference.

Example repository layout:

.. code-block:: text

   lab-experiment-skills/
   |-- skills/
   |   `-- laser-scan/
   |       `-- SKILL.md
   |-- scripts/
   |   `-- run_scan.py
   `-- docs/
       `-- instrument_notes.md

Install the repository as a **FermiLink** package knowledge base:

.. code-block:: bash

   fermilink install https://github.com/skilled-quantum/skill-mos2-quantum-transport --workflow-type experiment


The option ``--workflow-type experiment`` is necessary as **FermiLink** by default expects simulation workflows.

If the repository is private, make sure your GitHub SSH key has access. The
package id is derived from the GitHub repository name. In the example above,
the package id is ``skill-mos2-quantum-transport``.

Confirm that the package is registered:

.. code-block:: bash

   fermilink list


Step 4. Initialize a local experimental workspace
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a workspace for one experimental campaign and initialize it from the
installed GitHub repository name:

.. code-block:: bash

   mkdir -p ~/experiments/mos2-transport-demo
   cd ~/experiments/mos2-transport-demo

   fermilink init skill-mos2-quantum-transport

What ``fermilink init <github-url-repo-name>`` does here:

- Copies the package ``skills/`` directory into the workspace.
- Overlays the installed repository contents so the agent can inspect scripts,
  docs, and examples.
- Writes ``AGENTS.md`` plus ``CLAUDE.md`` / ``GEMINI.md`` aliases for local
  agent guidance.
- Records a manifest so ``fermilink clean`` can safely remove the overlay
  later.

After initialization, the workspace is the place where measurements, logs,
figures, and memory will accumulate.


Step 5. Write ``goal.md``
~~~~~~~~~~~~~~~~~~~~~~~~~

Create a concise goal file in the workspace. The exact content depends on your
instrument and skills, but a good experimental goal states the objective,
safety constraints, allowed scripts, expected artifacts, and done criteria.

If the experimental skill is well-written, you can write a brief goal and
let the agent to figure out what to do.

Example:

.. code-block:: markdown

   # Goal: Single-layer MOS2 quantum transport measurement

   This is a dry-run. You are expected to provide a procedure.md file with a step-by-step procedure, without launching and submitting pid jobs.

   Follow the five-step procedure to measure quantum transport in a single-layer MoS2 2D material sample.

   Note that for step 2, sample 1001 V_TG points instead of using smaller sweeps.


For the above goal, delete the second line (``This is a dry-run...``) for a real run. The agent will read the goal, plan a procedure, and execute it by calling the experimental skills and scripts you installed.


Step 6. Run ``exploop``
~~~~~~~~~~~~~~~~~~~~~~~

Start the iterative experimental workflow:

.. code-block:: bash

   fermilink exploop goal.md

For very long measurements, tune the iteration and polling limits:

.. code-block:: bash

   fermilink exploop --max-iterations 30 --wait-seconds 10 --max-wait-seconds 259200 goal.md

Useful options:

- ``--max-iterations``: maximum agent turns before stopping.
- ``--wait-seconds``: PID polling interval and fallback sleep between turns.
- ``--max-wait-seconds``: hard cap for one PID wait window.
- ``--sandbox``: per-run sandbox override if you do not want to use the global
  ``fermilink agent`` policy.


What happens during an ``exploop`` run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each iteration follows the same control loop:

1. ``exploop`` writes the local ``AGENTS.md`` experimental guide and prepares
   ``projects/memory.md``.
2. It discovers local ``skills/*/SKILL.md`` files and lists newly observed
   artifacts.
3. It prompts the configured agent with the goal, memory, skills, and artifact
   summary.
4. If the agent emits ``<pid_number>12345</pid_number>``, **FermiLink** polls
   that process instead of immediately asking the agent to continue.
5. When the PID exits, **FermiLink** snapshots new or modified files under
   ``projects/`` and starts the next agent turn with updated context.
6. When the agent emits ``<promise>DONE</promise>``, ``exploop`` exits
   successfully.

Typical PID-polling output looks like this:

.. code-block:: text

   [exploop] instructions: AGENTS.md
   [exploop] memory: projects/memory.md
   [exploop] iteration 1/30
   <pid_number>24816</pid_number>
   [exploop] measurement running; polling PID(s) every 10.0s and showing status every 60s: 24816
   [exploop] still waiting for measurement PID(s) after 60.1s: 24816
   [exploop] measurement PID polling complete after 312.4s
   [exploop] iteration 2/30

This PID boundary is the main difference between ``exploop`` and a normal
interactive agent session. The agent does not need to remain inside a blocking
tool call for hours. It launches a measurement, emits the PID, and **FermiLink**
handles the waiting before the next reasoning turn.


Files created in the workspace
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

After ``fermilink init`` and ``fermilink exploop``, you should expect:

- ``skills/``: copied experimental skills from the GitHub repository.
- ``AGENTS.md``: local exploop guidance for the agent.
- ``CLAUDE.md`` / ``GEMINI.md``: aliases for agents that read those filenames.
- ``projects/memory.md``: persistent campaign memory, pending work, commands,
  PIDs, and artifact inventory.
- ``projects/YYYY-MM-DD-short-name/``: recommended location for measurement
  outputs.
- ``fermilink-exploop/state.json``: artifact-tracking state used by
  ``exploop``.

Do not edit the managed ``skills/`` copy directly unless you intend local-only
changes. For reusable improvements, update the GitHub skills repository and
reinstall it.


Cleanup
~~~~~~~

When the campaign is finished and you no longer need the overlaid skills in
the workspace, run:

.. code-block:: bash

   fermilink clean

``fermilink clean`` removes the managed overlay files and leaves your
measurement outputs under ``projects/`` untouched. The installed GitHub package
stays under **FermiLink**'s package store, so you can initialize another
workspace from it later.


Troubleshooting
~~~~~~~~~~~~~~~

- **The agent does not see my experimental skills**: confirm that
  ``skills/*/SKILL.md`` exists in the workspace after ``fermilink init``.
- **Private GitHub install fails**: confirm your SSH key has repository read
  access; **FermiLink** registers GitHub repositories through SSH-backed clone
  metadata.
- **PID is immediately reported as finished or not found**: the agent likely
  emitted a launcher/wrapper PID instead of the final detached measurement PID.
  Update the skill to redirect stdin/stdout/stderr and emit the child process
  PID.
- **``exploop`` reaches ``--max-wait-seconds``**: the measurement is still
  running after the configured cap. Inspect the instrument state, then resume
  from the same workspace when safe.
- **``fermilink init`` reports a conflict**: move the conflicting local file or
  rerun with ``--force`` only if you are sure the managed overlay may replace
  it.
