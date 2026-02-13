# Repository Map

This map covers maintained source files and docs for the current layout.

## Root

| Path | Purpose |
| --- | --- |
| `README.md` | Project overview and quick-start flow. |
| `pyproject.toml` | Package metadata, dependencies, and CLI entrypoint (`fermilink`). |
| `.gitignore` | Excludes generated runtime roots, caches, and build artifacts. |

## Source Package (`src/fermilink`)

| Path | Purpose |
| --- | --- |
| `src/fermilink/__init__.py` | Package version export. |
| `src/fermilink/agent_runtime.py` | Persisted/global agent runtime policy model (provider + sandbox policy/mode). |
| `src/fermilink/cli.py` | Unified CLI for package lifecycle, `agent` runtime policy, local `exec`/`chat`, `compile`, and service start/stop/status/restart. |
| `src/fermilink/config.py` | Shared root/path resolution (`FERMILINK_HOME`, `SCIPKG_ROOT`, etc.). |
| `src/fermilink/curated_channels.py` | Curated package-channel catalog (`tel-research-group`) and resolution helpers. |
| `src/fermilink/package_registry.py` | Package registry CRUD, zip/local install, router sync hooks, overlay manifest helpers. |
| `src/fermilink/providers.py` | Provider CLI abstraction and command assembly (codex implemented, others stubbed). |
| `src/fermilink/router_rules.py` | Auto-generation/synchronization of `router_rules.json` from installed packages. |
| `src/fermilink/services.py` | Service specs and process lifecycle helpers used by CLI (`runner`, `web`). |

## Runner Backend (`src/fermilink/runner`)

| Path | Purpose |
| --- | --- |
| `src/fermilink/runner/app.py` | FastAPI runner service: `/run`, admission control, workspace provisioning, package overlay, Codex subprocess stream. |
| `src/fermilink/runner/admission.py` | In-memory admission queue with global/per-user active+pending controls. |
| `src/fermilink/runner/scientific_packages.py` | Runner-side package registry access, legacy bootstrap, session package resolution, and overlay mechanics. |
| `src/fermilink/runner/__init__.py` | Runner package marker. |

## Web Frontend (`src/fermilink/web`)

| Path | Purpose |
| --- | --- |
| `src/fermilink/web/app.py` | Chainlit app: auth, quotas, package routing, second-guess preflight, runner streaming, artifact attachment, transparency. |
| `src/fermilink/web/__init__.py` | Web package marker. |

## Packaged Runtime Assets

| Path | Purpose |
| --- | --- |
| `src/fermilink/software/AGENTS.md` | Baseline agent instructions copied into each workspace repo root. |
| `src/fermilink/public/` | Bundled Chainlit static assets (`custom.css`, `custom.js`, logos, landing markdown). Seeded into `$CHAINLIT_APP_ROOT/public/` by web UI only. |

## Documentation (`docs`)

| Path | Purpose |
| --- | --- |
| `docs/README.md` | Documentation index. |
| `docs/install.md` | Installation and runtime startup instructions. |
| `docs/scientific-packages.md` | Scientific package lifecycle and overlay/dependency configuration. |
| `docs/architecture.md` | End-to-end request and overlay architecture. |
| `docs/configuration.md` | Environment variables and operational configuration. |
| `docs/test-plan.md` | Regression and validation test plan for policy/provider/runtime behavior. |
| `docs/repository-map.md` | This file. |
| `docs/privacy.md` | Privacy policy for deployed service operators. |
| `docs/terms.md` | Terms of use for deployed service operators. |

## Tests (`tests`)

| Path | Purpose |
| --- | --- |
| `tests/test_agent_runtime.py` | Runtime policy persistence and precedence checks. |
| `tests/test_cli.py` | CLI package-management behavior. |
| `tests/test_cli_agent.py` | `fermilink agent` command behavior and persistence. |
| `tests/test_cli_chat.py` | Interactive `fermilink chat` behavior and package-routing history semantics. |
| `tests/test_cli_services.py` | CLI start/restart failure and rollback behavior. |
| `tests/test_cli_services_live.py` | Live process start/restart/stop lifecycle behavior. |
| `tests/test_package_registry.py` | Registry operations and dependency validation. |
| `tests/test_package_registry_zip.py` | Zip install safety and size checks. |
| `tests/test_providers.py` | Provider command builder behavior and non-implemented provider guards. |
| `tests/test_router_rules.py` | Router rule synchronization from installed packages. |
| `tests/test_runner_admission.py` | Admission queue scheduling and limits. |
| `tests/test_runner_overlay_manifest.py` | Overlay manifest update and stale-link cleanup behavior. |
| `tests/test_runner_policy.py` | Runner-side request policy resolution semantics. |
| `tests/test_runner_run_cleanup.py` | Admission-slot cleanup on cancellation paths. |
| `tests/test_web_app_router.py` | Router parsing/scoring and branding defaults in web layer. |
| `tests/test_web_auth_signup.py` | Signup and password auth behavior. |
| `tests/test_web_runner_integration.py` | End-to-end web-to-runner SSE integration checks. |
| `tests/test_services.py` | Service command/env defaults and status behavior. |
