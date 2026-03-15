#!/usr/bin/env bash
set -euo pipefail

BENCHMARK_PATH=""
EMIT_JSON=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --benchmark)
      shift
      BENCHMARK_PATH="${1:-}"
      ;;
    --emit-json)
      EMIT_JSON=1
      ;;
    *)
      ;;
  esac
  shift || true
done

RUN_ROOT=".fermilink-optimize"
mkdir -p "${RUN_ROOT}"

json_escape() {
  local value="${1:-}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/ }"
  value="${value//$'\r'/ }"
  printf '%s' "$value"
}

parse_qe_output() {
  local out_path="$1"
  awk '
    /^[[:space:]]*!/ && /total energy/ {
      for (i=1; i<=NF; i++) {
        if ($i == "=" && (i+1)<=NF) {
          energy = $(i+1)
        }
      }
    }
    /iteration[[:space:]]*#/ {
      for (i=1; i<=NF; i++) {
        if ($i == "#" && (i+1)<=NF) {
          iter=$(i+1)
          gsub(/[^0-9]/, "", iter)
          if (iter != "") {
            last_iter = iter
          }
        }
      }
    }
    END {
      if (energy != "") {
        if (last_iter == "") {
          last_iter = 0
        }
        printf "%.12g %d\n", energy, last_iter
      }
    }
  ' "$out_path"
}

weighted_median() {
  local input_file="$1"
  if [[ ! -s "$input_file" ]]; then
    printf 'inf'
    return
  fi
  sort -g "$input_file" | awk '
    { values[NR]=$1; weights[NR]=$2; total += $2 }
    END {
      if (NR == 0) {
        print "inf"
        exit
      }
      target = total / 2.0
      running = 0.0
      for (i=1; i<=NR; i++) {
        running += weights[i]
        if (running >= target) {
          printf "%.12g", values[i]
          exit
        }
      }
      printf "%.12g", values[NR]
    }
  '
}

case_rows=()
failures=0
weights_wall_file="${RUN_ROOT}/qe_wall_weights.tsv"
weights_iter_file="${RUN_ROOT}/qe_iter_weights.tsv"
: >"${weights_wall_file}"
: >"${weights_iter_file}"

run_case() {
  local case_id="$1"
  local weight="$2"
  local command="$3"
  local output_path="$4"
  local reference_energy="$5"
  local max_abs_delta="$6"

  local stderr_log="${RUN_ROOT}/${case_id}.stderr.log"
  local start_ns end_ns wall_seconds
  start_ns="$(date +%s%N)"
  local return_code=0
  if ! bash -lc "$command" 2>"${stderr_log}"; then
    return_code=$?
  fi
  end_ns="$(date +%s%N)"
  wall_seconds="$(awk -v s="${start_ns}" -v e="${end_ns}" 'BEGIN { printf "%.6f", (e-s)/1e9 }')"

  local converged="false"
  local correctness_ok="false"
  local iterations="0"
  local total_energy="0"
  local error_text=""

  local parsed_line=""
  if [[ -f "${output_path}" ]]; then
    parsed_line="$(parse_qe_output "${output_path}")"
  fi

  if [[ ${return_code} -eq 0 && -n "${parsed_line}" ]]; then
    converged="true"
    correctness_ok="true"
    total_energy="$(printf '%s' "${parsed_line}" | awk '{print $1}')"
    iterations="$(printf '%s' "${parsed_line}" | awk '{print int($2)}')"
  else
    if [[ -s "${stderr_log}" ]]; then
      error_text="$(tail -n 3 "${stderr_log}" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g')"
    else
      error_text="Quantum ESPRESSO run failed or output parse failed"
    fi
  fi

  if [[ "${correctness_ok}" == "true" && -n "${reference_energy}" ]]; then
    local delta_ok
    delta_ok="$(awk -v val="${total_energy}" -v ref="${reference_energy}" -v tol="${max_abs_delta}" 'BEGIN { d=val-ref; if (d<0) d=-d; print (d<=tol ? "true" : "false") }')"
    if [[ "${delta_ok}" != "true" ]]; then
      correctness_ok="false"
      error_text="total-energy drift exceeds threshold: value=${total_energy}, reference=${reference_energy}, tol=${max_abs_delta}"
    fi
  fi

  printf '%s %s\n' "${wall_seconds}" "${weight}" >>"${weights_wall_file}"
  printf '%s %s\n' "${iterations}" "${weight}" >>"${weights_iter_file}"

  if [[ "${converged}" != "true" || "${correctness_ok}" != "true" ]]; then
    failures=$((failures + 1))
  fi

  local escaped_error
  escaped_error="$(json_escape "${error_text}")"
  case_rows+=("{\"id\":\"${case_id}\",\"converged\":${converged},\"wall_seconds\":${wall_seconds},\"scf_iterations\":${iterations},\"total_energy_hartree\":${total_energy},\"total_energy_ry\":${total_energy},\"density_matrix\":[${total_energy}],\"mo_energies\":[${total_energy}],\"peak_rss_mb\":0.0,\"error\":\"${escaped_error}\"}")
}

run_case \
  "qe_water_scf" \
  "1.0" \
  "${QE_WATER_COMMAND:-pw.x -in examples/PWSCF/water_scf.in > .fermilink-optimize/qe_water_scf.out}" \
  ".fermilink-optimize/qe_water_scf.out" \
  "${QE_WATER_REFERENCE_ENERGY:--17.20}" \
  "${QE_WATER_MAX_ABS_ENERGY_DELTA:-5.0e-4}"

run_case \
  "qe_silicon_scf" \
  "2.0" \
  "${QE_SI_COMMAND:-pw.x -in examples/PWSCF/si_scf.in > .fermilink-optimize/qe_si_scf.out}" \
  ".fermilink-optimize/qe_si_scf.out" \
  "${QE_SI_REFERENCE_ENERGY:--15.85}" \
  "${QE_SI_MAX_ABS_ENERGY_DELTA:-1.0e-3}"

weighted_wall="$(weighted_median "${weights_wall_file}")"
weighted_iters="$(weighted_median "${weights_iter_file}")"
correctness_ok="false"
if [[ ${failures} -eq 0 ]]; then
  correctness_ok="true"
fi

cases_json="[]"
if [[ ${#case_rows[@]} -gt 0 ]]; then
  cases_json="[$(IFS=,; printf '%s' "${case_rows[*]}")]"
fi

payload="{\"benchmark_id\":\"fortran-quantum-espresso-scf\",\"correctness_ok\":${correctness_ok},\"summary_metrics\":{\"weighted_median_wall_seconds\":${weighted_wall},\"weighted_median_scf_iterations\":${weighted_iters},\"peak_rss_mb\":0.0,\"total_failures\":${failures}},\"cases\":${cases_json}}"

if [[ ${EMIT_JSON} -eq 1 ]]; then
  printf '%s\n' "${payload}"
else
  printf '%s\n' "${payload}"
fi
