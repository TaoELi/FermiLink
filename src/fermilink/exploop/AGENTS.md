# FermiLink Exploop Guide

You are running in FermiLink experimental loop mode for real laboratory
measurements.

## Operating rules

- Read `projects/memory.md` before acting.
- Read local measurement skills under `skills/`, especially `skills/*/SKILL.md`,
  before choosing scripts or parameters.
- Treat instrument state as persistent and safety-critical.
- Prefer small, reversible, well-documented measurements before broad sweeps.
- Do not modify hardcoded hardware safety limits unless the user explicitly
  asks for that change.
- Save measurement outputs under `projects/YYYY-MM-DD-short-name/`.
- Record commands, parameters, PIDs, output paths, and pending work in
  `projects/memory.md`.
- Proceed each step sequentially, and do post-processing only after the corresponding
  measurement is finished.
- Do not directly modify skills folder.

## Windows measurement launch

When starting a long-running measurement on native Windows, use a detached
process pattern and emit the final measurement child PID on its own line:

```xml
<pid_number>PID</pid_number>
```

Use one PID tag per process. Do not emit PID tags for finished processes.
Emit the XML tag exactly; a raw numeric PID is not enough for FermiLink to
poll the measurement.

Before emitting a PID tag:

- Ensure the PID belongs to the final detached measurement process, not a
  transient PowerShell, `cmd.exe`, Python launcher, or other wrapper process.
- Redirect stdin, stdout, and stderr so the measurement does not keep the
  Codex/tool process attached to its console or pipes.
- Do not call `Wait-Process`, `communicate()`, read child pipes, or otherwise
  wait for the measurement before returning the PID tag.

PowerShell pattern:

```powershell
$stdin = "projects\run.stdin"
if (-not (Test-Path $stdin)) { New-Item -ItemType File -Path $stdin -Force | Out-Null }
$p = Start-Process `
  -FilePath python `
  -ArgumentList @("script.py", "--mission", "Scan_TG") `
  -WorkingDirectory $PWD `
  -PassThru `
  -RedirectStandardInput $stdin `
  -RedirectStandardOutput "projects\run.out" `
  -RedirectStandardError "projects\run.err"
Write-Output "<pid_number>$($p.Id)</pid_number>"
```

Python pattern:

```python
import subprocess

stdout = open("projects/run.out", "a", encoding="utf-8")
stderr = open("projects/run.err", "a", encoding="utf-8")
creationflags = 0
if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
    creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
if hasattr(subprocess, "DETACHED_PROCESS"):
    creationflags |= subprocess.DETACHED_PROCESS
proc = subprocess.Popen(
    ["python", "script.py", "--mission", "Scan_TG"],
    stdin=subprocess.DEVNULL,
    stdout=stdout,
    stderr=stderr,
    creationflags=creationflags,
)
print(f"<pid_number>{proc.pid}</pid_number>")
```

When all requested measurements and analysis are complete, output exactly:

```xml
<promise>DONE</promise>
```
