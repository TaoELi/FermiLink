# Install and Run

## Fast Path (Recommended)

```bash
pip install .
codex login
fermilink install maxwelllink --activate
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

## Start, Check, Stop

```bash
fermilink start
fermilink status
fermilink stop
```

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
