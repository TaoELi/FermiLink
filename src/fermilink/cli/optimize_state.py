from __future__ import annotations

import json
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any


OPTIMIZE_DIRNAME = ".fermilink-optimize"
STATE_FILENAME = "state.json"
RESULTS_FILENAME = "results.tsv"
MEMORY_FILENAME = "memory.md"
WORKER_MEMORY_FILENAME = "worker_memory.md"
WORKER_BENCHMARK_FILENAME = "benchmark.worker.yaml"
PROGRAM_FILENAME = "program.md"
RUNS_DIRNAME = "runs"
AUTOGEN_DIRNAME = "autogen"
RUN_LOCK_FILENAME = "run.lock.json"
QUICK_MANIFEST_FILENAME = "quick_mode.json"
QUICK_BENCHMARK_FILENAME = "benchmark.yaml"
QUICK_RUNNER_FILENAME = "benchmark_runner.py"
QUICK_SUBMIT_FILENAME = "submit_poll_launcher.py"
QUICK_SETUP_FILENAME = "setup_env.sh"
QUICK_RUN_SCRIPT_FILENAME = "run_optimize.sh"
GOAL_MANIFEST_FILENAME = "goal_mode.json"
GOAL_ANALYSIS_FILENAME = "goal_analysis.json"
GOAL_BENCHMARK_FILENAME = "benchmark.yaml"
GOAL_RUNNER_FILENAME = "benchmark_runner.py"
GOAL_SUBMIT_FILENAME = "submit_poll_launcher.py"
GOAL_SETUP_FILENAME = "setup_env.sh"
GOAL_RUN_SCRIPT_FILENAME = "run_optimize.sh"

RESULTS_HEADER = "iteration\tcommit\tstatus\tprimary_metric_name\tprimary_metric_value\tdescription\n"


def _sanitize_cell(value: object) -> str:
    return " ".join(str(value or "").replace("\t", " ").split()).strip()


def utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def optimize_root(project_root: Path) -> Path:
    return project_root / OPTIMIZE_DIRNAME


def state_path(project_root: Path) -> Path:
    return optimize_root(project_root) / STATE_FILENAME


def results_path(project_root: Path) -> Path:
    return optimize_root(project_root) / RESULTS_FILENAME


def memory_path(project_root: Path) -> Path:
    return optimize_root(project_root) / MEMORY_FILENAME


def worker_memory_path(project_root: Path) -> Path:
    return optimize_root(project_root) / WORKER_MEMORY_FILENAME


def worker_benchmark_path(project_root: Path) -> Path:
    return optimize_root(project_root) / WORKER_BENCHMARK_FILENAME


def runs_root(project_root: Path) -> Path:
    return optimize_root(project_root) / RUNS_DIRNAME


def autogen_root(project_root: Path) -> Path:
    return optimize_root(project_root) / AUTOGEN_DIRNAME


def run_lock_path(project_root: Path) -> Path:
    return optimize_root(project_root) / RUN_LOCK_FILENAME


def quick_manifest_path(project_root: Path) -> Path:
    return autogen_root(project_root) / QUICK_MANIFEST_FILENAME


def quick_benchmark_path(project_root: Path) -> Path:
    return autogen_root(project_root) / QUICK_BENCHMARK_FILENAME


def quick_runner_path(project_root: Path) -> Path:
    return autogen_root(project_root) / QUICK_RUNNER_FILENAME


def quick_submit_launcher_path(project_root: Path) -> Path:
    return autogen_root(project_root) / QUICK_SUBMIT_FILENAME


def quick_setup_path(project_root: Path) -> Path:
    return autogen_root(project_root) / QUICK_SETUP_FILENAME


def quick_run_script_path(project_root: Path) -> Path:
    return autogen_root(project_root) / QUICK_RUN_SCRIPT_FILENAME


def goal_manifest_path(project_root: Path) -> Path:
    return autogen_root(project_root) / GOAL_MANIFEST_FILENAME


def goal_analysis_path(project_root: Path) -> Path:
    return autogen_root(project_root) / GOAL_ANALYSIS_FILENAME


def goal_benchmark_path(project_root: Path) -> Path:
    return autogen_root(project_root) / GOAL_BENCHMARK_FILENAME


