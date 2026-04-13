"""Local repo and evidence collection helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


_TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cu",
    ".f",
    ".f90",
    ".h",
    ".hpp",
    ".json",
    ".md",
    ".markdown",
    ".py",
    ".rst",
    ".txt",
    ".yaml",
    ".yml",
}
_SKIP_DIRS = {
    ".fermilink-design",
    ".fermilink-optimize",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}


@dataclass(slots=True)
class EvidenceDocument:
    rel_path: str
    excerpt: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


def collect_repo_file_listing(repo_dir: Path, *, max_files: int = 250) -> list[str]:
    collected: list[str] = []
    for path in sorted(repo_dir.rglob("*")):
        if len(collected) >= max_files:
            break
        if not path.is_file():
            continue
        if _should_skip(path.relative_to(repo_dir)):
            continue
        collected.append(path.relative_to(repo_dir).as_posix())
    return collected


def summarize_repo(repo_dir: Path, *, max_files: int = 250) -> str:
    files = collect_repo_file_listing(repo_dir, max_files=max_files)
    if not files:
        return "- (no files discovered)"
    return "\n".join(f"- `{path}`" for path in files)


def _read_excerpt(path: Path, *, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...[truncated]"


def _default_evidence_paths(repo_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for pattern in ("README*", "docs/**/*.md", "docs/**/*.rst", "*.md", "*.rst"):
        for path in sorted(repo_dir.glob(pattern)):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _TEXT_SUFFIXES and "README" not in path.name:
                continue
            if _should_skip(path.relative_to(repo_dir)):
                continue
            candidates.append(path)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
        if len(unique) >= 8:
            break
    return unique


def collect_evidence_bundle(
    repo_dir: Path,
    goal_spec: dict[str, Any],
    *,
    max_chars_per_file: int = 3000,
) -> dict[str, Any]:
    requested = goal_spec.get("local_evidence_paths")
    evidence_paths: list[Path] = []
    if isinstance(requested, list) and requested:
        for item in requested:
            raw = str(item or "").strip()
            if not raw:
                continue
            path = (repo_dir / raw).resolve()
            if path.is_file():
                evidence_paths.append(path)
    if not evidence_paths:
        evidence_paths = _default_evidence_paths(repo_dir)

    documents: list[EvidenceDocument] = []
    for path in evidence_paths:
        excerpt = _read_excerpt(path, max_chars=max_chars_per_file)
        if not excerpt:
            continue
        try:
            rel_path = path.relative_to(repo_dir).as_posix()
        except ValueError:
            rel_path = str(path)
        documents.append(EvidenceDocument(rel_path=rel_path, excerpt=excerpt))

    return {
        "documents": [doc.to_dict() for doc in documents],
        "repo_file_listing": collect_repo_file_listing(repo_dir),
    }


def render_evidence_summary(bundle: dict[str, Any]) -> str:
    documents = bundle.get("documents")
    if not isinstance(documents, list) or not documents:
        return "- (no local evidence excerpts collected)"
    lines: list[str] = []
    for item in documents:
        if not isinstance(item, dict):
            continue
        rel_path = str(item.get("rel_path") or "").strip()
        excerpt = str(item.get("excerpt") or "").strip()
        if not rel_path:
            continue
        lines.append(f"- `{rel_path}`")
        if excerpt:
            first_line = excerpt.splitlines()[0].strip()
            if first_line:
                lines.append(f"  excerpt: {first_line[:160]}")
    return "\n".join(lines) or "- (no local evidence excerpts collected)"


def build_local_corpus(goal_spec: dict[str, Any], bundle: dict[str, Any]) -> str:
    parts = [str(goal_spec.get("raw_text") or "")]
    for item in bundle.get("documents") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("excerpt") or ""))
    return "\n".join(part for part in parts if part)
