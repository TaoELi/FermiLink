# Install and Run

## Fast Path (Recommended)

```bash
pip install .
codex login
fermilink install maxwelllink --activate
fermilink agent --sandbox
fermilink start
```

Then open `http://localhost:7860`.

## Prerequisites

- Python `>= 3.11`
- `git` on `PATH`
- Codex CLI on `PATH` (`codex`)

Codex CLI install example:

```bash
npm i -g @openai/codex
```

## Agent Runtime Policy (Sandbox + Provider)

Configure global runtime policy once; it applies to web, runner, and `exec`:

```bash
# show current policy
fermilink agent --json

# enforce sandbox using current sandbox mode (default: workspace-write)
fermilink agent --sandbox

# bypass sandbox for scientific workloads that conflict with sandbox constraints
fermilink agent --bypass-sandbox

# provider selector (currently codex is implemented)
fermilink agent codex
```

Notes:

- Policy is persisted in `FERMILINK_HOME/agent_runtime.json`.
- `fermilink exec --sandbox <mode>` is a per-run override and forces sandbox
  enforcement for that run.
- In bypass mode, Codex is launched with
  `--dangerously-bypass-approvals-and-sandbox`.
- Bypass affects Codex internal sandboxing only; external host/container
  restrictions (for example blocked socket bind) still apply.
- `claude` and `gemini` provider values are accepted for forward compatibility,
  but execution remains codex-only until those providers are implemented.

## Runtime Roots (Optional But Recommended)

By default, FermiLink uses `~/.fermilink`. If you want local project-scoped
paths instead:

```bash
export FERMILINK_HOME=./.fermilink
export SCIPKG_ROOT=./.fermilink/scientific_packages
export WORKSPACES_ROOT=./.fermilink/workspaces
export FERMILINK_RUNTIME_ROOT=./.fermilink/runtime
export CHAINLIT_APP_ROOT=./.fermilink
```

## Install Scientific Packages

Install from curated channel (`tel-research-group` by default):

```bash
fermilink install maxwelllink --activate
fermilink install meep
```

Install from custom zip URL:

```bash
fermilink install mypkg \
  --zip-url https://github.com/<org>/<repo>/archive/refs/heads/main.zip \
  --activate
```

Install from local package directory:

```bash
fermilink install mypkg --local-path /absolute/path/to/package --activate
```

## Compile a Local Codebase Into a FermiLink Package

`compile` is the fastest way to convert an existing scientific repository into
a FermiLink package with an enriched `skills/` directory.

```bash
# compile current directory as package "pyscf"
fermilink compile pyscf .

# compile another path and activate immediately
fermilink compile mypkg /absolute/path/to/project --activate
```

Compile workflow:

1. Validate package id does not already exist in `registry.json`.
2. Copy `sci-skills-generator/` into project root.
3. Run `codex exec` pass 1 (generate `skills/`).
4. Run `codex exec` pass 2 (audit/refine `skills/`).
5. Delete `sci-skills-generator/`.
6. Run `codex exec` pass 3 (consistency + enrichment check).
7. Install local project to `SCIPKG_ROOT/packages/<package_id>`.
8. Sync `router_rules.json` (unless `--no-router-sync`).

Notes:

- If the package id already exists, compile stops immediately.
- Known benign Codex rollout-path noise is filtered from compile output.
- Compile inherits provider from `fermilink agent` policy.
- Compile sandbox behavior remains controlled by `FERMILINK_COMPILE_SANDBOX`
  (default `workspace-write`) for safety.
- Use `--json` to print full structured results.

## Start, Check, Stop

```bash
fermilink start
fermilink status
fermilink stop
```

## Exec One Prompt From CLI (Web-Mirror Mode)

Run one-shot Codex execution in the current repository while reusing the same
package-routing and overlay logic as the web app:

```bash
fermilink exec "simulate weakly excited cavity QED dynamics and plot results"
```

Behavior:

1. Ensure current directory is a git repo (prompt for `git init` if missing).
2. Sync `AGENTS.md` template into current directory.
3. Select package via keyword router + second-guess preflight.
4. Overlay selected package entries as symlinks into current directory.
5. Run `codex exec` with your prompt.

Useful flags:

- `--package <id>`: pin package id and bypass auto routing.
- `--sandbox <mode>`: per-run sandbox override; enforces sandbox for that run.
- `--init-git`: non-interactively initialize git repo if missing.
- `--no-init-git`: fail immediately if git repo is missing.

## Manual Startup (Without Service Manager)

You can also run services directly:

```bash
uvicorn fermilink.runner.app:app --host 0.0.0.0 --port 8000
RUNNER_URL=http://127.0.0.1:8000 chainlit run src/fermilink/web/app.py --host 0.0.0.0 --port 7860
```

## Smoke Test

In chat UI:

1. Run `/package list` and verify installed packages appear.
2. Send one package-specific prompt (for example, FDTD for Meep).
3. Confirm workspace creation under `WORKSPACES_ROOT/<session_id>/repo`.
4. Confirm overlay manifest at `WORKSPACES_ROOT/<session_id>/.package_manifest.json`.

## Common Issues

- `codex` not found:
  install Codex CLI or set `CODEX_BIN`.
- Runner returns auth errors:
  run `codex login` or set a real `CODEX_API_KEY`/`OPENAI_API_KEY`.
- Web cannot reach runner:
  set `RUNNER_URL` correctly (for local default use `http://127.0.0.1:8000`).
