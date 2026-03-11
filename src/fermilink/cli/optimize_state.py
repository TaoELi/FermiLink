from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OPTIMIZE_DIRNAME = ".fermilink-optimize"
STATE_FILENAME = "state.json"
RESULTS_FILENAME = "results.tsv"
MEMORY_FILENAME = "memory.md"
PROGRAM_FILENAME = "program.md"
RUNS_DIRNAME = "runs"

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


def runs_root(project_root: Path) -> Path:
    return optimize_root(project_root) / RUNS_DIRNAME


def default_program_path(project_root: Path) -> Path:
    return optimize_root(project_root) / PROGRAM_FILENAME


def ensure_optimize_root(project_root: Path) -> Path:
    root = optimize_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    runs_root(project_root).mkdir(parents=True, exist_ok=True)
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


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temp_path.replace(path)


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
