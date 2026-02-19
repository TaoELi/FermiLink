from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


AUTO_COMPILE_METADATA_TAG = "auto_compile_metadata"
AUTO_COMPILE_METADATA_TOKEN_RE = re.compile(
    rf"<{AUTO_COMPILE_METADATA_TAG}>(.*?)</{AUTO_COMPILE_METADATA_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
GITHUB_OWNER_TOKEN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
AUTO_COMPILE_COMMIT_TEMPLATE = "Add FermiLink skills for {package_id}"
ROUTER_KEYWORD_NOISE_TERMS = {
    "backend",
    "conda",
    "conda-forge",
    "cython",
    "cython backend",
    "numpy",
    "numpy scipy",
    "pip",
    "scipy",
}
ROUTER_NEGATIVE_NONSCIENTIFIC_TERMS = {
    "blockchain",
    "computer vision",
    "devops",
    "mobile app",
    "natural language processing",
    "relational database",
    "web frontend",
}


def _cli():
    from fermilink import cli

    return cli


def _save_curated_install_metadata(
    scipkg_root: Path,
    package_id: str,
    *,
    channel: str,
    curated_package_id: str,
    version_id: str,
    source_archive_url: str,
    verified: bool,
    source_ref_type: str | None,
    source_ref_value: str | None,
) -> None:
    cli = _cli()
    normalized_id = cli.normalize_package_id(package_id)
    registry = cli.load_registry(scipkg_root)
    packages = registry.get("packages")
    if not isinstance(packages, dict):
        return

    meta = packages.get(normalized_id)
    if not isinstance(meta, dict):
        return

    updated = dict(meta)
    updated["curated"] = {
        "channel": channel,
        "package_id": curated_package_id,
        "version_id": version_id,
        "source_archive_url": source_archive_url,
        "verified": verified,
        "source_ref": {
            "type": source_ref_type,
            "value": source_ref_value,
        },
    }
    packages[normalized_id] = updated
    cli.save_registry(scipkg_root, registry)


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_compile_run_id(prefix: str) -> str:
    raw = _utc_now_z().replace("-", "").replace(":", "").replace(".", "")
    token = re.sub(r"[^A-Za-z0-9]+", "", raw).strip() or "run"
    return f"{prefix}_{token}"


def _read_json_object(path: Path) -> dict[str, object]:
    cli = _cli()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise cli.PackageError(f"Failed to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise cli.PackageError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise cli.PackageError(f"Expected JSON object in {path}.")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    cli = _cli()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        raise cli.PackageError(f"Failed to write JSON file {path}: {exc}") from exc


def _run_external_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> object:
    cli = _cli()
    try:
        completed = cli.subprocess.run(
            command,
            cwd=str(cwd) if isinstance(cwd, Path) else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise cli.PackageError(f"Command not found: {command[0]}") from exc

    if check and completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        rendered = " ".join(command)
        raise cli.PackageError(f"Command failed ({rendered}): {detail}")
    return completed


def _normalize_github_repo_url(url: str) -> tuple[str, str, str]:
    cli = _cli()
    cleaned = str(url or "").strip()
    if not cleaned:
        raise cli.PackageError("upstream_repo_url is required.")

    owner = ""
    repo = ""
    if cleaned.startswith("git@github.com:"):
        suffix = cleaned.split(":", 1)[1]
        parts = [part for part in suffix.split("/") if part]
        if len(parts) >= 2:
            owner = parts[0].strip()
            repo = parts[1].strip()
    else:
        parsed = urlparse(cleaned)
        host = (parsed.netloc or "").lower()
        if host not in {"github.com", "www.github.com"}:
            raise cli.PackageError(f"Only GitHub repo URLs are supported: {cleaned}")
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 2:
            owner = path_parts[0].strip()
            repo = path_parts[1].strip()

    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise cli.PackageError(f"Invalid GitHub repository URL: {cleaned}")

    canonical = f"https://github.com/{owner}/{repo}"
    return owner, repo, canonical


def _load_auto_compile_specs(
    *,
    package_id_arg: str | None,
    upstream_repo_url_arg: str | None,
    spec_file_arg: str | None,
) -> list[dict[str, str]]:
    cli = _cli()
    package_id_raw = str(package_id_arg or "").strip()
    upstream_raw = str(upstream_repo_url_arg or "").strip()
    spec_raw = str(spec_file_arg or "").strip()

    if spec_raw and (package_id_raw or upstream_raw):
        raise cli.PackageError(
            "Use either positional package_id/upstream_repo_url or --spec-file, not both."
        )

    specs: list[dict[str, str]] = []
    if spec_raw:
        spec_path = Path(spec_raw).expanduser().resolve()
        payload = _read_json_object(spec_path)
        packages_raw = payload.get("packages")
        if not isinstance(packages_raw, list) or not packages_raw:
            raise cli.PackageError(
                f"--spec-file must include non-empty packages[]: {spec_path}"
            )
        for index, item in enumerate(packages_raw, start=1):
            if not isinstance(item, dict):
                raise cli.PackageError(
                    f"Invalid packages[{index}] in {spec_path}: expected object."
                )
            raw_id = str(item.get("package_id") or "").strip()
            raw_url = str(item.get("upstream_repo_url") or "").strip()
            if not raw_id or not raw_url:
                raise cli.PackageError(
                    f"Invalid packages[{index}] in {spec_path}: package_id and upstream_repo_url are required."
                )
            _, _, canonical = _normalize_github_repo_url(raw_url)
            specs.append(
                {
                    "package_id": cli.normalize_package_id(raw_id),
                    "upstream_repo_url": canonical,
                }
            )
    else:
        if not package_id_raw or not upstream_raw:
            raise cli.PackageError(
                "Provide <package_id> <upstream_repo_url> or --spec-file."
            )
        _, _, canonical = _normalize_github_repo_url(upstream_raw)
        specs.append(
            {
                "package_id": cli.normalize_package_id(package_id_raw),
                "upstream_repo_url": canonical,
            }
        )

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for spec in specs:
        package_id = spec["package_id"]
        if package_id in seen:
            raise cli.PackageError(
                f"Duplicate package_id in auto-compile input: {package_id}"
            )
        seen.add(package_id)
        deduped.append(spec)
    return deduped


def _ensure_required_commands_available(*, command_names: tuple[str, ...]) -> None:
    cli = _cli()
    missing = [
        name for name in command_names if not cli.shutil.which(str(name).strip())
    ]
    if missing:
        raise cli.PackageError(
            "Missing required commands: " + ", ".join(sorted(set(missing)))
        )


def _parse_repo_info_payload(raw_json: str, *, context: str) -> dict[str, object]:
    cli = _cli()
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise cli.PackageError(f"Invalid JSON from {context}: {exc}") from exc
    if not isinstance(payload, dict):
        raise cli.PackageError(f"Invalid payload from {context}: expected object.")
    return payload


def _try_fetch_repo_info(name_with_owner: str) -> dict[str, object] | None:
    completed = _run_external_command(
        [
            "gh",
            "repo",
            "view",
            name_with_owner,
            "--json",
            "name,nameWithOwner,url,description,homepageUrl,defaultBranchRef,visibility",
        ],
        check=False,
    )
    if int(getattr(completed, "returncode", 1)) != 0:
        return None
    stdout = str(getattr(completed, "stdout", "") or "").strip()
    if not stdout:
        return None
    return _parse_repo_info_payload(stdout, context=f"gh repo view {name_with_owner}")


def _fetch_repo_info(name_with_owner: str) -> dict[str, object]:
    payload = _try_fetch_repo_info(name_with_owner)
    cli = _cli()
    if payload is None:
        raise cli.PackageError(
            f"Unable to fetch repository metadata: {name_with_owner}"
        )
    return payload


def _resolve_github_login() -> str:
    completed = _run_external_command(["gh", "api", "user", "--jq", ".login"])
    login = str(getattr(completed, "stdout", "") or "").strip()
    cli = _cli()
    if not login:
        raise cli.PackageError(
            "Unable to resolve GitHub login from `gh api user --jq .login`."
        )
    return login


def _normalize_github_owner(raw_owner: str | None, *, field_name: str) -> str | None:
    cli = _cli()
    value = str(raw_owner or "").strip()
    if value.startswith("@"):
        value = value[1:].strip()
    if not value:
        return None
    if not GITHUB_OWNER_TOKEN_RE.fullmatch(value):
        raise cli.PackageError(
            f"{field_name} must be a GitHub account/organization name "
            "(letters, numbers, hyphens)."
        )
    return value


def _repo_default_branch(
    repo_info: dict[str, object], *, fallback: str = "main"
) -> str:
    branch_ref = repo_info.get("defaultBranchRef")
    if isinstance(branch_ref, dict):
        value = str(branch_ref.get("name") or "").strip()
        if value:
            return value
    return fallback


def _ensure_public_fork(
    *,
    upstream_owner: str,
    upstream_repo: str,
    github_login: str,
    organization: str | None,
) -> dict[str, str]:
    cli = _cli()
    upstream = f"{upstream_owner}/{upstream_repo}"
    fork_owner = organization or github_login
    fork_name = f"{fork_owner}/{upstream_repo}"

    fork_info = _try_fetch_repo_info(fork_name)
    if fork_info is None:
        command = ["gh", "repo", "fork", upstream, "--clone=false"]
        if organization:
            command.extend(["--org", organization])
        _run_external_command(command)
        fork_info = _fetch_repo_info(fork_name)

    visibility = str(fork_info.get("visibility") or "").strip().lower()
    if visibility and visibility != "public":
        raise cli.PackageError(
            f"Fork {fork_name} exists but is not public (visibility={visibility})."
        )

    default_branch = _repo_default_branch(fork_info, fallback="main")
    fork_url = (
        str(fork_info.get("url") or "").strip() or f"https://github.com/{fork_name}"
    )
    return {
        "fork_name": fork_name,
        "fork_url": fork_url,
        "fork_clone_url": f"https://github.com/{fork_name}.git",
        "default_branch": default_branch,
    }


def _git_status_porcelain(repo_dir: Path) -> str:
    completed = _run_external_command(
        ["git", "status", "--porcelain"], cwd=repo_dir, check=True
    )
    return str(getattr(completed, "stdout", "") or "").strip()


def _checkout_branch(repo_dir: Path, branch_name: str) -> None:
    completed = _run_external_command(
        ["git", "checkout", branch_name], cwd=repo_dir, check=False
    )
    if int(getattr(completed, "returncode", 1)) == 0:
        return
    _run_external_command(
        ["git", "checkout", "-B", branch_name, f"origin/{branch_name}"],
        cwd=repo_dir,
    )


def _prepare_fork_clone(
    *,
    workspace_root: Path,
    package_id: str,
    upstream_repo: str,
    clone_url: str,
    default_branch: str,
) -> Path:
    cli = _cli()
    safe_repo = cli.normalize_package_id(upstream_repo)
    clone_dir = workspace_root / f"{package_id}-{safe_repo}"
    workspace_root.mkdir(parents=True, exist_ok=True)

    if clone_dir.exists():
        if not (clone_dir / ".git").is_dir():
            raise cli.PackageError(
                f"Clone path exists but is not a git repository: {clone_dir}"
            )
        if _git_status_porcelain(clone_dir):
            raise cli.PackageError(
                f"Clone has uncommitted changes; clean it before auto-compile: {clone_dir}"
            )
        _run_external_command(
            ["git", "remote", "set-url", "origin", clone_url], cwd=clone_dir
        )
        _run_external_command(["git", "fetch", "origin"], cwd=clone_dir)
        _checkout_branch(clone_dir, default_branch)
        _run_external_command(
            ["git", "pull", "--ff-only", "origin", default_branch], cwd=clone_dir
        )
    else:
        _run_external_command(["git", "clone", clone_url, str(clone_dir)])
        _run_external_command(["git", "fetch", "origin"], cwd=clone_dir)
        _checkout_branch(clone_dir, default_branch)

    return clone_dir


def _invoke_compile_for_auto_compile(
    *,
    package_id: str,
    project_root: Path,
    max_skills: int,
    core_skill_count: int,
    docs_only: bool,
    keep_compile_artifacts: bool,
    strict_compile_validation: bool,
) -> dict[str, object]:
    skills_root = project_root / "skills"
    if skills_root.is_dir():
        return {
            "performed": False,
            "reason": "skills_already_exists",
            "project_root": str(project_root),
        }

    compile_args = argparse.Namespace(
        package_id=package_id,
        project_path=str(project_root),
        title=None,
        max_skills=max_skills,
        core_skill_count=core_skill_count,
        docs_only=docs_only,
        keep_compile_artifacts=keep_compile_artifacts,
        strict_compile_validation=strict_compile_validation,
        install_off=True,
        activate=False,
        no_router_sync=True,
        json=False,
    )
    exit_code = cmd_compile(compile_args)
    if exit_code != 0:
        raise _cli().PackageError(
            f"Compile failed for {package_id} at {project_root} with exit code {exit_code}."
        )
    return {
        "performed": True,
        "reason": "compiled",
        "project_root": str(project_root),
    }


def _commit_and_push_changes(
    *,
    repo_dir: Path,
    package_id: str,
    default_branch: str,
) -> dict[str, object]:
    _checkout_branch(repo_dir, default_branch)
    initial_status = _git_status_porcelain(repo_dir)
    committed = False
    commit_sha = ""
    if initial_status:
        _run_external_command(["git", "add", "-A"], cwd=repo_dir)
        staged_status = _git_status_porcelain(repo_dir)
        if staged_status:
            message = AUTO_COMPILE_COMMIT_TEMPLATE.format(package_id=package_id)
            _run_external_command(["git", "commit", "-m", message], cwd=repo_dir)
            committed = True
            commit_sha = str(
                getattr(
                    _run_external_command(["git", "rev-parse", "HEAD"], cwd=repo_dir),
                    "stdout",
                    "",
                )
                or ""
            ).strip()
    _run_external_command(
        ["git", "push", "origin", f"HEAD:{default_branch}"], cwd=repo_dir
    )
    return {
        "committed": committed,
        "commit_sha": commit_sha,
        "pushed_branch": default_branch,
        "has_changes": bool(initial_status),
    }


def _read_repo_excerpt(repo_dir: Path, *, max_chars: int = 5000) -> str:
    for candidate in ("README.md", "README.rst", "Readme.md"):
        path = repo_dir / candidate
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        cleaned = text.strip()
        if not cleaned:
            continue
        return cleaned[:max_chars]
    return ""


def _load_disambiguation_package_ids(
    *,
    fermilink_repo: Path,
    channel_id: str,
    package_id: str,
) -> list[str]:
    cli = _cli()
    normalized_channel = cli.normalize_channel_id(channel_id)
    normalized_package_id = cli.normalize_package_id(package_id)
    curated_path = (
        fermilink_repo
        / "src"
        / "fermilink"
        / "data"
        / "curated_channels"
        / f"{normalized_channel}.json"
    )
    family_path = (
        fermilink_repo / "src" / "fermilink" / "data" / "router" / "family_hints.json"
    )

    candidates: list[str] = []
    if curated_path.is_file():
        curated_payload = _read_json_object(curated_path)
        packages_raw = curated_payload.get("packages")
        if isinstance(packages_raw, list):
            for item in packages_raw:
                if not isinstance(item, dict):
                    continue
                candidate = cli.normalize_package_id(str(item.get("package_id") or ""))
                if candidate and candidate != normalized_package_id:
                    candidates.append(candidate)

    if family_path.is_file():
        family_payload = _read_json_object(family_path)
        families_raw = family_payload.get("families")
        if isinstance(families_raw, dict):
            for family_name in families_raw:
                candidate = cli.normalize_package_id(str(family_name or ""))
                if candidate and candidate != normalized_package_id:
                    candidates.append(candidate)

    return _normalize_unique_terms(
        candidates,
        field_name="disambiguation_package_ids",
        min_items=0,
        max_items=200,
        lowercase=True,
    )


def _normalize_unique_terms(
    raw: object,
    *,
    field_name: str,
    min_items: int = 0,
    max_items: int | None = None,
    lowercase: bool = True,
) -> list[str]:
    cli = _cli()
    if isinstance(raw, str):
        items = [token.strip() for token in raw.split(",")]
    elif isinstance(raw, list):
        items = [str(item).strip() for item in raw if isinstance(item, str)]
    else:
        items = []
    terms: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        value = item.lower() if lowercase else item
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(value)
    if max_items is not None:
        terms = terms[:max_items]
    if len(terms) < min_items:
        raise cli.PackageError(
            f"Codex metadata field `{field_name}` requires at least {min_items} item(s)."
        )
    return terms


def _build_auto_compile_metadata_prompt(
    *,
    package_id: str,
    upstream_repo_url: str,
    fork_repo_url: str,
    default_branch: str,
    upstream_description: str,
    upstream_homepage: str,
    readme_excerpt: str,
    disambiguation_package_ids: list[str],
) -> str:
    excerpt_block = readme_excerpt.strip() or "(no README excerpt available)"
    disambiguation_block = ", ".join(disambiguation_package_ids[:30]) or "(none)"
    return (
        "Generate metadata for onboarding one scientific package into FermiLink.\n"
        "Return only one tagged JSON payload and no extra text.\n"
        f"Use this exact format: <{AUTO_COMPILE_METADATA_TAG}>{{...}}</{AUTO_COMPILE_METADATA_TAG}>.\n"
        "Required JSON fields:\n"
        "- title: short human-friendly package title.\n"
        "- description: one concise sentence (12-40 words).\n"
        "- tags: list of 3-8 short lowercase tags.\n"
        "- family_description: one concise sentence for router family hints.\n"
        "- strong_keywords: list of 4-12 high-confidence routing terms.\n"
        "- keywords: list of 4-14 secondary routing terms.\n"
        "- negative_keywords: list of 0-10 disambiguation terms likely belonging "
        "to other scientific packages.\n"
        "Constraints:\n"
        "- Terms must be plain strings, no punctuation-only tokens.\n"
        "- Keep terms domain-specific and useful for routing user intents.\n"
        "- Avoid generic AI words.\n"
        "- Include package canonical name in strong_keywords.\n"
        "- For strong_keywords/keywords, avoid generic dependency/toolchain labels "
        "(numpy/scipy/cython/conda/backend) unless absolutely central to intent.\n"
        "- For negative_keywords, prefer entries from candidate disambiguation package "
        "ids when suitable.\n"
        "- Do not include non-scientific software/product terms "
        "(web frontend, mobile app, blockchain, devops).\n"
        f"- Package id: {package_id}\n"
        f"- Upstream repo: {upstream_repo_url}\n"
        f"- Fork repo: {fork_repo_url}\n"
        f"- Fork default branch: {default_branch}\n"
        f"- Upstream description: {upstream_description or '(none)'}\n"
        f"- Upstream homepage: {upstream_homepage or '(none)'}\n"
        f"- Candidate disambiguation package ids: {disambiguation_block}\n"
        "README excerpt:\n"
        "<<<README\n"
        f"{excerpt_block}\n"
        "README>>>\n"
    )


def _generate_metadata_with_codex(
    *,
    metadata_repo_dir: Path,
    package_id: str,
    upstream_repo_url: str,
    fork_repo_url: str,
    default_branch: str,
    upstream_description: str,
    upstream_homepage: str,
    readme_excerpt: str,
    disambiguation_package_ids: list[str],
) -> dict[str, object]:
    cli = _cli()
    runtime_policy = cli.resolve_agent_runtime_policy()
    if runtime_policy.provider != "codex":
        raise cli.PackageError(
            "auto-compile metadata generation requires Codex provider. "
            "Run `fermilink agent codex` first."
        )

    prompt = _build_auto_compile_metadata_prompt(
        package_id=package_id,
        upstream_repo_url=upstream_repo_url,
        fork_repo_url=fork_repo_url,
        default_branch=default_branch,
        upstream_description=upstream_description,
        upstream_homepage=upstream_homepage,
        readme_excerpt=readme_excerpt,
        disambiguation_package_ids=disambiguation_package_ids,
    )
    if not metadata_repo_dir.is_dir():
        raise cli.PackageError(
            f"Invalid metadata repo directory for auto-compile: {metadata_repo_dir}"
        )
    response = cli._run_exec_chat_turn(
        repo_dir=metadata_repo_dir,
        prompt=prompt,
        sandbox="read-only",
        codex_bin=cli.DEFAULT_COMPILE_CODEX_BIN,
        provider="codex",
        sandbox_policy="enforce",
    )
    return_code_raw = response.get("return_code")
    try:
        return_code = int(1 if return_code_raw is None else return_code_raw)
    except (TypeError, ValueError):
        return_code = 1
    if return_code != 0:
        stderr = str(response.get("stderr") or "").strip()
        detail = stderr or f"exit code {return_code}"
        raise cli.PackageError(f"Codex metadata generation failed: {detail}")

    assistant_text = str(response.get("assistant_text") or "")
    payload = cli._extract_tagged_json_payload(
        assistant_text,
        token_re=AUTO_COMPILE_METADATA_TOKEN_RE,
    )
    if not isinstance(payload, dict):
        raise cli.PackageError(
            "Failed to parse Codex metadata JSON payload from tagged response."
        )
    return payload


def _build_curated_entry_from_metadata(
    *,
    package_id: str,
    upstream_repo_url: str,
    upstream_homepage: str,
    fork_owner_repo: str,
    default_branch: str,
    metadata_payload: dict[str, object],
) -> dict[str, object]:
    cli = _cli()
    title_raw = str(metadata_payload.get("title") or "").strip()
    description_raw = str(metadata_payload.get("description") or "").strip()
    if not title_raw or not description_raw:
        raise cli.PackageError(
            "Codex metadata is missing required title/description fields."
        )
    tags = _normalize_unique_terms(
        metadata_payload.get("tags"),
        field_name="tags",
        min_items=3,
        max_items=8,
        lowercase=True,
    )
    branch = str(default_branch or "main").strip() or "main"
    source_archive_url = (
        f"https://github.com/{fork_owner_repo}/archive/refs/heads/{branch}.zip"
    )
    homepage = str(upstream_homepage or "").strip() or upstream_repo_url
    return {
        "package_id": cli.normalize_package_id(package_id),
        "title": title_raw,
        "description": description_raw,
        "upstream_repo_url": upstream_repo_url,
        "homepage_url": homepage,
        "zip_url": source_archive_url,
        "default_version": "branch-head",
        "versions": [
            {
                "version_id": "branch-head",
                "source_archive_url": source_archive_url,
                "source_ref": {
                    "type": "branch",
                    "value": branch,
                },
                "verified": False,
            }
        ],
        "tags": tags,
    }


def _build_family_entry_from_metadata(
    *,
    package_id: str,
    metadata_payload: dict[str, object],
    disambiguation_package_ids: list[str] | None = None,
) -> dict[str, object]:
    description = str(metadata_payload.get("family_description") or "").strip()
    if not description:
        description = f"Routing hints for {package_id} workflows."
    strong_keywords = _normalize_unique_terms(
        metadata_payload.get("strong_keywords"),
        field_name="strong_keywords",
        min_items=4,
        max_items=12,
        lowercase=True,
    )
    keywords = _normalize_unique_terms(
        metadata_payload.get("keywords"),
        field_name="keywords",
        min_items=4,
        max_items=14,
        lowercase=True,
    )
    filtered_keywords = [
        term for term in keywords if term not in ROUTER_KEYWORD_NOISE_TERMS
    ]
    if len(filtered_keywords) >= 4:
        keywords = filtered_keywords
    negative_keywords = _normalize_unique_terms(
        metadata_payload.get("negative_keywords"),
        field_name="negative_keywords",
        min_items=0,
        max_items=10,
        lowercase=True,
    )
    negative_keywords = [
        term
        for term in negative_keywords
        if term not in ROUTER_NEGATIVE_NONSCIENTIFIC_TERMS
    ]
    if isinstance(disambiguation_package_ids, list) and disambiguation_package_ids:
        peer_terms = _normalize_unique_terms(
            disambiguation_package_ids,
            field_name="disambiguation_package_ids",
            min_items=0,
            max_items=200,
            lowercase=True,
        )
        for peer in peer_terms:
            if peer == package_id or peer in negative_keywords:
                continue
            negative_keywords.append(peer)
    negative_keywords = _normalize_unique_terms(
        negative_keywords,
        field_name="negative_keywords",
        min_items=0,
        max_items=10,
        lowercase=True,
    )
    if package_id not in strong_keywords:
        strong_keywords.insert(0, package_id)
        strong_keywords = _normalize_unique_terms(
            strong_keywords,
            field_name="strong_keywords",
            min_items=4,
            max_items=12,
            lowercase=True,
        )
    return {
        "description": description,
        "strong_keywords": strong_keywords,
        "keywords": keywords,
        "negative_keywords": negative_keywords,
    }


def _validate_curated_entry_shape(
    *,
    package_id: str,
    curated_entry: dict[str, object],
) -> None:
    cli = _cli()
    required = {
        "package_id",
        "title",
        "description",
        "upstream_repo_url",
        "default_version",
        "versions",
    }
    missing = [key for key in sorted(required) if key not in curated_entry]
    if missing:
        raise cli.PackageError(
            f"Curated entry is missing required fields: {', '.join(missing)}"
        )
    normalized_id = cli.normalize_package_id(str(curated_entry.get("package_id") or ""))
    if normalized_id != package_id:
        raise cli.PackageError(
            "Curated entry package_id mismatch against requested package id."
        )
    for text_field in ("title", "description", "upstream_repo_url", "default_version"):
        value = str(curated_entry.get(text_field) or "").strip()
        if not value:
            raise cli.PackageError(
                f"Curated entry field `{text_field}` must be non-empty."
            )
    versions = curated_entry.get("versions")
    if not isinstance(versions, list) or not versions:
        raise cli.PackageError("Curated entry requires non-empty versions[].")
    for index, version in enumerate(versions, start=1):
        if not isinstance(version, dict):
            raise cli.PackageError(
                f"Curated entry versions[{index}] must be an object."
            )
        version_id = str(version.get("version_id") or "").strip()
        source_archive_url = str(version.get("source_archive_url") or "").strip()
        if not version_id or not source_archive_url:
            raise cli.PackageError(
                f"Curated entry versions[{index}] missing version_id/source_archive_url."
            )
        if not isinstance(version.get("verified"), bool):
            raise cli.PackageError(
                f"Curated entry versions[{index}].verified must be boolean."
            )
        source_ref = version.get("source_ref")
        if not isinstance(source_ref, dict):
            raise cli.PackageError(
                f"Curated entry versions[{index}].source_ref must be object."
            )
        ref_type = str(source_ref.get("type") or "").strip()
        ref_value = str(source_ref.get("value") or "").strip()
        if not ref_type or not ref_value:
            raise cli.PackageError(
                f"Curated entry versions[{index}].source_ref requires type/value."
            )
    if "tags" in curated_entry:
        _normalize_unique_terms(
            curated_entry.get("tags"),
            field_name="tags",
            min_items=1,
            max_items=20,
            lowercase=True,
        )


def _validate_family_entry_shape(family_entry: dict[str, object]) -> None:
    cli = _cli()
    description = str(family_entry.get("description") or "").strip()
    if not description:
        raise cli.PackageError("Family hints entry requires non-empty description.")
    for field_name in ("strong_keywords", "keywords", "negative_keywords"):
        raw = family_entry.get(field_name, [])
        _normalize_unique_terms(
            raw,
            field_name=field_name,
            min_items=0,
            max_items=50,
            lowercase=True,
        )


def _validate_data_payloads_with_script(
    *,
    fermilink_repo: Path,
    channel_id: str,
    curated_payload: dict[str, object],
    family_payload: dict[str, object],
) -> None:
    cli = _cli()
    validate_script = fermilink_repo / "scripts" / "validate_data.py"
    if not validate_script.is_file():
        raise cli.PackageError(f"Missing data validation script: {validate_script}")

    with cli.tempfile.TemporaryDirectory(
        prefix="fermilink-auto-compile-validate-"
    ) as temp_dir:
        temp_root = Path(temp_dir)
        curated_path = (
            temp_root
            / "src"
            / "fermilink"
            / "data"
            / "curated_channels"
            / f"{channel_id}.json"
        )
        family_path = (
            temp_root / "src" / "fermilink" / "data" / "router" / "family_hints.json"
        )
        _write_json_atomic(curated_path, curated_payload)
        _write_json_atomic(family_path, family_payload)
        completed = cli.subprocess.run(
            [cli.sys.executable, str(validate_script), "--repo-root", str(temp_root)],
            capture_output=True,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise cli.PackageError(f"Data validation failed after merge preview: {detail}")


def _precheck_metadata_merge_conflicts(
    *,
    fermilink_repo: Path,
    channel_id: str,
    package_id: str,
    update_existing: bool,
) -> None:
    if update_existing:
        return

    cli = _cli()
    normalized_channel = cli.normalize_channel_id(channel_id)
    curated_path = (
        fermilink_repo
        / "src"
        / "fermilink"
        / "data"
        / "curated_channels"
        / f"{normalized_channel}.json"
    )
    family_path = (
        fermilink_repo / "src" / "fermilink" / "data" / "router" / "family_hints.json"
    )
    if not curated_path.is_file():
        raise cli.PackageError(f"Missing curated channel file: {curated_path}")
    if not family_path.is_file():
        raise cli.PackageError(f"Missing family hints file: {family_path}")

    curated_payload = _read_json_object(curated_path)
    family_payload = _read_json_object(family_path)
    packages_raw = curated_payload.get("packages")
    if not isinstance(packages_raw, list):
        raise cli.PackageError(f"Invalid curated channel file: {curated_path}")
    existing_package_ids = {
        str(item.get("package_id") or "").strip().lower()
        for item in packages_raw
        if isinstance(item, dict)
    }
    if package_id in existing_package_ids:
        raise cli.PackageError(
            f"Package '{package_id}' already exists in {curated_path}. "
            "Use --update-existing to replace it."
        )

    families_raw = family_payload.get("families")
    if not isinstance(families_raw, dict):
        raise cli.PackageError(f"Invalid family hints file: {family_path}")
    if package_id in families_raw:
        raise cli.PackageError(
            f"Family '{package_id}' already exists in {family_path}. "
            "Use --update-existing to replace it."
        )


def _merge_metadata_entries(
    *,
    fermilink_repo: Path,
    channel_id: str,
    package_id: str,
    curated_entry: dict[str, object],
    family_entry: dict[str, object],
    update_existing: bool,
    dry_run: bool,
) -> dict[str, object]:
    cli = _cli()
    normalized_channel = cli.normalize_channel_id(channel_id)
    curated_path = (
        fermilink_repo
        / "src"
        / "fermilink"
        / "data"
        / "curated_channels"
        / f"{normalized_channel}.json"
    )
    family_path = (
        fermilink_repo / "src" / "fermilink" / "data" / "router" / "family_hints.json"
    )
    if not curated_path.is_file():
        raise cli.PackageError(f"Missing curated channel file: {curated_path}")
    if not family_path.is_file():
        raise cli.PackageError(f"Missing family hints file: {family_path}")

    curated_payload = _read_json_object(curated_path)
    family_payload = _read_json_object(family_path)

    packages_raw = curated_payload.get("packages")
    if not isinstance(packages_raw, list):
        raise cli.PackageError(f"Invalid curated channel file: {curated_path}")
    packages = [item for item in packages_raw if isinstance(item, dict)]
    existing_package_ids = {
        str(item.get("package_id") or "").strip().lower() for item in packages
    }

    replaced_curated = False
    if package_id in existing_package_ids:
        if not update_existing:
            raise cli.PackageError(
                f"Package '{package_id}' already exists in {curated_path}. "
                "Use --update-existing to replace it."
            )
        for index, item in enumerate(packages):
            package_key = str(item.get("package_id") or "").strip().lower()
            if package_key == package_id:
                packages[index] = curated_entry
                replaced_curated = True
                break
    if not replaced_curated:
        packages.append(curated_entry)
    packages.sort(key=lambda item: str(item.get("package_id") or "").strip().lower())
    curated_payload["packages"] = packages

    families_raw = family_payload.get("families")
    if not isinstance(families_raw, dict):
        raise cli.PackageError(f"Invalid family hints file: {family_path}")
    families = dict(families_raw)
    replaced_family = package_id in families
    if replaced_family and not update_existing:
        raise cli.PackageError(
            f"Family '{package_id}' already exists in {family_path}. "
            "Use --update-existing to replace it."
        )
    families[package_id] = family_entry
    family_payload["families"] = families

    timestamp = _utc_now_z()
    curated_payload["updated_at"] = timestamp
    family_payload["updated_at"] = timestamp

    _validate_data_payloads_with_script(
        fermilink_repo=fermilink_repo,
        channel_id=normalized_channel,
        curated_payload=curated_payload,
        family_payload=family_payload,
    )

    if not dry_run:
        _write_json_atomic(curated_path, curated_payload)
        _write_json_atomic(family_path, family_payload)

    return {
        "channel_id": normalized_channel,
        "curated_path": str(curated_path),
        "family_path": str(family_path),
        "replaced_curated": replaced_curated,
        "replaced_family": replaced_family,
        "dry_run": dry_run,
        "updated_at": timestamp,
    }


def _process_auto_compile_package(
    *,
    package_id: str,
    upstream_repo_url: str,
    github_login: str,
    organization: str | None,
    fermilink_repo: Path,
    workspace_root: Path,
    channel: str,
    max_skills: int,
    core_skill_count: int,
    docs_only: bool,
    keep_compile_artifacts: bool,
    strict_compile_validation: bool,
    update_existing: bool,
    dry_run: bool,
    cleanup_clone: bool,
) -> dict[str, object]:
    cli = _cli()
    upstream_owner, upstream_repo, canonical_upstream = _normalize_github_repo_url(
        upstream_repo_url
    )
    _precheck_metadata_merge_conflicts(
        fermilink_repo=fermilink_repo,
        channel_id=channel,
        package_id=package_id,
        update_existing=update_existing,
    )
    fork = _ensure_public_fork(
        upstream_owner=upstream_owner,
        upstream_repo=upstream_repo,
        github_login=github_login,
        organization=organization,
    )
    clone_dir: Path | None = None
    try:
        clone_dir = _prepare_fork_clone(
            workspace_root=workspace_root,
            package_id=package_id,
            upstream_repo=upstream_repo,
            clone_url=fork["fork_clone_url"],
            default_branch=fork["default_branch"],
        )
        compile_result = _invoke_compile_for_auto_compile(
            package_id=package_id,
            project_root=clone_dir,
            max_skills=max_skills,
            core_skill_count=core_skill_count,
            docs_only=docs_only,
            keep_compile_artifacts=keep_compile_artifacts,
            strict_compile_validation=strict_compile_validation,
        )
        push_result = _commit_and_push_changes(
            repo_dir=clone_dir,
            package_id=package_id,
            default_branch=fork["default_branch"],
        )

        upstream_info = _fetch_repo_info(f"{upstream_owner}/{upstream_repo}")
        upstream_description = str(upstream_info.get("description") or "").strip()
        upstream_homepage = str(upstream_info.get("homepageUrl") or "").strip()
        readme_excerpt = _read_repo_excerpt(clone_dir)
        disambiguation_package_ids = _load_disambiguation_package_ids(
            fermilink_repo=fermilink_repo,
            channel_id=channel,
            package_id=package_id,
        )

        codex_metadata = _generate_metadata_with_codex(
            metadata_repo_dir=clone_dir,
            package_id=package_id,
            upstream_repo_url=canonical_upstream,
            fork_repo_url=fork["fork_url"],
            default_branch=fork["default_branch"],
            upstream_description=upstream_description,
            upstream_homepage=upstream_homepage,
            readme_excerpt=readme_excerpt,
            disambiguation_package_ids=disambiguation_package_ids,
        )

        curated_entry = _build_curated_entry_from_metadata(
            package_id=package_id,
            upstream_repo_url=canonical_upstream,
            upstream_homepage=upstream_homepage,
            fork_owner_repo=fork["fork_name"],
            default_branch=fork["default_branch"],
            metadata_payload=codex_metadata,
        )
        family_entry = _build_family_entry_from_metadata(
            package_id=package_id,
            metadata_payload=codex_metadata,
            disambiguation_package_ids=disambiguation_package_ids,
        )
        _validate_curated_entry_shape(
            package_id=package_id,
            curated_entry=curated_entry,
        )
        _validate_family_entry_shape(family_entry)

        merge_result = _merge_metadata_entries(
            fermilink_repo=fermilink_repo,
            channel_id=channel,
            package_id=package_id,
            curated_entry=curated_entry,
            family_entry=family_entry,
            update_existing=update_existing,
            dry_run=dry_run,
        )
        return {
            "package_id": package_id,
            "upstream_repo_url": canonical_upstream,
            "fork": fork,
            "clone_dir": str(clone_dir),
            "compile": compile_result,
            "push": push_result,
            "metadata": {
                "title": curated_entry.get("title"),
                "description": curated_entry.get("description"),
                "tags": curated_entry.get("tags"),
                "family_description": family_entry.get("description"),
                "strong_keywords": family_entry.get("strong_keywords"),
                "keywords": family_entry.get("keywords"),
                "negative_keywords": family_entry.get("negative_keywords"),
            },
            "merge": merge_result,
            "status": "ok",
        }
    finally:
        if cleanup_clone and isinstance(clone_dir, Path) and clone_dir.exists():
            shutil.rmtree(clone_dir, ignore_errors=True)


def cmd_auto_compile(args: argparse.Namespace) -> int:
    """
    Execute the `auto-compile` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    _ensure_required_commands_available(command_names=("gh", "git"))

    runtime_policy = cli.resolve_agent_runtime_policy()
    if runtime_policy.provider != "codex":
        raise cli.PackageError(
            "auto-compile requires Codex provider for metadata generation. "
            "Run `fermilink agent codex` first."
        )

    specs = _load_auto_compile_specs(
        package_id_arg=getattr(args, "package_id", None),
        upstream_repo_url_arg=getattr(args, "upstream_repo_url", None),
        spec_file_arg=getattr(args, "spec_file", None),
    )
    if not specs:
        raise cli.PackageError("No packages provided for auto-compile.")

    fermilink_repo = Path(str(args.fermilink_repo)).expanduser().resolve()
    if not fermilink_repo.is_dir():
        raise cli.PackageError(f"Invalid --fermilink-repo path: {fermilink_repo}")
    workspace_root = Path(str(args.workspace_root)).expanduser().resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    max_skills = int(getattr(args, "max_skills", 30))
    if max_skills < 2:
        raise cli.PackageError("--max-skills must be >= 2.")
    core_skill_count = int(getattr(args, "core_skill_count", 6))
    if core_skill_count < 1:
        raise cli.PackageError("--core-skill-count must be >= 1.")

    channel = cli.normalize_channel_id(getattr(args, "channel", "skilled-scipkg"))
    github_login = _resolve_github_login()
    organization = _normalize_github_owner(
        getattr(args, "organization", None),
        field_name="--organization",
    )
    fork_owner = organization or github_login

    processed: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    for spec in specs:
        package_id = str(spec["package_id"])
        upstream_repo_url = str(spec["upstream_repo_url"])
        try:
            result = _process_auto_compile_package(
                package_id=package_id,
                upstream_repo_url=upstream_repo_url,
                github_login=github_login,
                organization=organization,
                fermilink_repo=fermilink_repo,
                workspace_root=workspace_root,
                channel=channel,
                max_skills=max_skills,
                core_skill_count=core_skill_count,
                docs_only=bool(getattr(args, "docs_only", False)),
                keep_compile_artifacts=bool(
                    getattr(args, "keep_compile_artifacts", False)
                ),
                strict_compile_validation=bool(
                    getattr(args, "strict_compile_validation", False)
                ),
                update_existing=bool(getattr(args, "update_existing", False)),
                dry_run=bool(getattr(args, "dry_run", False)),
                cleanup_clone=bool(getattr(args, "cleanup_clone", False)),
            )
            processed.append(result)
        except (
            cli.PackageError,
            ValueError,
            OSError,
            RuntimeError,
        ) as exc:
            failed.append(
                {
                    "package_id": package_id,
                    "upstream_repo_url": upstream_repo_url,
                    "error": str(exc),
                }
            )
            if bool(getattr(args, "fail_fast", False)):
                break

    payload = {
        "github_login": github_login,
        "organization": organization,
        "fork_owner": fork_owner,
        "channel": channel,
        "fermilink_repo": str(fermilink_repo),
        "workspace_root": str(workspace_root),
        "dry_run": bool(getattr(args, "dry_run", False)),
        "processed_count": len(processed),
        "failed_count": len(failed),
        "processed": processed,
        "failed": failed,
        "requested_count": len(specs),
    }
    lines = [
        (
            f"Auto-compile processed {len(processed)} package(s) with "
            f"{len(failed)} failure(s)."
        ),
        f"GitHub account: {github_login}.",
        (
            f"Fork owner organization: {organization}."
            if isinstance(organization, str) and organization
            else f"Fork owner account: {fork_owner}."
        ),
        f"Curated channel: {channel}.",
    ]
    if failed:
        for item in failed:
            lines.append(
                f"Failed: {item['package_id']} ({item['upstream_repo_url']}): {item['error']}"
            )
    else:
        lines.append("All packages completed successfully.")

    cli._emit_output(args, payload, lines)
    return 0 if not failed else 2


def cmd_compile(args: argparse.Namespace) -> int:
    """
    Execute the `compile` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    install_off = bool(getattr(args, "install_off", False))
    scipkg_root: Path | None = None
    if not install_off:
        scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)
    project_root = cli._resolve_project_path(args.project_path)
    if not project_root.exists() or not project_root.is_dir():
        raise cli.PackageError(f"Compile path is not a directory: {project_root}")
    max_skills = int(getattr(args, "max_skills", 30))
    if max_skills < 2:
        raise cli.PackageError("--max-skills must be >= 2.")
    core_skill_count = int(getattr(args, "core_skill_count", 6))
    if core_skill_count < 1:
        raise cli.PackageError("--core-skill-count must be >= 1.")
    docs_only_override = bool(getattr(args, "docs_only", False))
    keep_compile_artifacts = bool(getattr(args, "keep_compile_artifacts", False))
    strict_compile_validation = bool(getattr(args, "strict_compile_validation", False))

    if not install_off and scipkg_root is not None:
        registry = cli.load_registry(scipkg_root)
        packages = registry.get("packages", {})
        if isinstance(packages, dict) and package_id in packages:
            raise cli.PackageError(
                f"Warning: package id '{package_id}' already exists. "
                "Choose a new package id for compile."
            )

    tool_source = cli._resolve_compile_tool_source()
    if not tool_source.is_dir():
        raise cli.PackageError(f"Missing compile tool source: {tool_source}")

    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    provider_bin = cli.resolve_provider_binary(
        provider,
        codex_bin=cli.DEFAULT_COMPILE_CODEX_BIN if provider == "codex" else None,
    )

    tool_dest = project_root / "sci-skills-generator"
    if tool_dest.exists():
        raise cli.PackageError(
            f"Compile path already contains {tool_dest.name}/. "
            "Remove it first or choose a different path."
        )

    run_mode = "compile"
    run_id = _build_compile_run_id("compile")
    run_goal = (
        "Compile skills with deterministic generation, targeted enrichment, and audit."
    )
    compile_memory_path = cli._reset_compile_memory_short_term(
        project_root,
        package_id=package_id,
        mode=run_mode,
        run_id=run_id,
        run_goal=run_goal,
    )
    previous_source_inventory = cli._load_previous_source_inventory(project_root)

    shutil.copytree(tool_source, tool_dest)
    compile_runs: list[dict[str, object]] = []
    profile_payload: dict[str, object] = {}
    skill_plan_payload: dict[str, object] = {}
    skill_plan_path = ""
    pass_scope_diffs: dict[str, dict[str, list[str]]] = {}
    generation_result: dict[str, object] = {}
    evidence_payload: dict[str, object] = {}
    validation_payload: dict[str, object] = {}
    memory_update_payload: dict[str, object] = {}
    compile_report_path = ""
    try:
        pass_1_prompt = (
            f"{cli.COMPILE_PROMPT_1}\n\n"
            f"Compile memory file: {compile_memory_path}\n"
            f"Skill plan output file: {cli.COMPILE_SKILL_PLAN_REL_PATH}\n"
            "Read compile memory first and keep this run plan consistent with prior gaps."
        )
        pass_1 = cli._run_codex_compile_pass(
            project_root,
            prompt=pass_1_prompt,
            pass_index=1,
            total_passes=3,
            provider=provider,
            provider_bin=provider_bin,
        )
        pass_1_assistant_text = str(pass_1.pop("assistant_text", "") or "")
        compile_runs.append(pass_1)

        profile_payload = cli._load_compile_profile(
            project_root,
            default_package_name=package_id,
            assistant_text=pass_1_assistant_text,
        )
        skill_plan_payload = cli._load_compile_skill_plan(
            project_root,
            package_id=package_id,
            mode=run_mode,
            assistant_text=pass_1_assistant_text,
        )
        skill_plan_path = cli._write_compile_skill_plan(
            project_root,
            skill_plan=skill_plan_payload,
        )
        generation_result = cli._run_compile_generator(
            project_root,
            tool_dir=tool_dest,
            profile=profile_payload,
            max_skills=max_skills,
            docs_only_override=docs_only_override,
        )
        evidence_payload = cli._build_compile_evidence_bundle(
            project_root,
            core_skill_count=core_skill_count,
        )
        available_skill_ids = cli._list_skill_ids(project_root)
        core_skill_ids_raw = evidence_payload.get("core_skills")
        core_skill_ids = (
            list(core_skill_ids_raw)
            if isinstance(core_skill_ids_raw, list)
            else []
        )
        skill_plan_payload = cli._normalize_compile_skill_plan(
            skill_plan_payload,
            package_id=package_id,
            mode=run_mode,
            available_skill_ids=available_skill_ids,
            core_skill_ids=core_skill_ids,
        )
        skill_plan_path = cli._write_compile_skill_plan(
            project_root,
            skill_plan=skill_plan_payload,
        )

        pass_2_prompt = (
            f"{cli.COMPILE_PROMPT_2}\n\n"
            f"Compile memory file: {compile_memory_path}\n"
            f"Skill plan JSON file: {skill_plan_path or cli.COMPILE_SKILL_PLAN_REL_PATH}\n"
            "Allowed edit scope in pass 2:\n"
            "- skills/...\n\n"
            "Skill plan JSON payload:\n"
            f"{json.dumps(skill_plan_payload, indent=2)}\n"
        )
        pass_2_before_snapshot = cli._snapshot_skills_tree(project_root)

        pass_2 = cli._run_codex_compile_pass(
            project_root,
            prompt=pass_2_prompt,
            pass_index=2,
            total_passes=3,
            provider=provider,
            provider_bin=provider_bin,
        )
        pass_2.pop("assistant_text", None)
        compile_runs.append(pass_2)
        pass_2_after_snapshot = cli._snapshot_skills_tree(project_root)
        pass_2_diff = cli._diff_skills_tree_snapshot(
            pass_2_before_snapshot,
            pass_2_after_snapshot,
        )
        pass_scope_diffs["pass_2"] = pass_2_diff
        cli._assert_skills_change_scope(
            change_diff=pass_2_diff,
            allowed_prefixes=["skills"],
            stage_label="compile pass 2",
        )

        pass_3_prompt = (
            f"{cli.COMPILE_PROMPT_3}\n\n"
            f"Compile memory file: {compile_memory_path}\n"
            f"Skill plan JSON file: {skill_plan_path or cli.COMPILE_SKILL_PLAN_REL_PATH}\n"
            "Allowed edit scope in pass 3:\n"
            "- skills/...\n"
            "- skills/.compile_report.json (optional notes)\n"
        )
        pass_3_before_snapshot = cli._snapshot_skills_tree(project_root)

        pass_3 = cli._run_codex_compile_pass(
            project_root,
            prompt=pass_3_prompt,
            pass_index=3,
            total_passes=3,
            provider=provider,
            provider_bin=provider_bin,
        )
        pass_3.pop("assistant_text", None)
        compile_runs.append(pass_3)
        pass_3_after_snapshot = cli._snapshot_skills_tree(project_root)
        pass_3_diff = cli._diff_skills_tree_snapshot(
            pass_3_before_snapshot,
            pass_3_after_snapshot,
        )
        pass_scope_diffs["pass_3"] = pass_3_diff
        cli._assert_skills_change_scope(
            change_diff=pass_3_diff,
            allowed_prefixes=["skills"],
            stage_label="compile pass 3",
        )

        validation_payload = cli._validate_compiled_skills(
            project_root,
            profile=profile_payload,
            core_skill_count=core_skill_count,
            skill_plan=skill_plan_payload,
            source_inventory=evidence_payload.get("source_inventory")
            if isinstance(evidence_payload, dict)
            else None,
            previous_source_inventory=previous_source_inventory,
        )
        compile_report_path = cli._write_compile_report(
            project_root,
            payload={
                "mode": run_mode,
                "run_id": run_id,
                "compiled_package_id": package_id,
                "project_root": str(project_root),
                "compile_memory_path": compile_memory_path,
                "profile": profile_payload,
                "skill_plan": skill_plan_payload,
                "skill_plan_path": skill_plan_path,
                "generation": generation_result,
                "evidence": evidence_payload,
                "pass_scope_diffs": pass_scope_diffs,
                "passes": compile_runs,
                "validation": validation_payload,
            },
        )
        memory_update_payload = cli._record_compile_memory_run(
            project_root,
            package_id=package_id,
            mode=run_mode,
            run_id=run_id,
            run_goal=run_goal,
            skill_plan=skill_plan_payload,
            pass_scope_diffs=pass_scope_diffs,
            evidence=evidence_payload,
            validation=validation_payload,
            compile_report_path=compile_report_path,
        )
        if strict_compile_validation and not bool(validation_payload.get("ok", False)):
            errors = validation_payload.get("errors")
            if isinstance(errors, list) and errors:
                summary = "; ".join(str(item) for item in errors[:5])
            else:
                summary = "unknown validation error"
            raise cli.PackageError(f"Compile validation failed: {summary}")
    finally:
        if not keep_compile_artifacts:
            shutil.rmtree(tool_dest, ignore_errors=True)

    if not keep_compile_artifacts and tool_dest.exists():
        raise cli.PackageError(
            f"Failed to clean up temporary tool directory: {tool_dest}"
        )

    installed: dict[str, object] | None = None
    router_sync: dict[str, object] | None = None
    active: str | None = None
    if not install_off and scipkg_root is not None:
        installed = cli.install_from_local_path(
            scipkg_root,
            package_id,
            local_path=project_root,
            title=args.title,
            activate=args.activate,
            force=False,
        )
        if not args.no_router_sync:
            router_sync = cli.sync_router_rules(scipkg_root)
        active_raw = cli.load_registry(scipkg_root).get("active_package")
        if isinstance(active_raw, str):
            active = active_raw
    payload = {
        "compiled_package_id": package_id,
        "project_root": str(project_root),
        "run_id": run_id,
        "compile_runs": compile_runs,
        "compile_memory": compile_memory_path,
        "compile_memory_update": memory_update_payload,
        "compile_profile": profile_payload,
        "skill_plan": skill_plan_payload,
        "skill_plan_path": skill_plan_path,
        "generation": generation_result,
        "evidence": evidence_payload,
        "pass_scope_diffs": pass_scope_diffs,
        "previous_source_inventory": previous_source_inventory,
        "validation": validation_payload,
        "validation_enforced": strict_compile_validation,
        "compile_report": compile_report_path,
        "installed": installed,
        "active_package": active,
        "router_sync": router_sync,
        "install_off": install_off,
        "scipkg_root": str(scipkg_root) if isinstance(scipkg_root, Path) else None,
    }
    source_links_total = validation_payload.get("source_links_total", 0)
    validation_ok = bool(validation_payload.get("ok", False))
    validation_errors = validation_payload.get("errors", [])
    error_count = len(validation_errors) if isinstance(validation_errors, list) else 0
    lines = [
        f"Compiled skills for '{package_id}' from {project_root}.",
        f"Validated skills with {source_links_total} source-code links.",
        (
            "Validation status: ok."
            if validation_ok
            else (
                f"Validation status: {error_count} finding(s) (non-blocking). "
                "Use --strict-compile-validation to enforce failures."
            )
        ),
        (
            "Install step skipped (--install-off); updated local skills only."
            if install_off
            else (
                f"Installed to scientific packages. Active package: {active}."
                if isinstance(active, str) and active
                else "Installed to scientific packages."
            )
        ),
    ]
    cli._emit_output(args, payload, lines)
    return 0


def cmd_recompile(args: argparse.Namespace) -> int:
    """
    Execute the `recompile` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    install_off = bool(getattr(args, "install_off", False))
    scipkg_root: Path | None = None
    if not install_off:
        scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)
    project_root = cli._resolve_project_path(args.project_path)
    if not project_root.exists() or not project_root.is_dir():
        raise cli.PackageError(f"Recompile path is not a directory: {project_root}")

    raw_doc_path = str(getattr(args, "doc", "") or "").strip()
    raw_data_dir = str(getattr(args, "data_dir", "") or "").strip()
    comment_text = " ".join(str(getattr(args, "comment", "") or "").split()).strip()
    resolved_doc_path: Path | None = None
    resolved_data_dir: Path | None = None
    if comment_text and not raw_doc_path:
        raise cli.PackageError("--comment requires --doc.")
    if raw_data_dir and not raw_doc_path:
        raise cli.PackageError("--data-dir requires --doc.")
    if raw_doc_path:
        resolved_doc_path = cli._resolve_project_path(raw_doc_path)
        if not resolved_doc_path.exists():
            raise cli.PackageError(f"--doc does not exist: {resolved_doc_path}")
        if not resolved_doc_path.is_file():
            raise cli.PackageError(f"--doc must be a file: {resolved_doc_path}")
        try:
            resolved_doc_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise cli.PackageError(
                f"--doc is not readable: {resolved_doc_path}: {exc}"
            ) from exc
    if raw_data_dir:
        resolved_data_dir = cli._resolve_project_path(raw_data_dir)
        if not resolved_data_dir.exists():
            raise cli.PackageError(f"--data-dir does not exist: {resolved_data_dir}")
        if not resolved_data_dir.is_dir():
            raise cli.PackageError(f"--data-dir must be a directory: {resolved_data_dir}")
        try:
            next(resolved_data_dir.iterdir(), None)
        except OSError as exc:
            raise cli.PackageError(
                f"--data-dir is not readable: {resolved_data_dir}: {exc}"
            ) from exc
    paper_mode_enabled = bool(
        isinstance(resolved_doc_path, Path)
        or isinstance(resolved_data_dir, Path)
        or bool(comment_text)
    )
    paper_data_context: dict[str, object] | None = None
    paper_staged_assets: dict[str, object] | None = None
    if paper_mode_enabled:
        paper_run_dir = project_root / cli.COMPILE_EVIDENCE_DIR_REL_PATH / "paper_context"
        paper_data_context = cli._resolve_invocation_data_context(
            repo_dir=project_root,
            run_dir=paper_run_dir,
            workflow_name="recompile",
            args=args,
        )
        if bool(paper_data_context.get("enabled")):
            paper_data_context = cli._prepare_workflow_data_artifacts(
                repo_dir=project_root,
                run_dir=paper_run_dir,
                data_context=paper_data_context,
            )
        paper_staged_assets = cli._stage_recompile_paper_assets(
            project_root,
            data_context=paper_data_context,
        )

    core_skill_count = int(getattr(args, "core_skill_count", 6))
    if core_skill_count < 1:
        raise cli.PackageError("--core-skill-count must be >= 1.")
    docs_only_override = bool(getattr(args, "docs_only", False))
    keep_compile_artifacts = bool(getattr(args, "keep_compile_artifacts", False))
    strict_compile_validation = bool(getattr(args, "strict_compile_validation", False))
    # Recompile is an in-place refresh workflow and should always replace the
    # installed package payload for the same package id.
    force_install = True

    skills_root = project_root / "skills"
    if not skills_root.is_dir():
        raise cli.PackageError(
            f"Recompile requires an existing skills/ folder: {skills_root}"
        )

    tool_source = cli._resolve_compile_tool_source()
    if not tool_source.is_dir():
        raise cli.PackageError(f"Missing compile tool source: {tool_source}")

    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    provider_bin = cli.resolve_provider_binary(
        provider,
        codex_bin=cli.DEFAULT_COMPILE_CODEX_BIN if provider == "codex" else None,
    )

    tool_dest = project_root / "sci-skills-generator"
    if tool_dest.exists():
        raise cli.PackageError(
            f"Recompile path already contains {tool_dest.name}/. "
            "Remove it first or choose a different path."
        )

    run_mode = "recompile_paper" if paper_mode_enabled else "recompile"
    run_id = _build_compile_run_id("recompile")
    run_goal = (
        "Recompile paper tutorial and refresh package skills."
        if paper_mode_enabled
        else "Refresh existing skills with targeted coverage updates and audit."
    )
    compile_memory_path = cli._reset_compile_memory_short_term(
        project_root,
        package_id=package_id,
        mode=run_mode,
        run_id=run_id,
        run_goal=run_goal,
    )
    previous_source_inventory = cli._load_previous_source_inventory(project_root)

    shutil.copytree(tool_source, tool_dest)
    compile_runs: list[dict[str, object]] = []
    profile_payload: dict[str, object] = {}
    skill_plan_payload: dict[str, object] = {}
    skill_plan_path = ""
    paper_context_payload: dict[str, object] | None = None
    paper_plan_payload: dict[str, object] | None = None
    paper_plan_rel = ""
    paper_skill_id = ""
    pass_scope_diffs: dict[str, dict[str, list[str]]] = {}
    evidence_payload: dict[str, object] = {}
    validation_payload: dict[str, object] = {}
    paper_validation_payload: dict[str, object] | None = None
    memory_update_payload: dict[str, object] = {}
    compile_report_path = ""
    manuscript_text = ""
    manuscript_source = ""
    if paper_mode_enabled and isinstance(resolved_doc_path, Path):
        try:
            manuscript_text = resolved_doc_path.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError as exc:
            raise cli.PackageError(
                f"Failed to read --doc file: {resolved_doc_path}: {exc}"
            ) from exc
        if not manuscript_text.strip():
            raise cli.PackageError(f"--doc is empty: {resolved_doc_path}")
        try:
            manuscript_source = str(resolved_doc_path.relative_to(project_root))
        except ValueError:
            manuscript_source = str(resolved_doc_path)
    try:
        pass_1_prompt = (
            f"{cli.RECOMPILE_PROMPT_1}\n\n"
            f"Compile memory file: {compile_memory_path}\n"
            f"Skill plan output file: {cli.COMPILE_SKILL_PLAN_REL_PATH}\n"
            "Read compile memory first and keep this run plan consistent with prior gaps."
        )
        if paper_mode_enabled:
            scope_text = (
                comment_text
                if comment_text
                else "No --comment provided; include all key paper results."
            )
            pass_1_prompt = (
                f"{cli.RECOMPILE_PAPER_PROMPT_1_PLAN}\n\n"
                f"Package id: {package_id}\n"
                f"Paper source file: {manuscript_source}\n"
                f"Scope directive: {scope_text}\n"
                f"Write paper plan JSON to: {cli.RECOMPILE_PAPER_PLAN_REL_PATH}\n"
                f"Write compile profile JSON to: {cli.COMPILE_PROFILE_REL_PATH}\n"
                f"Paper context directory: {cli.RECOMPILE_PAPER_CONTEXT_DIR_REL_PATH}\n\n"
                "Original manuscript content:\n"
                f"{manuscript_text.strip()}\n"
            )
            pass_1_prompt += (
                f"\nCompile memory file: {compile_memory_path}\n"
                "Read compile memory first and align paper tutorial edits with prior gaps.\n"
            )
            if isinstance(paper_data_context, dict) and bool(
                paper_data_context.get("enabled")
            ):
                artifacts = (
                    paper_data_context.get("artifacts")
                    if isinstance(paper_data_context.get("artifacts"), dict)
                    else {}
                )
                summary_rel = str(artifacts.get("summary") or "").strip()
                manifest_rel = str(
                    artifacts.get("manifest_compact") or artifacts.get("manifest") or ""
                ).strip()
                if summary_rel:
                    pass_1_prompt += f"\nData summary file available: {summary_rel}\n"
                if manifest_rel:
                    pass_1_prompt += f"Compact data manifest file available: {manifest_rel}\n"

        pass_1 = cli._run_codex_compile_pass(
            project_root,
            prompt=pass_1_prompt,
            pass_index=1,
            total_passes=3,
            provider=provider,
            provider_bin=provider_bin,
        )
        pass_1_assistant_text = str(pass_1.pop("assistant_text", "") or "")
        compile_runs.append(pass_1)

        if paper_mode_enabled:
            raw_plan_payload = cli._extract_recompile_paper_plan_from_assistant_text(
                pass_1_assistant_text
            )
            if raw_plan_payload is None:
                raise cli.PackageError(
                    f"Paper-mode recompile pass 1 response must include "
                    f"<{cli.RECOMPILE_PAPER_PLAN_TAG}>...</{cli.RECOMPILE_PAPER_PLAN_TAG}>."
                )
            paper_plan_payload = cli._normalize_recompile_paper_plan(
                raw_plan_payload,
                paper_source=manuscript_source or str(resolved_doc_path),
                package_id=package_id,
                scope_comment=comment_text or None,
            )
            paper_plan_rel = cli._write_recompile_paper_plan(
                project_root,
                paper_plan=paper_plan_payload,
            )
            if not isinstance(resolved_doc_path, Path):
                raise cli.PackageError(
                    "Internal paper-mode error: --doc path missing after validation."
                )
            paper_skill_id = cli._derive_recompile_paper_skill_id(
                project_root,
                package_id=package_id,
                doc_path=resolved_doc_path,
                comment=comment_text or None,
                paper_plan=paper_plan_payload,
            )
            cli._ensure_recompile_paper_skill_scaffold(
                project_root,
                skill_id=paper_skill_id,
                paper_plan=paper_plan_payload,
            )
            cli._initialize_recompile_paper_sidecar_files(
                project_root,
                paper_plan=paper_plan_payload,
                paper_skill_id=paper_skill_id,
            )

        profile_payload = cli._load_compile_profile(
            project_root,
            default_package_name=package_id,
            assistant_text=pass_1_assistant_text,
        )
        if docs_only_override:
            profile_payload["docs_only"] = True
        if not paper_mode_enabled:
            skill_plan_payload = cli._load_compile_skill_plan(
                project_root,
                package_id=package_id,
                mode=run_mode,
                assistant_text=pass_1_assistant_text,
            )
            skill_plan_path = cli._write_compile_skill_plan(
                project_root,
                skill_plan=skill_plan_payload,
            )

        if isinstance(resolved_doc_path, Path):
            paper_context_payload = cli._build_recompile_paper_context(
                project_root,
                doc_path=resolved_doc_path,
                comment=comment_text or None,
                data_context=paper_data_context,
                staged_assets=paper_staged_assets,
                paper_plan=paper_plan_payload,
                paper_skill_id=paper_skill_id,
            )

        evidence_payload = cli._build_recompile_evidence_bundle(
            project_root,
            profile=profile_payload,
            core_skill_count=core_skill_count,
        )
        if isinstance(paper_context_payload, dict):
            evidence_payload["paper_context"] = paper_context_payload
        if isinstance(paper_staged_assets, dict):
            evidence_payload["paper_staged_assets"] = paper_staged_assets

        if not paper_mode_enabled:
            available_skill_ids = cli._list_skill_ids(project_root)
            core_skill_ids_raw = evidence_payload.get("core_skills")
            core_skill_ids = (
                list(core_skill_ids_raw)
                if isinstance(core_skill_ids_raw, list)
                else []
            )
            skill_plan_payload = cli._normalize_compile_skill_plan(
                skill_plan_payload,
                package_id=package_id,
                mode=run_mode,
                available_skill_ids=available_skill_ids,
                core_skill_ids=core_skill_ids,
            )
            skill_plan_path = cli._write_compile_skill_plan(
                project_root,
                skill_plan=skill_plan_payload,
            )

        pass_2_prompt = (
            f"{cli.RECOMPILE_PROMPT_2}\n\n"
            f"Compile memory file: {compile_memory_path}\n"
            f"Skill plan JSON file: {skill_plan_path or cli.COMPILE_SKILL_PLAN_REL_PATH}\n"
            "Allowed edit scope in pass 2:\n"
            "- skills/...\n"
        )
        if not paper_mode_enabled and isinstance(skill_plan_payload, dict):
            pass_2_prompt += (
                "\nSkill plan JSON payload:\n"
                f"{json.dumps(skill_plan_payload, indent=2)}\n"
            )
        pass_2_before_snapshot: dict[str, str] | None = cli._snapshot_skills_tree(
            project_root
        )
        if paper_mode_enabled:
            if not isinstance(paper_plan_payload, dict):
                raise cli.PackageError(
                    "Internal paper-mode error: paper plan payload missing after pass 1."
                )
            pass_2_before_snapshot = cli._snapshot_recompile_paper_skills(project_root)
            pass_2_prompt = (
                f"{cli.RECOMPILE_PAPER_PROMPT_2_TUTORIAL}\n\n"
                f"Package id: {package_id}\n"
                f"Paper plan JSON file: {paper_plan_rel}\n"
                f"Target tutorial skill id: {paper_skill_id}\n"
                f"Target tutorial skill root: skills/{paper_skill_id}/\n"
                f"Figure-data map file: {cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH}\n"
                f"Paper skill manifest file: {cli.RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH}\n"
                f"Staged assets manifest: {cli.RECOMPILE_PAPER_STAGED_ASSETS_MANIFEST_REL_PATH}\n"
                f"Staged assets root: {cli.RECOMPILE_PAPER_STAGED_ASSETS_DIR_REL_PATH}\n\n"
                f"Compile memory file: {compile_memory_path}\n"
                "Allowed edit scope in pass 2:\n"
                f"- skills/{paper_skill_id}/...\n"
                f"- {cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH}\n"
                f"- {cli.RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH}\n\n"
                "Paper plan JSON payload:\n"
                f"{json.dumps(paper_plan_payload, indent=2)}\n"
            )
            if isinstance(paper_data_context, dict) and bool(
                paper_data_context.get("enabled")
            ):
                artifacts = (
                    paper_data_context.get("artifacts")
                    if isinstance(paper_data_context.get("artifacts"), dict)
                    else {}
                )
                summary_rel = str(artifacts.get("summary") or "").strip()
                manifest_rel = str(
                    artifacts.get("manifest_compact") or artifacts.get("manifest") or ""
                ).strip()
                manifest_full_rel = str(artifacts.get("manifest_full") or "").strip()
                if summary_rel:
                    pass_2_prompt += f"\nData summary file: {summary_rel}\n"
                if manifest_rel:
                    pass_2_prompt += f"Compact manifest file: {manifest_rel}\n"
                if manifest_full_rel:
                    pass_2_prompt += f"Full manifest file: {manifest_full_rel}\n"

        pass_2 = cli._run_codex_compile_pass(
            project_root,
            prompt=pass_2_prompt,
            pass_index=2,
            total_passes=3,
            provider=provider,
            provider_bin=provider_bin,
        )
        pass_2.pop("assistant_text", None)
        compile_runs.append(pass_2)

        if not paper_mode_enabled and isinstance(pass_2_before_snapshot, dict):
            pass_2_after_snapshot = cli._snapshot_skills_tree(project_root)
            pass_2_diff = cli._diff_skills_tree_snapshot(
                pass_2_before_snapshot,
                pass_2_after_snapshot,
            )
            pass_scope_diffs["pass_2"] = pass_2_diff
            cli._assert_skills_change_scope(
                change_diff=pass_2_diff,
                allowed_prefixes=["skills"],
                stage_label="recompile pass 2",
            )

        if paper_mode_enabled and isinstance(pass_2_before_snapshot, dict):
            pass_2_after_snapshot = cli._snapshot_recompile_paper_skills(project_root)
            pass_2_diff = cli._diff_recompile_paper_skills_snapshot(
                pass_2_before_snapshot,
                pass_2_after_snapshot,
            )
            pass_scope_diffs["pass_2"] = pass_2_diff
            cli._assert_recompile_paper_change_scope(
                change_diff=pass_2_diff,
                allowed_prefixes=[
                    f"skills/{paper_skill_id}",
                    cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH,
                    cli.RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH,
                ],
                stage_label="paper-mode pass 2",
            )

        pass_3_prompt = (
            f"{cli.RECOMPILE_PROMPT_3}\n\n"
            f"Compile memory file: {compile_memory_path}\n"
            f"Skill plan JSON file: {skill_plan_path or cli.COMPILE_SKILL_PLAN_REL_PATH}\n"
            "Allowed edit scope in pass 3:\n"
            "- skills/...\n"
            "- skills/.compile_report.json (optional notes)\n"
        )
        pass_3_before_snapshot: dict[str, str] | None = cli._snapshot_skills_tree(
            project_root
        )
        if paper_mode_enabled:
            pass_3_before_snapshot = cli._snapshot_recompile_paper_skills(project_root)
            figure_data_map_text = ""
            figure_data_map_path = project_root / cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH
            if figure_data_map_path.is_file():
                figure_data_map_text = figure_data_map_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            pass_3_prompt = (
                f"{cli.RECOMPILE_PAPER_PROMPT_3_AUDIT}\n\n"
                f"Package id: {package_id}\n"
                f"Paper plan JSON file: {paper_plan_rel}\n"
                f"Paper context file: {cli.RECOMPILE_PAPER_CONTEXT_REL_PATH}\n"
                f"Figure-data map file: {cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH}\n"
                f"Paper skill manifest file: {cli.RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH}\n"
                f"Target tutorial skill id: {paper_skill_id}\n"
                f"Optional manuscript file for double check: {manuscript_source}\n"
                f"Compile memory file: {compile_memory_path}\n"
            )
            if isinstance(resolved_data_dir, Path):
                pass_3_prompt += f"Optional supplementary data dir: {resolved_data_dir}\n"
            pass_3_prompt += (
                "\nAllowed edit scope in pass 3:\n"
                f"- skills/{paper_skill_id}/...\n"
                "- skills/*-index/SKILL.md and related index references for routing update\n"
                f"- {cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH}\n"
                f"- {cli.RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH}\n\n"
                "Paper plan JSON payload:\n"
                f"{json.dumps(paper_plan_payload, indent=2)}\n"
            )
            if figure_data_map_text.strip():
                pass_3_prompt += (
                    "\nCurrent figure-data map JSON payload:\n"
                    f"{figure_data_map_text.strip()}\n"
                )

        pass_3 = cli._run_codex_compile_pass(
            project_root,
            prompt=pass_3_prompt,
            pass_index=3,
            total_passes=3,
            provider=provider,
            provider_bin=provider_bin,
        )
        pass_3.pop("assistant_text", None)
        compile_runs.append(pass_3)

        if not paper_mode_enabled and isinstance(pass_3_before_snapshot, dict):
            pass_3_after_snapshot = cli._snapshot_skills_tree(project_root)
            pass_3_diff = cli._diff_skills_tree_snapshot(
                pass_3_before_snapshot,
                pass_3_after_snapshot,
            )
            pass_scope_diffs["pass_3"] = pass_3_diff
            cli._assert_skills_change_scope(
                change_diff=pass_3_diff,
                allowed_prefixes=["skills"],
                stage_label="recompile pass 3",
            )

        if paper_mode_enabled and isinstance(pass_3_before_snapshot, dict):
            pass_3_after_snapshot = cli._snapshot_recompile_paper_skills(project_root)
            pass_3_diff = cli._diff_recompile_paper_skills_snapshot(
                pass_3_before_snapshot,
                pass_3_after_snapshot,
            )
            pass_scope_diffs["pass_3"] = pass_3_diff
            index_prefixes = []
            for entry in skills_root.iterdir():
                if not entry.is_dir() or not entry.name.endswith("-index"):
                    continue
                if not (entry / "SKILL.md").is_file():
                    continue
                index_prefixes.append(f"skills/{entry.name}")
            cli._assert_recompile_paper_change_scope(
                change_diff=pass_3_diff,
                allowed_prefixes=[
                    f"skills/{paper_skill_id}",
                    *index_prefixes,
                    cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH,
                    cli.RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH,
                ],
                stage_label="paper-mode pass 3",
            )

        validation_payload = cli._validate_compiled_skills(
            project_root,
            profile=profile_payload,
            core_skill_count=core_skill_count,
            skill_plan=skill_plan_payload if isinstance(skill_plan_payload, dict) else None,
            source_inventory=evidence_payload.get("source_inventory")
            if isinstance(evidence_payload, dict)
            else None,
            previous_source_inventory=previous_source_inventory,
        )
        if paper_mode_enabled:
            paper_validation_payload = cli._validate_recompile_paper_outputs(
                project_root,
                paper_plan=paper_plan_payload,
                paper_skill_id=paper_skill_id,
                doc_path=resolved_doc_path,
                data_dir=resolved_data_dir,
                staged_assets=paper_staged_assets,
            )
            validation_payload["paper"] = paper_validation_payload
            paper_errors = paper_validation_payload.get("errors")
            if isinstance(paper_errors, list) and paper_errors:
                existing_errors = validation_payload.get("errors")
                merged_errors = (
                    list(existing_errors) if isinstance(existing_errors, list) else []
                )
                merged_errors.extend(
                    f"[paper] {str(item)}" for item in paper_errors if str(item).strip()
                )
                validation_payload["errors"] = merged_errors
            paper_warnings = paper_validation_payload.get("warnings")
            if isinstance(paper_warnings, list) and paper_warnings:
                existing_warnings = validation_payload.get("warnings")
                merged_warnings = (
                    list(existing_warnings)
                    if isinstance(existing_warnings, list)
                    else []
                )
                merged_warnings.extend(
                    f"[paper] {str(item)}"
                    for item in paper_warnings
                    if str(item).strip()
                )
                validation_payload["warnings"] = merged_warnings
            validation_payload["ok"] = bool(validation_payload.get("ok", False)) and bool(
                paper_validation_payload.get("ok", False)
            )
        compile_report_path = cli._write_compile_report(
            project_root,
            payload={
                "mode": run_mode,
                "run_id": run_id,
                "recompiled_package_id": package_id,
                "project_root": str(project_root),
                "compile_memory_path": compile_memory_path,
                "profile": profile_payload,
                "skill_plan": skill_plan_payload if isinstance(skill_plan_payload, dict) else None,
                "skill_plan_path": skill_plan_path or None,
                "paper_context": paper_context_payload,
                "paper_plan": paper_plan_payload,
                "paper_plan_path": paper_plan_rel,
                "paper_skill_id": paper_skill_id,
                "pass_scope_diffs": pass_scope_diffs,
                "paper_pass_scope_diffs": pass_scope_diffs,
                "paper_staged_assets": paper_staged_assets,
                "paper_validation": paper_validation_payload,
                "evidence": evidence_payload,
                "previous_source_inventory": previous_source_inventory,
                "passes": compile_runs,
                "validation": validation_payload,
            },
        )
        memory_update_payload = cli._record_compile_memory_run(
            project_root,
            package_id=package_id,
            mode=run_mode,
            run_id=run_id,
            run_goal=run_goal,
            skill_plan=skill_plan_payload if isinstance(skill_plan_payload, dict) else None,
            pass_scope_diffs=pass_scope_diffs,
            evidence=evidence_payload,
            validation=validation_payload,
            compile_report_path=compile_report_path,
        )
        if strict_compile_validation and not bool(validation_payload.get("ok", False)):
            errors = validation_payload.get("errors")
            if isinstance(errors, list) and errors:
                summary = "; ".join(str(item) for item in errors[:5])
            else:
                summary = "unknown validation error"
            raise cli.PackageError(f"Recompile validation failed: {summary}")
    finally:
        if not keep_compile_artifacts:
            shutil.rmtree(tool_dest, ignore_errors=True)

    if not keep_compile_artifacts and tool_dest.exists():
        raise cli.PackageError(
            f"Failed to clean up temporary tool directory: {tool_dest}"
        )

    installed: dict[str, object] | None = None
    router_sync: dict[str, object] | None = None
    active: str | None = None
    if not install_off and scipkg_root is not None:
        installed = cli.install_from_local_path(
            scipkg_root,
            package_id,
            local_path=project_root,
            title=args.title,
            activate=args.activate,
            force=force_install,
        )
        if not args.no_router_sync:
            router_sync = cli.sync_router_rules(scipkg_root)
        active_raw = cli.load_registry(scipkg_root).get("active_package")
        if isinstance(active_raw, str):
            active = active_raw
    payload = {
        "recompiled_package_id": package_id,
        "project_root": str(project_root),
        "run_id": run_id,
        "run_mode": run_mode,
        "doc_path": str(resolved_doc_path) if isinstance(resolved_doc_path, Path) else None,
        "data_dir": str(resolved_data_dir) if isinstance(resolved_data_dir, Path) else None,
        "comment": comment_text or None,
        "compile_memory": compile_memory_path,
        "compile_memory_update": memory_update_payload,
        "skill_plan": skill_plan_payload if isinstance(skill_plan_payload, dict) else None,
        "skill_plan_path": skill_plan_path or None,
        "paper_mode": paper_mode_enabled,
        "paper_data_context": paper_data_context if isinstance(paper_data_context, dict) else None,
        "paper_context": paper_context_payload if isinstance(paper_context_payload, dict) else None,
        "paper_plan": paper_plan_payload if isinstance(paper_plan_payload, dict) else None,
        "paper_plan_path": paper_plan_rel or None,
        "paper_skill_id": paper_skill_id or None,
        "pass_scope_diffs": pass_scope_diffs,
        "paper_pass_scope_diffs": pass_scope_diffs,
        "paper_staged_assets": paper_staged_assets if isinstance(paper_staged_assets, dict) else None,
        "paper_validation": paper_validation_payload if isinstance(paper_validation_payload, dict) else None,
        "compile_runs": compile_runs,
        "compile_profile": profile_payload,
        "evidence": evidence_payload,
        "previous_source_inventory": previous_source_inventory,
        "validation": validation_payload,
        "validation_enforced": strict_compile_validation,
        "compile_report": compile_report_path,
        "installed": installed,
        "active_package": active,
        "router_sync": router_sync,
        "force_install": force_install,
        "install_off": install_off,
        "scipkg_root": str(scipkg_root) if isinstance(scipkg_root, Path) else None,
    }
    source_links_total = validation_payload.get("source_links_total", 0)
    validation_ok = bool(validation_payload.get("ok", False))
    validation_errors = validation_payload.get("errors", [])
    error_count = len(validation_errors) if isinstance(validation_errors, list) else 0
    lines = [
        f"Recompiled skills for '{package_id}' from {project_root}.",
        f"Validated skills with {source_links_total} source-code links.",
        (
            "Validation status: ok."
            if validation_ok
            else (
                f"Validation status: {error_count} finding(s) (non-blocking). "
                "Use --strict-compile-validation to enforce failures."
            )
        ),
        (
            "Install step skipped (--install-off); updated local skills only."
            if install_off
            else (
                f"Installed to scientific packages. Active package: {active}."
                if isinstance(active, str) and active
                else "Installed to scientific packages."
            )
        ),
    ]
    cli._emit_output(args, payload, lines)
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """
    Execute the `install` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    raw_package_id = getattr(args, "package_id", None)
    if isinstance(raw_package_id, list):
        requested_ids = [
            item for item in raw_package_id if isinstance(item, str) and item.strip()
        ]
    elif isinstance(raw_package_id, str) and raw_package_id.strip():
        requested_ids = [raw_package_id.strip()]
    else:
        requested_ids = []
    if not requested_ids:
        raise cli.PackageError("Package id is required for fermilink install.")

    requested_version_raw = getattr(args, "version_id", None)
    requested_version = (
        str(requested_version_raw).strip()
        if isinstance(requested_version_raw, str)
        else ""
    )
    if requested_version == "":
        requested_version = None

    require_verified = bool(getattr(args, "require_verified", False))
    if requested_version and (args.local_path or args.zip_url):
        raise cli.PackageError("--version only applies to curated channel installs.")
    if require_verified and (args.local_path or args.zip_url):
        raise cli.PackageError(
            "--require-verified only applies to curated channel installs."
        )

    package_ids = [cli.normalize_package_id(item) for item in requested_ids]
    normalized_channel = cli.normalize_channel_id(args.channel)
    if len(package_ids) > 1:
        if args.activate:
            raise cli.PackageError(
                "Cannot combine multiple package ids with --activate/--active. "
                "Install them first, then run `fermilink activate <package_id>`."
            )
        if args.local_path:
            raise cli.PackageError(
                "Cannot combine multiple package ids with --local-path."
            )
        if args.zip_url:
            raise cli.PackageError(
                "Cannot combine multiple package ids with --zip-url."
            )
        if args.title:
            raise cli.PackageError("Cannot combine multiple package ids with --title.")
        if requested_version:
            raise cli.PackageError(
                "Cannot combine multiple package ids with --version."
            )

        installed: list[dict[str, object]] = []
        sources: dict[str, str] = {}
        selected_versions: dict[str, str] = {}
        unverified: list[str] = []
        for package_id in package_ids:
            curated = cli.resolve_curated_package(
                package_id, channel=normalized_channel
            )
            selected_version = cli.select_package_version(curated)
            if require_verified and not selected_version.verified:
                raise cli.PackageError(
                    f"Selected curated version '{selected_version.version_id}' for package "
                    f"'{package_id}' in channel '{normalized_channel}' is not verified. "
                    "Use a verified version or remove --require-verified."
                )
            if not selected_version.verified:
                unverified.append(f"{package_id}@{selected_version.version_id}")

            meta = cli.install_from_zip(
                scipkg_root,
                package_id,
                zip_url=selected_version.source_archive_url,
                title=curated.title,
                activate=False,
                force=args.force,
                max_zip_bytes=args.max_zip_bytes,
            )
            installed_id = str(meta.get("id") or package_id)
            _save_curated_install_metadata(
                scipkg_root,
                installed_id,
                channel=normalized_channel,
                curated_package_id=curated.package_id,
                version_id=selected_version.version_id,
                source_archive_url=selected_version.source_archive_url,
                verified=selected_version.verified,
                source_ref_type=selected_version.source_ref_type,
                source_ref_value=selected_version.source_ref_value,
            )
            installed.append(meta)
            sources[installed_id] = str(selected_version.source_archive_url)
            selected_versions[installed_id] = selected_version.version_id

        router = None
        if not args.no_router_sync:
            router = cli.sync_router_rules(scipkg_root)

        active = cli.load_registry(scipkg_root).get("active_package")
        payload = {
            "installed": installed,
            "sources": sources,
            "selected_versions": selected_versions,
            "require_verified": require_verified,
            "scipkg_root": str(scipkg_root),
            "router_sync": router,
            "active_package": active,
        }
        if unverified:
            payload["unverified_versions"] = unverified
        summary = ", ".join(
            str(item.get("id") or "") for item in installed if isinstance(item, dict)
        )
        summary = summary or ", ".join(package_ids)
        lines = [
            f"Installed {len(installed)} packages: {summary}.",
            (
                f"Active package: {active}."
                if isinstance(active, str) and active
                else "Active package unchanged."
            ),
        ]
        if unverified:
            lines.append(
                "Warning: installed unverified curated versions: "
                + ", ".join(unverified)
                + "."
            )
        cli._emit_output(args, payload, lines)
        return 0

    package_id = package_ids[0]

    title = args.title
    source: str
    selected_unverified_label: str | None = None
    if args.local_path:
        meta = cli.install_from_local_path(
            scipkg_root,
            package_id,
            local_path=Path(args.local_path),
            title=title,
            activate=args.activate,
            force=args.force,
        )
        source = f"local-path:{Path(args.local_path).expanduser().resolve()}"
    else:
        zip_url = args.zip_url
        selected_version_id: str | None = None
        selected_version_verified: bool | None = None
        selected_source_ref: dict[str, str | None] | None = None
        if not zip_url:
            curated = cli.resolve_curated_package(
                package_id, channel=normalized_channel
            )
            selected_version = cli.select_package_version(
                curated, version_id=requested_version
            )
            if require_verified and not selected_version.verified:
                raise cli.PackageError(
                    f"Selected curated version '{selected_version.version_id}' for package "
                    f"'{package_id}' in channel '{normalized_channel}' is not verified. "
                    "Use a verified version or remove --require-verified."
                )
            zip_url = selected_version.source_archive_url
            if title is None:
                title = curated.title
            selected_version_id = selected_version.version_id
            selected_version_verified = selected_version.verified
            selected_source_ref = {
                "type": selected_version.source_ref_type,
                "value": selected_version.source_ref_value,
            }
            if not selected_version.verified:
                selected_unverified_label = (
                    f"{package_id}@{selected_version.version_id}"
                )

        meta = cli.install_from_zip(
            scipkg_root,
            package_id,
            zip_url=zip_url,
            title=title,
            activate=args.activate,
            force=args.force,
            max_zip_bytes=args.max_zip_bytes,
        )
        installed_id = str(meta.get("id") or package_id)
        if not args.zip_url:
            _save_curated_install_metadata(
                scipkg_root,
                installed_id,
                channel=normalized_channel,
                curated_package_id=package_id,
                version_id=selected_version_id or "branch-head",
                source_archive_url=str(zip_url),
                verified=bool(selected_version_verified),
                source_ref_type=(
                    selected_source_ref.get("type") if selected_source_ref else None
                ),
                source_ref_value=(
                    selected_source_ref.get("value") if selected_source_ref else None
                ),
            )
        source = str(zip_url)

    router = None
    if not args.no_router_sync:
        router = cli.sync_router_rules(scipkg_root)

    payload = {
        "installed": meta,
        "source": source,
        "requested_version": requested_version,
        "require_verified": require_verified,
        "scipkg_root": str(scipkg_root),
        "router_sync": router,
    }
    active = cli.load_registry(scipkg_root).get("active_package")
    lines = [
        f"Installed package '{meta.get('id', package_id)}' from {source}.",
        (
            f"Active package: {active}."
            if isinstance(active, str) and active
            else "Active package unchanged."
        ),
    ]
    if selected_unverified_label:
        lines.append(
            f"Warning: installed unverified curated version: {selected_unverified_label}."
        )
    cli._emit_output(args, payload, lines)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """
    Execute the `list` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    registry = cli.load_registry(scipkg_root)
    packages = cli.list_packages(scipkg_root)
    package_ids = sorted(packages.keys()) if isinstance(packages, dict) else []
    active = registry.get("active_package")
    payload = {
        "scipkg_root": str(scipkg_root),
        "active_package": active,
        "packages": packages,
    }
    summary = ", ".join(package_ids) if package_ids else "(none)"
    lines = [
        f"Installed packages: {len(package_ids)}. Active: {active or 'none'}.",
        f"Packages: {summary}.",
    ]
    cli._emit_output(args, payload, lines)
    return 0


def cmd_avail(args: argparse.Namespace) -> int:
    """
    Execute the `avail` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    query = str(getattr(args, "query", "") or "").strip()
    if not query:
        raise cli.PackageError("Query is required for fermilink avail.")
    normalized_channel = cli.normalize_channel_id(getattr(args, "channel", None))
    curated_packages = cli.list_curated_packages(channel=normalized_channel)

    lowered_query = query.lower()
    exact_match = curated_packages.get(lowered_query)
    matched: list[dict[str, object]] = []
    if exact_match is not None:
        versions = [
            {
                "version_id": version.version_id,
                "source_archive_url": version.source_archive_url,
                "verified": version.verified,
                "source_ref": {
                    "type": version.source_ref_type,
                    "value": version.source_ref_value,
                },
            }
            for version in exact_match.versions
        ]
        matched.append(
            {
                "package_id": exact_match.package_id,
                "title": exact_match.title,
                "zip_url": exact_match.zip_url,
                "match_type": "exact",
                "description": exact_match.description or "",
                "upstream_repo_url": exact_match.upstream_repo_url or "",
                "homepage_url": exact_match.homepage_url or "",
                "tags": list(exact_match.tags),
                "default_version": exact_match.default_version,
                "versions": versions,
            }
        )
    else:
        for package in curated_packages.values():
            package_id = package.package_id.lower()
            title = package.title.lower()
            if lowered_query in package_id or lowered_query in title:
                versions = [
                    {
                        "version_id": version.version_id,
                        "source_archive_url": version.source_archive_url,
                        "verified": version.verified,
                        "source_ref": {
                            "type": version.source_ref_type,
                            "value": version.source_ref_value,
                        },
                    }
                    for version in package.versions
                ]
                matched.append(
                    {
                        "package_id": package.package_id,
                        "title": package.title,
                        "zip_url": package.zip_url,
                        "match_type": "partial",
                        "description": package.description or "",
                        "upstream_repo_url": package.upstream_repo_url or "",
                        "homepage_url": package.homepage_url or "",
                        "tags": list(package.tags),
                        "default_version": package.default_version,
                        "versions": versions,
                    }
                )
    matched.sort(key=lambda item: str(item.get("package_id") or ""))
    payload = {
        "channel": normalized_channel,
        "query": query,
        "found": bool(matched),
        "results": matched,
        "total_curated_packages": len(curated_packages),
    }
    if matched:
        lines = [
            f"Found {len(matched)} package(s) in channel '{normalized_channel}' for '{query}'.",
        ]
        for item in matched:
            versions = item.get("versions")
            version_list = (
                ", ".join(
                    f"{str(version.get('version_id'))}{'' if bool(version.get('verified')) else ' (unverified)'}"
                    for version in versions
                    if isinstance(version, dict)
                )
                if isinstance(versions, list)
                else ""
            )
            default_version = str(item.get("default_version") or "branch-head")
            base_line = (
                f"{item['package_id']}: {item['title']} ({item['zip_url']}) "
                f"[default={default_version}]"
            )
            lines.append(base_line)
            description = str(item.get("description") or "").strip()
            if description:
                lines.append(f"  - {description}")
            if version_list:
                lines.append(f"  - versions: {version_list}")
    else:
        lines = [
            f"No curated package matched '{query}' in channel '{normalized_channel}'.",
            (
                "Try `fermilink list` to see installed packages, or "
                "`fermilink install <package_id>` for an exact curated id."
            ),
        ]
    cli._emit_output(args, payload, lines)
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    """
    Execute the `activate` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)
    meta = cli.activate_package(scipkg_root, package_id)
    payload = {
        "active_package": package_id,
        "meta": meta,
        "scipkg_root": str(scipkg_root),
    }
    cli._emit_output(args, payload, [f"Active package set to '{package_id}'."])
    return 0


def _collect_csv_and_repeat(
    values: list[str] | None, csv_value: str | None
) -> list[str]:
    collected: list[str] = []
    if values:
        collected.extend(values)
    if csv_value:
        collected.extend(csv_value.split(","))
    return collected


def cmd_overlay(args: argparse.Namespace) -> int:
    """
    Execute the `overlay` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)

    collected = _collect_csv_and_repeat(args.entry, args.entries_csv)
    if args.clear and collected:
        raise cli.PackageError("Cannot combine --clear with --entry/--entries.")

    if args.clear:
        entries: list[str] | None = None
    else:
        if not collected:
            raise cli.PackageError(
                "Provide --entry/--entries to set exposed items, or use --clear."
            )
        entries = collected

    meta = cli.set_package_overlay_entries(scipkg_root, package_id, entries)
    overlay_entries = meta.get("overlay_entries")
    if isinstance(overlay_entries, list) and overlay_entries:
        entry_text = ", ".join(str(item) for item in overlay_entries)
    else:
        entry_text = "(all exportable entries)"
    payload = {
        "package_id": package_id,
        "overlay_entries": overlay_entries,
        "meta": meta,
        "scipkg_root": str(scipkg_root),
    }
    cli._emit_output(
        args, payload, [f"Overlay entries for '{package_id}': {entry_text}."]
    )
    return 0


def cmd_dependencies(args: argparse.Namespace) -> int:
    """
    Execute the `dependencies` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)

    collected = _collect_csv_and_repeat(args.package, args.packages_csv)
    if args.clear and collected:
        raise cli.PackageError("Cannot combine --clear with --package/--packages.")

    if args.clear:
        dependency_ids: list[str] | None = None
    else:
        if not collected:
            raise cli.PackageError(
                "Provide --package/--packages to set dependencies, or use --clear."
            )
        dependency_ids = collected

    meta = cli.set_package_dependency_ids(scipkg_root, package_id, dependency_ids)
    dependency_ids = meta.get("dependency_package_ids")
    if isinstance(dependency_ids, list) and dependency_ids:
        deps_text = ", ".join(str(item) for item in dependency_ids)
    else:
        deps_text = "(none)"
    payload = {
        "package_id": package_id,
        "dependency_package_ids": dependency_ids,
        "meta": meta,
        "scipkg_root": str(scipkg_root),
    }
    cli._emit_output(args, payload, [f"Dependencies for '{package_id}': {deps_text}."])
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    """
    Execute the `delete` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    scipkg_root = cli.resolve_scipkg_root()
    package_id = cli.normalize_package_id(args.package_id)
    result = cli.delete_package(
        scipkg_root,
        package_id,
        remove_files=not args.keep_files,
    )

    router = None
    if not args.no_router_sync:
        router = cli.sync_router_rules(scipkg_root)

    payload = {
        "deleted": result,
        "router_sync": router,
        "scipkg_root": str(scipkg_root),
    }
    removed_files = bool(result.get("removed_files"))
    active = result.get("active_package")
    lines = [
        f"Deleted package '{package_id}' from registry. Removed files: {'yes' if removed_files else 'no'}.",
        (
            f"Active package: {active}."
            if isinstance(active, str) and active
            else "No active package set."
        ),
    ]
    cli._emit_output(args, payload, lines)
    return 0
