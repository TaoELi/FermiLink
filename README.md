<p align="center">
  <img src="src/fermilink/public/fermilink_logo.png" alt="FermiLink logo" width="300">
</p>

<p align="center">
  <a href="https://taoeli.github.io/FermiLink/"><img src="https://img.shields.io/badge/docs-project-blue.svg" alt="Docs badge"></a>
    <a href="https://pypi.org/project/fermilink/"><img src="https://img.shields.io/pypi/v/fermilink.svg?label=pypi&logo=pypi" alt="PyPI version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPLv3-blue.svg" alt="License: AGPLv3"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-brightgreen.svg" alt="Python versions">
  <a href="https://arxiv.org/abs/2604.03460"><img src="https://img.shields.io/badge/arXiv-2604.03460-b31b1b.svg" alt="arXiv:2604.03460"></a>
</p>

# FermiLink: Unified Agent Framework for Multidomain Autonomous Scientific Simulations

[**FermiLink**](https://taoeli.github.io/FermiLink/) is a unified agent framework for multidomain autonomous scientific simulations. It runs on personal laptops, **high-performance computing (HPC) clusters**, or even your cellphone. You can interact with it through **command-line tools**, a **web UI** with a ChatGPT-like chat interface, or a **Telegram bot** for remote computing when you travel or sleep.


With [an official package channel](https://github.com/orgs/skilled-scipkg/repositories), **FermiLink** ships with built-in support for more than 150 scientific packages. You can also use its command-line tools to build a local knowledge base from any local scientific package or simulation pipeline.

Once you describe a goal, **FermiLink** takes care of the simulations on both workstations and **HPC clusters**. It is designed to sustain long-running multi-task computational jobs for days or weeks without human intervention.


## Quick Start
Install the **FermiLink** package with pip:

```bash
pip install fermilink
```

To use any **FermiLink** feature, open a clean directory,

```bash
mkdir myproject
cd myproject
fermilink init
```

Then open **any local agent** (OpenAI Codex, Claude Code, Gemini CLI, their desktop apps, or VS Code extension, etc) within this directory and ask how to use:
- **exec/chat/loop/research/reproduce** command line tools 
- **Web UI** 
- **Telegram remote control**
- or a general question regarding how to setup or run simulations with **FermiLink**


You can also simply type in:
```bash
fermilink
```
for a step-by-step guide regarding how to setup the environment of **FermiLink**.

## Usage


### FermiLink as a package knowledge base provider

If you simply want to use **FermiLink** for easily accessing 150+ package knowledge bases and then use your **own custom agent** for running simulations:

<details>

```bash
# 1. Install the scientific package knowledge base
fermilink install meep
# 2. Enter a working directory
cd myproject/
# 3. Create a local environment with package knowledge bases
fermilink init meep
# 3. Open any local agent for simulations with this package knowledge base
codex/claude/gemini/deepseek
```
</details>

### FermiLink as a simulation workflow provider

If you want to use the existing **workflows in  FermiLink** for doing simulations:

<details>

```bash
# 1. Install at least one scientific package knowledge base
fermilink install meep

# 2. set up the agent provider
fermilink agent codex/claude/gemini

# 3. (Optional) initialize default HPC profile at ~/.fermilink/HPC_PROFILE.json
fermilink hpc

# 4. Use command-line tool to do autonomous scientific research
fermilink exec/loop/reproduce/research goal.md
```

</details>

### FermiLink as a Web UI provider 

For teaching and demonstration purposes, run the following command to [get a ChatGPT-like interface](https://fermilink.org/usage_web_ui.html):

<details>

```bash
# start the web UI
fermilink start
# end the web UI
fermilink stop
# restart the web UI
fermilink restart
```

</details>

### FermiLink as a cellphone controller of HPC

For [remotely controlling the HPC and running simulations](https://fermilink.org/usage_chatting_apps.html):

<details>

```bash
# Start the gateway for supporting Chatbots via Telegram
export FERMILINK_GATEWAY_TELEGRAM_TOKEN="<token-from-@BotFather>"
export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="<numeric-id-from-@get_telegram_id_smppcenter_bot>"
fermilink gateway
```

<p align="center">
  <img src="docs/source/_static/img/fermilink_hpc_bot.jpeg" alt="FermiLink Telegram Bot" width="300">
</p>
</details>

## Documentation

Visit the [documentation](https://taoeli.github.io/FermiLink/) for installation details and usage guide. 

In brief, the key design principle of **FermiLink** is the separation of package knowledge bases from simulation workflows, so that simulation workflows in **FermiLink**, from figure-level simulations to full-paper-level research on high-performance computing clusters, operate uniformly among supported packages via a four-layer progressive disclosure mechanism.
![FermiLink design](docs/source/_static/img/package_management_workflow.svg)

To accommodate simulations at different scopes, as demonstrated below, **FermiLink** delivers with three major computational workflows. 
![FermiLink major workflows](docs/source/_static/img/major_modes_workflow.svg)
- **exec** mode: Designed for short-duration simulations.
- **loop** mode: Connects iterative agent reasoning with simulation monitoring for PID and HPC SLURM jobs, thus providing robust support for long-duration simulations on both workstations and HPC clusters. 
- **research**/**reproduce** mode: Intended for multi-task simulations at the scope of a full research paper. 


## Citation

If you find **FermiLink** helpful for your research, please cite the following reference:

- Gang Meng†, Andres Felipe Bocanegra Vargas†, Xinwei Ji†, Federico Garcia-Gaitan, Felipe Reyes-Osorio, Jalil Varela-Manjarres, Yafei Ren, Mohammadhasan Dinpajooh, Branislav K. Nikolić, Tao E. Li. *FermiLink: A Unified Agent Framework for Multidomain Autonomous Scientific Simulations*. [**arXiv:2604.03460** (2026)](https://arxiv.org/abs/2604.03460).
