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

## Run One Prompt Locally (Web-Like Routing)

Use `exec` to mirror the web routing + overlay flow directly in your current
repository:

```bash
fermilink exec "run a single-mode cavity coupled to a weakly excited two-level system"
```

What `exec` does:

- routes your prompt to the best installed package (keyword router + second guess);
- overlays that package into the current directory via symlinks;
- syncs `AGENTS.md` from `software/`;
- runs `codex exec` with your prompt.

Optional flags:

- `--package <id>`: pin a package and skip auto routing
- `--init-git`: auto-run `git init` when current directory is not a git repo
- `--no-init-git`: fail instead of prompting for git init

Global runtime policy:

- `fermilink agent --sandbox`: enforce sandbox (uses configured mode)
- `fermilink agent --bypass-sandbox`: bypass sandbox
- `fermilink agent codex|claude|gemini`: set provider for runner/web/exec
- `fermilink compile` also inherits provider from `fermilink agent`, while keeping compile sandbox safety defaults.
- Current execution support is `codex`; `claude`/`gemini` are forward-compatible policy values.

## Core Runtime Paths

When not overridden, FermiLink stores runtime data under:

- `~/.fermilink/scientific_packages`
- `~/.fermilink/workspaces`
- `~/.fermilink/runtime`

## Documentation

- [Install and Run](docs/install.md)
- [Scientific Package Management](docs/scientific-packages.md)
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Repository Map](docs/repository-map.md)
