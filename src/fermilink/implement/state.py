from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IMPLEMENT_DIRNAME = ".fermilink-implement"
STATE_FILENAME = "state.json"
RESULTS_FILENAME = "results.tsv"
MEMORY_FILENAME = "memory.md"
WORKER_MEMORY_FILENAME = "worker_memory.md"
PROGRAM_FILENAME = "program.md"
RUN_LOCK_FILENAME = "run.lock.json"
RUNS_DIRNAME = "runs"
AUTOGEN_DIRNAME = "autogen"
GOAL_COPY_FILENAME = "goal.md"
GOAL_ANALYSIS_FILENAME = "goal_analysis.json"
CONTRACT_FILENAME = "implementation_contract.yaml"
VALIDATION_RUNNER_FILENAME = "validation_runner.py"
PLAN_FILENAME = "implementation_plan.md"

RESULTS_HEADER = "iteration\tcommit\tstatus\tscore\tcomplete\tdescription\n"


def utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def implement_root(project_root: Path) -> Path:
    return project_root / IMPLEMENT_DIRNAME


def state_path(project_root: Path) -> Path:
    return implement_root(project_root) / STATE_FILENAME


def results_path(project_root: Path) -> Path:
    return implement_root(project_root) / RESULTS_FILENAME


def memory_path(project_root: Path) -> Path:
    return implement_root(project_root) / MEMORY_FILENAME


def worker_memory_path(project_root: Path) -> Path:
    return implement_root(project_root) / WORKER_MEMORY_FILENAME


def program_path(project_root: Path) -> Path:
    return implement_root(project_root) / PROGRAM_FILENAME


def run_lock_path(project_root: Path) -> Path:
    return implement_root(project_root) / RUN_LOCK_FILENAME


def clear_run_lock(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def runs_root(project_root: Path) -> Path:
    return implement_root(project_root) / RUNS_DIRNAME


def autogen_root(project_root: Path) -> Path:
    return implement_root(project_root) / AUTOGEN_DIRNAME


def goal_copy_path(project_root: Path) -> Path:
    return autogen_root(project_root) / GOAL_COPY_FILENAME


def goal_analysis_path(project_root: Path) -> Path:
    return autogen_root(project_root) / GOAL_ANALYSIS_FILENAME


def contract_path(project_root: Path) -> Path:
    return autogen_root(project_root) / CONTRACT_FILENAME


def validation_runner_path(project_root: Path) -> Path:
    return autogen_root(project_root) / VALIDATION_RUNNER_FILENAME


def plan_path(project_root: Path) -> Path:
    return autogen_root(project_root) / PLAN_FILENAME


def ensure_implement_root(project_root: Path) -> Path:
    root = implement_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    runs_root(project_root).mkdir(parents=True, exist_ok=True)
    return root


def ensure_autogen_root(project_root: Path) -> Path:
    root = autogen_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_state(path: Path, payload: dict[str, Any]) -> None:
    write_json_file(path, payload)


def load_state(path: Path) -> dict[str, Any] | None:
    return load_json_file(path)


def ensure_results_file(path: Path) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RESULTS_HEADER, encoding="utf-8")
    return True


def ensure_program_file(path: Path, *, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def ensure_memory_file(
    path: Path,
    *,
    package_id: str,
    goal_rel: str,
    contract_rel: str,
    branch_name: str,
) -> bool:
    if path.exists():
        return False
    started = utc_now_z()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "# FermiLink Implement Memory\n"
            "\n"
            f"- package_id: {package_id}\n"
            f"- goal_path: {goal_rel}\n"
            f"- contract_path: {contract_rel}\n"
            f"- implement_branch: {branch_name}\n"
            f"- started_at_utc: {started}\n"
            f"- last_updated_utc: {started}\n"
            "\n"
            "## Short-Term Memory (Operational)\n"
            "### Current objective\n"
            "- Implement the feature described by goal.md and the generated contract.\n"
            "### Latest accepted implementation\n"
            "- commit: pending\n"
            "- score: pending\n"
            "- complete: false\n"
            "### Progress log\n"
            "- Campaign initialized.\n"
            "\n"
            "## Long-Term Memory (Persistent)\n"
            "### Accepted implementation steps\n"
            "- none yet\n"
            "### Rejected patterns\n"
            "- none yet\n"
            "### Open hypotheses\n"
            "- Review goal, contract, validation, and prior results before each change.\n"
        ),
        encoding="utf-8",
    )
    return True


def reset_worker_memory_file(
    path: Path,
    *,
    package_id: str,
    goal_rel: str,
    contract_rel: str,
    controller_memory_rel: str,
    results_rel: str,
    worker_iteration: int,
) -> None:
    now = utc_now_z()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "# FermiLink Implement Worker Memory\n"
            "\n"
            f"- package_id: {package_id}\n"
            f"- goal_path: {goal_rel}\n"
            f"- contract_path: {contract_rel}\n"
            f"- controller_memory_path: {controller_memory_rel}\n"
            f"- results_path: {results_rel}\n"
            f"- worker_iteration: {worker_iteration}\n"
            f"- reset_at_utc: {now}\n"
            "\n"
            "This file is reset at the start of each outer implement iteration and "
            "archived under `.fermilink-implement/runs/iter_XXXX/worker_memory.md`.\n"
            "\n"
            "## Short-Term Memory (Operational)\n"
            "### Current objective\n"
            "- Prepare exactly one focused implementation step for controller validation.\n"
            "### Plan\n"
            "- Read goal, contract, controller memory, results, and skills.\n"
            "- Inspect current implementation state.\n"
            "- Apply focused edits only within editable scope.\n"
            "- Run cheap local checks when useful.\n"
            "- Finish only when this step is ready for validation.\n"
            "### Progress log\n"
            "- Worker iteration initialized.\n"
            "\n"
            "## Tactical Notes\n"
            "### Candidate summary\n"
            "- pending\n"
            "### Debug notes\n"
            "- none yet\n"
        ),
        encoding="utf-8",
    )


def archive_worker_memory(source_path: Path, run_dir: Path) -> Path | None:
    if not source_path.is_file():
        return None
    run_dir.mkdir(parents=True, exist_ok=True)
    target_path = run_dir / WORKER_MEMORY_FILENAME
    target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    return target_path


def append_result(
    path: Path,
    *,
    iteration: int,
    commit: str,
    status: str,
    score: float | int | str,
    complete: bool,
    description: str,
) -> None:
    rendered_score = (
        f"{float(score):.12g}" if isinstance(score, (float, int)) else str(score)
    )
    safe_description = " ".join(str(description or "").replace("\t", " ").split())
    row = (
        f"{int(iteration)}\t{commit}\t{status}\t{rendered_score}\t"
        f"{str(bool(complete)).lower()}\t{safe_description}\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(row)


def recent_results_text(path: Path, *, limit: int = 8) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-limit:]) if lines else ""


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def ensure_executable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & 0o111:
        return
    try:
        path.chmod(mode | 0o111)
    except OSError:
        return
