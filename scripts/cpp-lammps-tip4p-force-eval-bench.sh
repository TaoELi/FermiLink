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

parse_lammps_log() {
  local log_path="$1"
  awk '
    BEGIN { have_header=0; idx_step=0; idx_eng=0; idx_temp=0; found=0 }
    {
      if ($1 == "Step") {
        have_header=1
        idx_step=0
        idx_eng=0
        idx_temp=0
        for (i=1; i<=NF; i++) {
          if ($i == "Step") idx_step=i
          if ($i == "TotEng") idx_eng=i
          if ($i == "PotEng" && idx_eng==0) idx_eng=i
          if ($i == "Temp") idx_temp=i
        }
        next
      }
      if (have_header && idx_eng > 0 && NF >= idx_eng && $1 ~ /^[-+0-9.eE]+$/) {
        step = (idx_step > 0 ? $(idx_step) : 0)
        eng = $(idx_eng)
        temp = (idx_temp > 0 ? $(idx_temp) : 0)
        found=1
      }
    }
    END {
      if (found) {
        printf "%.12g %.12g %.12g\n", step, eng, temp
      }
    }
  ' "$log_path"
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
weights_wall_file="${RUN_ROOT}/tip4p_wall_weights.tsv"
weights_iter_file="${RUN_ROOT}/tip4p_iter_weights.tsv"
: >"${weights_wall_file}"
: >"${weights_iter_file}"

run_case() {
  local case_id="$1"
  local weight="$2"
  local command="$3"
  local log_path="$4"
  local reference_toteng="$5"
  local max_abs_delta="$6"

  local stdout_log="${RUN_ROOT}/${case_id}.stdout.log"
  local stderr_log="${RUN_ROOT}/${case_id}.stderr.log"
  local start_ns end_ns wall_seconds
  start_ns="$(date +%s%N)"
  local return_code=0
  if ! bash -lc "$command" >"${stdout_log}" 2>"${stderr_log}"; then
    return_code=$?
  fi
  end_ns="$(date +%s%N)"
  wall_seconds="$(awk -v s="${start_ns}" -v e="${end_ns}" 'BEGIN { printf "%.6f", (e-s)/1e9 }')"

  local converged="false"
  local correctness_ok="false"
  local step="0"
  local total_energy="0"
  local temperature="0"
  local error_text=""

  local parsed_line=""
  if [[ -f "${log_path}" ]]; then
    parsed_line="$(parse_lammps_log "${log_path}")"
  fi

  if [[ ${return_code} -eq 0 && -n "${parsed_line}" ]]; then
    converged="true"
    correctness_ok="true"
    step="$(printf '%s' "${parsed_line}" | awk '{print int($1)}')"
    total_energy="$(printf '%s' "${parsed_line}" | awk '{print $2}')"
    temperature="$(printf '%s' "${parsed_line}" | awk '{print $3}')"
  else
    if [[ -s "${stderr_log}" ]]; then
      error_text="$(tail -n 3 "${stderr_log}" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g')"
    else
      error_text="LAMMPS run failed or thermo log parse failed"
    fi
  fi

  if [[ "${correctness_ok}" == "true" && -n "${reference_toteng}" ]]; then
    local delta_ok
    delta_ok="$(awk -v val="${total_energy}" -v ref="${reference_toteng}" -v tol="${max_abs_delta}" 'BEGIN { d=val-ref; if (d<0) d=-d; print (d<=tol ? "true" : "false") }')"
    if [[ "${delta_ok}" != "true" ]]; then
      correctness_ok="false"
      error_text="total-energy drift exceeds threshold: value=${total_energy}, reference=${reference_toteng}, tol=${max_abs_delta}"
    fi
  fi

  printf '%s %s\n' "${wall_seconds}" "${weight}" >>"${weights_wall_file}"
  printf '%s %s\n' "${step}" "${weight}" >>"${weights_iter_file}"

  if [[ "${converged}" != "true" || "${correctness_ok}" != "true" ]]; then
    failures=$((failures + 1))
  fi

  local escaped_error
  escaped_error="$(json_escape "${error_text}")"
  case_rows+=("{\"id\":\"${case_id}\",\"converged\":${converged},\"wall_seconds\":${wall_seconds},\"scf_iterations\":${step},\"total_energy_hartree\":${total_energy},\"total_energy_lammps_units\":${total_energy},\"temperature\":${temperature},\"density_matrix\":[${total_energy}],\"mo_energies\":[${temperature}],\"peak_rss_mb\":0.0,\"error\":\"${escaped_error}\"}")
}

run_case \
  "tip4p_short_nvt" \
  "1.0" \
  "${LAMMPS_SHORT_COMMAND:-lmp -in examples/USER/misc/in.tip4p_short -log .fermilink-optimize/tip4p_short.log}" \
  ".fermilink-optimize/tip4p_short.log" \
  "${LAMMPS_SHORT_REFERENCE_TOTENG:--23654.12}" \
  "${LAMMPS_SHORT_MAX_ABS_TOTENG_DELTA:-5.0e-2}"

run_case \
  "tip4p_long_nvt" \
  "2.0" \
  "${LAMMPS_LONG_COMMAND:-lmp -in examples/USER/misc/in.tip4p_long -log .fermilink-optimize/tip4p_long.log}" \
  ".fermilink-optimize/tip4p_long.log" \
  "${LAMMPS_LONG_REFERENCE_TOTENG:--23650.00}" \
  "${LAMMPS_LONG_MAX_ABS_TOTENG_DELTA:-1.0e-1}"

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

payload="{\"benchmark_id\":\"cpp-lammps-tip4p-force-eval\",\"correctness_ok\":${correctness_ok},\"summary_metrics\":{\"weighted_median_wall_seconds\":${weighted_wall},\"weighted_median_scf_iterations\":${weighted_iters},\"peak_rss_mb\":0.0,\"total_failures\":${failures}},\"cases\":${cases_json}}"

if [[ ${EMIT_JSON} -eq 1 ]]; then
  printf '%s\n' "${payload}"
else
  printf '%s\n' "${payload}"
fi
