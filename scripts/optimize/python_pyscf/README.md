# Tutorial: Optimizing PySCF with the Sample Goal File

This walkthrough uses the sample goal file shipped with FermiLink
(`scripts/optimize/python_pyscf/python-pyscf-diis-scf-goal.md`) to run a goal-mode campaign
against a local PySCF clone. The controller operates inside a **git
worktree** so the original clone stays untouched.

## Prerequisites

- FermiLink is installed and an agent provider CLI is authenticated.
- A `skills/` folder for PySCF is available either in your main clone or a
  FermiLink-managed package location.
- Git and Python >= 3.11 are available on `PATH`.
- You can run PySCF benchmarks on your HPC node/partition from the current shell.
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

Create a dedicated worktree from the original `master` branch so the
optimization campaign does not modify the main checkout:

```bash
# work under pyscf/
git worktree add -b fermilink-optimize/pyscf-diis ../pyscf-optimize-diis master
```

This places the worktree at `../pyscf-optimize-diis` on branch
`fermilink-optimize/pyscf-diis`. Change into it for the remaining steps:

```bash
cd ../pyscf-optimize-diis
```

## Step 3. Build in the worktree

Recommended for HPC: keep the virtual environment **outside** the git
worktree so optimize clean-tree checks are not affected.

```bash
export VENV="$HOME/.venvs/pyscf-optimize-diis"
python -m venv "$VENV"
source "$VENV/bin/activate"
# before pip install, we need to compile the C library of pyscf
cd pyscf/lib
mkdir -p build
cd build
cmake ..
make -j4
# go to pyscf repo root
cd ../../../
# after completing the C library, we proceed the conventional pip install
python -m pip install -U pip
python -m pip install -e .
python -m pip install PyYAML
python -m pip install -e /path/to/fermilink
```

If you must keep `.venv/` inside the worktree, add a local-only ignore rule
(no commit needed):

```bash
python -m venv .venv
source .venv/bin/activate
printf ".venv/\n" >> .git/info/exclude
```

Then verify the tree is clean before launching optimize:

```bash
git status --porcelain
```

## Step 4. Set the goal file path

Point an environment variable at the sample goal file shipped with
FermiLink. This avoids copying the file into the worktree (which would
create an untracked file in the git tree):

```bash
export GOAL=/path/to/fermilink/scripts/optimize/python_pyscf/python-pyscf-diis-scf-goal.md
test -f "$GOAL"
```

Replace `/path/to/fermilink` with the actual location of your FermiLink
source checkout. Review the file and, if needed, create a modified copy
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

## Step 6. Launch the full campaign

Run optimize in goal mode from inside the worktree, referencing the goal
file by its absolute path:

```bash
# under pyscf-optimize-diis/
fermilink optimize "$GOAL" \
  --max-iterations 30 \
  --stop-on-consecutive-rejections 8 \
  --timeout-seconds 900
```

FermiLink will:

1. Parse the goal file and analyse the PySCF source.
2. Auto-generate `benchmark.yaml` and `benchmark_runner.py` under
   `.fermilink-optimize/autogen/`.
3. Run the baseline benchmark on the unmodified source.
4. Enter the worker-controller optimization loop.

The worker operates in its own nested git worktree inside the campaign
directory (`fermilink-optimize-worktrees/`); the controller worktree you
created in Step 2 is the authoritative checkout.

## Step 7. Monitor progress

In a separate terminal, `cd` into the worktree and run:

```bash
cd pyscf-optimize-diis/
fermilink optimize status
```

This prints the current iteration count, accepted/rejected totals, incumbent
commit, and the most recent results from `results.tsv`.

## Step 8. Resume if interrupted

If the campaign is interrupted (e.g. by `Ctrl-C` or a timeout), resume
from the last checkpoint:

```bash
cd pyscf-optimize-diis/
fermilink optimize "$GOAL" --resume
```

If you used `--allow-dirty` on launch, include it on resume as well.

## Step 9. Troubleshooting on HPC

### Error: clean git tree required (often appears after goal files are generated)

Cause: optimize checks repo cleanliness before baseline, and untracked files
like `.venv/` can fail that gate.

Fix:

```bash
# Preferred: external venv
export VENV="$HOME/.venvs/pyscf-optimize-diis"
source "$VENV/bin/activate"

# Or local-only ignore (no commit)
printf ".venv/\n" >> .git/info/exclude

# Last resort for intentionally dirty tree
fermilink optimize "$GOAL" --allow-dirty ...
```

### Error: Baseline benchmark completed but did not satisfy correctness gates

Meaning: benchmark execution succeeded, but the runner reported
`correctness_ok: false`.

Triage:

```bash
# 1) Re-run baseline only with resume context
fermilink optimize "$GOAL" --resume --baseline-only --timeout-seconds 900

# 2) Inspect generated benchmark runtime and baseline artifacts
sed -n '1,240p' .fermilink-optimize/autogen/benchmark.yaml
cat .fermilink-optimize/runs/baseline/metrics.json
ls -1 .fermilink-optimize/runs/baseline
```

Common fixes:

```bash
# Ensure deterministic threading for BLAS/OpenMP paths
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1

# If autogen artifacts were produced under a wrong env, restart generation
rm -rf .fermilink-optimize
fermilink optimize "$GOAL" --max-iterations 30 --stop-on-consecutive-rejections 8 --timeout-seconds 900
```

## Step 10. Review accepted commits and clean up

```bash
cd pyscf-optimize-diis
git log --oneline

cd ../pyscf
git worktree remove ../pyscf-optimize-diis
# git branch -D fermilink-optimize/pyscf-diis  # if you no longer need the branch
```
