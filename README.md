<p align="center">
  <img src="src/fermilink/public/fermilink_logo.png" alt="FermiLink logo" width="300">
</p>

<p align="center">
  <a href="docs/source/overview.rst"><img src="https://img.shields.io/badge/docs-project-blue.svg" alt="Docs badge"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-brightgreen.svg" alt="Python versions">
</p>

# FermiLink: AI Agent for Autonomous Scientific Simulations

**FermiLink** is a unified agent framework for autonomous scientific simulations. It can be used in personal laptops, HPC clusters, or cellphones. Apart from providing a set of powerful **command-line tools**, **FermiLink** also supports a **web UI** for ChatGPT-like experience plus **chatting bots** in Telegram for OpenClaw-like experience. 

With [an official package channel](https://github.com/skilled-scipkg), **FermiLink** has built-in support for more than 65 scientific packages. Additionally, users can easily use the command-line tools in **FermiLink** to create knowledge database for arbitrary scientific packages, publications, or secret simulation receipes locally. 

After receiving the user's request, **FermiLink** utilizes a **four-layer progressive disclosure** mechansim to efficiently perform multidomain computational tasks. Specially designed for modern HPC simulations, it can run a set of computational tasks for days or even weeks in HPC clusters without human interference.

## Quick Start

```bash
# 1. Install
pip install .

# 2. Install at least one scientific package as the knowledge database
fermilink install meep

# 3.1. Use command-line tool to do autonomous scientific research
fermilink research goal.md

# 3.2. Start web UI service for ChatGPT-like experience
fermilink start

# 3.3. Start the gateway for supporting Chatbots via Telegram
fermilink gateway
```

## Documentation

Visit the [documentation](docs/source/overview.rst) for installation details, tutorials, and API reference.

## Citation

If you find **FermiLink** helpful for your research, please cite the following reference:

- TEL Research Group. *A Unified Agent Framework for Multidomain Autonomous Scientific Simulations*. [arXiv:tbd](https://arxiv.org/abs/tbd) (2026).
