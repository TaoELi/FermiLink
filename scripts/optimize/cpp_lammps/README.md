# LAMMPS TIP4P benchmark inputs (goal-mode support)

These files provide benchmark inputs adapted from the i-PI LAMMPS example:

- Upstream client input:
  - https://github.com/i-pi/i-pi/blob/main/examples/clients/lammps/h2o_pimd.4/in.lmp
- Upstream 216-water initial configurations:
  - https://github.com/i-pi/i-pi/blob/main/examples/init_files/water_216_data.lmp
  - https://github.com/i-pi/i-pi/blob/main/examples/init_files/water_216.xyz

## Included files

- `cpp-lammps-tip4p-water-nve-goal.md`: goal-mode optimize specification.
- `in.tip4p_nve`: short NVE run (`run 200`) on the 216-water system.
- `in.tip4p_nve_long`: longer NVE run (`run 400`) on the same 216-water system.
- `water_216_data.lmp`: upstream LAMMPS data file (216 waters).
- `water_216.xyz`: upstream XYZ configuration (216 waters).

## Adaptation notes

- i-PI socket coupling (`fix ... ipi ...`) is replaced with local LAMMPS `fix nve`.
- TIP4P pair style and coefficients are retained from the upstream example.
- Both inputs keep `timestep 0.5`.
- Both inputs use `replicate 1 1 1` (no system enlargement; same 216-water base).
- Thermo output includes `etotal`, `pe`, `ke`, and `press` for correctness checks.
- Keep deterministic runtime settings for fair baseline/candidate comparison:
  - same input files and seeds
  - same MPI/OMP settings

## Tutorial: optimize LAMMPS TIP4P NVE with `fermilink optimize`

This walkthrough uses:

- Goal file: `scripts/optimize/cpp_lammps/cpp-lammps-tip4p-water-nve-goal.md`
- Input assets in this folder: `in.tip4p_nve`, `in.tip4p_nve_long`, `water_216_data.lmp`, `water_216.xyz`

### 1) Set paths

```bash
export FERMILINK_ROOT=/path/to/FermiLink_development
export LAMMPS_ROOT=~/lammps
export LAMMPS_OPT=~/lammps-optimize-tip4p
export GOAL="$FERMILINK_ROOT/scripts/optimize/cpp_lammps/cpp-lammps-tip4p-water-nve-goal.md"
test -f "$GOAL"
```

### 2) Prepare a clean LAMMPS checkout and controller worktree

```bash
git clone https://github.com/lammps/lammps.git "$LAMMPS_ROOT"
cd "$LAMMPS_ROOT"
git fetch origin
git checkout master
git pull --ff-only origin master

git worktree add -b fermilink-optimize/lammps-tip4p "$LAMMPS_OPT" master
cd "$LAMMPS_OPT"
```

### 3) Copy benchmark assets into the controller worktree

```bash
cp "$FERMILINK_ROOT"/scripts/optimize/cpp_lammps/in.tip4p_nve .
cp "$FERMILINK_ROOT"/scripts/optimize/cpp_lammps/in.tip4p_nve_long .
cp "$FERMILINK_ROOT"/scripts/optimize/cpp_lammps/water_216_data.lmp .
cp "$FERMILINK_ROOT"/scripts/optimize/cpp_lammps/water_216.xyz .
```

### 4) Commit the benchmark assets

`fermilink optimize` requires a clean tree by default.

```bash
git add in.tip4p_nve in.tip4p_nve_long water_216_data.lmp water_216.xyz
git commit -m "chore(optimize): add LAMMPS TIP4P NVE benchmark inputs"
```

### 5) Launch goal-mode optimize

The current goal is configured for deterministic 1-rank runs with `OMP_NUM_THREADS=1`.

```bash
export OMP_NUM_THREADS=1

fermilink optimize "$GOAL" \
  --branch fermilink-optimize/lammps-tip4p \
  --skills-source channel \
  --max-iterations 30 \
  --stop-on-consecutive-rejections 8 \
  --timeout-seconds 1800
```

### 6) Monitor and resume

```bash
fermilink optimize status --tail 30

# If interrupted:
fermilink optimize "$GOAL" --resume \
  --branch fermilink-optimize/lammps-tip4p \
  --skills-source channel
```

### 7) Review outputs

```bash
ls -la .fermilink-optimize/autogen
tail -n 30 .fermilink-optimize/results.tsv
git log --oneline
```

Notes:

- `train-small` uses `in.tip4p_nve` (`run 200`).
- `test-long` uses `in.tip4p_nve_long` (`run 400`).
