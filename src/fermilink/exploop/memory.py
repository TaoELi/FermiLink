from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fermilink.exploop.prompts import EXPLOOP_MEMORY_DIRNAME, EXPLOOP_MEMORY_FILENAME


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def memory_path_for(repo_dir: Path) -> Path:
    return repo_dir / EXPLOOP_MEMORY_DIRNAME / EXPLOOP_MEMORY_FILENAME


def ensure_exploop_memory(
    *,
    repo_dir: Path,
    user_prompt: str,
    prompt_file: str | None,
) -> Path:
    """Create or reuse the persistent exploop memory file."""

    projects_dir = repo_dir / EXPLOOP_MEMORY_DIRNAME
    if projects_dir.exists() and not projects_dir.is_dir():
        raise RuntimeError(f"{projects_dir} exists but is not a directory.")
    projects_dir.mkdir(parents=True, exist_ok=True)

    memory_path = projects_dir / EXPLOOP_MEMORY_FILENAME
    if memory_path.exists():
        if memory_path.is_dir():
            raise RuntimeError(f"{memory_path} exists but is a directory.")
        return memory_path

    started_at = _utc_now_z()
    source_line = f"- prompt_source: {prompt_file}\n" if prompt_file else ""
    initial = (
        "# FermiLink Exploop Memory\n"
        "\n"
        "- schema_version: 1\n"
        f"- started_at_utc: {started_at}\n"
        f"- last_updated_utc: {started_at}\n"
        f"{source_line}"
        "\n"
        "## Original request\n"
        f"{user_prompt.strip()}\n"
        "\n"
        "## Short-Term Memory (Operational)\n"
        "\n"
        "### Plan\n"
        "- [ ] Read local measurement skills and confirm the next safe step.\n"
        "\n"
        "### Pending work\n"
        "- No pending work recorded yet.\n"
        "\n"
        "### Progress log\n"
        "- initialized\n"
        "\n"
        "## Long-Term Memory (Persistent)\n"
        "\n"
        "### Experiment history\n"
        "- (run_id | objective | status | artifacts | notes)\n"
        "\n"
        "### Measurement data inventory\n"
        "- (path | size_bytes | modified_utc | notes)\n"
        "- For many machine-generated files with similar names or patterns, "
        "combine them into one grouped entry by pattern/count/location instead "
        "of listing every file, so this memory file stays compact.\n"
        "\n"
        "### Instrument and sample state\n"
        "- (timestamp | state | evidence | notes)\n"
        "\n"
        "### Safety notes\n"
        "- (constraint | source | status | notes)\n"
        "\n"
        "### Suggested skills updates\n"
        "- (issue_pattern | proposed_skill_update | evidence | status)\n"
    )
    memory_path.write_text(initial, encoding="utf-8")
    return memory_path


def append_to_memory_section(
    memory_path: Path,
    *,
    heading: str,
    lines: list[str],
) -> None:
    """Append lines to a markdown section, creating it when absent."""

    if not lines:
        return
    normalized_heading = heading.strip()
    if not normalized_heading.startswith("#"):
        raise ValueError("heading must be a markdown heading")

    content = memory_path.read_text(encoding="utf-8")
    block = "\n".join(lines).rstrip() + "\n"
    marker = normalized_heading + "\n"
    index = content.find(marker)
    if index < 0:
        updated = content.rstrip() + f"\n\n{marker}{block}"
    else:
        insert_at = index + len(marker)
        next_heading = len(content)
        for prefix in ("\n### ", "\n## "):
            candidate = content.find(prefix, insert_at)
            if candidate >= 0:
                next_heading = min(next_heading, candidate)
        before = content[:next_heading].rstrip()
        after = content[next_heading:]
        updated = before + "\n" + block + after

    updated = _replace_last_updated(updated, _utc_now_z())
    memory_path.write_text(updated, encoding="utf-8")


def _replace_last_updated(content: str, timestamp: str) -> str:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("- last_updated_utc:"):
            lines[index] = f"- last_updated_utc: {timestamp}"
            return "\n".join(lines).rstrip() + "\n"
    return content.rstrip() + f"\n- last_updated_utc: {timestamp}\n"
