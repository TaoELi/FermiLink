---
name: optimize-report
description: Generate a Sphinx-ready report bundle (plots, RST, contract, per-accepted-commit pages) from a completed `fermilink optimize` workspace. Use when the user asks to visualize, export, or publish optimization results from a `.fermilink-optimize` directory, or wants to prepare material for the Project Optimization website.
---

# FermiLink Optimize Report

Use this skill when the user asks to visualize or publish the results of a
finished `fermilink optimize` run. The input is a `.fermilink-optimize`
directory (e.g. `<workspace>/<task>/.fermilink-optimize/`). The output is a
self-contained bundle that is ready to drop into an external Sphinx site.

## When to use

- "visualize the optimize results", "plot metric vs iteration"
- "export the optimization outcome", "make a report"
- "prepare this optimize run for the Project Optimization website"

## Inputs

Ask the user for (or infer from the current working directory):

1. Path to the `.fermilink-optimize` directory. It must contain `results.tsv`
   and (usually) `runs/iter_XXXX/` subdirectories plus `autogen/`.
2. Optional output directory. Default is
   `<optimize-dir>/../optimize-report/`.
3. Optional title, metric label, and direction (`lower` or `higher`). If
   omitted, direction is auto-detected from the baseline/accepted trajectory
   and label is humanized from `primary_metric_name` in `results.tsv`.

Do not silently invent a path. If the user has not named one, ask.

## How to run

Call the generator as a plain Python script. It depends only on the standard
library + matplotlib:

```bash
python skills/optimize-report/assets/build_report.py \
    <path-to>/.fermilink-optimize \
    --out <path-to>/optimize-report
```

Optional flags: `--title`, `--metric-label`, `--direction {lower,higher}`.
Use `--git-push` to also run `git push --set-upstream` for the checked-out
worktree branch after the bundle is written; this is only allowed when the
branch name starts with `fermilink-optimize`, so the script refuses to push
`main` or other long-lived branches. When that push target resolves to a
GitHub remote, the generated `index.rst` and accepted-commit detail pages also
turn displayed commit hashes into clickable GitHub commit links.

The script is idempotent — it wipes and rewrites the output directory on each
run.

## Output bundle

```
optimize-report/
  index.rst                      # title, summary, plots, accepted toctree, rerun appendix, benchmark inventory
  img/
    metric_vs_iter.{png,svg}     # all iterations, colored by status
    improvement_cumulative.{png,svg}  # running-incumbent staircase
  iterations/
    iter_XXXX_accepted.rst       # one page per accepted commit
    _diffs/iter_XXXX_<sha>.diff  # raw diff for download
  contract/
    benchmark.yaml               # copied from autogen/
    benchmark_runner.py          # copied from autogen/
    goal.md                      # archived goal-mode source copied from autogen/
    goal_inputs.json             # if present
    ...                          # goal_analysis.json, goal_mode.json, run_optimize.sh, setup_env.sh
  inputs/
    all/...                      # copied benchmark input files from .fermilink-optimize/inputs/all/
  data/
    results.tsv                  # copied verbatim
    summary.json                 # machine-readable roll-up
```

Each `iter_XXXX_accepted.rst` page contains:

- change summary (from the `description` field, with the trailing rationale
  stripped)
- acceptance rationale (the bracketed `[...]` tail from the description)
- guardrails & metrics table (from `review_context.json` +
  `controller_result.json`: decision, correctness, hard-reject flag, incumbent
  vs candidate metric, Δ%, changed paths)
- diffstat (from `candidate_diff_stat.txt`)
- truncated diff block + download link for the full `candidate.diff`

Rejected and correctness-failure iterations appear in the index table and in
the plot, but do not get their own pages — the index stays focused on the
commits that actually shipped.

## Behavior notes

- Safe to run before the optimize job has produced any accepted iterations;
  the index will still render with only a baseline row.
- The plotter auto-detects metric direction from the baseline→accepted trend,
  but the user can override with `--direction`.
- The generated `index.rst` ends with a rerun appendix that points back to the
  default upstream repo (`git@github.com:skilled-scipkg/<pkg-id>.git`),
  links the upstream GitHub default branch when local git metadata can resolve
  it, chooses the matching launcher (`fermilink-optimize-python` vs
  `fermilink-optimize-cpp`) from the copied goal/contract metadata, and
  includes a deterministic expert-mode rerun path based on copied
  `benchmark.yaml` + `benchmark_runner.py`. When the copied `goal.md` contains
  a `## Build` code block, the rerun appendix also shows that block under a
  `Building environment` subsection and reminds users that these commands are
  rerun before benchmarks via `runtime.pre_commands`. If
  `.fermilink-optimize/inputs/all/` exists, those staged benchmark input files
  are copied into the report bundle under `inputs/all/` and the deterministic
  rerun path restores them into `.fermilink-optimize/inputs/all/` before
  invoking `fermilink optimize`.
- The generated `index.rst` inserts an `Input files for Benchmarks` section
  immediately below `Benchmark Contracts`, with download links for the copied
  `inputs/all/**` files.
- The `Benchmark Contracts` section itself only shows the three rerun-critical
  downloads:
  `benchmark.yaml`, `benchmark_runner.py`, and `goal.md`, even when the bundle
  also carries additional sidecars such as `goal_inputs.json` or
  `setup_env.sh`.
- The generated `index.rst` also ends with a top-level `Benchmark Examples`
  section
  that copies the `train-*` and `test-*` case blocks from `benchmark.yaml`
  into separate YAML code blocks, noting that train cases are used by workers
  and test cases by the controller.
- Other reader-facing section titles in the generated index are also slightly
  expanded for clarity: `Optimization Trajectory`, `Runtime Data`, and
  `Rerun Guide`.
- Commit hashes are truncated to 12 characters in all rendered output.
- Diffs longer than 600 lines are truncated inline; the full file is still
  written to `iterations/_diffs/` and linked for download.
- The script does not require pandas or jinja — only `matplotlib` plus the
  standard library.
- `--git-push` pushes the checked-out branch from the optimize worktree to its
  configured remote (falling back to `origin`) with `git push --set-upstream`,
  so the remote branch is created automatically when it does not already
  exist. The safety gate only allows this for branches whose names start with
  `fermilink-optimize`. If the push remote resolves to `github.com`, the
  generated `index.rst` and `iterations/iter_XXXX_accepted.rst` pages also add
  clickable commit-hash links back to the published GitHub branch history.

## Scope boundaries

- This skill only produces the per-task bundle. It does not publish a website
  or modify any hero-page link in `src/fermilink/`. Those are separate
  follow-ups once the external Project Optimization repo exists.
- Do not modify anything inside the source `.fermilink-optimize` directory —
  only read from it.
