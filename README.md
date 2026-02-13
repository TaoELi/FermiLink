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
```

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
