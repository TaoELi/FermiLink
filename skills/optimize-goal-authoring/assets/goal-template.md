# Optimization Goal

## Package
package_id

## Language
python

## Target
Optimize the named hot path, with primary focus on the specific files that own
the behavior.

Target optimization opportunities include:
- hot-path implementation improvement
- algorithm-level reduction in iterations or linear algebra cost
- lower allocation or communication overhead
- preserved scientific fixed point and model semantics

## Editable Scope
- path/to/hot_path_file.py
- path/to/call_site.py

## Performance Metric
Minimize the targeted runtime metric.

Primary objective should be weighted median `<primary_metric>` across all
benchmark cases. Secondary objective should be lower `<iteration_or_phase_metric>`
when the benchmark runner can expose those metrics.

## Correctness Constraints
- Scientific scalar output absolute delta <= ...
- Scientific array RMS or max-absolute delta <= ...
- All cases must converge or finish successfully under incumbent limits
- Do not loosen tolerances, model semantics, basis/mesh/timestep, or other scientific controls
- No case-specific shortcuts keyed on workload identity

## Representative Workloads
- train-case-1: clear human-readable description of the case
- train-case-2: clear human-readable description of the case
- test-case-1: held-out case that stresses a different branch or scale

## Build
```bash
build and install commands here
```

## Notes
- Keep benchmark behavior deterministic across repeated runs.
- Pin thread counts or launcher settings explicitly when relevant.
- Include `runtime.pre_commands` derived from the build section when authoritative runs need rebuild/install steps.
- If workload files are needed, mention them directly in `## Representative Workloads` and keep them bundled with the goal file.
- In the generated benchmark YAML, include a top-level split block:
  ```yaml
  split:
    train_case_ids:
      - train-case-1
      - train-case-2
  ```
