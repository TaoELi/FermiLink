from __future__ import annotations

import re
from importlib import resources


EXPLOOP_DONE_TOKEN = "<promise>DONE</promise>"
EXPLOOP_MEMORY_DIRNAME = "projects"
EXPLOOP_MEMORY_FILENAME = "memory.md"
EXPLOOP_STATE_DIRNAME = "fermilink-exploop"
EXPLOOP_LEGACY_STATE_DIRNAME = ".fermilink-exploop"
EXPLOOP_STATE_FILENAME = "state.json"

EXPLOOP_WAIT_TOKEN_RE = re.compile(
    r"^\s*<wait_seconds>\s*([0-9]+(?:\.[0-9]+)?)\s*</wait_seconds>\s*$",
    re.MULTILINE,
)
EXPLOOP_PID_TOKEN_RE = re.compile(
    r"^\s*<pid_number>\s*([0-9]+)\s*</pid_number>\s*$",
    re.MULTILINE,
)


EXPLOOP_PROMPT_PREFIX = (
    "You are running in **FermiLink exploop mode** for real experimental "
    "measurements at Windows OS system.\n"
    "\n"
    "Required workflow at the start of every turn:\n"
    "1) Read the local `AGENTS.md` guide in this workspace.\n"
    "2) Read `projects/memory.md`.\n"
    "3) Read relevant local measurement skills under `skills/`.\n"
    "4) Inspect any newly observed measurement artifacts listed in this prompt.\n"
    "5) Execute only the next measurement/analysis step.\n"
    "6) Update `projects/memory.md` with completed work, pending work, "
    "commands, PIDs, parameters, and artifact paths.\n"
    "\n"
    "Measurement outputs must be saved under `projects/YYYY-MM-DD-short-name/`.\n"
    "For native Windows long-running measurements, launch a detached process "
    "and emit `<pid_number>PID</pid_number>` on its own line. When all requested measurement work is complete, output "
    f"exactly `{EXPLOOP_DONE_TOKEN}` on its own line.\n"
)


def load_exploop_guide() -> str:
    """Load the packaged exploop AGENTS guide."""

    try:
        guide = resources.files("fermilink.exploop").joinpath("AGENTS.md")
        return guide.read_text(encoding="utf-8")
    except Exception:
        return ""


def extract_wait_seconds(assistant_text: str) -> float | None:
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        return None
    matches = EXPLOOP_WAIT_TOKEN_RE.findall(assistant_text)
    if not matches:
        return None
    try:
        value = float(matches[-1])
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def extract_pid_numbers(assistant_text: str) -> list[int]:
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        return []
    matches = EXPLOOP_PID_TOKEN_RE.findall(assistant_text)
    if not matches:
        return []
    seen: set[int] = set()
    pids: list[int] = []
    for raw in matches:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        pids.append(pid)
    return pids
