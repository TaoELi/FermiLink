# Example invocations for common scientific packages

Adjust package paths to your local checkout locations.

## LAMMPS
```bash
python sci-skills-generator/scripts/generate_skills_folder.py \
  --package-root /path/to/lammps \
  --package-name lammps \
  --output-dir /path/to/lammps/skills \
  --max-skills 30 \
  --overwrite
```

## PSI4
```bash
python sci-skills-generator/scripts/generate_skills_folder.py \
  --package-root /path/to/psi4 \
  --package-name psi4 \
  --output-dir /path/to/psi4/skills \
  --max-skills 30 \
  --overwrite
```

## GROMACS
```bash
python sci-skills-generator/scripts/generate_skills_folder.py \
  --package-root /path/to/gromacs \
  --package-name gromacs \
  --output-dir /path/to/gromacs/skills \
  --max-skills 30 \
  --overwrite
```

## Q-Chem documentation bundle (docs-only)
```bash
python sci-skills-generator/scripts/generate_skills_folder.py \
  --package-root /path/to/qchem-docs \
  --package-name qchem \
  --docs-only \
  --output-dir /path/to/qchem-docs/skills \
  --max-skills 30 \
  --overwrite
```

## MEEP FDTD
```bash
python sci-skills-generator/scripts/generate_skills_folder.py \
  --package-root /path/to/meep \
  --package-name meep \
  --output-dir /path/to/meep/skills \
  --max-skills 30 \
  --overwrite
```

## Preview before writing
```bash
python sci-skills-generator/scripts/generate_skills_folder.py \
  --package-root /path/to/package \
  --package-name package \
  --max-skills 30 \
  --dry-run
```
