#!/usr/bin/env bash

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${here}"

read -r include_molecules nmol clients_per_job <<<"$(python -c 'import json; c=json.load(open("config.json")); r=c["run"]; d=c["driver_launch"]; print(int(bool(r.get("include_molecules", True))), int(r["nmol"]), int(d.get("clients_per_job", 1)))')"

job_main_id=$(sbatch submit_main.sh | awk '{print $4}')
echo "Submitted main job with Job ID: ${job_main_id}"

if [[ "${include_molecules}" -ne 1 ]]; then
    echo "include_molecules=false in config.json; skipping driver submissions."
    exit 0
fi

if [[ "${clients_per_job}" -le 0 ]]; then
    echo "Invalid clients_per_job=${clients_per_job}; must be > 0" >&2
    exit 1
fi

n_jobs=$(( (nmol + clients_per_job - 1) / clients_per_job ))
for ((j=0; j<n_jobs; j++)); do
    remaining=$(( nmol - j * clients_per_job ))
    clients_this_job=${clients_per_job}
    if [[ "${remaining}" -lt "${clients_per_job}" ]]; then
        clients_this_job=${remaining}
    fi

    sbatch --dependency=after:${job_main_id} \
      --export=ALL,MXL_DRIVER_CLIENTS_THIS_JOB=${clients_this_job} \
      submit_driver.sh

done

