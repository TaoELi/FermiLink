#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=128
#SBATCH --time=02:00:00
#SBATCH -J __PROJECT___driver
#SBATCH -o driver.%j.out
#SBATCH -e driver.%j.err
#SBATCH -p shared

set -euo pipefail
if command -v module >/dev/null 2>&1; then
  module purge
fi

python -u driver.py

