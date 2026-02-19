from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path


PROFILE_DIR_KEYS = ("docs_dirs", "tutorial_dirs", "test_dirs", "source_dirs")
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
DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc", ".qmd"}
PLAYBOOK_REQUIRED_TOKENS = (
    "route",
    "triage questions",
    "canonical workflow",
    "minimal working example",
    "pitfalls",
    "convergence/validation",
)
SOURCE_IGNORE_DIR_NAMES = {
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
SOURCE_NON_IMPL_DIR_NAMES = {
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
PAPER_SKILL_SCOPE_STOPWORDS = {
    "a",
    "an",
    "and",
    "appendix",
    "as",
    "based",
    "brief",
    "by",
    "comment",
    "draft",
    "figure",
    "figures",
    "fig",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "its",
    "manuscript",
    "mode",
    "modes",
    "of",
    "on",
    "or",
    "paper",
    "reproduce",
    "reproduction",
    "result",
    "results",
    "revised",
    "revision",
    "scope",
    "section",
    "simulation",
    "simulations",
    "study",
    "summary",
    "supplementary",
    "that",
    "the",
    "their",
    "this",
    "to",
    "tutorial",
    "use",
    "used",
    "using",
    "with",
}


def _cli():
    from fermilink import cli

    return cli


def _resolve_compile_tool_source() -> Path:
    return Path(__file__).resolve().parent.parent / "tools" / "sci-skills-generator"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _normalize_profile_dir_list(
    project_root: Path,
    raw_value: object,
    *,
    key_name: str,
    warnings: list[str],
) -> list[str]:
    values: list[str] = []
    if isinstance(raw_value, list):
        for item in raw_value:
            if isinstance(item, str) and item.strip():
                values.append(item.strip())
    elif isinstance(raw_value, str) and raw_value.strip():
        values.extend(part.strip() for part in raw_value.split(",") if part.strip())

    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        candidate = Path(item).expanduser()
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(project_root)
            except (OSError, ValueError):
                warnings.append(
                    f"Ignoring {key_name} entry outside package root: {item}"
                )
                continue
        else:
            rel = Path(item)

        rel = Path(str(rel).replace("\\", "/"))
        resolved = (project_root / rel).resolve()
        if not resolved.exists() or not resolved.is_dir():
            warnings.append(f"Ignoring missing {key_name} entry: {rel}")
            continue
        canonical = str(resolved.relative_to(project_root))
        if canonical in seen:
            continue
        seen.add(canonical)
        normalized.append(canonical)
    return normalized


def _extract_profile_from_assistant_text(assistant_text: str) -> dict[str, object] | None:
    cli = _cli()
    parsed = cli._extract_tagged_json_payload(
        assistant_text, token_re=cli.COMPILE_PROFILE_TOKEN_RE
    )
    if not isinstance(parsed, dict):
        return None
    return parsed


def _default_compile_profile(package_name: str) -> dict[str, object]:
    return {
        "package_name": package_name,
        "docs_only": False,
        "docs_dirs": [],
        "tutorial_dirs": [],
        "test_dirs": [],
        "source_dirs": [],
        "notes": [],
    }


def _load_compile_profile(
    project_root: Path,
    *,
    default_package_name: str,
    assistant_text: str = "",
) -> dict[str, object]:
    cli = _cli()
    profile_path = project_root / cli.COMPILE_PROFILE_REL_PATH
    warnings: list[str] = []

    raw_profile: dict[str, object] | None = None
    profile_source = "auto-default"
    if profile_path.is_file():
        try:
            payload = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise cli.PackageError(
                f"Invalid compile profile JSON at {profile_path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise cli.PackageError(
                f"Invalid compile profile JSON at {profile_path}: expected object."
            )
        raw_profile = payload
        profile_source = "file"
    else:
        raw_profile = _extract_profile_from_assistant_text(assistant_text)
        if isinstance(raw_profile, dict):
            profile_source = "assistant_tag"
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                profile_path.write_text(
                    json.dumps(raw_profile, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                warnings.append(f"Failed to persist compile profile: {exc}")

    profile = _default_compile_profile(default_package_name)
    if isinstance(raw_profile, dict):
        package_name = str(raw_profile.get("package_name") or "").strip()
        if package_name:
            profile["package_name"] = package_name
        profile["docs_only"] = bool(raw_profile.get("docs_only", False))
        profile["notes"] = [
            str(item).strip()
            for item in raw_profile.get("notes", [])
            if isinstance(item, str) and str(item).strip()
        ]
        for key in PROFILE_DIR_KEYS:
            profile[key] = _normalize_profile_dir_list(
                project_root,
                raw_profile.get(key),
                key_name=key,
                warnings=warnings,
            )

    profile["profile_source"] = profile_source
    profile["profile_path"] = str(profile_path.relative_to(project_root))
    profile["warnings"] = warnings
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        profile_path.write_text(
            json.dumps(profile, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        warnings.append(f"Failed to write normalized compile profile: {exc}")
    return profile


def _run_compile_generator(
    project_root: Path,
    *,
    tool_dir: Path,
    profile: dict[str, object],
    max_skills: int,
    docs_only_override: bool = False,
) -> dict[str, object]:
    cli = _cli()
    script_path = tool_dir / "scripts" / "generate_skills_folder.py"
    if not script_path.is_file():
        raise cli.PackageError(
            f"Missing generator script for compile at: {script_path}"
        )

    package_name = str(profile.get("package_name") or "").strip() or project_root.name
    cmd: list[str] = [
        cli.sys.executable,
        str(script_path),
        "--package-root",
        str(project_root),
        "--package-name",
        package_name,
        "--output-dir",
        str(project_root / "skills"),
        "--max-skills",
        str(max_skills),
        "--overwrite",
    ]
    docs_only = bool(profile.get("docs_only", False)) or docs_only_override
    if docs_only:
        cmd.append("--docs-only")

    def _append_dirs(flag: str, key: str) -> None:
        raw_value = profile.get(key)
        if not isinstance(raw_value, list):
            return
        values = [str(item).strip() for item in raw_value if str(item).strip()]
        if values:
            cmd.extend([flag, ",".join(values)])

    _append_dirs("--docs-dirs", "docs_dirs")
    _append_dirs("--tutorial-dirs", "tutorial_dirs")
    _append_dirs("--test-dirs", "test_dirs")
    _append_dirs("--source-dirs", "source_dirs")

    print("[compile] deterministic generation: python generate_skills_folder.py")
    try:
        completed = cli.subprocess.run(cmd, check=False, cwd=str(project_root))
    except FileNotFoundError as exc:
        raise cli.PackageError(
            f"Python executable not found for compile generation: {cli.sys.executable}"
        ) from exc

    if completed.returncode != 0:
        raise cli.PackageError(
            f"skills generation failed with exit code {completed.returncode}."
        )

    return {
        "status": "ok",
        "return_code": int(completed.returncode),
        "docs_only": docs_only,
        "max_skills": int(max_skills),
        "generator_script": str(script_path.relative_to(project_root)),
    }


def _extract_backtick_tokens(text: str) -> list[str]:
    if not text:
        return []
    tokens = re.findall(r"`([^`\n]+)`", text)
    cleaned: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        candidate = token.strip().rstrip(".,:;")
        if not candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        cleaned.append(candidate)
    return cleaned


def _looks_like_path(token: str) -> bool:
    if not token:
        return False
    if token.startswith(("http://", "https://", "<", "--")):
        return False
    if token.startswith("$"):
        return False
    if " " in token:
        return False
    if token in {"None", "none"}:
        return False
    if token.startswith("rg") or token.startswith("python"):
        return False
    return "/" in token or token.endswith((".md", ".rst", ".txt", ".py", ".cpp", ".c"))


def _resolve_existing_path(
    project_root: Path, token: str, *, context_dirs: list[Path] | None = None
) -> Path | None:
    if not _looks_like_path(token):
        return None
    candidate = Path(token)
    if candidate.is_absolute():
        try:
            resolved_abs = candidate.resolve()
        except OSError:
            return None
        try:
            resolved_abs.relative_to(project_root)
        except ValueError:
            return None
        if resolved_abs.exists():
            return resolved_abs
        return None

    candidate_paths: list[Path] = []
    if isinstance(context_dirs, list):
        for context in context_dirs:
            if isinstance(context, Path):
                candidate_paths.append((context / candidate).resolve())
    candidate_paths.append((project_root / candidate).resolve())

    seen: set[Path] = set()
    for resolved in candidate_paths:
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            resolved.relative_to(project_root)
        except ValueError:
            continue
        if resolved.exists():
            return resolved
    return None


def _is_source_file(path: Path, *, source_roots: list[Path]) -> bool:
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix in SOURCE_EXTENSIONS or path.name.lower() in SOURCE_BASENAMES:
            return True
    for root in source_roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _extract_doc_signals(path: Path) -> tuple[list[str], list[str], list[str]]:
    text = _read_text(path)
    if not text:
        return [], [], []

    headings: list[str] = []
    commands: list[str] = []
    warnings: list[str] = []
    heading_re = re.compile(r"^\s{0,3}#{1,3}\s+(.+?)\s*$")
    command_markers = ("python ", "mpirun ", "mpiexec ", "sbatch ", "srun ", "./")
    warning_re = re.compile(
        r"\b(warning|caution|important|pitfall|error|stability|convergence)\b",
        re.IGNORECASE,
    )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = heading_re.match(line)
        if match:
            headings.append(match.group(1).strip())
        lowered = line.lower()
        if line.startswith("$") or any(lowered.startswith(m) for m in command_markers):
            commands.append(line)
        if warning_re.search(line):
            warnings.append(line)

    def _dedupe(items: list[str], *, limit: int) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= limit:
                break
        return out

    return (
        _dedupe(headings, limit=10),
        _dedupe(commands, limit=12),
        _dedupe(warnings, limit=12),
    )


def _doc_count_from_doc_map(path: Path) -> int:
    text = _read_text(path)
    if not text:
        return 0
    match = re.search(r"Total docs grouped in this topic:\s*(\d+)", text)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _collect_skill_dirs(skills_root: Path) -> list[Path]:
    if not skills_root.is_dir():
        return []
    return sorted(
        [
            path
            for path in skills_root.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and (path / "SKILL.md").is_file()
        ],
        key=lambda p: p.name,
    )


def _select_core_skill_dirs(skills_root: Path, core_skill_count: int) -> list[Path]:
    skill_dirs = _collect_skill_dirs(skills_root)
    topic_dirs = [path for path in skill_dirs if not path.name.endswith("-index")]
    ranked = sorted(
        topic_dirs,
        key=lambda path: (
            _doc_count_from_doc_map(path / "references" / "doc_map.md"),
            path.name,
        ),
        reverse=True,
    )
    if core_skill_count <= 0:
        core_skill_count = min(6, len(ranked))
    return ranked[:core_skill_count]


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _safe_relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _slugify_token(raw: str, *, default: str = "paper") -> str:
    token = re.sub(r"[^a-zA-Z0-9]+", "_", str(raw or "").strip().lower()).strip("_")
    if not token:
        token = default
    return token[:48]


def _sanitize_plan_figure_id(raw_id: object, *, index: int, used: set[str]) -> str:
    token = _slugify_token(str(raw_id or ""), default=f"fig_{index:03d}")
    if not token.startswith("fig_"):
        token = f"fig_{token}"
    candidate = token
    suffix = 2
    while candidate in used:
        candidate = f"{token}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _normalize_string_list(raw: object) -> list[str]:
    if isinstance(raw, list):
        items = [str(item).strip() for item in raw]
        return [item for item in items if item]
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    return []


def _extract_recompile_paper_plan_from_assistant_text(
    assistant_text: str,
) -> dict[str, object] | None:
    cli = _cli()
    parsed = cli._extract_tagged_json_payload(
        assistant_text, token_re=cli.RECOMPILE_PAPER_PLAN_TOKEN_RE
    )
    if not isinstance(parsed, dict):
        return None
    return parsed


def _normalize_recompile_paper_plan(
    raw_plan: object,
    *,
    paper_source: str,
    package_id: str,
    scope_comment: str | None = None,
) -> dict[str, object]:
    cli = _cli()
    if not isinstance(raw_plan, dict):
        raise cli.PackageError("Paper plan must be a JSON object.")

    used_packages = _normalize_string_list(raw_plan.get("used_packages"))
    if package_id not in used_packages:
        used_packages.insert(0, package_id)
    used_packages = list(dict.fromkeys(used_packages))
    scope_comment_text = " ".join(str(scope_comment or "").split()).strip()
    scope_mode = "comment_filtered" if scope_comment_text else "all_results"

    raw_figures = raw_plan.get("figures")
    if not isinstance(raw_figures, list) or not raw_figures:
        raise cli.PackageError("Paper plan must contain a non-empty `figures` list.")

    figures: list[dict[str, object]] = []
    used_ids: set[str] = set()
    for index, raw_figure in enumerate(raw_figures, start=1):
        if not isinstance(raw_figure, dict):
            raise cli.PackageError(
                f"Paper plan figure {index} must be a JSON object."
            )
        fig_id = _sanitize_plan_figure_id(
            raw_figure.get("id"), index=index, used=used_ids
        )
        title = str(raw_figure.get("title") or "").strip() or f"Figure {index}"
        targets = _normalize_string_list(raw_figure.get("targets"))
        if not targets:
            targets = [title]
        objective = (
            str(raw_figure.get("objective") or "").strip()
            or "Reproduce this figure according to the manuscript requirements."
        )
        simulation_config = _normalize_string_list(raw_figure.get("simulation_config"))
        parameter_requirements = _normalize_string_list(
            raw_figure.get("parameter_requirements")
        )
        required_packages = _normalize_string_list(raw_figure.get("required_packages"))
        if not required_packages:
            required_packages = [package_id]
        expected_artifacts = _normalize_string_list(raw_figure.get("expected_artifacts"))
        acceptance_checks = _normalize_string_list(raw_figure.get("acceptance_checks"))
        if not acceptance_checks:
            acceptance_checks = ["Generated results are consistent with manuscript claims."]

        figures.append(
            {
                "id": fig_id,
                "title": title,
                "targets": targets,
                "objective": objective,
                "simulation_config": simulation_config,
                "parameter_requirements": parameter_requirements,
                "required_packages": required_packages,
                "expected_artifacts": expected_artifacts,
                "acceptance_checks": acceptance_checks,
            }
        )

    return {
        "version": 1,
        "paper_source": str(raw_plan.get("paper_source") or paper_source).strip()
        or paper_source,
        "scope_mode": scope_mode,
        "scope_comment": scope_comment_text or None,
        "used_packages": used_packages,
        "global_assumptions": _normalize_string_list(raw_plan.get("global_assumptions")),
        "figures": figures,
    }


def _write_recompile_paper_plan(
    project_root: Path,
    *,
    paper_plan: dict[str, object],
) -> str:
    cli = _cli()
    plan_path = project_root / cli.RECOMPILE_PAPER_PLAN_REL_PATH
    _write_text(plan_path, json.dumps(paper_plan, indent=2, sort_keys=True))
    return _safe_relative_path(plan_path, project_root)


def _derive_recompile_paper_skill_id(
    project_root: Path,
    *,
    package_id: str,
    doc_path: Path,
    comment: str | None = None,
    paper_plan: dict[str, object] | None = None,
) -> str:
    def _scope_tokens_from_text(text: str) -> list[str]:
        tokens: list[str] = []
        for token in re.findall(r"[a-zA-Z0-9]+", text.lower()):
            if not token:
                continue
            if token in PAPER_SKILL_SCOPE_STOPWORDS:
                continue
            if token.startswith("fig"):
                continue
            if re.fullmatch(r"[0-9]+[a-z]?", token):
                continue
            if len(token) <= 2 and token not in {"h2", "h2o"}:
                continue
            tokens.append(token)
        return tokens

    def _collect_plan_scope_texts(plan: dict[str, object] | None) -> list[str]:
        texts: list[str] = []
        if not isinstance(plan, dict):
            return texts
        scope_comment = str(plan.get("scope_comment") or "").strip()
        if scope_comment:
            texts.append(scope_comment)
        assumptions = plan.get("global_assumptions")
        if isinstance(assumptions, list):
            for item in assumptions[:4]:
                if isinstance(item, str) and item.strip():
                    texts.append(item.strip())
        figures = plan.get("figures")
        if isinstance(figures, list):
            for figure in figures[:5]:
                if not isinstance(figure, dict):
                    continue
                for key in ("title", "objective"):
                    value = str(figure.get(key) or "").strip()
                    if value:
                        texts.append(value)
                for key in ("targets", "parameter_requirements"):
                    value = figure.get(key)
                    if isinstance(value, list):
                        for item in value[:3]:
                            if isinstance(item, str) and item.strip():
                                texts.append(item.strip())
        return texts

    def _brief_scope_slug() -> str:
        ranked_tokens: list[str] = []
        seen: set[str] = set()
        candidate_texts = []
        comment_text = " ".join(str(comment or "").split()).strip()
        if comment_text:
            candidate_texts.append(comment_text)
        candidate_texts.extend(_collect_plan_scope_texts(paper_plan))
        if not candidate_texts:
            candidate_texts.append(doc_path.stem)

        for text in candidate_texts:
            for token in _scope_tokens_from_text(text):
                if token in seen:
                    continue
                seen.add(token)
                ranked_tokens.append(token)
                if len(ranked_tokens) >= 5:
                    break
            if len(ranked_tokens) >= 5:
                break

        if not ranked_tokens:
            package_tokens = _scope_tokens_from_text(package_id)
            ranked_tokens.extend(package_tokens[:2])
        if not ranked_tokens:
            ranked_tokens = ["scope"]
        return _slugify_token("_".join(ranked_tokens), default="scope")

    base_slug = _brief_scope_slug()[:36]
    base = f"paper_tutorial_{base_slug}"
    skills_root = project_root / "skills"
    candidate = base
    suffix = 2
    while (skills_root / candidate).exists():
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _ensure_recompile_paper_skill_scaffold(
    project_root: Path,
    *,
    skill_id: str,
    paper_plan: dict[str, object],
) -> dict[str, object]:
    skill_root = project_root / "skills" / skill_id
    references_dir = skill_root / "references"
    assets_dir = skill_root / "assets"
    references_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    skill_md = skill_root / "SKILL.md"
    if not skill_md.exists():
        _write_text(
            skill_md,
            "\n".join(
                [
                    f"# {skill_id}",
                    "",
                    "## Scope",
                    "- Paper tutorial generated by `fermilink recompile --doc ...`.",
                    "- Keep workflows self-contained within this skill directory (especially `assets/`).",
                    "",
                    "## Core Simulation Strategy",
                    "- Summarize the minimal simulation protocol used across the paper.",
                    "- Include concrete parameter anchors (ensembles, step counts, cadence, convergence checks).",
                    "",
                    "## Minimal Execution Recipes",
                    "- Add concrete command templates for simulation, postprocessing, and plotting.",
                    "- Run simulations under `projects/YYYY-MM-DD-<scope>/` (copy inputs/scripts from this skill `assets/` into that run directory).",
                    "- Do not create directories under this tutorial skill.",
                    "",
                    "## Figure Routing",
                    "- For each figure id, add one bullet with a brief scope summary "
                    "(scientific aim/condition) plus the corresponding playbook path in `playbooks/`.",
                    "",
                    "## Beyond Manuscript Exploration",
                    "- Add safe parameter sweeps and extension ideas to explore new science.",
                    "- Include guardrails so exploration remains physically meaningful and reproducible.",
                ]
            ),
        )

    doc_map = references_dir / "doc_map.md"
    if not doc_map.exists():
        targets = []
        figures = paper_plan.get("figures")
        if isinstance(figures, list):
            for item in figures[:12]:
                if isinstance(item, dict):
                    for token in _normalize_string_list(item.get("targets")):
                        targets.append(token)
        target_lines = [f"- {item}" for item in targets] if targets else [
            "- (populate in pass 2)"
        ]
        _write_text(
            doc_map,
            "\n".join(
                [
                    f"# Doc Map: {skill_id}",
                    "",
                    "## Paper Targets",
                    *target_lines,
                ]
            ),
        )

    source_map = references_dir / "source_map.md"
    if not source_map.exists():
        _write_text(
            source_map,
            "\n".join(
                [
                    f"# Source Map: {skill_id}",
                    "",
                    "- Add package source entry points needed for manuscript reproduction.",
                ]
            ),
        )

    assets_readme = assets_dir / "README.md"
    if not assets_readme.exists():
        _write_text(
            assets_readme,
            "\n".join(
                [
                    "# Paper Tutorial Assets",
                    "",
                    "Store staged supplementary files required by this paper tutorial skill.",
                ]
            ),
        )
    return {
        "skill_id": skill_id,
        "skill_root": _safe_relative_path(skill_root, project_root),
    }


def _initialize_recompile_paper_sidecar_files(
    project_root: Path,
    *,
    paper_plan: dict[str, object],
    paper_skill_id: str,
) -> dict[str, object]:
    cli = _cli()
    map_path = project_root / cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH
    manifest_path = project_root / cli.RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH
    figures_payload: list[dict[str, object]] = []
    figures = paper_plan.get("figures")
    if isinstance(figures, list):
        for item in figures:
            if not isinstance(item, dict):
                continue
            fig_id = str(item.get("id") or "").strip()
            if not fig_id:
                continue
            figures_payload.append(
                {
                    "id": fig_id,
                    "files": [],
                    "unknowns": ["Pending pass-2 data mapping update."],
                }
            )
    map_payload = {
        "version": 1,
        "generated_at_utc": cli._utc_now_z(),
        "figures": figures_payload,
        "global_unknowns": [],
    }
    _write_text(map_path, json.dumps(map_payload, indent=2, sort_keys=True))

    manifest_payload = {
        "version": 1,
        "generated_at_utc": cli._utc_now_z(),
        "skill_id": paper_skill_id,
        "created_files": [
            f"skills/{paper_skill_id}/SKILL.md",
            f"skills/{paper_skill_id}/references/doc_map.md",
            f"skills/{paper_skill_id}/references/source_map.md",
            f"skills/{paper_skill_id}/assets/README.md",
        ],
        "index_updated": False,
    }
    _write_text(manifest_path, json.dumps(manifest_payload, indent=2, sort_keys=True))
    return {
        "figure_data_map": _safe_relative_path(map_path, project_root),
        "paper_skill_manifest": _safe_relative_path(manifest_path, project_root),
    }


def _snapshot_recompile_paper_skills(project_root: Path) -> dict[str, str]:
    skills_root = project_root / "skills"
    snapshot: dict[str, str] = {}
    if not skills_root.is_dir():
        return snapshot
    for path in sorted(skills_root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = _safe_relative_path(path, project_root).replace("\\", "/")
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = "unreadable"
        snapshot[rel_path] = digest
    return snapshot


def _diff_recompile_paper_skills_snapshot(
    before: dict[str, str],
    after: dict[str, str],
) -> dict[str, list[str]]:
    before_paths = set(before.keys())
    after_paths = set(after.keys())
    added = sorted(after_paths - before_paths)
    deleted = sorted(before_paths - after_paths)
    modified = sorted(
        path for path in before_paths.intersection(after_paths) if before[path] != after[path]
    )
    return {
        "added": added,
        "deleted": deleted,
        "modified": modified,
        "changed": sorted(set(added + deleted + modified)),
    }


def _assert_recompile_paper_change_scope(
    *,
    change_diff: dict[str, list[str]],
    allowed_prefixes: list[str],
    stage_label: str,
) -> None:
    cli = _cli()
    allowed = [prefix.replace("\\", "/").rstrip("/") + "/" for prefix in allowed_prefixes]
    violations: list[str] = []
    for path in change_diff.get("changed", []):
        normalized = str(path or "").replace("\\", "/")
        if any(
            normalized == prefix.rstrip("/") or normalized.startswith(prefix)
            for prefix in allowed
        ):
            continue
        violations.append(normalized)
    if violations:
        preview = "; ".join(violations[:12])
        raise cli.PackageError(
            f"{stage_label} modified files outside allowed paper-mode scope: {preview}"
        )


def _build_recompile_paper_context(
    project_root: Path,
    *,
    doc_path: Path,
    comment: str | None = None,
    data_context: dict[str, object] | None = None,
    staged_assets: dict[str, object] | None = None,
    paper_plan: dict[str, object] | None = None,
    paper_skill_id: str | None = None,
) -> dict[str, object]:
    cli = _cli()
    paper_root = project_root / cli.COMPILE_EVIDENCE_DIR_REL_PATH / "paper_context"
    paper_root.mkdir(parents=True, exist_ok=True)
    context_path = paper_root / "paper_context.json"
    normalized_comment = " ".join(str(comment or "").split()).strip()
    objective_source = "comment" if normalized_comment else "default_full_reproduction"
    objective_text = (
        normalized_comment
        if normalized_comment
        else (
            "Reproduce all major manuscript results with package-local skills and "
            "assets so key outcomes remain executable without external paper/data paths."
        )
    )

    try:
        doc_bytes = doc_path.read_bytes()
    except OSError as exc:
        raise cli.PackageError(f"Failed to read --doc file: {doc_path}: {exc}") from exc

    doc_sha256 = hashlib.sha256(doc_bytes).hexdigest()
    doc_relpath = _safe_relative_path(doc_path, project_root)
    doc_preview = doc_bytes[:12_000].decode("utf-8", errors="replace")

    normalized_data_context: dict[str, object] | None = None
    if isinstance(data_context, dict):
        normalized_data_context = {
            "enabled": bool(data_context.get("enabled")),
            "source_path": str(data_context.get("source_path") or ""),
            "source_path_input": str(data_context.get("source_path_input") or ""),
            "read_only": bool(data_context.get("read_only", True)),
            "artifacts": data_context.get("artifacts")
            if isinstance(data_context.get("artifacts"), dict)
            else {},
            "manifest_fingerprint": str(data_context.get("manifest_fingerprint") or ""),
            "manifest_full_fingerprint": str(
                data_context.get("manifest_full_fingerprint") or ""
            ),
            "manifest_compact_fingerprint": str(
                data_context.get("manifest_compact_fingerprint") or ""
            ),
        }

    payload: dict[str, object] = {
        "version": 1,
        "mode": "recompile_paper",
        "generated_at_utc": cli._utc_now_z(),
        "objective": objective_text,
        "objective_source": objective_source,
        "comment": normalized_comment or None,
        "doc": {
            "path": doc_relpath,
            "absolute_path": str(doc_path),
            "sha256": doc_sha256,
            "bytes": len(doc_bytes),
            "preview_utf8": doc_preview,
        },
        "data_context": normalized_data_context,
        "staged_assets": staged_assets if isinstance(staged_assets, dict) else None,
        "paper_plan_path": cli.RECOMPILE_PAPER_PLAN_REL_PATH,
        "paper_plan": paper_plan if isinstance(paper_plan, dict) else None,
        "paper_tutorial_skill_id": str(paper_skill_id or "").strip() or None,
    }
    _write_text(context_path, json.dumps(payload, indent=2, sort_keys=True))
    payload["context_path"] = _safe_relative_path(context_path, project_root)
    return payload


def _stage_recompile_paper_assets(
    project_root: Path,
    *,
    data_context: dict[str, object] | None = None,
    max_files: int = 24,
    max_total_bytes: int = 134_217_728,
) -> dict[str, object]:
    cli = _cli()
    paper_root = project_root / cli.RECOMPILE_PAPER_CONTEXT_DIR_REL_PATH
    stage_root = project_root / cli.RECOMPILE_PAPER_STAGED_ASSETS_DIR_REL_PATH
    manifest_path = project_root / cli.RECOMPILE_PAPER_STAGED_ASSETS_MANIFEST_REL_PATH
    paper_root.mkdir(parents=True, exist_ok=True)

    payload: dict[str, object] = {
        "version": 1,
        "generated_at_utc": cli._utc_now_z(),
        "enabled": False,
        "stage_root": _safe_relative_path(stage_root, project_root),
        "manifest_path": _safe_relative_path(manifest_path, project_root),
        "max_files": int(max_files),
        "max_total_bytes": int(max_total_bytes),
        "source_data_dir": "",
        "source_manifest": "",
        "staged_files": [],
        "skipped": [],
        "staged_count": 0,
        "staged_total_bytes": 0,
    }

    if not isinstance(data_context, dict) or not bool(data_context.get("enabled")):
        _write_text(manifest_path, json.dumps(payload, indent=2, sort_keys=True))
        return payload

    source_data_dir = Path(str(data_context.get("source_path") or "").strip())
    artifacts = data_context.get("artifacts")
    if (
        not str(source_data_dir)
        or not source_data_dir.is_dir()
        or not isinstance(artifacts, dict)
    ):
        payload["skipped"] = ["data_context is enabled but source/artifacts are invalid."]
        _write_text(manifest_path, json.dumps(payload, indent=2, sort_keys=True))
        return payload

    manifest_rel = str(artifacts.get("manifest_compact") or artifacts.get("manifest") or "").strip()
    if not manifest_rel:
        payload["skipped"] = ["compact manifest path is missing from data_context artifacts."]
        _write_text(manifest_path, json.dumps(payload, indent=2, sort_keys=True))
        return payload

    compact_manifest_path = project_root / manifest_rel
    try:
        compact_manifest_raw = json.loads(
            compact_manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        payload["skipped"] = [f"failed to read compact manifest: {exc}"]
        _write_text(manifest_path, json.dumps(payload, indent=2, sort_keys=True))
        return payload

    files_payload = compact_manifest_raw.get("files") if isinstance(compact_manifest_raw, dict) else None
    files = files_payload if isinstance(files_payload, list) else []

    shutil.rmtree(stage_root, ignore_errors=True)
    stage_root.mkdir(parents=True, exist_ok=True)
    staged_files: list[dict[str, object]] = []
    skipped: list[str] = []
    total_bytes = 0

    for item in files:
        if not isinstance(item, dict):
            continue
        rel_path = str(item.get("path") or "").strip().replace("\\", "/")
        if not rel_path:
            continue
        rel_candidate = Path(rel_path)
        if rel_candidate.is_absolute() or ".." in rel_candidate.parts:
            skipped.append(f"unsafe path: {rel_path}")
            continue

        source_file = (source_data_dir / rel_candidate).resolve()
        try:
            source_file.relative_to(source_data_dir.resolve())
        except ValueError:
            skipped.append(f"path escapes source root: {rel_path}")
            continue
        if not source_file.is_file():
            skipped.append(f"missing source file: {rel_path}")
            continue

        file_size = int(source_file.stat().st_size)
        if len(staged_files) >= int(max_files):
            skipped.append("staging capped by max_files.")
            break
        if total_bytes + file_size > int(max_total_bytes):
            skipped.append("staging capped by max_total_bytes.")
            break

        target_file = stage_root / rel_candidate
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
        try:
            sha256 = hashlib.sha256(target_file.read_bytes()).hexdigest()
        except OSError:
            sha256 = ""

        staged_files.append(
            {
                "source_path": rel_path,
                "staged_path": _safe_relative_path(target_file, project_root),
                "size": file_size,
                "sha256": sha256,
                "representative_score": item.get("representative_score"),
                "family_count": int(item.get("family_count") or 1),
            }
        )
        total_bytes += file_size

    payload["enabled"] = True
    payload["source_data_dir"] = str(source_data_dir)
    payload["source_manifest"] = _safe_relative_path(compact_manifest_path, project_root)
    payload["staged_files"] = staged_files
    payload["skipped"] = skipped
    payload["staged_count"] = len(staged_files)
    payload["staged_total_bytes"] = total_bytes
    _write_text(manifest_path, json.dumps(payload, indent=2, sort_keys=True))
    return payload


def _build_compile_evidence_bundle(
    project_root: Path,
    *,
    core_skill_count: int,
) -> dict[str, object]:
    cli = _cli()
    skills_root = project_root / "skills"
    if not skills_root.is_dir():
        raise cli.PackageError("Missing skills/ folder before enrichment stage.")

    evidence_root = project_root / cli.COMPILE_EVIDENCE_DIR_REL_PATH
    shutil.rmtree(evidence_root, ignore_errors=True)
    evidence_root.mkdir(parents=True, exist_ok=True)

    selected_skill_dirs = _select_core_skill_dirs(skills_root, core_skill_count)
    written_files: list[str] = []
    core_skill_names: list[str] = []
    for skill_dir in selected_skill_dirs:
        core_skill_names.append(skill_dir.name)
        doc_map_path = skill_dir / "references" / "doc_map.md"
        source_map_path = skill_dir / "references" / "source_map.md"

        doc_tokens = _extract_backtick_tokens(_read_text(doc_map_path))
        source_tokens = _extract_backtick_tokens(_read_text(source_map_path))

        docs: list[Path] = []
        for token in doc_tokens:
            resolved = _resolve_existing_path(
                project_root,
                token,
                context_dirs=[doc_map_path.parent, skill_dir],
            )
            if resolved is None or not resolved.is_file():
                continue
            if resolved.suffix.lower() not in DOC_EXTENSIONS:
                continue
            docs.append(resolved)

        source_files: list[Path] = []
        for token in source_tokens:
            resolved = _resolve_existing_path(
                project_root,
                token,
                context_dirs=[source_map_path.parent, skill_dir],
            )
            if resolved is None or not resolved.is_file():
                continue
            source_files.append(resolved)

        headings: list[str] = []
        commands: list[str] = []
        warnings: list[str] = []
        for doc_path in docs[:8]:
            h_items, c_items, w_items = _extract_doc_signals(doc_path)
            headings.extend(h_items)
            commands.extend(c_items)
            warnings.extend(w_items)

        def _compact(items: list[str], *, limit: int) -> list[str]:
            out: list[str] = []
            seen: set[str] = set()
            for item in items:
                key = item.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
                if len(out) >= limit:
                    break
            return out

        docs_lines = [f"- `{path.relative_to(project_root)}`" for path in docs[:12]]
        source_lines = [
            f"- `{path.relative_to(project_root)}`" for path in source_files[:20]
        ]
        heading_lines = [f"- {item}" for item in _compact(headings, limit=12)]
        command_lines = [f"- {item}" for item in _compact(commands, limit=12)]
        warning_lines = [f"- {item}" for item in _compact(warnings, limit=12)]

        evidence_lines = [
            f"# Evidence: {skill_dir.name}",
            "",
            "## Primary docs",
            *(docs_lines if docs_lines else ["- (none detected)"]),
            "",
            "## Primary source entry points",
            *(source_lines if source_lines else ["- (none detected)"]),
            "",
            "## Extracted headings",
            *(heading_lines if heading_lines else ["- (none extracted)"]),
            "",
            "## Executable command hints",
            *(command_lines if command_lines else ["- (none extracted)"]),
            "",
            "## Warnings and pitfalls",
            *(warning_lines if warning_lines else ["- (none extracted)"]),
        ]
        evidence_text = "\n".join(evidence_lines)
        evidence_path = evidence_root / f"{skill_dir.name}.md"
        _write_text(evidence_path, evidence_text)
        written_files.append(str(evidence_path.relative_to(project_root)))

    manifest = {
        "evidence_dir": str(evidence_root.relative_to(project_root)),
        "core_skills": core_skill_names,
        "files": written_files,
    }
    manifest_path = evidence_root / "manifest.json"
    _write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def _iter_source_files_for_coverage(source_roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for source_root in source_roots:
        for path in source_root.rglob("*"):
            if not path.is_file():
                continue
            lowered_parts = {part.lower() for part in path.parts}
            if lowered_parts.intersection(SOURCE_IGNORE_DIR_NAMES):
                continue
            if lowered_parts.intersection(SOURCE_NON_IMPL_DIR_NAMES):
                continue
            suffix = path.suffix.lower()
            basename = path.name.lower()
            if suffix not in SOURCE_EXTENSIONS and basename not in SOURCE_BASENAMES:
                continue
            files.append(path.resolve())
    return sorted(set(files))


def _collect_referenced_source_files(
    project_root: Path,
    *,
    skills_root: Path,
    source_roots: list[Path],
) -> set[Path]:
    references: set[Path] = set()
    for source_map_path in sorted(skills_root.rglob("references/source_map.md")):
        skill_dir = source_map_path.parent.parent
        source_map_text = _read_text(source_map_path)
        for token in _extract_backtick_tokens(source_map_text):
            if not _looks_like_path(token):
                continue
            resolved = _resolve_existing_path(
                project_root,
                token,
                context_dirs=[source_map_path.parent, skill_dir],
            )
            if resolved is None:
                continue
            if _is_source_file(resolved, source_roots=source_roots):
                references.add(resolved.resolve())
    return references


def _extract_source_symbol_hints(path: Path, *, limit: int = 8) -> list[str]:
    text = _read_text(path)
    if not text:
        return []
    patterns = (
        re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
        re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[\(:]", re.MULTILINE),
        re.compile(
            r"^\s*(?:[A-Za-z_][A-Za-z0-9_:\<\>\*\&\s]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;\)]*\)\s*\{?",
            re.MULTILINE,
        ),
    )
    hints: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            symbol = str(match.group(1) or "").strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            hints.append(symbol)
            if len(hints) >= limit:
                return hints
    return hints


def _build_recompile_evidence_bundle(
    project_root: Path,
    *,
    profile: dict[str, object],
    core_skill_count: int,
) -> dict[str, object]:
    cli = _cli()
    manifest = _build_compile_evidence_bundle(
        project_root,
        core_skill_count=core_skill_count,
    )
    skills_root = project_root / "skills"
    evidence_root = project_root / cli.COMPILE_EVIDENCE_DIR_REL_PATH
    coverage_report_path = project_root / cli.RECOMPILE_COVERAGE_REL_PATH
    source_roots = _inferred_source_roots(project_root, profile)

    if not source_roots:
        lines = [
            "# Recompile source coverage report",
            "",
            "No source roots discovered from compile profile.",
            "Use docs-only recompile behavior or update `skills/.compile_profile.json`.",
        ]
        _write_text(coverage_report_path, "\n".join(lines))
        manifest["coverage_report"] = str(coverage_report_path.relative_to(project_root))
        manifest["source_inventory"] = {
            "source_roots": [],
            "total_source_files": 0,
            "referenced_source_files": 0,
            "uncovered_source_files": 0,
        }
        return manifest

    source_candidates = _iter_source_files_for_coverage(source_roots)
    referenced_sources = _collect_referenced_source_files(
        project_root,
        skills_root=skills_root,
        source_roots=source_roots,
    )
    uncovered = [
        path
        for path in source_candidates
        if path.resolve() not in referenced_sources
    ]

    uncovered_lines: list[str] = []
    for path in uncovered[:80]:
        symbol_hints = _extract_source_symbol_hints(path, limit=6)
        if symbol_hints:
            uncovered_lines.append(
                f"- `{path.relative_to(project_root)}` | symbols: {', '.join(symbol_hints)}"
            )
        else:
            uncovered_lines.append(f"- `{path.relative_to(project_root)}`")

    source_root_lines = [f"- `{root.relative_to(project_root)}`" for root in source_roots]
    coverage_lines = [
        "# Recompile source coverage report",
        "",
        "## Source roots",
        *(source_root_lines if source_root_lines else ["- (none)"]),
        "",
        "## Coverage summary",
        f"- Total source files discovered: {len(source_candidates)}",
        f"- Source files referenced by skills source maps: {len(referenced_sources)}",
        f"- Potential uncovered source files: {len(uncovered)}",
        "",
        "## Potential uncovered source files/functions",
        *(
            uncovered_lines
            if uncovered_lines
            else ["- No uncovered source files detected from current source-map links."]
        ),
    ]
    _write_text(coverage_report_path, "\n".join(coverage_lines))

    existing_files = manifest.get("files")
    files_list = list(existing_files) if isinstance(existing_files, list) else []
    coverage_rel = str(coverage_report_path.relative_to(project_root))
    if coverage_rel not in files_list:
        files_list.append(coverage_rel)
    manifest["files"] = files_list
    manifest["coverage_report"] = coverage_rel
    manifest["source_inventory"] = {
        "source_roots": [str(root.relative_to(project_root)) for root in source_roots],
        "total_source_files": len(source_candidates),
        "referenced_source_files": len(referenced_sources),
        "uncovered_source_files": len(uncovered),
    }
    if uncovered:
        manifest["uncovered_source_examples"] = [
            str(path.relative_to(project_root)) for path in uncovered[:20]
        ]

    manifest_path = evidence_root / "manifest.json"
    _write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def _inferred_source_roots(project_root: Path, profile: dict[str, object]) -> list[Path]:
    roots: list[Path] = []
    raw_source_dirs = profile.get("source_dirs")
    if isinstance(raw_source_dirs, list):
        for item in raw_source_dirs:
            if not isinstance(item, str) or not item.strip():
                continue
            resolved = (project_root / item).resolve()
            if resolved.exists() and resolved.is_dir():
                roots.append(resolved)

    if roots:
        return roots

    for name in (
        "src",
        "source",
        "lib",
        "python",
        "fortran",
        "cpp",
        "cxx",
        "modules",
        "pkg",
    ):
        resolved = (project_root / name).resolve()
        if resolved.exists() and resolved.is_dir():
            roots.append(resolved)
    return roots


def _validate_skill_playbook(skill_md_path: Path) -> list[str]:
    errors: list[str] = []
    text = _read_text(skill_md_path)
    lowered = text.lower()
    if "## high-signal playbook" not in lowered:
        errors.append(f"{skill_md_path}: missing `## High-Signal Playbook` section.")
        return errors
    for token in PLAYBOOK_REQUIRED_TOKENS:
        if token not in lowered:
            errors.append(
                f"{skill_md_path}: playbook missing required element containing '{token}'."
            )
    return errors


def _extract_markdown_section(text: str, heading: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    heading_re = re.compile(rf"^\s*##\s+{re.escape(heading)}\s*$", re.IGNORECASE)
    next_heading_re = re.compile(r"^\s*##\s+.+$")
    start_idx = -1
    for idx, line in enumerate(lines):
        if heading_re.match(line):
            start_idx = idx + 1
            break
    if start_idx < 0:
        return ""
    collected: list[str] = []
    for line in lines[start_idx:]:
        if next_heading_re.match(line):
            break
        collected.append(line)
    return "\n".join(collected).strip()


def _figure_route_has_scope_description(route_line: str, fig_id: str) -> bool:
    if not route_line.strip():
        return False
    lowered = route_line.lower()
    normalized = lowered.replace("\\", "/")

    scope_match = re.search(r"scope\s*:\s*([^;\n]+)", normalized)
    if scope_match:
        scope_tokens = re.findall(r"[a-z]{3,}", scope_match.group(1))
        scope_tokens = [
            token
            for token in scope_tokens
            if token not in PAPER_SKILL_SCOPE_STOPWORDS
            and token not in {"figure", "fig", "playbook", "playbooks"}
        ]
        if len(scope_tokens) >= 2:
            return True

    cleaned = re.sub(r"`[^`\n]+`", " ", normalized)
    cleaned = cleaned.replace(fig_id.lower(), " ")
    cleaned = re.sub(r"playbooks?/[^)\s`]+", " ", cleaned)
    cleaned = re.sub(r"figure\s*\d+[a-z]?(?:[-–]\d+[a-z]?)?", " ", cleaned)
    cleaned = re.sub(r"\b(?:si|sec|section|appendix|fig|figure|playbook|playbooks)\b", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    tokens = [
        token
        for token in cleaned.split()
        if len(token) >= 3
        and token not in PAPER_SKILL_SCOPE_STOPWORDS
        and not re.fullmatch(r"\d+[a-z]?", token)
    ]
    return len(tokens) >= 3


def _iter_markdown_path_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    if not text:
        return candidates
    for token in re.findall(r"`([^`\n]+)`", text):
        cleaned = str(token).strip().strip("()[]{}.,;:")
        if cleaned:
            candidates.append(cleaned)
    for token in re.findall(r"\[[^\]]*?\]\(([^)\n]+)\)", text):
        cleaned = str(token).strip().strip("()[]{}.,;:")
        if cleaned:
            candidates.append(cleaned)
    return candidates


def _is_path_escape_from_root(
    raw_path: str,
    *,
    markdown_path: Path,
    tutorial_root: Path,
    project_root: Path,
) -> bool:
    token = str(raw_path or "").strip().strip("\"'")
    if not token:
        return False
    lowered = token.lower()
    if "://" in lowered or lowered.startswith(("mailto:", "#")):
        return False
    if any(ch in token for ch in ("*", "{", "}", "$", "<", ">", "|")):
        return False
    if "\\" in token:
        token = token.replace("\\", "/")
    if "/" not in token and not token.startswith("."):
        return False
    if token.startswith("skills/"):
        candidate = (project_root / token).resolve()
    else:
        candidate = (markdown_path.parent / token).resolve()
    try:
        candidate.relative_to(tutorial_root.resolve())
    except ValueError:
        return True
    return False


def _validate_recompile_paper_outputs(
    project_root: Path,
    *,
    paper_plan: dict[str, object] | None = None,
    paper_skill_id: str | None = None,
    doc_path: Path | None = None,
    data_dir: Path | None = None,
    staged_assets: dict[str, object] | None = None,
) -> dict[str, object]:
    cli = _cli()
    skills_root = project_root / "skills"
    errors: list[str] = []
    warnings: list[str] = []

    plan_payload = paper_plan if isinstance(paper_plan, dict) else None
    if plan_payload is None:
        plan_path = project_root / cli.RECOMPILE_PAPER_PLAN_REL_PATH
        if plan_path.is_file():
            try:
                loaded = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                plan_payload = loaded
    if not isinstance(plan_payload, dict):
        errors.append(f"Missing paper plan payload: {cli.RECOMPILE_PAPER_PLAN_REL_PATH}.")
        plan_payload = {"figures": []}

    figures = plan_payload.get("figures")
    if not isinstance(figures, list) or not figures:
        errors.append("Paper plan must include non-empty `figures` list.")
        figures = []
    plan_figure_ids = [
        str(item.get("id") or "").strip()
        for item in figures
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    if not plan_figure_ids:
        errors.append("Paper plan does not include any valid figure ids.")

    resolved_skill_id = str(paper_skill_id or "").strip()
    if not resolved_skill_id:
        manifest_path = project_root / cli.RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH
        if manifest_path.is_file():
            try:
                manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest_payload = None
            if isinstance(manifest_payload, dict):
                resolved_skill_id = str(manifest_payload.get("skill_id") or "").strip()
    if not resolved_skill_id:
        errors.append(
            "Paper tutorial skill id is missing (`paper_skill_manifest.json` missing or invalid)."
        )
        resolved_skill_id = "paper_tutorial_unknown"

    paper_skill_root = skills_root / resolved_skill_id
    if not paper_skill_root.is_dir():
        errors.append(
            f"Paper tutorial skill directory missing: skills/{resolved_skill_id}"
        )
    else:
        required_paths = [
            paper_skill_root / "SKILL.md",
            paper_skill_root / "references" / "doc_map.md",
            paper_skill_root / "references" / "source_map.md",
        ]
        for required in required_paths:
            if not required.is_file():
                errors.append(f"Missing required paper tutorial file: {required}")
        root_skill_raw = _read_text(paper_skill_root / "SKILL.md")
        root_skill_text = root_skill_raw.lower()
        if root_skill_text:
            for required_heading in (
                "## core simulation strategy",
                "## minimal execution recipes",
                "## figure routing",
                "## beyond manuscript exploration",
            ):
                if required_heading not in root_skill_text:
                    errors.append(
                        "Paper tutorial SKILL.md missing required section: "
                        f"`{required_heading}`."
                    )
            normalized_root_text = root_skill_text.replace("\\", "/")
            runtime_projects_re = re.compile(
                r"projects/(?:yyyy-mm-dd-[a-z0-9_<>\-]+|\d{4}-\d{2}-\d{2}-[a-z0-9_\-]+)"
            )
            if not runtime_projects_re.search(normalized_root_text):
                errors.append(
                    "Paper tutorial SKILL.md must direct runtime work to "
                    "`projects/YYYY-MM-DD-<scope>/`."
                )
            if "workspace/" in normalized_root_text:
                errors.append(
                    "Paper tutorial SKILL.md must not direct runtime work to `workspace/`; "
                    "use `projects/YYYY-MM-DD-<scope>/`."
                )
            figure_routing_section = _extract_markdown_section(
                root_skill_raw, "Figure Routing"
            )
            if figure_routing_section:
                routing_lines = [
                    line.strip()
                    for line in figure_routing_section.splitlines()
                    if line.strip()
                ]
                for fig_id in plan_figure_ids:
                    fig_lines = [
                        line
                        for line in routing_lines
                        if fig_id.lower() in line.lower()
                    ]
                    if not fig_lines:
                        errors.append(
                            "Paper tutorial `## Figure Routing` is missing an entry for "
                            f"`{fig_id}`."
                        )
                        continue
                    if not any(
                        _figure_route_has_scope_description(line, fig_id)
                        for line in fig_lines
                    ):
                        errors.append(
                            "Paper tutorial `## Figure Routing` entry for "
                            f"`{fig_id}` must include a brief scope description "
                            "(scientific aim/condition) beyond figure label + playbook path."
                        )

    figure_map_path = project_root / cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH
    figure_map_payload: dict[str, object] | None = None
    if not figure_map_path.is_file():
        errors.append(
            f"Missing figure-to-data map file: {cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH}"
        )
    else:
        try:
            loaded_map = json.loads(figure_map_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(
                f"Invalid figure-to-data map JSON at {figure_map_path}: {exc}"
            )
            loaded_map = None
        if isinstance(loaded_map, dict):
            figure_map_payload = loaded_map
    map_figure_ids: set[str] = set()
    if isinstance(figure_map_payload, dict):
        map_figures = figure_map_payload.get("figures")
        if isinstance(map_figures, list):
            for item in map_figures:
                if not isinstance(item, dict):
                    continue
                fig_id = str(item.get("id") or "").strip()
                if fig_id:
                    map_figure_ids.add(fig_id)
        for fig_id in plan_figure_ids:
            if fig_id not in map_figure_ids:
                errors.append(f"Figure data map missing plan figure id: {fig_id}")

    index_skill_dirs = [
        path for path in _collect_skill_dirs(skills_root) if path.name.endswith("-index")
    ]
    if not index_skill_dirs:
        errors.append("No index skill found for paper tutorial routing update.")
    else:
        index_route_ok = False
        for index_dir in index_skill_dirs:
            text = _read_text(index_dir / "SKILL.md").lower()
            if resolved_skill_id.lower() in text and "advanced" in text:
                index_route_ok = True
                break
        if not index_route_ok:
            errors.append(
                "Index skill does not route to paper tutorial as an advanced topic."
            )

    staged_count = 0
    if isinstance(staged_assets, dict):
        staged_count = int(staged_assets.get("staged_count") or 0)
        manifest_rel = str(staged_assets.get("manifest_path") or "").strip()
        if manifest_rel and not (project_root / manifest_rel).is_file():
            errors.append(
                f"Staged-assets manifest missing after paper-mode recompile: {manifest_rel}"
            )
        stage_root_rel = str(staged_assets.get("stage_root") or "").strip()
        if stage_root_rel and not (project_root / stage_root_rel).is_dir():
            errors.append(
                f"Staged-assets directory missing after paper-mode recompile: {stage_root_rel}"
            )
        staged_files = staged_assets.get("staged_files")
        if isinstance(staged_files, list):
            for item in staged_files:
                if not isinstance(item, dict):
                    continue
                staged_path = str(item.get("staged_path") or "").strip()
                if staged_path and not (project_root / staged_path).is_file():
                    errors.append(
                        "Missing staged asset referenced by staged_assets_manifest: "
                        f"{staged_path}"
                    )
        if staged_count > 0:
            skill_asset_files = 0
            assets_dir = paper_skill_root / "assets"
            if assets_dir.is_dir():
                for path in assets_dir.rglob("*"):
                    if path.is_file():
                        skill_asset_files += 1
            if skill_asset_files <= 0:
                errors.append(
                    "Staged supplementary assets were prepared, but no paper-focused skill "
                    "contains an `assets/` directory with packaged files."
                )
        if paper_skill_root.is_dir():
            assets_dir = paper_skill_root / "assets"
            if assets_dir.is_dir():
                asset_total_bytes = 0
                oversized_assets: list[str] = []
                max_single_asset_bytes = 25 * 1024 * 1024
                max_total_asset_bytes = 200 * 1024 * 1024
                for path in sorted(assets_dir.rglob("*")):
                    if not path.is_file():
                        continue
                    try:
                        size = int(path.stat().st_size)
                    except OSError:
                        continue
                    asset_total_bytes += size
                    if size > max_single_asset_bytes:
                        oversized_assets.append(
                            f"{_safe_relative_path(path, project_root)} ({size} bytes)"
                        )
                if oversized_assets:
                    errors.append(
                        "Paper tutorial assets contain oversized files. Keep only lightweight "
                        "inputs/scripts/plots in `assets/`: "
                        + "; ".join(oversized_assets[:8])
                    )
                if asset_total_bytes > max_total_asset_bytes:
                    errors.append(
                        "Paper tutorial assets are too large "
                        f"({asset_total_bytes} bytes). Avoid packaging raw trajectories/results."
                    )

    external_refs: list[str] = []
    evidence_refs: list[str] = []
    path_escape_refs: list[str] = []
    workspace_refs: list[str] = []
    doc_abs = str(doc_path.resolve()) if isinstance(doc_path, Path) and doc_path.exists() else ""
    data_abs = (
        str(data_dir.resolve())
        if isinstance(data_dir, Path) and data_dir.exists() and data_dir.is_dir()
        else ""
    )
    evidence_tokens = [
        "skills/.evidence/",
        str(cli.COMPILE_EVIDENCE_DIR_REL_PATH).replace("\\", "/"),
        str(cli.RECOMPILE_PAPER_CONTEXT_DIR_REL_PATH).replace("\\", "/"),
        str(cli.RECOMPILE_PAPER_STAGED_ASSETS_DIR_REL_PATH).replace("\\", "/"),
    ]
    scoped_dirs = []
    if paper_skill_root.is_dir():
        scoped_dirs.append(paper_skill_root)
    scoped_dirs.extend(index_skill_dirs)
    for skill_dir in scoped_dirs:
        for md_path in sorted(skill_dir.rglob("*.md")):
            text = _read_text(md_path)
            if not text:
                continue
            normalized_text = text.replace("\\", "/")
            lowered_text = normalized_text.lower()
            if doc_abs and doc_abs in text:
                external_refs.append(f"{md_path}: references external --doc path.")
            if data_abs and data_abs in text:
                external_refs.append(f"{md_path}: references external --data-dir path.")
            if any(token.lower() in lowered_text for token in evidence_tokens):
                evidence_refs.append(
                    f"{md_path}: references `skills/.evidence/*`; paper tutorial must be self-contained."
                )
            if paper_skill_root.is_dir():
                try:
                    md_path.relative_to(paper_skill_root)
                except ValueError:
                    in_tutorial_skill = False
                else:
                    in_tutorial_skill = True
                if in_tutorial_skill:
                    if "workspace/" in lowered_text:
                        workspace_refs.append(
                            f"{md_path}: references `workspace/`; runtime should use "
                            "`projects/YYYY-MM-DD-<scope>/`."
                        )
                    for candidate in _iter_markdown_path_candidates(text):
                        if _is_path_escape_from_root(
                            candidate,
                            markdown_path=md_path,
                            tutorial_root=paper_skill_root,
                            project_root=project_root,
                        ):
                            path_escape_refs.append(
                                f"{md_path}: path escapes tutorial skill root: `{candidate}`."
                            )
    if external_refs:
        errors.extend(external_refs[:40])
        if len(external_refs) > 40:
            warnings.append(
                f"Suppressed {len(external_refs) - 40} additional external-path reference findings."
            )
    if evidence_refs:
        errors.extend(evidence_refs[:40])
        if len(evidence_refs) > 40:
            warnings.append(
                f"Suppressed {len(evidence_refs) - 40} additional evidence-path reference findings."
            )
    if path_escape_refs:
        errors.extend(path_escape_refs[:40])
        if len(path_escape_refs) > 40:
            warnings.append(
                f"Suppressed {len(path_escape_refs) - 40} additional path-escape findings."
            )
    if workspace_refs:
        errors.extend(workspace_refs[:40])
        if len(workspace_refs) > 40:
            warnings.append(
                f"Suppressed {len(workspace_refs) - 40} additional workspace-path findings."
            )

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "paper_plan_figures": plan_figure_ids,
        "paper_map_figures": sorted(map_figure_ids),
        "paper_skill_id": resolved_skill_id,
        "staged_assets_count": staged_count,
    }


def _validate_compiled_skills(
    project_root: Path,
    *,
    profile: dict[str, object],
    core_skill_count: int,
) -> dict[str, object]:
    skills_root = project_root / "skills"
    errors: list[str] = []
    warnings: list[str] = []

    skill_dirs = _collect_skill_dirs(skills_root)
    if not skill_dirs:
        return {
            "ok": False,
            "errors": ["No skill directories with SKILL.md were generated under skills/."],
            "warnings": [],
            "skills_total": 0,
            "source_links_total": 0,
            "core_skills_checked": [],
        }

    index_skills = [path.name for path in skill_dirs if path.name.endswith("-index")]
    if not index_skills:
        errors.append("No index skill was generated (expected a `*-index` skill).")

    docs_only = bool(profile.get("docs_only", False))
    source_roots = _inferred_source_roots(project_root, profile)
    total_source_links = 0
    topic_skills = [path for path in skill_dirs if not path.name.endswith("-index")]
    for skill_dir in topic_skills:
        skill_md_path = skill_dir / "SKILL.md"
        doc_map_path = skill_dir / "references" / "doc_map.md"
        source_map_path = skill_dir / "references" / "source_map.md"
        if not doc_map_path.is_file():
            errors.append(f"{doc_map_path}: missing.")
        if not source_map_path.is_file():
            errors.append(f"{source_map_path}: missing.")
            continue

        source_map_text = _read_text(source_map_path)
        source_tokens = _extract_backtick_tokens(source_map_text)
        source_files: list[Path] = []
        broken_source_tokens: list[str] = []
        for token in source_tokens:
            if not _looks_like_path(token):
                continue
            resolved = _resolve_existing_path(
                project_root,
                token,
                context_dirs=[source_map_path.parent, skill_dir],
            )
            if resolved is None:
                broken_source_tokens.append(token)
                continue
            if _is_source_file(resolved, source_roots=source_roots):
                source_files.append(resolved)
        total_source_links += len(source_files)

        if broken_source_tokens:
            preview = ", ".join(broken_source_tokens[:5])
            errors.append(f"{source_map_path}: broken path references: {preview}")

        if not docs_only and not source_files:
            errors.append(
                f"{source_map_path}: no valid source-code entry links were found."
            )

        skill_text = _read_text(skill_md_path)
        for token in _extract_backtick_tokens(skill_text):
            if not _looks_like_path(token):
                continue
            resolved = _resolve_existing_path(
                project_root,
                token,
                context_dirs=[skill_md_path.parent],
            )
            if resolved is None and token.startswith("skills/"):
                warnings.append(f"{skill_md_path}: unresolved internal link `{token}`.")

    core_skill_dirs = _select_core_skill_dirs(skills_root, core_skill_count)
    core_skill_names = [path.name for path in core_skill_dirs]
    for skill_dir in core_skill_dirs:
        errors.extend(_validate_skill_playbook(skill_dir / "SKILL.md"))

    if not docs_only and total_source_links <= 0:
        errors.append("No source-code links were detected across topic skills.")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "skills_total": len(skill_dirs),
        "index_skills": index_skills,
        "topic_skills": len(topic_skills),
        "core_skills_checked": core_skill_names,
        "source_links_total": total_source_links,
        "docs_only": docs_only,
    }


def _write_compile_report(
    project_root: Path,
    *,
    payload: dict[str, object],
) -> str:
    cli = _cli()
    report_path = project_root / cli.COMPILE_REPORT_REL_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        report_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise cli.PackageError(
            f"Failed to write compile report at {report_path}: {exc}"
        ) from exc
    return str(report_path.relative_to(project_root))


def _run_codex_compile_pass(
    project_root: Path,
    *,
    prompt: str,
    pass_index: int,
    total_passes: int,
    provider: str,
    provider_bin: str,
) -> dict[str, object]:
    cli = _cli()
    sandbox = cli.DEFAULT_COMPILE_SANDBOX
    try:
        cmd = cli.build_exec_command(
            provider=provider,
            provider_bin=provider_bin,
            repo_dir=project_root,
            prompt=prompt,
            sandbox_policy="enforce",
            sandbox_mode=sandbox,
            json_output=False,
        )
    except NotImplementedError as exc:
        raise cli.PackageError(
            f"Compile provider '{provider}' is not implemented yet. "
            "Switch to codex via `fermilink agent codex`."
        ) from exc

    with cli.tempfile.TemporaryDirectory(prefix="fermilink-compile-pass-") as temp_dir:
        last_message_path = Path(temp_dir) / "last_message.txt"
        cmd = cli._inject_exec_option_before_prompt(cmd, "--color", "always")
        cmd = cli._inject_exec_option_before_prompt(
            cmd, "--output-last-message", str(last_message_path)
        )

        print(f"[compile] pass {pass_index}/{total_passes}: {provider} exec")
        try:
            completed = cli.subprocess.run(cmd, check=False, cwd=str(project_root))
        except FileNotFoundError as exc:
            env_key = cli.provider_bin_env_key(provider)
            raise cli.PackageError(
                f"{provider} CLI not found: {provider_bin}. "
                f"Install {provider} or set {env_key}."
            ) from exc

        if completed.returncode != 0:
            raise cli.PackageError(
                f"{provider} exec failed at compile pass {pass_index}/{total_passes} "
                f"with exit code {completed.returncode}."
            )

        assistant_text = _read_text(last_message_path).strip()
        return {
            "pass": pass_index,
            "status": "ok",
            "return_code": int(completed.returncode),
            "assistant_text": assistant_text,
        }
