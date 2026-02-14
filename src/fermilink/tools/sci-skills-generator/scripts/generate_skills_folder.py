#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc", ".qmd"}
SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".f",
    ".f90",
    ".f95",
    ".f03",
    ".f08",
    ".py",
    ".pyi",
    ".pyx",
    ".pxd",
    ".i",
    ".scm",
    ".cmake",
    ".m",
    ".am",
    ".in",
}
SOURCE_BASENAMES = {
    "makefile",
    "cmakelists.txt",
    "configure",
    "configure.ac",
    "meson.build",
    "sconstruct",
    "setup.py",
}
IGNORE_DIR_NAMES = {
    ".git",
    "__pycache__",
    "_build",
    "build",
    "dist",
    "venv",
    ".venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    "cmake-build-debug",
    "cmake-build-release",
}

DOC_DIR_CANDIDATES = {
    "docs",
    "doc",
    "documentation",
    "manual",
    "man",
    "guides",
    "guide",
    "userguide",
    "user-guides",
}

TUTORIAL_DIR_CANDIDATES = {
    "tutorials",
    "tutorial",
    "examples",
    "example",
    "demos",
    "demo",
}

TEST_DIR_CANDIDATES = {
    "tests",
    "test",
}

SOURCE_DIR_CANDIDATES = {
    "src",
    "source",
    "lib",
    "python",
    "fortran",
    "cpp",
    "cxx",
    "modules",
    "pkg",
}

GENERIC_PATH_PARTS = {
    "docs",
    "doc",
    "documentation",
    "manual",
    "man",
    "guides",
    "guide",
    "source",
    "sources",
    "latest",
    "stable",
    "en",
    "api",
    "index",
}
STOPWORD_TOKENS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "does",
    "for",
    "from",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "not",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "these",
    "those",
    "to",
    "using",
    "when",
    "where",
    "which",
    "with",
}

RST_UNDERLINE_CHARS = set('=-~^"`*+#')
MAX_DOCS_IN_REFERENCE_FILE = 350
MAX_PRIMARY_DOCS_IN_SKILL = 12
MAX_SOURCE_FILES_IN_REFERENCE_FILE = 30
MAX_SOURCE_LINKS_IN_SKILL = 8


@dataclass(frozen=True)
class TopicRule:
    slug: str
    title: str
    summary: str
    keywords: tuple[str, ...]


TOPIC_RULES = [
    TopicRule(
        slug="getting-started",
        title="Getting Started",
        summary="initial setup, quickstarts, and core concepts",
        keywords=(
            "quickstart",
            "getting started",
            "intro",
            "introduction",
            "basics",
            "overview",
        ),
    ),
    TopicRule(
        slug="build-and-install",
        title="Build and Install",
        summary="build, installation, compilation, and environment setup",
        keywords=(
            "install",
            "installation",
            "build",
            "compile",
            "cmake",
            "make",
            "dependencies",
        ),
    ),
    TopicRule(
        slug="inputs-and-modeling",
        title="Inputs and Modeling",
        summary="inputs, system setup, models, and physical parameterization",
        keywords=(
            "input",
            "model",
            "force field",
            "forcefield",
            "basis",
            "geometry",
            "structure",
            "material",
            "boundary",
        ),
    ),
    TopicRule(
        slug="simulation-workflows",
        title="Simulation Workflows",
        summary="simulation setup, execution flow, and runtime controls",
        keywords=(
            "workflow",
            "simulation",
            "run",
            "dynamics",
            "integrator",
            "time step",
            "pipeline",
        ),
    ),
    TopicRule(
        slug="parallel-hpc",
        title="Parallel and HPC",
        summary="MPI/OpenMP/GPU execution, scaling, and batch systems",
        keywords=(
            "mpi",
            "openmp",
            "gpu",
            "hpc",
            "parallel",
            "slurm",
            "scaling",
            "performance",
        ),
    ),
    TopicRule(
        slug="api-and-scripting",
        title="API and Scripting",
        summary="language bindings, APIs, and programmatic interfaces",
        keywords=(
            "api",
            "python",
            "library",
            "bindings",
            "interface",
            "class",
            "function",
        ),
    ),
    TopicRule(
        slug="examples-and-tutorials",
        title="Examples and Tutorials",
        summary="worked examples, tutorials, and cookbook usage",
        keywords=("tutorial", "example", "howto", "how-to", "cookbook", "walkthrough"),
    ),
    TopicRule(
        slug="analysis-and-output",
        title="Analysis and Output",
        summary="output formats, analysis, and post-processing",
        keywords=(
            "output",
            "analysis",
            "postprocess",
            "post-processing",
            "visualization",
            "trajectory",
            "plot",
        ),
    ),
    TopicRule(
        slug="developer-guide",
        title="Developer Guide",
        summary="developer architecture, extension points, and contribution workflow",
        keywords=(
            "developer",
            "develop",
            "contributing",
            "contribution",
            "architecture",
            "internals",
            "plugin",
        ),
    ),
    TopicRule(
        slug="troubleshooting",
        title="Troubleshooting",
        summary="known issues, diagnostics, and debugging patterns",
        keywords=(
            "troubleshoot",
            "debug",
            "faq",
            "error",
            "issue",
            "known problem",
            "failure",
        ),
    ),
    TopicRule(
        slug="theory-and-methods",
        title="Theory and Methods",
        summary="theoretical background and algorithmic methods",
        keywords=(
            "theory",
            "method",
            "algorithm",
            "equation",
            "formalism",
            "derivation",
        ),
    ),
]

