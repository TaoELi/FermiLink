from __future__ import annotations

import csv
import io
import os
import subprocess


def is_pid_alive(pid: int) -> bool:
    """Return whether a PID is alive on the current platform."""

    try:
        normalized = int(pid)
    except (TypeError, ValueError):
        return False
    if normalized <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_alive(normalized)
    return _posix_pid_alive(normalized)


def _posix_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_alive(pid: int, *, runner=subprocess.run) -> bool:
    try:
        result = runner(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    if getattr(result, "returncode", 1) != 0:
        return False
    stdout = str(getattr(result, "stdout", "") or "").strip()
    if not stdout or stdout.upper().startswith("INFO:"):
        return False
    try:
        rows = csv.reader(io.StringIO(stdout))
        for row in rows:
            if len(row) >= 2 and row[1].strip() == str(pid):
                return True
    except csv.Error:
        return False
    return False
