<p align="center">
  <img src="src/fermilink/public/fermilink_logo.png" alt="FermiLink logo" width="200"/>
</p>

<p align="center">
  <a href="https://fermilink.org"><img src="https://img.shields.io/badge/docs-project-blue.svg" alt="Docs"></a>
  <a href="https://pypi.org/project/fermilink/"><img src="https://img.shields.io/pypi/v/fermilink.svg?label=pypi&logo=pypi" alt="PyPI"></a>
  <a href="https://github.com/TaoELi/FermiLink/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-AGPLv3-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-brightgreen.svg" alt="Python">
  <a href="https://arxiv.org/abs/2604.03460"><img src="https://img.shields.io/badge/arXiv-2604.03460-b31b1b.svg" alt="arXiv:2604.03460"></a>
</p>

<h3 align="center">Write a <code>goal.md</code> for scientific simulations!</h3>

<p align="center">
<b> FermiLink </b> reads a simple markdown file describing your scientific computing goal,<br>
then autonomously runs multi-job multi-day simulations on your laptop or HPC clusters.</p>

---

## Key Features

#### Package Knowledge Base Manager 

Use **FermiLink** to conveniently load package knowledge base to your local workspace; and then apply any local agent for interactive research.

```bash
fermilink install <pkg-id>
cd /path/to/workspace/
fermilink init <pkg-id>
# then try any agent 
codex 
```

#### Autonomous Scientific Simulations 

Beyond interactive use,  **FermiLink** can also sustain **multi-task multi-day** simulations on either your laptop or HPC.

```bash
fermilink loop goal.md          # long-running simulations on workstations or HPC
fermilink reproduce paper.tex   # reproduction of a full research paper
fermilink research goal.md      # full research-paper-scale, multi-task campaigns
```

#### Autonomous Code Optimization *(beta)*

Apply **FermiLink** to your existing scientific code with a **performance goal**. It iteratively modifies hot paths and runs deterministic benchmarks. 

At the end of the day, it provides optimized scientific code with  improved speed while preserving computational accuracy.

```bash
fermilink optimize goal.md      # iterative code with correctness guarantees
```

---

## Quickstart

```bash
# 1. Install
pip install fermilink

# 2. Set up an AI agent (pick one)
fermilink agent codex        # or: claude, gemini

# 3. Install a scientific package knowledge base
fermilink install meep       # 150+ packages available

# 4. (Optional) Configure the default SLURM HPC profile
fermilink hpc

# 5. Write your goal.md (see example above), then run
fermilink loop goal.md
```

> **First time?** Just run `fermilink` with no arguments. Then, an interactive setup wizard will walk you through everything.

---

## Design principles

The key design principle of **FermiLink** is the segregation of  package knowledge bases and simulation workflows. 

![design](./docs/source/_static/img/package_management_workflow.svg)

---

## Supported Workflows

**FermiLink** supports the following six major commands for various simulation workflows:

| Command | Best for | Duration | Recommended Environment |
|---|---|---|---|
| `exec` | Quick one-off simulations | Minutes | Laptop / workstation |
| `chat` | Interactive conversation with agents | Hours | Laptop / workstation |
| `loop` | Iterative jobs with PID/SLURM monitoring | Hours -> days | Workstation / HPC |
| `reproduce` | Multi-task, paper-scale reproducation of papers | Days -> weeks | HPC clusters |
| `research` | Multi-task, paper-scale research | Days -> weeks | HPC clusters |
| `optimize` | Code performance tuning *(beta)* | Hours | Any |

---

## Built-in Scientific Packages

FermiLink ships with 150+ knowledge bases for packages spanning multiple scientific domains. Install any of them with a single command:

```bash
fermilink install <package>       # e.g., meep, lammps, pyscf, openfoam
fermilink list                    # see all installed packages
```

Don't see your package? Compile your own local knowledge base:

```bash
fermilink compile /path/to/your/code
```

Browse the [full package list →](https://fermilink.org/built_in_scientific_packages.html)

---

## Interaction surfaces

**FermiLink** isn't just a CLI tool. Pick the interface that fits you:

- **Command line** — `fermilink exec/loop/reproduce/research/optimize goal.md` for headless, scriptable autonomy
- **Web UI** — `fermilink start` launches a ChatGPT-style browser interface for interactive sessions
- **Telegram bot** — `fermilink gateway` connects to Telegram so you can run and monitor HPC jobs from your phone

---

## Documentation

| Resource | Link |
|---|---|
| Full documentation | [fermilink.org](https://fermilink.org) |
| Installation guide | [fermilink.org/installation](https://fermilink.org/installation.html) |
| Laptop tutorial | [fermilink.org/tutorial_laptop](https://fermilink.org/tutorial_laptop.html) |
| HPC tutorial | [fermilink.org/tutorial_hpc](https://fermilink.org/tutorial_hpc.html) |

---

## Citation

If FermiLink is useful in your research, please cite:

> - Gang Meng†, Andres Felipe Bocanegra Vargas†, Xinwei Ji†, Federico Garcia-Gaitan, Felipe Reyes-Osorio, Jalil Varela-Manjarres, Yafei Ren, Mohammadhasan Dinpajooh, Branislav K. Nikolić, Tao E. Li. *FermiLink: A Unified Agent Framework for Multidomain Autonomous Scientific Simulations*. [**arXiv:2604.03460** (2026)](https://arxiv.org/abs/2604.03460).

---

## Contributing

We welcome contributions, from bug reports to new package knowledge bases. See [CONTRIBUTING →](https://fermilink.org/contributing.html)

---

## License

[AGPL-3.0](LICENSE)