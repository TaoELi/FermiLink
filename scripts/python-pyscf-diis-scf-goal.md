# Optimization Goal

## Package
pyscf

## Language
python

## Target
Optimize DIIS (Direct Inversion in the Iterative Subspace) behavior for SCF in
PySCF, with primary focus on `pyscf/lib/diis.py` and SCF call sites that
invoke DIIS during iterative convergence.

Target optimization opportunities include:
- reduced overhead in DIIS history management and error-vector assembly
- lower-cost linear algebra in DIIS extrapolation updates
- fewer transient allocations and better memory locality in tight SCF loops
- faster convergence-path handling without relaxing tolerances

## Editable Scope
- pyscf/lib/diis.py
- pyscf/scf/**

## Performance Metric
Minimize end-to-end SCF convergence time.

Primary objective should be weighted median total wall-clock time across all
benchmark cases (including both setup and kernel phases).

## Correctness Constraints
- Total SCF energy absolute delta <= 5e-8 Hartree vs incumbent baseline
- Molecular orbital energies RMS delta <= 2e-5 vs incumbent baseline
- Open-shell `<S^2>` absolute delta <= 1e-3 vs incumbent baseline
- All benchmark cases must converge within configured cycle limits
- Do not loosen `conv_tol`, `conv_tol_grad`, DIIS start criteria, or max-cycle defaults
- No case-specific shortcuts keyed on molecule identity

## Representative Workloads
- train-o2: O2 / 6-31g / UHF (spin=2) / DIIS space=12
- train-h2o: H2O / 6-31g / RHF / DIIS space=12
- test-h2o: H2O / cc-pVDZ / RHF / DIIS space=12
- test-nh3: NH3 / cc-pVDZ / RHF / DIIS space=12
- test-o2: O2 / cc-pVDZ / UHF (spin=2) / DIIS space=12
- test-no: NO / cc-pVDZ / UHF (spin=1) / DIIS space=12

## Build
```bash
python -m pip install -U pip
python -m pip install -e .
python -m pip install PyYAML
```

## Notes
- Use MINAO initial guess unless a case explicitly specifies otherwise.
- Keep benchmark behavior deterministic across repeated runs.
- If multithreading is used, keep thread counts explicit in benchmark runtime config.
- In the generated benchmark YAML, include a top-level split block:
  ```yaml
  split:
    train_case_ids:
      - train-o2
      - train-h2o
  ```

## Step-by-Step Usage (Goal Mode + master worktree)

1. Prepare a local PySCF checkout and update `master`.
   ```bash
   git clone https://github.com/pyscf/pyscf.git ~/pyscf
   cd ~/pyscf
   git fetch origin
   git checkout master
   git pull --ff-only origin master
   ```

2. Create a controller worktree from the original `master` branch.
   ```bash
   cd ~/pyscf
   git worktree add -b fermilink-optimize/pyscf-diis ../pyscf-optimize-diis master
   cd ~/pyscf-optimize-diis
   ```

3. Ensure the package can be built in the worktree.
   ```bash
   python -m pip install -U pip
   python -m pip install -e .
   python -m pip install PyYAML
   ```

4. Ensure a `skills/` folder exists in the worktree.
   ```bash
   cp -r ~/pyscf/skills ./skills
   ```
   If `~/pyscf/skills` does not exist yet, create it first with your normal
   FermiLink package install/compile flow.

5. Point `GOAL` to this sample file (inside your FermiLink repo checkout).
   ```bash
   export GOAL=/path/to/FermiLink_development/scripts/python-pyscf-diis-scf-goal.md
   test -f "$GOAL"
   ```

6. Launch optimize goal mode from the controller worktree.
   ```bash
   cd ~/pyscf-optimize-diis
   fermilink optimize "$GOAL" \
     --skills-source existing \
     --max-iterations 30 \
     --stop-on-consecutive-rejections 8 \
     --timeout-seconds 900
   ```

7. Monitor campaign progress.
   ```bash
   cd ~/pyscf-optimize-diis
   fermilink optimize status
   ```

8. Resume if interrupted.
   ```bash
   cd ~/pyscf-optimize-diis
   fermilink optimize "$GOAL" --resume
   ```

9. Review accepted commits and clean up when done.
   ```bash
   cd ~/pyscf-optimize-diis
   git log --oneline

   cd ~/pyscf
   git worktree remove ../pyscf-optimize-diis
   # git branch -D fermilink-optimize/pyscf-diis
   ```
