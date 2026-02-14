<p align="center">
  <img src="src/fermilink/public/fermilink_logo.png" alt="Chatbot Server icon" width="300">
</p>

<p align="center">
  <a href="docs/README.md">
    <img src="https://img.shields.io/badge/docs-project-blue.svg" alt="Docs badge">
  </a>
  <img src="https://img.shields.io/badge/python-3.11%2B-brightgreen.svg" alt="Python versions">
</p>

# FermiLink: Unified AI Agent for Scientific Simulations

**FermiLink** is a unified AI agent for automated scientific simulations. It can be used as both a **web service** and also a **command-line tool**, accommodating a wide range of scientific packages and machines. **FermiLink** utilizes a **three-layer progressive disclosure** mechansim to efficiently performing computational tasks.

In each round of conversation, depending on the user's prompt, **FermiLink** (i) automatically loads the most suitable scientific package as the background knowledge. Then, it uses the (ii) built-in **Agent Skills** for that scientific package as the starting point to reason until reaching to the (iii) every corner of the source code tree of this package. It's unique features include:

- Unified support for a wide range of scientific packages;
- User-friendly web UI frontend and command-line tool;
- Concurrent support of multiple users.

It has built-in support for many scientific packages, including psi4, lammps, qutip, meep, ase, maxwelllink. Users can also easily add their custom scientific package in this workflow.

