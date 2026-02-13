# Meep 3D Plasmonic + RT-Ehrenfest (SLURM, TCP)

This template provides a scaffold for coupling:
- a realistic 3D plasmonic Meep structure (Pt nanorod + Pt film + Si substrate), and
- a lattice of HCN molecules propagated by MaxwellLink's `rtehrenfest` driver.

The workflow is a two-step SLURM pattern over TCP sockets:
1. `submit_main.sh` runs `em_main.py` (Meep + SocketHub server).
2. `submit_driver.sh` runs `driver.py` (RT-Ehrenfest clients).

## Files
- `config.json`: simulation/driver settings.
- `em_main.py`: Meep + MaxwellLink server-side simulation.
- `driver.py`: launch one or many `mxl_driver --model rtehrenfest` clients.
- `submit_main.sh`, `submit_driver.sh`, `submit_all.sh`: SLURM scripts.
- `HCN_benchmark/hcn.xyz`: benchmark HCN geometry for RT-Ehrenfest driver.
- `HCN_benchmark/frequency_calc.py`: optional helper for standalone Psi4 frequency checks.

## Submit
```bash
./submit_all.sh
```

Equivalent manual submission:
```bash
job_main_id=$(sbatch submit_main.sh | awk '{print $4}')
# submit_all.sh computes number of driver jobs from config.json
```

## Key edits in `config.json`
- `run.nmol`: number of molecules embedded in the plasmonic unit cell.
- `run.lattice_a_um`, `run.rod_radius_fraction`: plasmonic geometry.
- `run.include_dielectric`, `run.include_molecules`: physics toggles.
- `rtehrenfest.*`: driver-level RT-Ehrenfest parameters.
- `driver_launch.clients_per_job`: number of driver clients launched per driver SLURM job.

## Notes
- `driver_launch.clients_per_job` should usually match `#SBATCH --ntasks` in `submit_driver.sh`.
- For large `run.nmol`, `submit_all.sh` submits multiple dependent driver jobs automatically.
- If hostname resolution is restricted on your cluster, set:
  - `export MXL_HOST=$(hostname -f)`
