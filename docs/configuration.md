# Configuration

## Highest-Impact Settings First

Set these first for predictable local deployments:

```bash
export FERMILINK_HOME=./.fermilink
export FERMILINK_CHAINLIT_APP_ROOT=./.fermilink
export FERMILINK_RUNNER_URL=http://127.0.0.1:8000
export FERMILINK_CODEX_AUTH_MODE=login
```

Then run:

```bash
codex login
fermilink start
```

## Agent Runtime Policy (Global)

FermiLink resolves provider and sandbox behavior from three layers:

1. Environment overrides: `FERMILINK_AGENT_PROVIDER`,
   `FERMILINK_AGENT_SANDBOX_POLICY`, `FERMILINK_AGENT_SANDBOX_MODE`
2. Persisted config file: `FERMILINK_HOME/agent_runtime.json`
3. Built-in defaults: provider=`codex`, sandbox policy=`enforce`,
   sandbox mode=`workspace-write`

Update the persisted policy with:

```bash
fermilink agent --sandbox
fermilink agent --bypass-sandbox
fermilink agent codex
```

Notes:

- `fermilink exec --sandbox <mode>` is a per-run override that enforces sandbox
  for that run.
- `fermilink chat --sandbox <mode>` is a per-session override that enforces
  sandbox for that interactive session.
- Under codex provider, bypass mode maps to
  `--dangerously-bypass-approvals-and-sandbox`.
- Bypass mode does not remove OS/container-level restrictions outside Codex.
- `fermilink compile` also inherits provider from this policy, but keeps compile
  sandbox enforcement via `FERMILINK_COMPILE_SANDBOX`.
- `claude` and `gemini` policy values are accepted for future expansion; current
  execution support is codex-only.

## Path Resolution

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERMILINK_HOME` | `~/.fermilink` | Base root for default scientific packages, workspaces, and runtime state. |
| `FERMILINK_SCIPKG_ROOT` | `$FERMILINK_HOME/scientific_packages` | Scientific package root. |
| `FERMILINK_SCIENTIFIC_PACKAGES_ROOT` | alias of `FERMILINK_SCIPKG_ROOT` | Backward-compatible alias. |
| `FERMILINK_WORKSPACES_ROOT` | `$FERMILINK_HOME/workspaces` | Session workspace root. |
| `FERMILINK_RUNTIME_ROOT` | `$FERMILINK_HOME/runtime` | Service state/log root for `fermilink start/stop/status`. |
| `FERMILINK_CHAINLIT_APP_ROOT` | `$FERMILINK_HOME` | Chainlit app root for web UI state: `.chainlit/` sqlite DBs, `chainlit.md`, and `public/` assets. |

## CLI and Service Manager

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERMILINK_SCIPKG_MAX_ZIP_BYTES` | `838860800` | Max zip download size for `fermilink install`. |
| `FERMILINK_COMPILE_SANDBOX` | `workspace-write` | Sandbox mode used by `fermilink compile` passes (always enforced). |
| `FERMILINK_RUNNER_CMD` | `uvicorn fermilink.runner.app:app --host 0.0.0.0 --port 8000` | Override runner start command used by `fermilink start`. |
| `FERMILINK_WEB_CMD` | `chainlit run <packaged_web_app.py> --host 0.0.0.0 --port 7860` | Override web start command used by `fermilink start`. |
| `FERMILINK_RUNNER_URL` | `http://127.0.0.1:8000` (service manager env) | Runner URL injected into web when launched by `fermilink start`. |
| `FERMILINK_CODEX_HOME` | inherited/unset | Passed to both services only if explicitly set. |
| `FERMILINK_AGENT_PROVIDER` | from `agent_runtime.json` or `codex` | Force provider at runtime without editing persisted policy. |
| `FERMILINK_AGENT_SANDBOX_POLICY` | from `agent_runtime.json` or `enforce` | Force sandbox policy (`enforce` or `bypass`) at runtime. |
| `FERMILINK_AGENT_SANDBOX_MODE` | from `agent_runtime.json` or `workspace-write` | Sandbox mode used when policy is `enforce`. |

