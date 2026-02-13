#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=128
#SBATCH --time=02:00:00
#SBATCH -J __PROJECT___main
#SBATCH -o main.%j.out
#SBATCH -e main.%j.err
#SBATCH -p shared

set -euo pipefail
if command -v module >/dev/null 2>&1; then
  module purge
fi

mpirun -np "${SLURM_NTASKS:-1}" python -u em_main.py