RULE_BY_SLUG = {rule.slug: rule for rule in TOPIC_RULES}


@dataclass(frozen=True)
class DocRecord:
    rel_path: Path
    title: str
    headings: tuple[str, ...]
    topic_slug: str


@dataclass(frozen=True)
class SourceRecord:
    rel_path: Path
    tokens: tuple[str, ...]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "topic"


def _humanize_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def _dedupe_ancestor_paths(paths: list[Path]) -> list[Path]:
    unique = sorted({p.resolve() for p in paths}, key=lambda p: (len(p.parts), str(p)))
    kept: list[Path] = []
    for path in unique:
        if any(existing == path or existing in path.parents for existing in kept):
            continue
        kept.append(path)
    return kept


def _discover_named_dirs(
    root: Path, candidates: set[str], max_depth: int = 3
) -> list[Path]:
    found: list[Path] = []
    queue: list[Path] = [root]

    while queue:
        current = queue.pop(0)
        try:
            entries = list(current.iterdir())
        except OSError:
            continue

        for entry in entries:
            if not entry.is_dir():
                continue
            name = entry.name.lower()
            if entry.name.startswith(".") or name in IGNORE_DIR_NAMES:
                continue

            try:
                depth = len(entry.relative_to(root).parts)
            except ValueError:
                continue

            if name in candidates:
                found.append(entry)

            if depth < max_depth:
                queue.append(entry)

    return _dedupe_ancestor_paths(found)


def _parse_user_dirs(raw: str | None, package_root: Path) -> list[Path]:
    if not raw:
        return []

    dirs: list[Path] = []
    for token in raw.split(","):
        value = token.strip()
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = package_root / path
        path = path.resolve()
        if path.is_dir():
            dirs.append(path)

    return _dedupe_ancestor_paths(dirs)


def _find_docs_dirs(package_root: Path, docs_dirs_arg: str | None) -> list[Path]:
    user_dirs = _parse_user_dirs(docs_dirs_arg, package_root)
    if user_dirs:
        return user_dirs

    top_level = [
        d.resolve()
        for d in package_root.iterdir()
        if d.is_dir() and d.name.lower() in DOC_DIR_CANDIDATES
    ]
    if top_level:
        return _dedupe_ancestor_paths(top_level)

    return _discover_named_dirs(package_root, DOC_DIR_CANDIDATES, max_depth=3)


def _find_source_dirs(
    package_root: Path, source_dirs_arg: str | None, docs_only: bool
) -> list[Path]:
    if docs_only:
        return []

    user_dirs = _parse_user_dirs(source_dirs_arg, package_root)
    if user_dirs:
        return user_dirs

    source_dirs = [
        d.resolve()
        for d in package_root.iterdir()
        if d.is_dir() and d.name.lower() in SOURCE_DIR_CANDIDATES
    ]
    return _dedupe_ancestor_paths(source_dirs)


def _find_aux_dirs(
    package_root: Path, aux_dirs_arg: str | None, candidates: set[str]
) -> list[Path]:
    user_dirs = _parse_user_dirs(aux_dirs_arg, package_root)
    if user_dirs:
        return user_dirs

    top_level = [
        d.resolve()
        for d in package_root.iterdir()
        if d.is_dir() and d.name.lower() in candidates
    ]
    if top_level:
        return _dedupe_ancestor_paths(top_level)

    return _discover_named_dirs(package_root, candidates, max_depth=2)