## Runner (`src/fermilink/runner/app.py`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERMILINK_CODEX_BIN` | `codex` | Codex executable path (used when provider is `codex`). |
| `FERMILINK_CLAUDE_BIN` | `claude` | Claude executable path (future provider support). |
| `FERMILINK_GEMINI_BIN` | `gemini` | Gemini executable path (future provider support). |
| `FERMILINK_RUNNER_MAX_RUNTIME_SECONDS` | `600` | Hard timeout per run. |
| `FERMILINK_RUNNER_GLOBAL_CONCURRENT_RUNS` | `200` | Max active runs in one runner process. |
| `FERMILINK_RUNNER_PER_USER_CONCURRENT_RUNS` | `10` | Max active runs per user key. |
| `FERMILINK_RUNNER_MAX_QUEUE_SIZE` | `2000` | Max queued run requests (`0` means unbounded). |
| `FERMILINK_RUNNER_MAX_PENDING_PER_USER` | `10` | Max queued requests per user (`0` means unbounded). |
| `FERMILINK_RUNNER_METRICS_TOKEN` | unset | Optional token guard for `/ops/*` endpoints. |
| `FERMILINK_SOFTWARE_ROOT` | `/opt/software` | Workspace template source directory (falls back to packaged `software/`). |
| `FERMILINK_CODEX_AUTH_MODE` | unset | If `login`/`oauth`/`keychain`/`stored`, runner strips API keys from subprocess env. |
| `FERMILINK_CODEX_API_KEY`, `FERMILINK_OPENAI_API_KEY` | unset | API key auth values (placeholder keys are stripped). |

Notes:

- Runner has a hard request-size limit of 10,000 characters per `user_prompt`.
- Runner normalizes `FERMILINK_CODEX_HOME` to a writable directory if provided.
- Runner resolves effective provider/sandbox policy per request from
  `FERMILINK_AGENT_*` (or persisted defaults), then applies request-level
  sandbox narrowing only when policy is `enforce`.

## Package Resolver (`src/fermilink/runner/scientific_packages.py`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERMILINK_SCIPKG_ROOT` | `$FERMILINK_HOME/scientific_packages` | Registry/package root. |
| `FERMILINK_SCIENTIFIC_PACKAGES_ROOT` | alias | Alias for `FERMILINK_SCIPKG_ROOT`. |
| `FERMILINK_SCIPKG_ACTIVE` | unset | Override active package id for selection fallback. |
| `FERMILINK_MAXWELLLINK_ROOT` | `<project>/maxwelllink` | Optional legacy local MaxwellLink auto-registration source. |
| `FERMILINK_LEGACY_MAXWELLLINK_PACKAGE_ID` | `maxwelllink-local` | Package id for legacy MaxwellLink bootstrap registration. |

## Web (`src/fermilink/web/app.py`)

### Connectivity and DB/Auth

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERMILINK_RUNNER_URL` | `http://runner:8000` | Runner base URL used by web app if not injected by service manager. |
| `FERMILINK_RUNNER_METRICS_TOKEN` | unset | Forwarded to runner `/ops/admission` as `X-Runner-Metrics-Token`. |
| `FERMILINK_DATABASE_URL` | sqlite under `$FERMILINK_CHAINLIT_APP_ROOT/.chainlit/chainlit.db` | Chainlit data DB URL. |
| `FERMILINK_AUTH_DB_URL` | sqlite under `$FERMILINK_CHAINLIT_APP_ROOT/.chainlit/auth.db` | Auth/quota DB URL. |
| `FERMILINK_CHAINLIT_AUTH_SECRET` | generated at startup if unset | Auth signing secret. Set explicitly for persistent sessions. |
| `FERMILINK_AUTH_AUTO_REGISTER` | `false` | Auto-create unknown users on login attempt. |
| `FERMILINK_AUTH_SIGNUP_ENABLED` | `true` | Enable signup endpoints/UI flow. |
| `FERMILINK_AUTH_MAX_USERS` | `0` | Max registered users (`0` means unlimited). |
| `FERMILINK_AUTH_MIN_PASSWORD_LEN` | `8` | Minimum password length. |

### Prompt Quotas

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERMILINK_PROMPT_DEFAULT_GROUP` | `average` | Default quota group. |
| `FERMILINK_PROMPT_LIMIT_AVERAGE` | `100` | Daily prompt cap for `average`. |
| `FERMILINK_PROMPT_LIMIT_STAR` | `100` | Daily prompt cap for `star`. |
| `FERMILINK_PROMPT_DAY_TZ` | `UTC` | Timezone for quota reset boundaries. |

### Prompt and History Bounds

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERMILINK_RUNNER_MAX_PROMPT_CHARS` | `10000` | Web-side prompt transcript cap sent to runner. |
| `FERMILINK_CHAINLIT_HISTORY_MAX_MESSAGES` | `40` | Max history entries kept in session memory. |
| `FERMILINK_CHAINLIT_HISTORY_MAX_CHARS` | `40000` | Max total history characters. |
| `FERMILINK_CHAINLIT_HISTORY_ENTRY_CHARS` | `4000` | Max per-entry characters before truncation. |
| `FERMILINK_CHAINLIT_STREAM_PARTIAL_PERSIST_SECONDS` | `1.0` | Periodic persistence interval for in-progress assistant messages (`0` disables). |
| `FERMILINK_CHAINLIT_ADMISSION_POLL_INTERVAL_SECONDS` | `0.5` | Poll interval before submitting run to runner. |
| `FERMILINK_CHAINLIT_ADMISSION_POLL_TIMEOUT_SECONDS` | `0.0` | Max poll wait (`0` means wait indefinitely). |

