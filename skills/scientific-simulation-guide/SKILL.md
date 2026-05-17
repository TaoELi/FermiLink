---
name: scientific-simulation-guide
description: Guide users through setting up and running scientific simulations with FermiLink after `pip install fermilink` and `fermilink init`. Covers package installation, provider setup, execution modes, HPC configuration, and workflow selection.
---

# FermiLink Scientific Simulation Guide

Use this skill when the user asks how to use FermiLink for scientific
simulations, needs help choosing an execution mode, or wants guidance on
setting up their environment after running `pip install fermilink` and
`fermilink init`.

Read this first:

- `references/simulation-walkthrough.md`

## Assumptions

The user has already completed:

1. `pip install fermilink`
2. `fermilink init` in a clean project directory

They now have a local workspace with FermiLink's `AGENTS.md`, `skills/`,
and `CLAUDE.md`/`GEMINI.md` aliases provisioned. They are inside this
initialized directory talking to a local agent (Codex, Claude Code, Gemini
CLI, a desktop app, or a VS Code extension).

## Guidance Priorities

1. **Check prerequisites first.** Before suggesting any simulation command,
   confirm these are in place:
   - At least one provider CLI is authenticated (`codex login`, `claude`,
     or `gemini`).
   - At least one scientific package is installed
     (`fermilink install <package_id>`).

2. **Help the user pick the right mode.** Match the simulation scope to the
   right FermiLink mode:
   - Quick, self-contained runs (< 30 min) -> `fermilink exec`
   - Interactive exploration or learning -> `fermilink chat`
   - Long-running or iterative jobs (PID/SLURM polling) -> `fermilink loop`
   - Full-paper reproduction -> `fermilink reproduce`
   - Idea-driven research campaigns -> `fermilink research`
   - ChatGPT-like browser interface -> `fermilink start` (web UI)
   - Remote control from phone/travel -> `fermilink gateway` (Telegram bot)

3. **Prefer concrete commands over conceptual explanations.** Show the exact
   command, expected output paths, and next steps.

4. **Surface HPC options when relevant.** If the user mentions clusters,
   SLURM, multi-node, or large-scale runs, guide them to:
   - `fermilink hpc` for default HPC profile setup.
   - `--hpc-profile <json>` flag on `exec`/`loop`/`research`/`reproduce`.

5. **Point to package discovery.** If the user is unsure which package to
   install, suggest:
   - `fermilink avail <keyword>` to search the curated catalog.
   - `fermilink list` to see what is already installed locally.

## Step-by-Step Recommended Flow

### Step 1: Authenticate a provider

The user needs at least one agent provider CLI installed and logged in.

```bash
# Codex (OpenAI)
npm i -g @openai/codex   # or: brew install codex
codex login

# Claude (Anthropic)
# install from official distribution, then authenticate

# Gemini (Google)
# install from official distribution, then authenticate
```

Then set FermiLink's default provider:

```bash
fermilink agent codex    # or claude, gemini, opencode
```

### Step 2: Install a scientific package

```bash
# search for available packages
fermilink avail <keyword>

# install a package knowledge base
fermilink install <package_id> --activate

# install multiple packages
fermilink install ase meep qutip
fermilink activate meep
```

### Step 3: Run a simulation

Pick the mode that matches the job scope:

```bash
# one-shot execution
fermilink exec "simulate X and plot Y"
fermilink exec goal.md

# interactive multi-turn chat
fermilink chat

# autonomous iterative loop (supports PID/SLURM job polling)
fermilink loop goal.md
fermilink loop --max-iterations 10 --max-wait-seconds 3600 goal.md

# full-paper reproduction
fermilink reproduce paper.tex --plan-only   # review plan first
fermilink reproduce paper.tex               # execute

# idea-driven research
fermilink research idea.md --plan-only      # review plan first
fermilink research idea.md                  # execute
```

### Step 4 (optional): HPC configuration

```bash
# interactive HPC profile setup
fermilink hpc

# use explicit HPC profile per run
fermilink loop goal.md --hpc-profile hpc_profile.json
```

### Step 5 (optional): Alternative interfaces

```bash
# web UI (ChatGPT-like browser experience)
fermilink start

# Telegram bot for remote control
export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token>"
export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<user-id>"
fermilink gateway
```

## When Users Ask About Specific Topics

- **"Which package should I use?"** -> Run `fermilink avail <keyword>` and
  describe what the top results cover. If the user's domain is not in the
  curated catalog, suggest `fermilink compile` to build a custom knowledge
  base from a local package.
- **"How do I write a goal file?"** -> A goal file is a plain-text or
  markdown prompt describing the simulation. It can be as simple as one
  sentence or a detailed multi-paragraph specification. Pass it to any mode:
  `fermilink exec goal.md`.
- **"How do I use multiple packages together?"** -> Use
  `fermilink dependencies <main_pkg> --package <aux_pkg>` to expose an
  auxiliary package alongside the main one during execution.
- **"How do I check what FermiLink set up?"** -> `fermilink agent --json`
  shows provider/model/sandbox state. `fermilink list` shows installed
  packages. `fermilink` (bare) gives a full status overview.
- **"How does memory work?"** -> FermiLink maintains unified memory at
  `projects/memory.md` across modes. It includes short-term plan/progress
  and long-term durable outcomes (file maps, simulation history, key
  results). Memory persists across `exec`/`loop`/`research`/`reproduce`
  runs in the same workspace.
- **"How do I clean up and start over?"** -> `fermilink clean` removes
  managed workspace artifacts. Then `fermilink init` to re-bootstrap.

## Response Style

- Lead with the command, not the explanation.
- Include expected artifacts/paths when relevant.
- Keep answers short unless the user asks for detail.
- When HPC is involved, always mention `--hpc-profile`.
- If the user's question maps to a specific docs page, mention it:
  `docs/source/usage.rst`, `docs/source/installation.rst`,
  `docs/source/scientific_packages.rst`, etc.
