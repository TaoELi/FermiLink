from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _resolve_workspaces_root(
    *,
    resolve_default_workspaces_root: Callable[[], Path],
    cwd: Path,
) -> Path:
    """Resolve workspace root path used for artifact attachment."""

    try:
        return resolve_default_workspaces_root()
    except OSError:
        fallback = cwd / "workspaces"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _extract_candidate_paths(text: str) -> list[str]:
    """Extract likely file-path tokens from assistant output text."""

    if not text:
        return []
    candidates: set[str] = set()
    for raw in re.split(r"\s+", text):
        token = raw.strip("`'\".,;:()[]{}<>")
        if not token:
            continue
        if "://" in token:
            continue
        if "/" not in token:
            continue
        if token.endswith("/"):
            continue
        if "." not in Path(token).name:
            continue
        candidates.add(token)
    return sorted(candidates)


def _resolve_artifact_path(
    repo_root: Path,
    token: str,
    *,
    artifact_prefixes: tuple[str, ...],
) -> tuple[Path, Path] | None:
    """Resolve a candidate artifact token to a real file under repo root."""

    candidates = [token]
    if "/repo/" in token:
        candidates.append(token.split("/repo/", 1)[1])
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_absolute():
            path = repo_root / path
        path = path.resolve(strict=False)
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(repo_root)
        except ValueError:
            continue
        if artifact_prefixes:
            if not relative.parts or relative.parts[0] not in artifact_prefixes:
                continue
        return path, relative
    return None


def _element_for_path(
    path: Path,
    relative: Path,
    *,
    image_exts: set[str],
    cl_module: Any,
):
    """Create a Chainlit element object for a file path."""

    name = relative.as_posix()
    ext = path.suffix.lower()
    if ext in image_exts:
        return cl_module.Image(
            name=name, path=str(path), display="inline", size="large"
        )
    if ext == ".pdf":
        return cl_module.Pdf(name=name, path=str(path))
    return cl_module.File(name=name, path=str(path))


