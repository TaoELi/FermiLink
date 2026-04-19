# Tutorial: Optimizing PySCF TDDFT Davidson

This folder contains two reference entry points for PySCF TDDFT Davidson
optimization:

- goal mode, using the sample goal markdown file
- expert mode, using the hand-authored benchmark YAML and runner

Goal mode is the simpler starting point. Expert mode is useful when you want
full control over the benchmark contract, train/test split, and runtime
settings.

## Prerequisites

- FermiLink is installed and an agent provider CLI (OpenAI Codex, Claude Code,
  or Gemini) is authenticated.
- Git and Python >= 3.11 are available on `PATH`.
- Optimize startup requires a clean git tree unless you pass `--allow-dirty`.

Clone or update PySCF on `master`:

```bash
git clone git@github.com:skilled-scipkg/pyscf.git pyscf
cd pyscf/
git fetch origin
git checkout master
git pull --ff-only origin master
```

Create a dedicated controller worktree so the optimization campaign does not
modify the main checkout:

```bash
# work under pyscf/
git worktree add -b fermilink-optimize/pyscf-davidson ../pyscf-optimize-davidson master
cd ../pyscf-optimize-davidson
```

Recommended for HPC: keep the virtual environment outside the git worktree so
optimize clean-tree checks are not affected.

```bash
export VENV="$HOME/.venvs/fermilink-optimize/pyscf-davidson"
python -m venv "$VENV"
source "$VENV/bin/activate"

cd pyscf/lib
mkdir -p build
cd build
cmake ..
cmake --build . -j4
cd ../../../

python -m pip install -U pip
python -m pip install -e .
python -m pip install -e /path/to/fermilink
```

Replace `/path/to/fermilink` with the actual path to your FermiLink checkout.

## Goal Mode

Use the bundled goal file and let FermiLink generate the benchmark contract and
runner under `.fermilink-optimize/autogen/`.

Set the sample goal path:

```bash
export GOAL=/path/to/fermilink/scripts/optimize/python_pyscf/python-pyscf-tddft-davidson-goal.md
test -f "$GOAL"
```

Optional but recommended: run the baseline only first to validate runtime and
correctness before a longer campaign.

```bash
fermilink optimize "$GOAL" \
  --baseline-only \
  --timeout-seconds 3600
```

Then launch the full goal-mode campaign from inside the worktree:

```bash
fermilink optimize "$GOAL" \
  --max-iterations 18 \
  --stop-on-consecutive-rejections 8 \
  --timeout-seconds 3600 \
  --resume
```

Monitor progress at any time with:

```bash
fermilink optimize status
```


