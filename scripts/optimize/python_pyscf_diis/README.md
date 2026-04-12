# Tutorial: Optimizing PySCF DIIS with the Sample Goal File

This is a toy-model example of `fermilink optimize` aiming to showcase the performance improvement workflow of FermiLink. More practical optimization examples are given elsewhere in `scripts/optimize/`.

This walkthrough uses the sample DIIS goal file shipped with FermiLink (`scripts/optimize/python_pyscf_diis/python-pyscf-diis-scf-goal.md`) to run a goal-mode campaign against a local PySCF clone. The controller operates inside a **git worktree** so the original clone stays untouched.

## Prerequisites

- FermiLink is installed and an agent provider CLI (OpenAI Codex, Claude Code, or Gemini) is authenticated.
- Git and Python >= 3.11 are available on `PATH`.
- Important: optimize startup requires a clean git tree unless you pass
  `--allow-dirty`.

## Step 1. Clone or update PySCF on `master`

Working under a clean repo:
```bash
git clone git@github.com:skilled-scipkg/pyscf.git pyscf
cd pyscf/
git fetch origin
git checkout master
git pull --ff-only origin master
```

If you already have a local clone, skip this step.

## Step 2. Create a controller worktree

Create a dedicated worktree from the original `master` branch so the optimization campaign does not modify the main checkout:

```bash
# work under pyscf/
git worktree add -b fermilink-optimize/pyscf-diis ../pyscf-optimize-diis master
```

This places the worktree at `../pyscf-optimize-diis` on branch
`fermilink-optimize/pyscf-diis`. 

Then, we need to work in this worktree:

```bash
cd ../pyscf-optimize-diis
```

## Step 3. Build in the worktree

Recommended for HPC: keep the virtual environment **outside** the git
worktree so optimize clean-tree checks are not affected.

```bash
export VENV="$HOME/.venvs/fermilink-optimize/pyscf-diis"
python -m venv "$VENV"
source "$VENV/bin/activate"
# before pip install, we need to compile the C library of pyscf
cd pyscf/lib
mkdir -p build
cd build
cmake ..
make -j4
# go to pyscf-optimize-diis/ repo root
cd ../../../
# after completing the C library, we proceed the conventional pip install
python -m pip install -U pip
python -m pip install -e .
python -m pip install -e /path/to/fermilink
```

Replace the above `/path/to/fermilink` with the actual location of your FermiLink source checkout.

## Step 4. Set the goal file path

Point an environment variable at the sample goal file shipped with FermiLink. This avoids copying the file into the worktree (which would create an untracked file in the git tree):

```bash
export GOAL=/path/to/fermilink/scripts/optimize/python_pyscf_diis/python-pyscf-diis-scf-goal.md
test -f "$GOAL"
```

 Review the file and, if needed, create a modified copy
**outside** the worktree and point `GOAL` at that copy instead.

## Step 5. Launch the campaign

Verify the benchmark runtime will use the current environment:

```bash
which python
python -c "import sys; print(sys.executable)"
```

Optional but strongly recommended on HPC: run baseline only first to validate
runtime/correctness before a long campaign:

```bash
fermilink optimize "$GOAL" \
  --baseline-only \
  --timeout-seconds 900
```

If your worktree is intentionally dirty (for example, local `.venv/`), add
`--allow-dirty` to both baseline and full runs.

FermiLink will:

1. Parse the goal file and analyse the PySCF source.
2. Auto-generate `benchmark.yaml` and `benchmark_runner.py` under `.fermilink-optimize/autogen/`.
3. Run the baseline benchmark on the unmodified source.

## Step 6. Launch the full campaign

After the baseline calculations, run `optimize` in goal mode from inside the worktree, referencing the goal file by its absolute path:

```bash
# under pyscf-optimize-diis/
fermilink optimize "$GOAL" \
  --max-iterations 30 \
  --stop-on-consecutive-rejections 8 \
  --timeout-seconds 900 --resume
```

The worker operates in its own hidden sibling git worktree next to the original source repo (`.<repo>-fermilink-optimize-worktrees/`); the controller worktree you created in Step 2 is the authoritative checkout.

## Step 7. Monitor progress

In a separate terminal, `cd` into the worktree and run:

```bash
cd pyscf-optimize-diis/
fermilink optimize status
```

This prints the current iteration count, accepted/rejected totals, incumbent commit, and the most recent results from `results.tsv`.

## Step 8. Review accepted commits and clean up

```bash
cd pyscf-optimize-diis
git log --oneline

cd ../pyscf
git worktree remove ../pyscf-optimize-diis
# git branch -D fermilink-optimize/pyscf-diis  # if you no longer need the branch
```
