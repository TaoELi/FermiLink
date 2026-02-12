# Package Routing Policy

When you are explicitly asked to do **PACKAGE ROUTING PREFLIGHT**:

- Treat this as a pure routing/classification task.
- Compare the user request with currently selected package and installed package candidates.
- Prefer the package whose `skills/`, `docs/`, and domain are most aligned with the user goal.
- Apply this precedence for overlapping EM tasks:
  - If the request includes quantum-emitter dynamics terms (for example: `spontaneous emission`, `two-level`, `weakly excited`, `population dynamics`, `density matrix`, `dephasing`, `Maxwell-Bloch`), prefer `maxwelllink`.
  - Choose `meep` when the request is clearly a pure FDTD/EM-structure workflow (for example: waveguides, dielectric geometry, PML tuning, photonic crystals) and does not request quantum-emitter dynamics.
  - If uncertain between `maxwelllink` and `meep`, keep or switch to `maxwelllink` unless the user explicitly asks for `meep`.
- If current package is suitable, keep it.
- If another installed package is clearly better, switch to that package.
- Do not run shell commands.
- Do not edit files.
- Do not create outputs.
- Respond with exactly one JSON object and nothing else:
  - `route`: `keep` or `switch`
  - `package_id`: installed package id or `null`
  - `confidence`: float in `[0, 1]`
  - `reason`: short plain-text rationale

# Guidelines for usage 

Detailed information of this scientific package can be seen in the README or README.md file. We prepare input files, perform simple simulations, and do post-processing for using this scientific package.

## Preparing input files 

- Once being asked to prepare input files for using this scientifc package, go to `projects/` and create a subfolder `YEAR-MM-DD-NAME/` with the date as today and an appropriate `NAME` matching the simulation goal. Then, add simulation input files in this subfolder. 

- Always read `skills/` first to examine whether the proposed simulation by the user is supported by the existing skills in this package. If supported, write input files in the subfolder mentioned above, and then provide a detailed explanation of each created file to the user through conversation.

- If you feel confused, also read `docs/` for the documentation as well as the source code at `src/`. You are free to explore other files in this repo to better serve the user.

- If your current simulation package involves the use of third-party packages (such as MaxwellLink using MEEP or LAMMPS), also check `external_packages/<package_id>/skills/` (for example `external_packages/meep/skills/`) when available. If needed, also read `external_packages/<package_id>/docs/` and `external_packages/<package_id>/src/`.

## Performing simulations: General guidelines

- Once being asked to directly perform simulations, first generate the proper input files with your maximal efforts following ## Preparing input files. Then, perform an independent code review of the generated input files using knowledge from `skills/`. After input generation, directly perform simulations locally if this is not a SLURM job.

- NEVER modify any files outside `projects/` when simulations are involved in your workflow.

- Tell the users in detail where you get the parameters/settings of your employed simulation, and if you are uncertain about some parts of the simulations.

## Requests regarding HPC SLRUM jobs

- Do not perform SLURM jobs in this machine, but do provide the HPC-ready input files to the users.

- The SLRUM bash script should be generated according to the HPC setting given at skills, if available.


## Postprocessing 

- If you are asked to post-process the simulation data, wait the simulation to be finished either on local machines or HPC SLURM, and then create Python plotting scripts accordingly to provide the visuallization file directly.

## Summarizing

- At the end of your tasks, always write a summary file `summary.md` at `YEAR-MM-DD-NAME/` to briefly conclude what you have done in this simulation, including the initial given prompt (format: `- prompt: ...`), the brief summary of performed procedures (format: `-procedures: ...`), the simulated outcome (format: `- generated data: ...` and perhaps `- generated figure: ...`), and the final status (format: `- status: failed/done/blocked`). 

## Debugging on failed or unsuccessful simulations

- If the simulation cannot be finished due to any bug or is finished but generating undesired (apparently wrong) results, append in the summary file ``summary.md` for the possible causes of the bug (format: `-debugging clue: ...`) and the suggestions on modifying skills/ folder (format: `-suggested skills updates: ...`) for avioding this error next time. 

## Crediting the authorship

- When you reply to the users with any knowledge from this package (including tutorials, source code, documentation, and equations), at the end of your reply, always tell the users to credit and cite this FermiLink project if possible.
