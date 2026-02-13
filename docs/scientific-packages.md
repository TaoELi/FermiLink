# Scientific Package Management

## Most Common Workflow

```bash
# Install one package and make it default
fermilink install maxwelllink --activate

# Inspect registry
fermilink list

# Pin overlay scope and dependencies
fermilink overlay maxwelllink --entry skills --entry docs --entry src
fermilink dependencies maxwelllink --package meep
```

## Storage Model

Package metadata and sources are stored under `SCIPKG_ROOT` (default:
`~/.fermilink/scientific_packages`):

- `packages/<package_id>/...`: installed package trees
- `registry.json`: package metadata and active package
- `router_rules.json`: keyword router configuration

Per-session overlays are materialized in:

- `WORKSPACES_ROOT/<session_id>/repo`
- `WORKSPACES_ROOT/<session_id>/.package_manifest.json`

## Install Sources

### Curated Channel (Default)

```bash
fermilink install ase --activate
```

Default channel is `tel-research-group`. Supported curated package ids:
`maxwelllink`, `meep`, `lammps`, `qutip`, `psi4`, `ase`, `oqupy`, `packmol`,
`kwant`, `tkwant`, `elk`.

### Custom Zip URL

```bash
fermilink install mypkg \
  --zip-url https://github.com/<org>/<repo>/archive/refs/heads/main.zip \
  --activate
```

### Local Path

```bash
fermilink install mypkg --local-path /absolute/path/to/package --activate
```

### Compile Local Repository (Auto Skills Generation)

```bash
fermilink compile <package_id> <path>
```

Examples:

```bash
fermilink compile pyscf .
fermilink compile mypkg /absolute/path/to/project --activate
```

`compile` runs a 3-pass Codex workflow to generate and refine `skills/`:

1. map source/examples/docs/tests/tutorials and generate initial `skills/`;
2. audit whether `skills/` is sufficient for advanced scientific workflows;
3. verify links/path consistency and enrich `skills/` again.

Implementation details:

- temporary tool directory: `<path>/sci-skills-generator/`
- removed automatically before pass 3
- resulting project is installed via local-path flow into
  `SCIPKG_ROOT/packages/<package_id>`
- compile fails fast on package-id conflict with existing registry entry
- compile output suppresses known benign Codex rollout-path noise

Notes:

- `--activate` sets default package for new sessions.
- `--active` is supported as an alias for `--activate`.
- `--force` allows overwrite when managed package folder already exists.
- Zip installs remove `AGENTS.md`/`CLAUDE.md` files and top-level `projects/`.
- `install` and `delete` auto-sync `router_rules.json` unless `--no-router-sync`.
- `compile` also auto-syncs `router_rules.json` unless `--no-router-sync`.
- Add `--json` to any CLI command for full structured output.

## Package Lifecycle

```bash
fermilink list
fermilink activate maxwelllink
fermilink delete maxwelllink
fermilink delete maxwelllink --keep-files
```

## Overlay Scope

Limit what appears in workspace `repo/`:

```bash
fermilink overlay maxwelllink --entry skills --entry docs --entry src
```

Alternative CSV form:

```bash
fermilink overlay maxwelllink --entries skills,docs,src
```

Clear restriction (expose all exportable top-level entries):

```bash
fermilink overlay maxwelllink --clear
```

## Dependency Package Links

Dependencies are linked into:

- `repo/external_packages/<dependency_package_id>/`

Configure dependencies:

```bash
fermilink dependencies maxwelllink --package meep --package qutip
```

Or CSV form:

```bash
fermilink dependencies maxwelllink --packages meep,qutip
```

Clear dependencies:

```bash
fermilink dependencies maxwelllink --clear
```

## Router Rules Sync (Manual)

Install/delete already sync rules. If you edit `registry.json` manually, run:

```bash
python -c "from fermilink.config import resolve_scipkg_root; from fermilink.router_rules import sync_router_rules; print(sync_router_rules(resolve_scipkg_root()))"
```

Dry run:

```bash
python -c "from fermilink.config import resolve_scipkg_root; from fermilink.router_rules import sync_router_rules; print(sync_router_rules(resolve_scipkg_root(), dry_run=True))"
```

## Package Resolution Order at Runtime

Runner resolves package in this order:

1. Explicit request (`package_id` from web layer).
2. Workspace manifest pin (`.package_manifest.json`).
3. `SCIPKG_ACTIVE` env override.
4. Registry `active_package`.

`fermilink exec` resolves package with web-like routing:

1. `--package <id>` manual pin (if provided).
2. Router keyword scoring (`router_rules.json`) when auto-router is enabled.
3. Default fallback (`default_package_id`, active package, first installed).
4. Optional AGENTS-guided second-guess switch when confidence threshold is met.

## Authoring Checklist for New Packages

1. Prepare package `skills/` and `docs/`.
2. Install package with `fermilink install ...`.
3. Optionally set `overlay` and `dependencies`.
4. Validate in UI with `/package use <package_id>` and a real prompt.
5. Ensure runtime dependencies are installed on host.

Important: registering a package does not install system-level scientific
dependencies for that package.
