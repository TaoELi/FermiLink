# FermiLink Simulation Walkthrough

This reference covers the end-to-end flow for running scientific simulations
with FermiLink, from a freshly initialized workspace to completed results.

## What `fermilink init` sets up

Running `fermilink init` in a clean directory creates:

- `AGENTS.md` — workspace-level instructions that tell local agents (Codex,
  Claude Code, Gemini CLI, etc.) how to use FermiLink features.
- `CLAUDE.md` / `GEMINI.md` — aliases pointing to `AGENTS.md` so each
  provider picks up the same instructions automatically.
- `skills/` — a copy of FermiLink's bundled skill files for agent reasoning.

The directory is now ready for agent-assisted scientific simulation.

## Provider setup

FermiLink delegates actual code generation and reasoning to a provider CLI.
At least one must be installed, authenticated, and set as the default.

| Provider | Install                          | Auth            |
|----------|----------------------------------|-----------------|
| Codex    | `npm i -g @openai/codex` or `brew install codex` | `codex login`   |
| Claude   | Official distribution            | `claude` (interactive) |
| Gemini   | Official distribution            | `gemini` (interactive) |

Set the active provider:

```bash
fermilink agent codex          # or claude, gemini, deepseek
```

Optional tuning:

```bash
fermilink agent codex --model gpt-5.3-codex --reasoning-effort xhigh
fermilink agent claude --bypass-sandbox --model sonnet --reasoning-effort high
```

Check current settings:

```bash
fermilink agent --json
```

## Package installation

FermiLink separates **package knowledge bases** (documentation, source code
trees, agent skills) from **execution environments**. Installing a package
in FermiLink means downloading its knowledge base so agents can reason about
it — it does not install the package's runtime dependencies.

### Discovery

```bash
fermilink avail meep           # exact match
fermilink avail quantum        # keyword search
```

The curated catalog (`skilled-scipkg` GitHub org) has 150+ packages across
domains: electromagnetics, quantum chemistry, molecular dynamics, materials
science, astrophysics, bioinformatics, and more.

### Install and activate

```bash
fermilink install meep --activate
```

`--activate` sets this package as the default for routing. Without it, the
package is installed but not the default.

Install multiple packages:

```bash
fermilink install ase meep qutip
fermilink activate meep
```

### Multi-package sessions

When a simulation needs auxiliary tools (e.g., Packmol for LAMMPS
preprocessing):

```bash
fermilink dependencies lammps --package packmol
```

### Check installed packages

```bash
fermilink list
```

## Execution modes

### `exec` — one-shot execution

Best for: quick, self-contained simulations that complete within ~30 minutes.

```bash
fermilink exec "simulate a photonic crystal bandgap and plot the band structure"
fermilink exec goal.md
fermilink exec goal.md --hpc-profile hpc_profile.json
fermilink exec goal.md --package meep    # pin a specific package
```

What happens:

1. FermiLink routes the prompt to the best-matching installed package.
2. Package knowledge base is overlaid into the workspace.
3. The provider generates and executes code.
4. A best-effort git checkpoint commit is created.

### `chat` — interactive terminal chat

Best for: exploratory work, learning a package, iterating on parameters.

```bash
fermilink chat
fermilink chat --package ase
```

Each turn re-routes and overlays as needed. Session history is maintained.

Note: `chat` does not support `--hpc-profile`. Use `exec` or `loop` for
HPC-targeted runs.

### `loop` — autonomous iterative loop

Best for: long-running simulations, iterative refinement, jobs that require
PID or SLURM polling between iterations.

```bash
fermilink loop goal.md
fermilink loop --max-iterations 10 --max-wait-seconds 3600 goal.md
fermilink loop --hpc-profile hpc_profile.json goal.md
```

Key behaviors:

- Iterates until a done signal or iteration cap.
- Persists memory to `projects/memory.md`.
- Polls PID and SLURM jobs between iterations (via `<pid_number>` and
  `<slurm_job_number>` tags).