def _snapshot_repo(
    repo_root: Path,
    *,
    excluded_snapshot_dirs: set[str],
) -> dict[str, tuple[int, int]]:
    """Snapshot repository files for change detection."""

    snapshot: dict[str, tuple[int, int]] = {}
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in excluded_snapshot_dirs]
        for filename in files:
            path = Path(root) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            try:
                rel = path.relative_to(repo_root).as_posix()
            except ValueError:
                continue
            snapshot[rel] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _diff_snapshots(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> tuple[list[str], list[str]]:
    """Compute created and modified files between two snapshots."""

    created = sorted(path for path in after.keys() if path not in before)
    modified = sorted(
        path for path, meta in after.items() if path in before and before[path] != meta
    )
    return created, modified


def _truncate_items(items: list[str], max_items: int) -> list[str]:
    """Truncate a list to a maximum count with overflow marker."""

    if max_items <= 0:
        return []
    if len(items) <= max_items:
        return items
    return items[:max_items] + [f"... ({len(items) - max_items} more)"]


def _dedupe_preserve(items: list[str]) -> list[str]:
    """Remove duplicates while preserving original order."""

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _truncate_entry(text: str, *, transparency_max_entry_chars: int) -> str:
    """Truncate one transparency report entry by character budget."""

    if transparency_max_entry_chars <= 0:
        return text
    if len(text) <= transparency_max_entry_chars:
        return text
    overflow = len(text) - transparency_max_entry_chars
    return f"{text[:transparency_max_entry_chars]}... ({overflow} more chars)"


def _format_transparency_report(
    active_package: str | None,
    tool_calls: list[str],
    commands_run: list[str],
    created_files: list[str],
    modified_files: list[str],
    log_entries: list[str],
    error_entries: list[str],
    *,
    transparency_max_items: int,
    transparency_max_log_entries: int,
    transparency_max_entry_chars: int,
) -> str:
    """Build a structured transparency report for post-run disclosure."""

    lines: list[str] = ["**Transparency**"]
    lines.append(
        f"Active package: `{active_package}`"
        if active_package
        else "Active package: none"
    )

    lines.append("Tool calls:")
    for entry in _truncate_items(tool_calls, transparency_max_items) or ["none"]:
        lines.append(
            f"- {_truncate_entry(entry, transparency_max_entry_chars=transparency_max_entry_chars)}"
        )

    lines.append("Commands run:")
    for entry in _truncate_items(commands_run, transparency_max_items) or ["none"]:
        lines.append(
            f"- {_truncate_entry(entry, transparency_max_entry_chars=transparency_max_entry_chars)}"
        )

    lines.append("Files created:")
    for entry in _truncate_items(created_files, transparency_max_items) or ["none"]:
        lines.append(
            f"- {_truncate_entry(entry, transparency_max_entry_chars=transparency_max_entry_chars)}"
        )

    lines.append("Files modified:")
    for entry in _truncate_items(modified_files, transparency_max_items) or ["none"]:
        lines.append(
            f"- {_truncate_entry(entry, transparency_max_entry_chars=transparency_max_entry_chars)}"
        )

    lines.append("Errors/logs:")
    combined = []
    combined.extend([f"[error] {e}" for e in error_entries])
    combined.extend([f"[log] {l}" for l in log_entries])
    combined = _truncate_items(combined, transparency_max_log_entries)
    for entry in combined or ["none"]:
        lines.append(
            f"- {_truncate_entry(entry, transparency_max_entry_chars=transparency_max_entry_chars)}"
        )

    summary = (
        "Summary: "
        f"{len(commands_run)} command(s), "
        f"{len(created_files)} file(s) created, "
        f"{len(modified_files)} file(s) modified."
    )
    lines.append(summary)

    return "\n".join(lines)


async def _attach_artifacts_from_text(
    text: str,
    session_id: str | None,
    message: Any,
    *,
    resolve_workspaces_root: Callable[[], Path],
    extract_candidate_paths: Callable[[str], list[str]],
    resolve_artifact_path: Callable[[Path, str], tuple[Path, Path] | None],
    element_for_path: Callable[[Path, Path], Any],
    max_attachment_bytes: int,
    image_exts: set[str],
    zip_min_count: int,
    cl_module: Any,
    logger: Any,
) -> None:
    """Attach artifacts referenced in assistant text to a Chainlit message."""

    if not session_id:
        return
    repo_dir = resolve_workspaces_root() / session_id / "repo"
    if not repo_dir.exists():
        return
    repo_root = repo_dir.resolve()
    candidates = extract_candidate_paths(text)
    if not candidates:
        return
    seen: set[str] = set()
    resolved_files: list[tuple[Path, Path]] = []
    for token in candidates:
        resolved = resolve_artifact_path(repo_root, token)
        if not resolved:
            continue
        path, relative = resolved
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if max_attachment_bytes > 0:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > max_attachment_bytes:
                logger.info("Skipping large artifact %s (%d bytes)", path, size)
                continue
        resolved_files.append((path, relative))

    if not resolved_files:
        return

    image_files: list[tuple[Path, Path]] = []
    for path, relative in resolved_files:
        if path.suffix.lower() in image_exts:
            image_files.append((path, relative))

    if zip_min_count > 0 and len(resolved_files) >= zip_min_count:
        try:
            bundle_dir = repo_root / "outputs" / "_bundles"
            bundle_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            zip_path = bundle_dir / f"artifacts-{timestamp}.zip"
            import zipfile

            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path, relative in resolved_files:
                    zf.write(path, arcname=relative.as_posix())

            if max_attachment_bytes > 0:
                zip_size = zip_path.stat().st_size
                if zip_size > max_attachment_bytes:
                    logger.info("Skipping zip bundle %s (%d bytes)", zip_path, zip_size)
                    zip_path.unlink(missing_ok=True)
                    raise RuntimeError("Zip bundle exceeded attachment size limit.")

            zip_relative = zip_path.relative_to(repo_root)
            zip_element = cl_module.File(
                name=zip_relative.as_posix(), path=str(zip_path)
            )
            await zip_element.send(for_id=message.id)

            for path, relative in image_files:
                element = element_for_path(path, relative)
                await element.send(for_id=message.id)
            return
        except Exception as exc:
            logger.exception("Failed to bundle artifacts: %s", exc)

    for path, relative in resolved_files:
        element = element_for_path(path, relative)
        await element.send(for_id=message.id)
