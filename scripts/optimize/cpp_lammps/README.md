# LAMMPS TIP4P benchmark inputs (goal-mode support)

These files provide benchmark inputs adapted from the i-PI LAMMPS example:

- Upstream client input:
  - https://github.com/i-pi/i-pi/blob/main/examples/clients/lammps/h2o_pimd.4/in.lmp
- Upstream 216-water initial configurations:
  - https://github.com/i-pi/i-pi/blob/main/examples/init_files/water_216_data.lmp
  - https://github.com/i-pi/i-pi/blob/main/examples/init_files/water_216.xyz

## Included files

- `cpp-lammps-tip4p-water-nve-goal.md`: goal-mode optimize specification.
- `cpp-lammps-tip4p-water-nve-bonded-goal.md`: goal-mode optimize specification focused on `bond_style class2` plus `angle_style harmonic`.
- `cpp-lammps-tip4p-water-nve-comm-goal.md`: goal-mode optimize specification focused on the brick MPI communication path.
- `cpp-lammps-tip4p-water-nve-neighbor-goal.md`: goal-mode optimize specification focused on neighbor-list rebuild and binning.
- `in.tip4p_nve`: short NVE run (`run 1200`) on the (216x64)-water system.
- `in.tip4p_nve_long`: longer NVE run (`run 10000`) on the same (216x64)-water system.
- `water_216_data.lmp`: upstream LAMMPS data file (216 waters).
- `water_216.xyz`: upstream XYZ configuration (216 waters).

The four bundled goal files now cover different phases of the same TIP4P NVE timestep:

- pair plus PPPM: [`cpp-lammps-tip4p-water-nve-goal.md`](./cpp-lammps-tip4p-water-nve-goal.md)
- bonded forces: [`cpp-lammps-tip4p-water-nve-bonded-goal.md`](./cpp-lammps-tip4p-water-nve-bonded-goal.md)
- neighbor rebuilds: [`cpp-lammps-tip4p-water-nve-neighbor-goal.md`](./cpp-lammps-tip4p-water-nve-neighbor-goal.md)
- communication: [`cpp-lammps-tip4p-water-nve-comm-goal.md`](./cpp-lammps-tip4p-water-nve-comm-goal.md)


## Tutorial: optimize LAMMPS TIP4P NVE with `fermilink optimize`

This walkthrough uses:

- Goal file: `scripts/optimize/cpp_lammps/cpp-lammps-tip4p-water-nve-goal.md`
- Input assets in this folder: `in.tip4p_nve`, `in.tip4p_nve_long`, `water_216_data.lmp`, `water_216.xyz`

To target a different LAMMPS phase, swap in one of the alternative goal files listed above; they reuse the same staged inputs and build procedure, but point optimize at a different editable scope and timer bucket.

### 1) Set paths

```bash
export FERMILINK_ROOT=/path/to/FermiLink_development
export GOAL="$FERMILINK_ROOT/scripts/optimize/cpp_lammps/cpp-lammps-tip4p-water-nve-goal.md"
export WORKSPACE=/path/to/simulation/workspace
export LAMMPS_ROOT="$WORKSPACE/lammps"
export LAMMPS_OPT="$WORKSPACE/lammps-optimize-tip4p"
test -f "$GOAL"
```

### 2) Prepare a clean LAMMPS checkout and controller worktree

```bash
git clone https://github.com/skilled-scipkg/lammps.git "$LAMMPS_ROOT"
cd "$LAMMPS_ROOT"
git fetch origin
# note that the main branch of lammps is called "develop"
git checkout develop
git pull --ff-only origin develop
# create a new git worktree for lammps code modification
git worktree add -b fermilink-optimize/lammps-tip4p "$LAMMPS_OPT" develop
```

### 3) Compile LAMMPS once

Then install lammps in the new git worktree following the `## Build` section in [goal.md](./cpp-lammps-tip4p-water-nve-goal.md) file:

```bash
cd "$LAMMPS_OPT"
# the commands below are identical to the `## Build` section in goal.md
mkdir -p build/
cd build/ 
cmake -C ../cmake/presets/most.cmake -C ../cmake/presets/nolib.cmake -D PKG_GPU=off ../cmake
cmake --build . -j4
```

If the above command does not work for your environment, modify the above script, and **update the working build script to the `## Build` section in [goal.md](./cpp-lammps-tip4p-water-nve-goal.md) file**. This is because the agent will call this  `## Build` section to compile the LAMMPS source code.

### 4) Launch goal-mode optimize

The current goal uses deterministic mixed-rank MPI cases (16, 32, and 64 ranks) with `OMP_NUM_THREADS=1`.

```bash
cd "$LAMMPS_OPT"

export OMP_NUM_THREADS=1

fermilink optimize "$GOAL" \
  --branch fermilink-optimize/lammps-tip4p \
  --max-iterations 30 \
  --stop-on-consecutive-rejections 8 \
  --timeout-seconds 6000
```

### 5) Monitor and resume

```bash
# at the repo root of $LAMMPS_OPT
cd "$LAMMPS_OPT"
fermilink optimize status --tail 30
```

If the previous job was ended, we can restart the job from previous ending point by:

```bash
# at the repo root of $LAMMPS_OPT
cd "$LAMMPS_OPT"
fermilink optimize "$GOAL" --resume \
  --branch fermilink-optimize/lammps-tip4p 
```

### 6) Review outputs

```bash
ls -la .fermilink-optimize/autogen
tail -n 30 .fermilink-optimize/results.tsv
git log --oneline
```
