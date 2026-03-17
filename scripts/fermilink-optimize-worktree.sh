#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"

usage() {
  cat <<'EOF'
Create/reuse a git worktree and launch one isolated `fermilink optimize` run.

Usage:
  fermilink-optimize-worktree.sh \
    --project-root /path/to/package-repo \
    --branch fermilink-optimize/pyscf-hf-small \
    --benchmark scripts/python-pyscf-hf-small-diis-benchmark.yaml \
    --bench scripts/python-pyscf-scf-bench.py \
    [options] \
    -- [extra fermilink optimize args]

Required options:
  --project-root, --repo PATH      Clean git repo clone to optimize.
  --branch NAME                    Optimization branch/worktree branch name.
  --benchmark, --yaml PATH         Benchmark YAML path.
  --bench, --bench-script PATH     Benchmark runner script path (validation + copy).

Common options:
  --package-id ID                  Package id for `fermilink optimize` (default: repo dir name).
  --base-ref REF                   Base ref when creating a new branch (default: main).
  --worktree-root PATH             Parent dir for generated worktrees.
  --worktree-name NAME             Explicit worktree directory name.
  --skills-source MODE             `existing|channel|compile|auto` (default: existing).
  --hpc-profile PATH               Forwarded to `fermilink optimize --hpc-profile`.
  --venv-dir NAME                  Per-worktree venv directory (default: .venv).
  --no-venv                        Skip venv setup and editable install.
  --python-bin BIN                 Python binary for venv setup (default: python3).
  --sync-editable                  Always run `pip install -e <worktree>` when venv exists.
  --fermilink-bin BIN              `fermilink` executable (default: fermilink).
  --isolate-fermilink-home         Set `FERMILINK_HOME` under this worktree.
  --fermilink-home PATH            Explicit `FERMILINK_HOME` (implies isolation).
  --allow-dirty-base               Allow uncommitted changes in --project-root.
  --dry-run                        Print resolved command and exit.
  -h, --help                       Show this help.

Extra optimize arguments:
  Everything after `--` is forwarded to `fermilink optimize`.

Examples:
  ./scripts/fermilink-optimize-worktree.sh \
    --project-root /data/pyscf \
    --branch fermilink-optimize/pyscf-dft-large \
    --benchmark scripts/python-pyscf-dft-large-diis-benchmark.yaml \
    --bench scripts/python-pyscf-scf-bench.py \
    --hpc-profile scripts/hpc_profile_anvil.json \
    -- --max-iterations 80 --worker-max-iterations 8 --baseline-only

EOF
}

log() {
  printf '[opt-worktree] %s\n' "$*"
}

err() {
  printf '[opt-worktree] error: %s\n' "$*" >&2
}

die() {
  err "$*"
  exit 1
}

require_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || die "Required command not found: $cmd"
}

abs_path() {
  # abs_path <base_dir> <path_text>
  python3 - "$1" "$2" <<'PY'
import os
import sys
base = sys.argv[1]
raw = sys.argv[2]
value = os.path.expanduser(raw)
if not os.path.isabs(value):
    value = os.path.join(base, value)
print(os.path.abspath(value))
PY
}

is_subpath() {
  # is_subpath <root_abs> <path_abs> -> prints 1 or 0
  python3 - "$1" "$2" <<'PY'
import os
import sys
root = os.path.abspath(sys.argv[1])
path = os.path.abspath(sys.argv[2])
try:
    os.path.commonpath([root, path])
except ValueError:
    print(0)
    raise SystemExit(0)
print(1 if os.path.commonpath([root, path]) == root else 0)
PY
}

rel_path() {
  # rel_path <root_abs> <path_abs>
  python3 - "$1" "$2" <<'PY'
import os
import sys
root = os.path.abspath(sys.argv[1])
path = os.path.abspath(sys.argv[2])
print(os.path.relpath(path, root))
PY
}

slugify_branch() {
  local raw="$1"
  local value="${raw//\//__}"
  value="${value//:/_}"
  value="${value//@/_}"
  value="${value// /_}"
  # Keep a conservative path-safe set.
  value="$(printf '%s' "$value" | tr -cd 'A-Za-z0-9._-')"
  [[ -n "$value" ]] || value="worktree"
  printf '%s' "$value"
}

resolve_repo_file_rel() {
  # resolve_repo_file_rel <repo_root_abs> <source_abs>
  local repo_root_abs="$1"
  local source_abs="$2"
  local inside
  inside="$(is_subpath "$repo_root_abs" "$source_abs")"
  if [[ "$inside" == "1" ]]; then
    rel_path "$repo_root_abs" "$source_abs"
  else
    printf 'scripts/%s' "$(basename "$source_abs")"
  fi
}