- Best-effort checkpoint commit on completion.

### `reproduce` — publication reproduction

Best for: reproducing figures and results from an existing paper.

```bash
fermilink reproduce paper.tex --plan-only    # review plan first
# (optionally edit projects/reproduce/<run-id>/plan.json)
fermilink reproduce paper.tex                # execute
fermilink reproduce paper.tex --report-only  # generate report only
fermilink reproduce paper.tex --hpc-profile hpc_profile.json
```

Artifacts are written to `projects/reproduce/<run-id>/`.

### `research` — idea-driven research

Best for: starting from a research idea and running multi-task campaigns.

```bash
fermilink research idea.md --plan-only    # review plan first
# (optionally edit projects/research/<run-id>/plan.json)
fermilink research idea.md                # execute
fermilink research idea.md --report-only  # generate report only
fermilink research idea.md --hpc-profile hpc_profile.json
```

Artifacts are written to `projects/research/<run-id>/`.

### Web UI and Telegram

```bash
# browser-based ChatGPT-like interface
fermilink start

# Telegram bot for remote control
export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token>"
export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<user-id>"
fermilink gateway
```

## HPC configuration

For cluster runs, create a default HPC profile:

```bash
fermilink hpc
```

This writes `~/.fermilink/HPC_PROFILE.json`. A typical profile:

```json
{
   "slurm_default_partition": "shared",
   "slurm_defaults": "--nodes=1 --ntasks=1 --cpus-per-task=4 --time=24:00:00",
   "slurm_resource_policy": "Use serial/single-node defaults unless the method explicitly requires MPI or multi-node scaling"
}
```

Per-run override:

```bash
fermilink exec goal.md --hpc-profile my_cluster.json
```

Priority: explicit `--hpc-profile` > default home profile > local PID-based
execution.

## Unified memory

FermiLink maintains `projects/memory.md` across all modes in a workspace.
It contains:

- **Short-term sections**: `Plan`, `Progress log` — reset at each
  `research`/`reproduce` workflow entry.
- **Long-term sections**: `File map`, `Simulation history`, `Key results`,
  `Parameter source mapping` — persisted across runs.

This lets sequential `exec` and `loop` runs build on prior context without
re-specifying everything.

## Writing effective prompts / goal files

A goal file is plain text or markdown describing what to simulate. Tips:

- Be specific about the physical system, parameters, and desired outputs.
- Mention the target package if you know it, or let FermiLink route
  automatically.
- For `loop` mode, describe the full campaign (multiple runs, parameter
  sweeps) so the agent can plan iterations.
- For `research`/`reproduce`, provide enough detail for multi-task planning
  (figures to reproduce, methods to implement, comparisons to make).

Example (`goal.md`):

```markdown
Simulate a 1D photonic crystal with alternating dielectric layers
(n=1.0 and n=3.5, period=1 um) using FDTD.

Compute and plot the transmission spectrum from 0.1 to 1.0 um wavelength.
Identify the photonic bandgap edges.

Use Meep for the simulation.
```

## Quick reference

| Task                        | Command                                          |
|-----------------------------|--------------------------------------------------|
| Check status                | `fermilink`                                      |
| Set provider                | `fermilink agent codex`                          |
| Search packages             | `fermilink avail <keyword>`                      |
| Install package             | `fermilink install <id> --activate`               |
| List installed              | `fermilink list`                                 |
| Quick run                   | `fermilink exec "..."`                           |
| Interactive chat            | `fermilink chat`                                 |
| Long-running loop           | `fermilink loop goal.md`                         |
| Reproduce paper             | `fermilink reproduce paper.tex`                  |
| Research from idea          | `fermilink research idea.md`                     |
| HPC setup                   | `fermilink hpc`                                  |
| Web UI                      | `fermilink start`                                |
| Telegram bot                | `fermilink gateway`                              |
| Build custom package        | `fermilink compile`                              |
| Clean workspace             | `fermilink clean`                                |
