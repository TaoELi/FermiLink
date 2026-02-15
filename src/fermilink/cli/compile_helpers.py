from __future__ import annotations

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
