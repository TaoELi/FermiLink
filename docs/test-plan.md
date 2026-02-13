# Test Plan

This plan validates dynamic sandbox policy control, provider policy plumbing,
and no-regression behavior across CLI, runner, and web paths.

## 1. Automated Unit and Integration Tests

Run focused policy/provider tests:

```bash
pytest -q \
  tests/test_agent_runtime.py \
  tests/test_providers.py \
  tests/test_cli_agent.py \
  tests/test_runner_policy.py \
  tests/test_services.py \
  tests/test_cli_exec.py
```

Run full regression:

```bash
pytest -q
```

## 2. CLI Policy Controls

1. Verify defaults:
   `fermilink agent --json`
2. Enable bypass:
   `fermilink agent --bypass-sandbox --json`
3. Re-enable sandbox:
   `fermilink agent --sandbox --json`
4. Verify provider setting:
   `fermilink agent codex --json`

Expected:

- Policy values persist between commands.
- Sandbox toggles between `enforce` and `bypass`.
- Sandbox mode remains stable when only toggling policy.

## 3. Exec Behavior

1. Set bypass globally:
   `fermilink agent --bypass-sandbox`
2. Run one prompt:
   `fermilink exec "test prompt"`
3. Force per-run sandbox:
   `fermilink exec --sandbox read-only "test prompt"`

Expected:

- Console shows `[agent] provider: <provider>, sandbox: bypass` for bypass policy.
- Effective codex command includes `--dangerously-bypass-approvals-and-sandbox`.
- External host restrictions may still block actions requiring capabilities not
  granted to the process (for example local socket bind for MPI launchers).
- `--sandbox` forces `enforce(read-only)` for that run.
- Overlay symlink cleanup still runs after completion.

## 4. Runner/Web Policy Propagation

1. Set policy:
   `fermilink agent --sandbox`
2. Start services:
   `fermilink start`
3. Trigger a chat turn in web UI.
4. Inspect runner SSE `meta` event payload.

Expected `meta.agent` fields:

- `provider`
- `sandbox_policy`
- `sandbox_mode`

And behavior matches configured policy (sandbox included only when enforced).

## 5. Negative and Forward-Compat Checks

1. Set forward provider:
   `fermilink agent gemini`
2. Run `fermilink exec "test"`
3. Submit a web turn.

Expected:

- Current build returns a clear "provider not implemented yet" error path.
- System remains stable and recoverable by switching back:
  `fermilink agent codex`.