def _read_file_text(path: Path, max_chars: int = 250_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(max_chars)
    except OSError:
        return ""


def _extract_headings(path: Path) -> tuple[str, ...]:
    text = _read_file_text(path)
    if not text:
        return ()

    lines = text.splitlines()
    headings: list[str] = []

    for line in lines:
        match = re.match(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", line)
        if match:
            heading = match.group(1).strip().strip("#").strip()
            if heading:
                headings.append(heading)

    for index in range(1, len(lines)):
        prev = lines[index - 1].strip()
        curr = lines[index].strip()
        if not prev or len(curr) < 3:
            continue
        if set(curr) <= RST_UNDERLINE_CHARS and len(curr) >= min(3, len(prev)):
            headings.append(prev)

    deduped: list[str] = []
    seen: set[str] = set()
    for heading in headings:
        key = heading.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(heading)
        if len(deduped) >= 10:
            break

    return tuple(deduped)


def _iter_doc_files(docs_dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for docs_dir in docs_dirs:
        for path in docs_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in DOC_EXTENSIONS:
                continue
            if any(part.lower() in IGNORE_DIR_NAMES for part in path.parts):
                continue
            files.append(path.resolve())
    return sorted(set(files))


def _infer_topic_slug(
    rel_path: Path, headings: tuple[str, ...], package_slug: str
) -> str:
    blob = " ".join([str(rel_path).lower(), *[h.lower() for h in headings]])

    for rule in TOPIC_RULES:
        if any(keyword in blob for keyword in rule.keywords):
            return rule.slug

    path_tokens: list[str] = []
    for part in rel_path.with_suffix("").parts:
        token = _slugify(part)
        if token in GENERIC_PATH_PARTS or token == package_slug:
            continue
        path_tokens.append(token)

    if path_tokens:
        return path_tokens[0]

    return "general"


def _pick_doc_title(rel_path: Path, headings: tuple[str, ...]) -> str:
    if headings:
        return headings[0]
    return _humanize_slug(_slugify(rel_path.stem))


def _collect_doc_records(
    package_root: Path, docs_dirs: list[Path], package_slug: str
) -> list[DocRecord]:
    records: list[DocRecord] = []
    for path in _iter_doc_files(docs_dirs):
        try:
            rel_path = path.relative_to(package_root)
        except ValueError:
            continue

        headings = _extract_headings(path)
        topic_slug = _infer_topic_slug(rel_path, headings, package_slug)
        title = _pick_doc_title(rel_path, headings)
        records.append(
            DocRecord(
                rel_path=rel_path, title=title, headings=headings, topic_slug=topic_slug
            )
        )

    return records


def _iter_source_files(source_dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    non_impl_dir_names = {
        "tests",
        "test",
        "examples",
        "example",
        "tutorials",
        "tutorial",
        "demos",
        "demo",
        "benchmarks",
        "benchmark",
    }
    for source_dir in source_dirs:
        for path in source_dir.rglob("*"):
            if not path.is_file():
                continue
            if any(part.lower() in IGNORE_DIR_NAMES for part in path.parts):
                continue
            if any(part.lower() in non_impl_dir_names for part in path.parts):
                continue
            suffix = path.suffix.lower()
            basename = path.name.lower()
            if suffix not in SOURCE_EXTENSIONS and basename not in SOURCE_BASENAMES:
                continue
            files.append(path.resolve())
    return sorted(set(files))


def _tokenize_blob(text: str) -> tuple[str, ...]:
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    return tuple(token for token in tokens if token)


def _path_tokens(rel_path: Path) -> tuple[str, ...]:
    tokens: list[str] = []
    for part in rel_path.parts:
        tokens.extend(_tokenize_blob(part))
    return tuple(tokens)


def _collect_source_records(
    package_root: Path, source_dirs: list[Path]
) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for path in _iter_source_files(source_dirs):
        try:
            rel_path = path.relative_to(package_root)
        except ValueError:
            continue
        records.append(SourceRecord(rel_path=rel_path, tokens=_path_tokens(rel_path)))
    return records


def _topic_query_tokens(
    topic_slug: str, docs: list[DocRecord], package_slug: str
) -> tuple[str, ...]:
    token_set: set[str] = set(_tokenize_blob(topic_slug.replace("-", " ")))
    rule = RULE_BY_SLUG.get(topic_slug)
    if rule:
        for keyword in rule.keywords:
            token_set.update(_tokenize_blob(keyword))

    for record in _sorted_docs(docs)[:20]:
        token_set.update(_tokenize_blob(str(record.rel_path)))

    blocked = GENERIC_PATH_PARTS | {
        package_slug,
        "rst",
        "md",
        "txt",
        "adoc",
        "qmd",
        "docs",
        "examples",
        "example",
        "tutorial",
        "tutorials",
        "test",
        "tests",
        "readme",
        "python",
        "scheme",
    }
    allow_short = {"2d", "3d", "api", "mpi", "gpu", "hpc", "pml", "fdtd"}
    cleaned = [
        token
        for token in token_set
        if token not in blocked
        and token not in STOPWORD_TOKENS
        and (len(token) >= 3 or token in allow_short)
    ]
    return tuple(sorted(cleaned))


def _source_default_priority(
    record: SourceRecord, package_slug: str
) -> tuple[int, int, str]:
    rel_path_str = str(record.rel_path).lower()
    depth = len(record.rel_path.parts)
    filename = record.rel_path.name.lower()

    score = 0
    if depth <= 2:
        score += 8
    if (
        record.rel_path.parts
        and record.rel_path.parts[0].lower() in SOURCE_DIR_CANDIDATES
    ):
        score += 5
    if filename.startswith(package_slug):
        score += 6
    if filename in {"__init__.py", "simulation.py", "solver.py"}:
        score += 8

    for keyword in (
        "core",
        "main",
        "api",
        "interface",
        "solver",
        "simulation",
        "model",
        "field",
        "geometry",
        "structure",
        "material",
        "boundary",
        "source",
    ):
        if keyword in filename:
            score += 3

    if "test" in rel_path_str:
        score -= 4
    if "example" in rel_path_str:
        score -= 2
    return (score, -depth, rel_path_str)


def _score_source_record(
    record: SourceRecord,
    topic_slug: str,
    query_tokens: tuple[str, ...],
) -> tuple[int, tuple[str, ...]]:
    rel_path_str = str(record.rel_path).lower()
    path_tokens = set(record.tokens)
    matched_tokens: list[str] = []
    score = 0

    for token in query_tokens:
        token_score = 0
        if token in path_tokens:
            token_score += 3
        if token in rel_path_str:
            token_score += 2
        if token_score > 0:
            matched_tokens.append(token)
            score += token_score

    first_part = record.rel_path.parts[0].lower() if record.rel_path.parts else ""
    filename = record.rel_path.name.lower()

    if topic_slug == "api-and-scripting":
        if first_part == "python":
            score += 8
        if record.rel_path.suffix.lower() in {".py", ".pyi", ".i"}:
            score += 3
    if topic_slug == "build-and-install":
        if filename in SOURCE_BASENAMES:
            score += 6
        if "cmake" in rel_path_str or "makefile" in rel_path_str:
            score += 4
    if topic_slug == "inputs-and-modeling":
        for token in (
            "material",
            "geom",
            "geometry",
            "structure",
            "boundary",
            "source",
        ):
            if token in rel_path_str:
                score += 2
    if topic_slug == "simulation-workflows":
        for token in ("simulation", "step", "run", "solver", "fields", "time"):
            if token in rel_path_str:
                score += 2

    if "/tests/" in f"/{rel_path_str}/":
        score -= 2

    deduped_matches: list[str] = []
    seen: set[str] = set()
    for token in matched_tokens:
        if token in seen:
            continue
        seen.add(token)
        deduped_matches.append(token)
        if len(deduped_matches) >= 6:
            break

    return score, tuple(deduped_matches)


def _select_topic_sources(
    topic_slug: str,
    docs: list[DocRecord],
    source_records: list[SourceRecord],
    package_slug: str,
) -> tuple[tuple[str, ...], list[tuple[SourceRecord, int, tuple[str, ...]]]]:
    if not source_records:
        return (), []

    query_tokens = _topic_query_tokens(topic_slug, docs, package_slug)
    scored: list[tuple[SourceRecord, int, tuple[str, ...]]] = []
    for record in source_records:
        score, matched_tokens = _score_source_record(record, topic_slug, query_tokens)
        if score <= 0:
            continue
        scored.append((record, score, matched_tokens))

    scored.sort(
        key=lambda item: (
            item[1],
            *_source_default_priority(item[0], package_slug),
        ),
        reverse=True,
    )

    selected: list[tuple[SourceRecord, int, tuple[str, ...]]] = []
    used_paths: set[Path] = set()
    for match in scored:
        record = match[0]
        if record.rel_path in used_paths:
            continue
        used_paths.add(record.rel_path)
        selected.append(match)
        if len(selected) >= MAX_SOURCE_FILES_IN_REFERENCE_FILE:
            break

    if len(selected) < MAX_SOURCE_LINKS_IN_SKILL:
        fallback = sorted(
            source_records,
            key=lambda record: _source_default_priority(record, package_slug),
            reverse=True,
        )
        for record in fallback:
            if record.rel_path in used_paths:
                continue
            used_paths.add(record.rel_path)
            selected.append((record, 0, ()))
            if len(selected) >= MAX_SOURCE_FILES_IN_REFERENCE_FILE:
                break

    return query_tokens, selected


def _topic_score(topic_slug: str, records: list[DocRecord]) -> tuple[int, int, str]:
    canonical_bonus = 1_000 if topic_slug in RULE_BY_SLUG else 0
    doc_bonus = len(records) * 10
    heading_bonus = min(sum(len(record.headings) for record in records), 40)
    return (canonical_bonus + doc_bonus + heading_bonus, len(records), topic_slug)


def _select_topics(
    buckets: dict[str, list[DocRecord]],
    max_skills: int,
) -> tuple[list[str], dict[str, list[DocRecord]]]:
    max_topic_skills = max(1, max_skills - 1)
    sorted_topics = sorted(
        buckets,
        key=lambda slug: _topic_score(slug, buckets[slug]),
        reverse=True,
    )

    if len(sorted_topics) <= max_topic_skills:
        return sorted_topics, buckets

    if max_topic_skills == 1:
        first = sorted_topics[0]
        for slug in sorted_topics[1:]:
            buckets[first].extend(buckets[slug])
        return [first], buckets

    kept = sorted_topics[: max_topic_skills - 1]
    overflow = sorted_topics[max_topic_skills - 1 :]
    advanced_records: list[DocRecord] = []
    for slug in overflow:
        advanced_records.extend(buckets[slug])

    buckets["advanced-topics"] = advanced_records
    return [*kept, "advanced-topics"], buckets


def _doc_priority(record: DocRecord) -> tuple[int, int, str]:
    stem = record.rel_path.stem.lower()
    priority = 0
    if stem in {
        "index",
        "readme",
        "overview",
        "quickstart",
        "getting-started",
        "introduction",
    }:
        priority += 15
    if record.headings:
        priority += min(8, len(record.headings))

    depth_penalty = len(record.rel_path.parts)
    return (priority - depth_penalty, -len(record.headings), str(record.rel_path))


def _sorted_docs(records: list[DocRecord]) -> list[DocRecord]:
    return sorted(records, key=_doc_priority, reverse=True)


def _topic_title(topic_slug: str) -> str:
    rule = RULE_BY_SLUG.get(topic_slug)
    if rule:
        return rule.title
    if topic_slug == "advanced-topics":
        return "Advanced and Specialized Topics"
    return _humanize_slug(topic_slug)


def _topic_summary(topic_slug: str) -> str:
    rule = RULE_BY_SLUG.get(topic_slug)
    if rule:
        return rule.summary
    if topic_slug == "advanced-topics":
        return (
            "overflow specialized documentation not captured by higher-priority skills"
        )
    return f"documentation grouped under the '{topic_slug}' theme"


def _truncate_skill_name(name: str) -> str:
    return name[:63].rstrip("-")


def _unique_skill_name(base: str, used: set[str]) -> str:
    candidate = _truncate_skill_name(base)
    if candidate not in used:
        used.add(candidate)
        return candidate

    index = 2
    while True:
        suffix = f"-{index}"
        clipped = _truncate_skill_name(base[: 63 - len(suffix)] + suffix)
        if clipped not in used:
            used.add(clipped)
            return clipped
        index += 1


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _render_index_skill(
    package_name: str,
    index_skill_name: str,
    topic_entries: list[tuple[str, str, str]],
    docs_dirs: list[Path],
    tutorial_dirs: list[Path],
    test_dirs: list[Path],
    source_dirs: list[Path],
    package_root: Path,
) -> str:
    docs_lines = "\n".join(
        f"- `{path.relative_to(package_root)}`" for path in docs_dirs
    )
    tutorial_lines = (
        "\n".join(f"- `{path.relative_to(package_root)}`" for path in tutorial_dirs)
        if tutorial_dirs
        else "- None discovered."
    )
    test_lines = (
        "\n".join(f"- `{path.relative_to(package_root)}`" for path in test_dirs)
        if test_dirs
        else "- None discovered."
    )
    if source_dirs:
        source_lines = "\n".join(
            f"- `{path.relative_to(package_root)}`" for path in source_dirs
        )
    else:
        source_lines = (
            "- No source tree was provided or discovered; rely on docs-first skills."
        )

    topic_lines = "\n".join(
        f"- `{skill_name}`: {title} ({summary})"
        for skill_name, title, summary in topic_entries
    )

    description = (
        f"This skill should be used when users ask how to use {package_name} and the correct "
        "generated documentation skill must be selected before going deeper into source code."
    )

    return f"""---
name: {index_skill_name}
description: {description}
---

# {package_name} Skills Index

## Route the request
- Classify the request into one of the generated topic skills listed below.
- Prefer abstract, workflow-level guidance for large scientific packages; do not attempt full function-by-function coverage unless explicitly requested.

## Generated topic skills
{topic_lines}

## Documentation-first inputs
{docs_lines}

## Tutorials and examples roots
{tutorial_lines}

## Test roots for behavior checks
{test_lines}

## Escalate only when needed
- Start from topic skill primary references.
- If those references are insufficient, search the topic skill `references/doc_map.md`.
- If documentation still leaves ambiguity, open `references/source_map.md` inside the same topic skill and inspect the suggested source entry points.
- Use targeted symbol search while inspecting source (e.g., `rg -n "<symbol_or_keyword>" {" ".join(str(path.relative_to(package_root)) for path in source_dirs) if source_dirs else "<source-dir>"}`).

## Source directories for deeper inspection
{source_lines}
"""


def _render_topic_skill(
    package_name: str,
    skill_name: str,
    topic_slug: str,
    docs: list[DocRecord],
    tutorial_dirs: list[Path],
    test_dirs: list[Path],
    source_dirs: list[Path],
    topic_source_matches: list[tuple[SourceRecord, int, tuple[str, ...]]],
    package_root: Path,
) -> str:
    title = _topic_title(topic_slug)
    summary = _topic_summary(topic_slug)
    primary_docs = _sorted_docs(docs)[:MAX_PRIMARY_DOCS_IN_SKILL]

    docs_block = "\n".join(f"- `{record.rel_path}`" for record in primary_docs)
    if not docs_block:
        docs_block = "- No documentation files were assigned to this topic."

    if source_dirs:
        source_block = "\n".join(
            f"- `{path.relative_to(package_root)}`" for path in source_dirs
        )
    else:
        source_block = "- Source code access is unavailable or intentionally skipped (`--docs-only`)."
    if topic_source_matches:
        source_primary_block = "\n".join(
            f"- `{match[0].rel_path}`"
            for match in topic_source_matches[:MAX_SOURCE_LINKS_IN_SKILL]
        )
    elif source_dirs:
        source_primary_block = "- Source roots are available; use `references/source_map.md` for ranked entry points."
    else:
        source_primary_block = (
            "- No source entry points are available for this docs-only run."
        )
    if tutorial_dirs:
        tutorial_block = "\n".join(
            f"- `{path.relative_to(package_root)}`" for path in tutorial_dirs
        )
    else:
        tutorial_block = "- None discovered."
    if test_dirs:
        test_block = "\n".join(
            f"- `{path.relative_to(package_root)}`" for path in test_dirs
        )
    else:
        test_block = "- None discovered."

    description = (
        f"This skill should be used when users ask about {title.lower()} in {package_name}; "
        "it prioritizes documentation references and then source inspection only for unresolved details."
    )

    return f"""---
name: {skill_name}
description: {description}
---

# {package_name}: {title}

## Scope
- Handle questions about {summary}.
- Keep responses abstract and architectural for large codebases; avoid exhaustive per-function documentation unless requested.

## Primary documentation references
{docs_block}

## Workflow
- Start with the primary references above.
- If details are missing, inspect `references/doc_map.md` for the complete topic document list.
- Use tutorials/examples as executable usage patterns when available.
- Use tests as behavior or regression references when available.
- If ambiguity remains after docs, inspect `references/source_map.md` and start with the ranked source entry points.
- Cite exact documentation file paths in responses.

## Tutorials and examples
{tutorial_block}

## Test references
{test_block}

## Optional deeper inspection
{source_block}

## Source entry points for unresolved issues
{source_primary_block}
- Prefer targeted source search (for example: `rg -n "<symbol_or_keyword>" {" ".join(str(path.relative_to(package_root)) for path in source_dirs) if source_dirs else "<source-dir>"}`).
"""


def _render_doc_map(
    package_name: str,
    topic_slug: str,
    docs: list[DocRecord],
    knowledge_dirs: list[Path],
    package_root: Path,
) -> str:
    title = _topic_title(topic_slug)
    docs_dir_lines = "\n".join(
        f"- `{path.relative_to(package_root)}`" for path in knowledge_dirs
    )

    ordered_docs = _sorted_docs(docs)
    shown_docs = ordered_docs[:MAX_DOCS_IN_REFERENCE_FILE]
    omitted = len(ordered_docs) - len(shown_docs)

    lines = []
    for record in shown_docs:
        heading_preview = (
            "; ".join(record.headings[:3])
            if record.headings
            else "(no heading extracted)"
        )
        lines.append(
            f"- `{record.rel_path}` | title: {record.title} | headings: {heading_preview}"
        )

    if not lines:
        lines.append("- No docs were grouped in this topic.")

    overflow_line = ""
    if omitted > 0:
        overflow_line = (
            f"\n\nAdditional files omitted from this map: {omitted}. "
            "Increase `MAX_DOCS_IN_REFERENCE_FILE` in the generator script if needed."
        )

    return f"""# {package_name} documentation map: {title}

Generated from documentation roots:
{docs_dir_lines}

Total docs grouped in this topic: {len(ordered_docs)}

## File inventory
{"\n".join(lines)}{overflow_line}
"""


def _render_source_map(
    package_name: str,
    topic_slug: str,
    source_matches: list[tuple[SourceRecord, int, tuple[str, ...]]],
    source_dirs: list[Path],
    query_tokens: tuple[str, ...],
    package_root: Path,
) -> str:
    title = _topic_title(topic_slug)

    if source_dirs:
        source_dir_lines = "\n".join(
            f"- `{path.relative_to(package_root)}`" for path in source_dirs
        )
    else:
        source_dir_lines = "- None (docs-only generation)."

    if query_tokens:
        query_lines = "\n".join(f"- `{token}`" for token in query_tokens[:30])
    else:
        query_lines = "- No topic tokens were extracted."

    if source_matches:
        shown_matches = source_matches[:MAX_SOURCE_FILES_IN_REFERENCE_FILE]
        lines: list[str] = []
        for record, score, matched in shown_matches:
            if score > 0 and matched:
                lines.append(
                    f"- `{record.rel_path}` | score: {score} | matched tokens: {', '.join(matched)}"
                )
            elif score > 0:
                lines.append(f"- `{record.rel_path}` | score: {score}")
            else:
                lines.append(f"- `{record.rel_path}` | fallback entry point")
    else:
        lines = ["- No source files were available for this topic."]

    source_roots_arg = (
        " ".join(str(path.relative_to(package_root)) for path in source_dirs)
        if source_dirs
        else "<source-dir>"
    )

    return f"""# {package_name} source map: {title}

Generated from source roots:
{source_dir_lines}

Use this map only after exhausting the topic docs in `references/doc_map.md`.

## Topic query tokens
{query_lines}

## Fast source navigation
- `rg -n "<symbol_or_keyword>" {source_roots_arg}`
- `rg -n "class|def|struct|namespace" {source_roots_arg}`
- If a doc mentions a function/class, search that exact symbol first, then inspect nearby implementation files.

## Suggested source entry points
{"\n".join(lines)}
"""


def _print_plan(
    package_name: str,
    docs_dirs: list[Path],
    tutorial_dirs: list[Path],
    test_dirs: list[Path],
    source_dirs: list[Path],
    selected_topics: list[str],
    buckets: dict[str, list[DocRecord]],
    package_root: Path,
) -> None:
    print(f"Package: {package_name}")
    print("Docs roots:")
    for path in docs_dirs:
        print(f"  - {path.relative_to(package_root)}")
    if tutorial_dirs:
        print("Tutorial/example roots:")
        for path in tutorial_dirs:
            print(f"  - {path.relative_to(package_root)}")
    else:
        print("Tutorial/example roots: (none)")
    if test_dirs:
        print("Test roots:")
        for path in test_dirs:
            print(f"  - {path.relative_to(package_root)}")
    else:
        print("Test roots: (none)")
    if source_dirs:
        print("Source roots:")
        for path in source_dirs:
            print(f"  - {path.relative_to(package_root)}")
    else:
        print("Source roots: (none)")

    print("\nPlanned skills:")
    print(f"  - {package_name.lower()}-index")
    for topic_slug in selected_topics:
        print(
            f"  - {package_name.lower()}-{topic_slug} ({len(buckets[topic_slug])} docs)"
        )


def generate(args: argparse.Namespace) -> int:
    package_root = Path(args.package_root).resolve()
    if not package_root.is_dir():
        raise FileNotFoundError(f"Package root does not exist: {package_root}")

    package_name = args.package_name.strip() if args.package_name else package_root.name
    if not package_name:
        raise ValueError("Package name is empty; provide --package-name")

    package_slug = _slugify(package_name)
    docs_dirs = _find_docs_dirs(package_root, args.docs_dirs)
    if not docs_dirs:
        raise FileNotFoundError(
            "No documentation directories were found. Provide --docs-dirs or place docs under common names (docs/, doc/, manual/, ...)."
        )

    tutorial_dirs = _find_aux_dirs(
        package_root, args.tutorial_dirs, TUTORIAL_DIR_CANDIDATES
    )
    test_dirs = _find_aux_dirs(package_root, args.test_dirs, TEST_DIR_CANDIDATES)
    source_dirs = _find_source_dirs(package_root, args.source_dirs, args.docs_only)
    source_records = _collect_source_records(package_root, source_dirs)

    knowledge_dirs = list(docs_dirs)
    for path in [*tutorial_dirs, *test_dirs]:
        if path not in knowledge_dirs:
            knowledge_dirs.append(path)

    records = _collect_doc_records(package_root, knowledge_dirs, package_slug)

    if not records:
        raise FileNotFoundError(
            "No documentation files were found under the selected docs directories."
        )

    buckets: dict[str, list[DocRecord]] = defaultdict(list)
    for record in records:
        buckets[record.topic_slug].append(record)

    selected_topics, buckets = _select_topics(dict(buckets), args.max_skills)

    output_dir = (
        Path(args.output_dir).resolve() if args.output_dir else package_root / "skills"
    )

    if args.dry_run:
        _print_plan(
            package_name,
            docs_dirs,
            tutorial_dirs,
            test_dirs,
            source_dirs,
            selected_topics,
            buckets,
            package_root,
        )
        print(f"\nOutput directory (not written in dry-run): {output_dir}")
        return 0

    if output_dir.exists() and any(output_dir.iterdir()):
        if not args.overwrite:
            raise FileExistsError(
                f"Output directory is not empty: {output_dir}. Use --overwrite to replace it."
            )
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    used_skill_names: set[str] = set()
    index_skill_name = _unique_skill_name(f"{package_slug}-index", used_skill_names)

    topic_entries: list[tuple[str, str, str]] = []
    for topic_slug in selected_topics:
        base_name = f"{package_slug}-{topic_slug}"
        skill_name = _unique_skill_name(base_name, used_skill_names)

        topic_docs = buckets[topic_slug]
        query_tokens, topic_source_matches = _select_topic_sources(
            topic_slug=topic_slug,
            docs=topic_docs,
            source_records=source_records,
            package_slug=package_slug,
        )
        topic_title = _topic_title(topic_slug)
        topic_summary = _topic_summary(topic_slug)

        skill_dir = output_dir / skill_name
        topic_skill = _render_topic_skill(
            package_name=package_name,
            skill_name=skill_name,
            topic_slug=topic_slug,
            docs=topic_docs,
            tutorial_dirs=tutorial_dirs,
            test_dirs=test_dirs,
            source_dirs=source_dirs,
            topic_source_matches=topic_source_matches,
            package_root=package_root,
        )
        _write_text(skill_dir / "SKILL.md", topic_skill)

        doc_map = _render_doc_map(
            package_name=package_name,
            topic_slug=topic_slug,
            docs=topic_docs,
            knowledge_dirs=knowledge_dirs,
            package_root=package_root,
        )
        _write_text(skill_dir / "references" / "doc_map.md", doc_map)

        source_map = _render_source_map(
            package_name=package_name,
            topic_slug=topic_slug,
            source_matches=topic_source_matches,
            source_dirs=source_dirs,
            query_tokens=query_tokens,
            package_root=package_root,
        )
        _write_text(skill_dir / "references" / "source_map.md", source_map)

        topic_entries.append((skill_name, topic_title, topic_summary))

    index_dir = output_dir / index_skill_name
    index_skill = _render_index_skill(
        package_name=package_name,
        index_skill_name=index_skill_name,
        topic_entries=topic_entries,
        docs_dirs=docs_dirs,
        tutorial_dirs=tutorial_dirs,
        test_dirs=test_dirs,
        source_dirs=source_dirs,
        package_root=package_root,
    )
    _write_text(index_dir / "SKILL.md", index_skill)

    print(f"Generated {len(topic_entries) + 1} skills at {output_dir}")
    print(f"Index skill: {index_skill_name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a documentation-first skills folder for scientific software repositories. "
            "The generated skill count is capped by --max-skills and remains abstract for large packages."
        )
    )
    parser.add_argument(
        "--package-root",
        required=True,
        help="Path to the target scientific software repository.",
    )
    parser.add_argument(
        "--package-name",
        default=None,
        help="Human-readable package name. Defaults to the package root directory name.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Path where generated skill folders are written. Defaults to <package-root>/skills.",
    )
    parser.add_argument(
        "--docs-dirs",
        default=None,
        help="Comma-separated docs directory paths relative to --package-root (or absolute paths).",
    )
    parser.add_argument(
        "--tutorial-dirs",
        default=None,
        help="Comma-separated tutorial/example directory paths relative to --package-root (or absolute paths).",
    )
    parser.add_argument(
        "--test-dirs",
        default=None,
        help="Comma-separated test directory paths relative to --package-root (or absolute paths).",
    )
    parser.add_argument(
        "--source-dirs",
        default=None,
        help="Comma-separated source directory paths relative to --package-root (or absolute paths).",
    )
    parser.add_argument(
        "--docs-only",
        action="store_true",
        help="Skip source-directory discovery and produce docs-only skills (useful for closed-source packages).",
    )
    parser.add_argument(
        "--max-skills",
        type=int,
        default=30,
        help="Maximum number of generated skills (including the index skill). Default: 30.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing non-empty output directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned skill generation without writing files.",
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.max_skills < 2:
        parser.error(
            "--max-skills must be >= 2 so an index skill plus at least one topic skill can be created."
        )

    return generate(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
