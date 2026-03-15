<p align="center">
  <img src="src/fermilink/public/fermilink_logo.png" alt="FermiLink logo" width="300">
</p>

<p align="center">
  <a href="docs/source/overview.rst"><img src="https://img.shields.io/badge/docs-project-blue.svg" alt="Docs badge"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPLv3-blue.svg" alt="License: AGPLv3"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-brightgreen.svg" alt="Python versions">
</p>

# FermiLink: AI Agent for Autonomous Scientific Simulations

**FermiLink** is a unified agent framework for autonomous scientific simulations. It runs on personal laptops, HPC clusters, or even your phone. You can interact with it through **command-line tools**, a **web UI** with a ChatGPT-like chat interface, or a **Telegram bot** for on-the-go access.

With [an official package channel](https://github.com/skilled-scipkg), **FermiLink** ships with built-in support for more than 150 scientific packages. You can also use its command-line tools to build a local knowledge base from any scientific package, publication, or custom simulation recipe.

FermiLink now also includes a separate benchmark-gated optimization mode,
``fermilink optimize``, for iterative code optimization inside a scientific
package source tree using fixed ``skills/`` context, an embedded worker loop
for iterative code changes/debugging/long-job waits, a controller agent for
reflective decision/memory updates, and hard scientific benchmark validation.
Quick mode (``fermilink optimize prompt.md``) can scaffold benchmark assets by
seeding from language-specific examples under ``scripts/``.

Once you describe a goal, **FermiLink** takes care of the rest — routing tasks to the right packages, running multi-step simulations, and iterating autonomously. It is designed to sustain long-running computational jobs for days or weeks without human intervention.

## Quick Start
For beginners, start with:

```bash
pip install .
fermilink
```

Running ``fermilink`` with no subcommand opens a guided setup flow in an
interactive terminal. It checks provider availability, installed scientific
packages, web/runner status, Telegram configuration, and optional HPC profile
setup, then routes you to the right next step. The same beginner menu also
includes guided advanced entrypoints for ``fermilink compile`` and
``fermilink recompile`` when you need to onboard a local package or patch
installed package skills from a manuscript or workspace memory. On the first
interactive entry it also prints a terminal hero banner before entering the
setup flow.

You can also use the manual workflow directly:

```bash
# 1. Install
pip install .

# 2. Install at least one scientific package
fermilink install meep

# 3. set up the agent provider 
fermilink agent codex/claude/gemini

# 4.1. Use command-line tool to do autonomous scientific research
fermilink exec/loop/reproduce/research goal.md

# 4.1b. Run benchmark-gated code optimization inside a package repo
fermilink optimize pyscf /path/to/pyscf --benchmark scripts/python-pyscf-scf-benchmark.yaml

# 4.2. Start web UI service for ChatGPT-like experience
fermilink start

# 4.3. Start the gateway for supporting Chatbots via Telegram
export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token-from-@BotFather>"
export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<numeric-id-from-@get_telegram_id_smppcenter_bot>"
fermilink gateway
```

## Documentation

Visit the [documentation](docs/source/overview.rst) for installation details, tutorials, and API reference.

## Build documentation

User can build the documentation website for FermiLink locally with the following commands:

```bash
   pip install ".[docs]"
   make doc html
```

## Citation

If you find **FermiLink** helpful for your research, please cite the following reference:

- TEL Research Group. *A Unified Agent Framework for Multidomain Autonomous Scientific Simulations*. [arXiv:tbd](https://arxiv.org/abs/tbd) (2026).
