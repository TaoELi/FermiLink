# MaxwellLink skills

This `skills/` directory is for AI-agent workflows (not end-user runtime code).

Start here:
- `skills/mxl-index/SKILL.md` (router: choose solver/driver/HPC/post-processing workflows)
- `skills/mxl-project-scaffold/SKILL.md` (create `projects/YYYY-MM-DD-NAME/` inputs from templates)

Each skill is a self-contained folder with:
- `SKILL.md` (required instructions + YAML frontmatter)
- optional `scripts/`, `references/`, `assets/`

**For custom HPC setting, modify `mxl-hpc-slurm/resources/hpc_setting.md`.**
