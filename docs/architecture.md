# Architecture

## System Snapshot

FermiLink has two runtime services:

- Runner API (`src/fermilink/runner/app.py`): admission control, workspace
  provisioning, package overlay, Codex execution, SSE streaming.
- Chainlit Web (`src/fermilink/web/app.py`): auth/quota, package routing,
  second-guess routing preflight, runner stream rendering.

Shared infrastructure:

- Package registry and overlay logic:
  `src/fermilink/runner/scientific_packages.py` and
  `src/fermilink/package_registry.py`.
- Router rule synchronization: `src/fermilink/router_rules.py`.
- Unified CLI and service management: `src/fermilink/cli.py`,
  `src/fermilink/services.py`.
- Base workspace policy copied into repos: `src/fermilink/software/AGENTS.md`.

## End-to-End Request Flow

1. User sends message in Chainlit.
2. Web handles `/package` commands or resolves package automatically.
3. Optional second-guess preflight runs a policy-aware runner call with strict JSON output.
4. Web calls runner `POST /run` with `session_id`, `user_id`, prompt, provider, optional sandbox, optional `package_id`.
5. Runner admission queue enforces global and per-user concurrency limits.
6. Runner provisions `WORKSPACES_ROOT/<session_id>/repo`, ensures `AGENTS.md`, ensures git repo.
7. Runner resolves package and overlays selected package entries into `repo/`.
8. Runner resolves effective agent policy and executes provider CLI (`codex` today) via unified command builder.
9. Web renders assistant stream, command steps, artifacts, and optional transparency report.
10. Session metadata/history is persisted by Chainlit.

## Workspace and Overlay Model

Per session:

- Workspace root: `WORKSPACES_ROOT/<session_id>/`
- Working repo: `WORKSPACES_ROOT/<session_id>/repo/`
- Overlay manifest: `WORKSPACES_ROOT/<session_id>/.package_manifest.json`
- Output convention: `repo/outputs/`

Overlay behavior:

- Exportable top-level package entries are symlinked when possible, copied as fallback.
- Hidden/system/cache entries are skipped.
- `AGENTS.md` from package content is reserved and never exported into repo root.
- Dependencies are linked under `repo/external_packages/`.
- Overlay summary (collisions, missing entries, dependency link status) is written to manifest.

## Package Selection Layers

Web-layer routing (`src/fermilink/web/app.py`):

- Manual pin via `/package use ...`
- Keyword scoring against `router_rules.json` + built-in family hints
- Sticky switching policy
- Optional second-guess preflight (defaults to read-only when sandbox is enforced)

Runner-layer resolution (`src/fermilink/runner/scientific_packages.py`):

- Final package source of truth before overlay:
  requested package -> workspace manifest -> `SCIPKG_ACTIVE` -> registry active package

## Streaming Contract

Runner SSE events:

- `meta`: resolved session id and package overlay payload
- `codex`: raw Codex JSON stream records
- `log`: runner stderr/log lines
- `runner.exit`: terminal status and return code

Web consumes these events and builds:

- assistant text stream
- command/tool steps
- optional artifact attachments from referenced files
- optional transparency report with command/file/log summary

`meta` also includes resolved agent policy:

- `provider`
- `sandbox_policy`
- `sandbox_mode`

## Admission and Backpressure

Runner admission controller (`src/fermilink/runner/admission.py`) tracks:

- active runs (global + per user)
- pending queued runs
- bounded/unbounded queue policies

Operational endpoints:

- `GET /ops/concurrency` (JSON)
- `GET /ops/concurrency.prom` (Prometheus text)
- `GET /ops/admission` (per-user readiness snapshot)

When `RUNNER_METRICS_TOKEN` is set, these endpoints require
`X-Runner-Metrics-Token`.

## Agent Runtime Policy Plane

Runtime policy is shared across `fermilink start` services and `fermilink exec`:

- Persisted state lives in `FERMILINK_HOME/agent_runtime.json`.
- `fermilink agent` updates this state (`--sandbox`, `--bypass-sandbox`, provider).
- Service startup injects policy env into runner/web when env overrides are unset.
- Runner is final policy authority for each `/run` request.
- Request-level sandbox narrowing is allowed only under enforced sandbox policy
  (for example, `workspace-write` to `read-only`).
- Under codex provider, bypass policy executes with
  `--dangerously-bypass-approvals-and-sandbox`.
- Bypass policy cannot override host/container-level restrictions external to
  Codex itself.

Provider abstraction entry points:

- `src/fermilink/providers.py` for command assembly and binary resolution.
- `src/fermilink/agent_runtime.py` for policy persistence and resolution.