copy_into_worktree() {
  # copy_into_worktree <src_abs> <worktree_abs> <dst_rel>
  local src_abs="$1"
  local worktree_abs="$2"
  local dst_rel="$3"
  local dst_abs="$worktree_abs/$dst_rel"
  mkdir -p "$(dirname "$dst_abs")"
  cp "$src_abs" "$dst_abs"
}

project_root_raw=""
branch_name=""
benchmark_raw=""
bench_raw=""
package_id=""
base_ref="main"
worktree_root_raw=""
worktree_name=""
skills_source="existing"
hpc_profile_raw=""
venv_dir=".venv"
use_venv=1
python_bin="python3"
sync_editable=0
fermilink_bin="fermilink"
allow_dirty_base=0
dry_run=0
isolate_fermilink_home=0
fermilink_home_raw=""

extra_optimize_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-root|--repo)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      project_root_raw="$2"
      shift 2
      ;;
    --branch)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      branch_name="$2"
      shift 2
      ;;
    --benchmark|--yaml)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      benchmark_raw="$2"
      shift 2
      ;;
    --bench|--bench-script)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      bench_raw="$2"
      shift 2
      ;;
    --package-id)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      package_id="$2"
      shift 2
      ;;
    --base-ref)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      base_ref="$2"
      shift 2
      ;;
    --worktree-root)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      worktree_root_raw="$2"
      shift 2
      ;;
    --worktree-name)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      worktree_name="$2"
      shift 2
      ;;
    --skills-source)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      skills_source="$2"
      shift 2
      ;;
    --hpc-profile)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      hpc_profile_raw="$2"
      shift 2
      ;;
    --venv-dir)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      venv_dir="$2"
      shift 2
      ;;
    --no-venv)
      use_venv=0
      shift
      ;;
    --python-bin)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      python_bin="$2"
      shift 2
      ;;
    --sync-editable)
      sync_editable=1
      shift
      ;;
    --fermilink-bin)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      fermilink_bin="$2"
      shift 2
      ;;
    --allow-dirty-base)
      allow_dirty_base=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --isolate-fermilink-home)
      isolate_fermilink_home=1
      shift
      ;;
    --fermilink-home)
      [[ $# -ge 2 ]] || die "Missing value for $1"
      fermilink_home_raw="$2"
      isolate_fermilink_home=1
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      extra_optimize_args=("$@")
      break
      ;;
    *)
      die "Unknown option: $1 (use -- to forward options to fermilink optimize)"
      ;;
  esac
done

[[ -n "$project_root_raw" ]] || die "--project-root is required"
[[ -n "$branch_name" ]] || die "--branch is required"
[[ -n "$benchmark_raw" ]] || die "--benchmark is required"
[[ -n "$bench_raw" ]] || die "--bench is required"

require_cmd git
require_cmd python3
require_cmd cp
require_cmd mkdir
require_cmd "$fermilink_bin"

project_root_abs="$(abs_path "$PWD" "$project_root_raw")"
[[ -d "$project_root_abs" ]] || die "Project root does not exist: $project_root_abs"
git -C "$project_root_abs" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "Not a git repository: $project_root_abs"

if [[ "$allow_dirty_base" -ne 1 ]]; then
  if [[ -n "$(git -C "$project_root_abs" status --porcelain)" ]]; then
    die "Base repo is dirty. Commit/stash first, or pass --allow-dirty-base."
  fi
fi

benchmark_src_abs="$(abs_path "$PWD" "$benchmark_raw")"
bench_src_abs="$(abs_path "$PWD" "$bench_raw")"
[[ -f "$benchmark_src_abs" ]] || die "Benchmark file does not exist: $benchmark_src_abs"
[[ -f "$bench_src_abs" ]] || die "Bench script does not exist: $bench_src_abs"

if [[ -z "$package_id" ]]; then
  package_id="$(basename "$project_root_abs" | tr '[:upper:]' '[:lower:]')"
fi

if [[ -z "$worktree_root_raw" ]]; then
  worktree_root_raw="$(dirname "$project_root_abs")/.fermilink-worktrees/$(basename "$project_root_abs")"
fi
worktree_root_abs="$(abs_path "$PWD" "$worktree_root_raw")"
mkdir -p "$worktree_root_abs"

if [[ -z "$worktree_name" ]]; then
  worktree_name="$(slugify_branch "$branch_name")"
fi
worktree_abs="$worktree_root_abs/$worktree_name"

if [[ -e "$worktree_abs" ]]; then
  git -C "$worktree_abs" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "Existing worktree path is not a git worktree: $worktree_abs"
  current_branch="$(git -C "$worktree_abs" rev-parse --abbrev-ref HEAD)"
  [[ "$current_branch" == "$branch_name" ]] \
    || die "Worktree exists on branch '$current_branch' (expected '$branch_name'): $worktree_abs"
  log "Reusing existing worktree: $worktree_abs"
else
  if git -C "$project_root_abs" show-ref --verify --quiet "refs/heads/$branch_name"; then
    log "Creating worktree for existing branch '$branch_name' at $worktree_abs"
    git -C "$project_root_abs" worktree add "$worktree_abs" "$branch_name"
  else
    log "Creating worktree and new branch '$branch_name' from '$base_ref' at $worktree_abs"
    git -C "$project_root_abs" worktree add -b "$branch_name" "$worktree_abs" "$base_ref"
  fi
fi

benchmark_rel="$(resolve_repo_file_rel "$project_root_abs" "$benchmark_src_abs")"
bench_rel="$(resolve_repo_file_rel "$project_root_abs" "$bench_src_abs")"

copy_into_worktree "$benchmark_src_abs" "$worktree_abs" "$benchmark_rel"
copy_into_worktree "$bench_src_abs" "$worktree_abs" "$bench_rel"
chmod +x "$worktree_abs/$bench_rel" || true

if ! grep -Fq "$bench_rel" "$worktree_abs/$benchmark_rel"; then
  die "Benchmark '$benchmark_rel' does not reference '$bench_rel'. Use a matched benchmark/bench pair."
fi

if [[ "$skills_source" == "existing" && ! -d "$worktree_abs/skills" ]]; then
  die "skills/ is missing in worktree but --skills-source=existing was requested."
fi

hpc_profile_abs=""
if [[ -n "$hpc_profile_raw" ]]; then
  hpc_profile_abs="$(abs_path "$PWD" "$hpc_profile_raw")"
  [[ -f "$hpc_profile_abs" ]] || die "HPC profile file does not exist: $hpc_profile_abs"
fi

if [[ "$use_venv" -eq 1 ]]; then
  require_cmd "$python_bin"
  venv_abs="$worktree_abs/$venv_dir"
  if [[ ! -x "$venv_abs/bin/python" ]]; then
    log "Creating venv: $venv_abs"
    "$python_bin" -m venv "$venv_abs"
    "$venv_abs/bin/python" -m pip install -U pip
    "$venv_abs/bin/python" -m pip install -e "$worktree_abs"
  elif [[ "$sync_editable" -eq 1 ]]; then
    log "Refreshing editable install in existing venv: $venv_abs"
    "$venv_abs/bin/python" -m pip install -e "$worktree_abs"
  fi
  export PATH="$venv_abs/bin:$PATH"
fi

if [[ "$isolate_fermilink_home" -eq 1 ]]; then
  if [[ -n "$fermilink_home_raw" ]]; then
    fermilink_home_abs="$(abs_path "$PWD" "$fermilink_home_raw")"
  else
    fermilink_home_abs="$worktree_abs/.fermilink-home"
  fi
  mkdir -p "$fermilink_home_abs"
  export FERMILINK_HOME="$fermilink_home_abs"
fi

optimize_cmd=(
  "$fermilink_bin"
  optimize
  "$package_id"
  "$worktree_abs"
  --benchmark
  "$worktree_abs/$benchmark_rel"
  --branch
  "$branch_name"
  --skills-source
  "$skills_source"
)

if [[ -n "$hpc_profile_abs" ]]; then
  optimize_cmd+=(--hpc-profile "$hpc_profile_abs")
fi
if [[ ${#extra_optimize_args[@]} -gt 0 ]]; then
  optimize_cmd+=("${extra_optimize_args[@]}")
fi

log "Prepared worktree optimize run:"
log "  project_root:   $project_root_abs"
log "  worktree:       $worktree_abs"
log "  branch:         $branch_name"
log "  package_id:     $package_id"
log "  benchmark:      $worktree_abs/$benchmark_rel"
log "  bench:          $worktree_abs/$bench_rel"
if [[ "$use_venv" -eq 1 ]]; then
  log "  venv:           $worktree_abs/$venv_dir"
fi
if [[ -n "${FERMILINK_HOME:-}" ]]; then
  log "  FERMILINK_HOME: $FERMILINK_HOME"
fi
if [[ -n "$hpc_profile_abs" ]]; then
  log "  hpc_profile:    $hpc_profile_abs"
fi
log "Command:"
printf '  '
printf '%q ' "${optimize_cmd[@]}"
printf '\n'

if [[ "$dry_run" -eq 1 ]]; then
  log "Dry-run requested; exiting."
  exit 0
fi

cd "$worktree_abs"
exec "${optimize_cmd[@]}"