### Artifact Attachment

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERMILINK_CHAINLIT_ARTIFACT_PREFIXES` | `outputs,projects` | Allowed repo subtrees for auto-attachment. |
| `FERMILINK_CHAINLIT_MAX_ATTACHMENT_BYTES` | `52428800` | Per-file attachment size limit. |
| `FERMILINK_CHAINLIT_ZIP_MIN_COUNT` | `3` | Auto-zip when at least this many artifacts are detected. |
| `FERMILINK_CHAINLIT_LOCAL_STORAGE_SUBDIR` | `.chainlit/artifacts` | Artifact storage subdirectory under public root. |

### Web Static Assets (`public/`)

FermiLink bundles Chainlit UI assets (CSS/JS/logos/landing markdown) in
`src/fermilink/public/`. When you run the web UI (`fermilink start` or
`chainlit run src/fermilink/web/app.py ...`), the app ensures
`$FERMILINK_CHAINLIT_APP_ROOT/public/` exists and seeds any missing packaged assets
there. Chainlit then serves those files at `/public/...`.

Notes:

- This is intentionally a web-only side effect. `fermilink exec` and
  `fermilink chat` reuse the web routing logic but do not seed/copy `public/`
  into your current working repository.
- If you want web runtime folders to live somewhere other than your repo root,
  set `FERMILINK_CHAINLIT_APP_ROOT` to a dedicated directory (for example `./.fermilink`
  or `~/.fermilink`).
- In this repository layout, `public/` and `.chainlit/` are listed in
  `.gitignore` as runtime-generated folders.

### Package Router and Second Guess

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERMILINK_CHAINLIT_PACKAGE_ROUTER_ENABLED` | `true` | Enable keyword-based package routing. |
| `FERMILINK_CHAINLIT_PACKAGE_ROUTER_AUTO` | `true` | Default session auto-route state. |
| `FERMILINK_CHAINLIT_PACKAGE_ROUTER_STICKY` | `true` | Resist package switching unless margin is strong. |
| `FERMILINK_CHAINLIT_PACKAGE_ROUTER_MIN_SCORE` | `2` | Minimum top score for automatic selection. |
| `FERMILINK_CHAINLIT_PACKAGE_ROUTER_MIN_MARGIN` | `1` | Minimum lead over second candidate. |
| `FERMILINK_CHAINLIT_PACKAGE_ROUTER_SWITCH_MARGIN` | `2` | Margin required to switch from current package. |
| `FERMILINK_CHAINLIT_PACKAGE_ROUTER_RULES` | `router_rules.json` | Router rules file under `FERMILINK_SCIPKG_ROOT`. |
| `FERMILINK_PACKAGE_SECOND_GUESS_ENABLED` | `true` | Enable read-only Codex second-guess preflight. |
| `FERMILINK_CHAINLIT_PACKAGE_SECOND_GUESS_MIN_CONFIDENCE` | `0.75` | Confidence threshold for accepting switch. |
| `FERMILINK_CHAINLIT_PACKAGE_SECOND_GUESS_TIMEOUT_SECONDS` | `25.0` | Timeout for second-guess run. |

### Transparency and Logging

| Variable | Default | Purpose |
| --- | --- | --- |
| `FERMILINK_CHAINLIT_TRANSPARENCY_ENABLED` | `false` | Emit post-run transparency report. |
| `FERMILINK_CHAINLIT_TRANSPARENCY_MAX_ITEMS` | `200` | Max items per report section. |
| `FERMILINK_CHAINLIT_TRANSPARENCY_MAX_LOG_ENTRIES` | `50` | Max combined log/error items in report. |
| `FERMILINK_CHAINLIT_TRANSPARENCY_MAX_ENTRY_CHARS` | `500` | Max chars per report entry. |
| `FERMILINK_CHAINLIT_FORWARD_RUNNER_LOGS` | `false` | Echo selected runner log lines into chat stream. |

## Runner Metrics Endpoints

- `GET /ops/concurrency`
- `GET /ops/concurrency.prom`
- `GET /ops/admission`

Examples:

```bash
curl http://127.0.0.1:8000/ops/concurrency
curl http://127.0.0.1:8000/ops/concurrency.prom
```

When token-protected:

```bash
curl -H "X-Runner-Metrics-Token: $FERMILINK_RUNNER_METRICS_TOKEN" \
  http://127.0.0.1:8000/ops/concurrency
```

## Chainlit UI Config

Chainlit UI presentation is still controlled by `.chainlit/config.toml` and
public assets. Use environment variables for runtime behavior, limits, routing,
and backend connectivity.