def goal_runner_path(project_root: Path) -> Path:
    return autogen_root(project_root) / GOAL_RUNNER_FILENAME


def goal_submit_launcher_path(project_root: Path) -> Path:
    return autogen_root(project_root) / GOAL_SUBMIT_FILENAME


def goal_setup_path(project_root: Path) -> Path:
    return autogen_root(project_root) / GOAL_SETUP_FILENAME


def goal_run_script_path(project_root: Path) -> Path:
    return autogen_root(project_root) / GOAL_RUN_SCRIPT_FILENAME


def default_program_path(project_root: Path) -> Path:
    return optimize_root(project_root) / PROGRAM_FILENAME


def ensure_optimize_root(project_root: Path) -> Path:
    root = optimize_root(project_root)
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


def ensure_program_file(path: Path, *, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def ensure_results_file(path: Path) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RESULTS_HEADER, encoding="utf-8")
    return True


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_run_lock(path: Path, payload: dict[str, Any]) -> None:
    write_json_file(path, payload)


def load_run_lock(path: Path) -> dict[str, Any] | None:
    return load_json_file(path)


def clear_run_lock(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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


def ensure_memory_file(
    path: Path,
    *,
    package_id: str,
    benchmark_id: str,
    benchmark_rel: str,
    optimize_branch: str,
) -> bool:
    if path.exists():
        return False
    started = utc_now_z()
    path.parent.mkdir(parents=True, exist_ok=True)
    initial = (
        "# FermiLink Optimize Memory\n"
        "\n"
        f"- package_id: {package_id}\n"
        f"- benchmark_id: {benchmark_id}\n"
        f"- benchmark_path: {benchmark_rel}\n"
        f"- optimize_branch: {optimize_branch}\n"
        f"- started_at_utc: {started}\n"
        f"- last_updated_utc: {started}\n"
        "\n"
        "## Short-Term Memory (Operational)\n"
        "### Current objective\n"
        "- Improve benchmark performance while preserving correctness.\n"
        "### Latest accepted commit\n"
        "- commit: pending\n"
        "- primary_metric: pending\n"
        "### Progress log\n"
        "- Campaign initialized.\n"
        "\n"
        "## Long-Term Memory (Persistent)\n"
        "### Benchmark contract\n"
        f"- Benchmark file: `{benchmark_rel}`\n"
        "### Accepted experiments\n"
        "- none yet\n"
        "### Rejected patterns\n"
        "- none yet\n"
        "### Open hypotheses\n"
        "- Review `skills/` and recent results before proposing the next change.\n"
    )
    path.write_text(initial, encoding="utf-8")
    return True


def reset_worker_memory_file(
    path: Path,
    *,
    package_id: str,
    benchmark_id: str,
    benchmark_rel: str,
    program_rel: str,
    controller_memory_rel: str,
    results_rel: str,
    worker_iteration: int,
) -> None:
    now = utc_now_z()
    path.parent.mkdir(parents=True, exist_ok=True)
    initial = (
        "# FermiLink Optimize Worker Memory\n"
        "\n"
        f"- package_id: {package_id}\n"
        f"- benchmark_id: {benchmark_id}\n"
        f"- benchmark_path: {benchmark_rel}\n"
        f"- program_path: {program_rel}\n"
        f"- controller_memory_path: {controller_memory_rel}\n"
        f"- results_path: {results_rel}\n"
        f"- worker_iteration: {worker_iteration}\n"
        f"- reset_at_utc: {now}\n"
        "\n"
        "This file is reset at the start of each outer optimize iteration and "
        "archived under `.fermilink-optimize/runs/iter_XXXX/worker_memory.md`.\n"
        "\n"
        "## Short-Term Memory (Operational)\n"
        "### Current objective\n"
        "- Prepare exactly one candidate that is ready for authoritative benchmark evaluation.\n"
        "### Plan\n"
        "- Read benchmark/program/controller memory/results/skills.\n"
        "- Inspect the current implementation and pick one optimization hypothesis.\n"
        "- Apply focused edits only within benchmark-approved paths.\n"
        "- Run quick local checks or launch/poll long-running worker jobs if needed.\n"
        "- Update this memory with progress and finish only when the candidate is benchmark-ready.\n"
        "### Progress log\n"
        "- Worker iteration initialized.\n"
        "\n"
        "## Tactical Notes\n"
        "### Job tracking\n"
        "- none yet\n"
        "### Candidate summary\n"
        "- pending\n"
        "### Debug notes\n"
        "- none yet\n"
    )
    path.write_text(initial, encoding="utf-8")


def archive_worker_memory(source_path: Path, run_dir: Path) -> Path | None:
    if not source_path.is_file():
        return None
    run_dir.mkdir(parents=True, exist_ok=True)
    target_path = run_dir / WORKER_MEMORY_FILENAME
    target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    return target_path


def load_state(path: Path) -> dict[str, Any] | None:
    return load_json_file(path)


def write_state(path: Path, payload: dict[str, Any]) -> None:
    write_json_file(path, payload)


def append_result(
    path: Path,
    *,
    iteration: int,
    commit: str,
    status: str,
    primary_metric_name: str,
    primary_metric_value: float | int | str,
    description: str,
) -> None:
    rendered_value = (
        f"{float(primary_metric_value):.12g}"
        if isinstance(primary_metric_value, (int, float))
        else str(primary_metric_value)
    )
    row = (
        f"{int(iteration)}\t{commit}\t{status}\t{primary_metric_name}\t"
        f"{rendered_value}\t{_sanitize_cell(description)}\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(row)


def recent_results_text(path: Path, *, limit: int = 8) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if not lines:
        return ""
    return "\n".join(lines[-limit:])


def _find_section_bounds(lines: list[str], heading: str) -> tuple[int, int] | None:
    try:
        start = lines.index(heading)
    except ValueError:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("## ") or line.startswith("### "):
            end = index
            break
    return start, end


def _set_section_lines(content: str, heading: str, new_lines: list[str]) -> str:
    lines = content.splitlines()
    bounds = _find_section_bounds(lines, heading)
    if bounds is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(heading)
        lines.extend(new_lines)
        return "\n".join(lines) + "\n"
    start, end = bounds
    updated = lines[: start + 1] + new_lines + lines[end:]
    return "\n".join(updated) + "\n"


def _append_section_line(content: str, heading: str, entry: str) -> str:
    lines = content.splitlines()
    bounds = _find_section_bounds(lines, heading)
    if bounds is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(heading)
        lines.append(entry)
        return "\n".join(lines) + "\n"
    start, end = bounds
    body = lines[start + 1 : end]
    cleaned = [
        line
        for line in body
        if line.strip() not in {"- none yet", "- Campaign initialized."}
    ]
    cleaned.append(entry)
    updated = lines[: start + 1] + cleaned + lines[end:]
    return "\n".join(updated) + "\n"


def _replace_metadata_line(content: str, prefix: str, value: str) -> str:
    lines = content.splitlines()
    target = f"{prefix}{value}"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = target
            return "\n".join(lines) + "\n"
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(target)
    return "\n".join(lines) + "\n"


def record_campaign_event(
    path: Path,
    *,
    commit: str,
    primary_metric_name: str,
    primary_metric_value: float | int | str,
    status: str,
    description: str,
) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    now = utc_now_z()
    metric_text = (
        f"{float(primary_metric_value):.12g}"
        if isinstance(primary_metric_value, (int, float))
        else str(primary_metric_value)
    )
    if status in {"baseline", "accepted"}:
        latest_lines = [
            f"- commit: {commit}",
            f"- primary_metric: {primary_metric_name}={metric_text}",
            f"- status: {status}",
        ]
        content = _set_section_lines(
            content, "### Latest accepted commit", latest_lines
        )
    content = _append_section_line(
        content,
        "### Progress log",
        (
            f"- [{now}] {status}: {_sanitize_cell(description)} "
            f"({commit}, {primary_metric_name}={metric_text})"
        ),
    )
    target_heading = (
        "### Accepted experiments"
        if status in {"baseline", "accepted"}
        else "### Rejected patterns"
    )
    content = _append_section_line(
        content,
        target_heading,
        (
            f"- [{now}] {_sanitize_cell(description)} "
            f"({commit}, {primary_metric_name}={metric_text})"
        ),
    )
    content = _replace_metadata_line(content, "- last_updated_utc: ", now)
    path.write_text(content, encoding="utf-8")
