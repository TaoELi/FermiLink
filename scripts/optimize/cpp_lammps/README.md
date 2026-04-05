# LAMMPS TIP4P benchmark inputs (goal-mode support)

These files provide credible benchmark inputs adapted from the i-PI LAMMPS
example:

- Upstream LAMMPS client input used for adaptation:
  - https://github.com/i-pi/i-pi/blob/main/examples/clients/lammps/h2o_pimd.4/in.lmp
- Upstream 216-water initial configurations:
  - https://github.com/i-pi/i-pi/blob/main/examples/init_files/water_216_data.lmp
  - https://github.com/i-pi/i-pi/blob/main/examples/init_files/water_216.xyz

## Included files

- `water_216_data.lmp`: upstream LAMMPS data file (216 waters).
- `water_216.xyz`: upstream XYZ configuration (216 waters).
- `in.tip4p_nve`: NVE adaptation using base 216-water system.
- `in.tip4p_nve_medium`: NVE adaptation using `replicate 2 1 1` (~2x molecules).
- `in.tip4p_nve_large`: NVE adaptation using `replicate 2 2 1` (~4x molecules).
- `in.tip4p_nve_long`: longer-horizon NVE drift case on `replicate 2 1 1`.

## Adaptation notes

- i-PI coupling (`fix ... ipi ...`) is replaced with local LAMMPS `fix nve`.
- TIP4P force-field style and coefficients are retained from the upstream input.
- The NVE benchmark inputs use `timestep 0.5` (0.5 fs request).
- Each NVE input initializes velocities from a Maxwell-Boltzmann distribution at
  300 K via `velocity all create 300.0 <seed> dist gaussian mom yes rot yes`.
- Per-case deterministic seeds are used so small/medium/large/long cases start
  from different velocity realizations while remaining reproducible.
- Larger systems use LAMMPS `replicate`, as requested, instead of invented
  standalone coordinates.
- Keep `OMP_NUM_THREADS=1` for benchmark determinism unless your benchmark
  contract explicitly tests hybrid MPI+OpenMP.

## Tutorial: optimize LAMMPS NVE H2O with `fermilink optimize`

This walkthrough uses:

- Goal file: `scripts/optimize/cpp_lammps/cpp-lammps-tip4p-water-nve-goal.md`
- Input assets in this folder: `in.tip4p_nve*`, `water_216_data.lmp`, `water_216.xyz`

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

### 3) Copy benchmark input assets into the controller worktree

The goal and generated benchmark expect these filenames in the optimize target
repo.

```bash
cp "$FERMILINK_ROOT"/scripts/optimize/cpp_lammps/in.tip4p_nve* .
cp "$FERMILINK_ROOT"/scripts/optimize/cpp_lammps/water_216_data.lmp .
cp "$FERMILINK_ROOT"/scripts/optimize/cpp_lammps/water_216.xyz .
```

### 4) Commit the benchmark assets on the optimize branch

`fermilink optimize` requires a clean working tree by default.

```bash
git add in.tip4p_nve in.tip4p_nve_medium in.tip4p_nve_large in.tip4p_nve_long \
        water_216_data.lmp water_216.xyz
git commit -m "chore(optimize): add LAMMPS TIP4P NVE benchmark inputs"
```

### 5) Launch goal-mode optimize

The goal requests MPI benchmarking with exactly 16 ranks and deterministic
threading (`OMP_NUM_THREADS=1`).

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

- `train-small` uses the base 216-water system.
- `train-medium` and `test-large` enlarge the system using `replicate`.
- `test-long` checks longer-horizon NVE behavior on the medium replicated
  system.