The official web service for **FermiLink** is: [https://glogg.physics.udel.edu](https://glogg.physics.udel.edu). For security reasons, MPI parallel computing, SLRUM jobs, and network communications are **disabled** in this web service.

## Quick Start

```bash
# 1) Install
pip install .

# 2) Authenticate Codex
codex login

# 3) Install at least one scientific package as background knowledge
fermilink install maxwelllink --activate
# or install multiple at once (cannot combine with --activate):
# fermilink install ase meep qutip
# fermilink activate ase

# 4) Start web service for ChatGPT-like experience
fermilink start

# 5) (Optional) Configure agent runtime policy
fermilink agent codex --sandbox
# or: fermilink agent --bypass-sandbox
```

## Compile Local Projects Into Packages

Use `compile` to generate an enriched `skills/` folder for a local scientific
codebase (via 3 Codex passes) and install it into FermiLink package storage:

```bash
fermilink compile pyscf .
```

This command:

- checks package-id conflicts in the registry;
- copies `sci-skills-generator` into your project root;
- runs Codex twice to generate/audit `skills/`;
- removes `sci-skills-generator`;
- runs Codex a third time to validate and enrich `skills/`;
- installs the project to `scientific_packages/packages/<package-id>`.

## Check Curated Package Availability

Use `avail` to query whether a package is available in the curated
`TEL-Research-Group` source channel:

```bash
fermilink avail ase
fermilink avail quantum
```

## Run One Prompt Locally (Web-Like Routing)

Use `exec` to mirror the web routing + overlay flow directly in your current
repository:

```bash
fermilink exec "run a single-mode cavity coupled to a weakly excited two-level system"

# Or provide prompt from a file
fermilink exec prompt.md
```

What `exec` does:

- routes your prompt to the best installed package (keyword router + second guess);
- overlays that package into the current directory via symlinks;
- syncs `AGENTS.md` from `software/`;
- does not copy web UI assets (`public/`) into your repo (those are seeded only for `fermilink start`);
- runs `codex exec` with your prompt.

Optional flags:

- `--package <id>`: pin a package and skip auto routing
- `--init-git`: auto-run `git init` when current directory is not a git repo
- `--no-init-git`: fail instead of prompting for git init

## Run an Autonomous Loop Iteration (Exec + Persistent Memory)

Use `loop` to run up to `--max-iterations` autonomous iterations (default: 10)
that persist long-term state to `projects/memory.md`. It stops early when it
prints `<promise>DONE</promise>`. When not done, the agent can suggest the next
poll interval via `<wait_seconds>...</wait_seconds>`; loop applies
`min(agent_wait, --max-wait-seconds)` and falls back to `--wait-seconds` when
the wait tag is missing/invalid.

```bash
# Prompt as a file
fermilink loop prompt.md

# Prompt as an inline string
fermilink loop "refactor the router and add tests"

# Override iteration cap
fermilink loop --max-iterations 50 prompt.md

# Sleep between iterations (useful for long-running jobs)
fermilink loop --wait-seconds 30 prompt.md

# Cap dynamic waits from agent hints
fermilink loop --wait-seconds 30 --max-wait-seconds 300 prompt.md
```

## Reproduce a Full Paper (Planner + Auditor + Multi-Task Loop)

Use `reproduce` to orchestrate many `loop` runs for publication-scale workflows:

```bash
# Source from a file (tex/md/txt/pdf)
fermilink reproduce paper.tex

# Source from inline text
fermilink reproduce "reproduce Figures 1-4 from this paper ..."

# Generate plan only (no loop execution)
fermilink reproduce paper.tex --plan-only

# Generate/audit report only from existing run artifacts
fermilink reproduce paper.tex --report-only

# Override loop controls forwarded to each task run
fermilink reproduce paper.tex --max-iterations 20 --wait-seconds 30 --max-wait-seconds 300
```

What `reproduce` does:

- runs a planner pass to split the paper request into medium tasks;
- runs an auditor pass to validate/fix that plan;
- writes run artifacts under `projects/reproduce/<run-id>/`:
  - `plan.json`, `state.json`, `prompts/task_*.md`, `logs/`, `archive/`, `summaries/`;
- executes tasks sequentially via `fermilink loop` until each task prints `<promise>DONE</promise>`;
- retries a task up to `--task-max-runs` when loop reaches its internal max-iteration bound;
- after all tasks finish, generates and audits a polished report at `projects/reproduce/report.md`;
- `--skip-report` skips final report generation; `--report-only` regenerates/audits report from existing artifacts;
- supports resume by default (`--restart` starts a new run).

## Research Mode (Idea -> Plan -> Multi-Task Loop)

Use `research` when you start from a short idea instead of an existing paper:

```bash
# Plan + execute
fermilink research "Design and validate a cavity QED protocol with parameter sweeps"

# Plan only (then manually edit plan.json / prompts/*.md)
fermilink research idea.md --plan-only

# Report only from existing run artifacts
fermilink research idea.md --report-only

# Resume execution from edited plan artifacts
fermilink research idea.md
```

What `research` does:

- runs planner + auditor passes to produce a structured multi-task research plan;
- writes artifacts under `projects/research/<run-id>/` (`plan.json`, `state.json`, `prompts/`, `logs/`, `archive/`);
- executes tasks sequentially through `fermilink loop` with the same wait/iteration controls as `reproduce`;
- after all tasks finish, generates and audits a polished report at `projects/research/report.md`;
- `--skip-report` skips final report generation; `--report-only` regenerates/audits report from existing artifacts;
- supports manual plan edits after `--plan-only`, then resumes from edited `plan.json`.

## Run Interactive Multi-Turn Chat From CLI

Use `chat` for a terminal REPL that mirrors web conversation behavior:

```bash
fermilink chat
```

What `chat` does each turn:

- builds the same simplified transcript-style prompt as web mode;
- re-runs package routing (plus second guess) and can switch package when needed;
- overlays selected package content via symlinks into the current directory;
- does not copy web UI assets (`public/`) into your repo (those are seeded only for `fermilink start`);
- streams provider stdout/stderr live to the terminal;
- runs provider execution and appends the assistant reply to local chat history.

Optional flags:

- `--package <id>`: pin one installed package for the whole session
- `--sandbox <mode>`: enforce a sandbox mode for this chat session only
- `--init-git`: auto-run `git init` when current directory is not a git repo
- `--no-init-git`: fail instead of prompting for git init

Global runtime policy:

- `fermilink agent --sandbox`: enforce sandbox (uses configured mode)
- `fermilink agent --bypass-sandbox`: bypass sandbox
- `fermilink agent codex|claude|gemini`: set provider for runner/web/exec/chat/compile
- `fermilink compile` inherits provider from `fermilink agent`, while keeping compile sandbox safety defaults.
- Current execution support is `codex`; `claude`/`gemini` are forward-compatible policy values.

## Core Runtime Paths

When not overridden, FermiLink stores runtime data under:

- `~/.fermilink/scientific_packages`
- `~/.fermilink/workspaces`
- `~/.fermilink/runtime`
- `~/.fermilink/public` (Chainlit static assets + local artifact storage for web UI)
- `~/.fermilink/.chainlit` (Chainlit sqlite DBs and internal state)

## Documentation

- [Install and Run](docs/install.md)
- [Scientific Package Management](docs/scientific-packages.md)
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Repository Map](docs/repository-map.md)

Internal code layout note: shared package-management internals now live under
`src/fermilink/packages/`; legacy import paths under `src/fermilink/` are kept
as compatibility shims.
