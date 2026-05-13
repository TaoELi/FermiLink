from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from fermilink.agents import get_provider_agent


WORKER_BRANCH_PREFIX = "fermilink-optimize-worker/"
WORKER_GIT_HIDDEN_BASENAME = ".git.fermilink-hidden"
WORKER_WORKTREE_STORAGE_DIRNAME = "fermilink-optimize-worktrees"
OPTIMIZE_TEMP_AGENTS_MARKER = "<!-- FERMILINK_TEMP_OPTIMIZE_AGENTS -->"
OPTIMIZE_TEMP_AGENTS_HEADER = f"{OPTIMIZE_TEMP_AGENTS_MARKER}\n"
_WORKSPACE_INSTRUCTION_ALIAS_PROVIDERS = ("claude", "gemini")
WORKER_GIT_ENV_KEYS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _cli():
    from fermilink import cli

    return cli


def run_git(
    repo_dir: Path,
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        text=True,
        capture_output=capture_output,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise _cli().PackageError(
            f"git {' '.join(args)} failed: {detail or completed.returncode}"
        )
    return completed


def current_branch(repo_dir: Path) -> str | None:
    completed = run_git(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = (completed.stdout or "").strip()
    if not branch or branch == "HEAD":
        return None
    return branch


def head_sha(repo_dir: Path) -> str:
    completed = run_git(repo_dir, ["rev-parse", "--verify", "HEAD"])
    return (completed.stdout or "").strip()


def branch_exists(repo_dir: Path, branch_name: str) -> bool:
    completed = run_git(
        repo_dir,
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        check=False,
    )
    return completed.returncode == 0


def ensure_clean_repo(repo_dir: Path, *, allow_dirty: bool) -> None:
    if allow_dirty:
        return
    completed = run_git(repo_dir, ["status", "--porcelain"])
    if (completed.stdout or "").strip():
        raise _cli().PackageError(
            "Optimize mode requires a clean git working tree. Commit/stash changes "
            "first, or rerun with --allow-dirty."
        )


def _is_tracked_path(repo_dir: Path, rel_path: str) -> bool:
    completed = run_git(
        repo_dir,
        ["ls-files", "--error-unmatch", "--", rel_path],
        check=False,
    )
    return completed.returncode == 0


def _has_optimize_temp_agents_marker(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return text.startswith(OPTIMIZE_TEMP_AGENTS_HEADER)


def _temporary_optimize_agents_content(content: str) -> str:
    text = str(content or "")
    if text.startswith(OPTIMIZE_TEMP_AGENTS_HEADER):
        return text
    return f"{OPTIMIZE_TEMP_AGENTS_HEADER}{text}"


def cleanup_stale_temporary_optimize_agents(repo_dir: Path) -> list[str]:
    """Remove stale temporary AGENTS artifacts left by interrupted optimize turns."""

    removed: list[str] = []
    repo_agents = repo_dir / "AGENTS.md"
    agents_removed = False
    if (
        repo_agents.is_file()
        and not _is_tracked_path(repo_dir, "AGENTS.md")
        and _has_optimize_temp_agents_marker(repo_agents)
    ):
        try:
            repo_agents.unlink()
        except OSError:
            pass
        else:
            removed.append("AGENTS.md")
            agents_removed = True

    alias_names: set[str] = set()
    for provider in _WORKSPACE_INSTRUCTION_ALIAS_PROVIDERS:
        alias_name = get_provider_agent(provider).workspace_instruction_alias_name()
        if isinstance(alias_name, str) and alias_name.strip():
            alias_names.add(alias_name.strip())

    for alias_name in sorted(alias_names):
        if _is_tracked_path(repo_dir, alias_name):
            continue
        alias_path = repo_dir / alias_name
        if not (alias_path.exists() or alias_path.is_symlink()):
            continue

        remove_alias = False
        if alias_path.is_symlink():
            try:
                link_target = os.readlink(alias_path)
            except OSError:
                link_target = ""
            if link_target == "AGENTS.md" and (
                agents_removed
                or not repo_agents.exists()
                or _has_optimize_temp_agents_marker(repo_agents)
            ):
                remove_alias = True
        elif alias_path.is_file() and _has_optimize_temp_agents_marker(alias_path):
            remove_alias = True

        if remove_alias:
            try:
                alias_path.unlink()
            except OSError:
                pass
            else:
                removed.append(alias_name)

    return removed


def checkout_optimize_branch(
    repo_dir: Path,
    *,
    branch_name: str,
) -> dict[str, str | bool | None]:
    original_branch = current_branch(repo_dir)
    created = False
    if original_branch != branch_name:
        if branch_exists(repo_dir, branch_name):
            run_git(
                repo_dir, ["checkout", branch_name], check=True, capture_output=True
            )
        else:
            run_git(
                repo_dir,
                ["checkout", "-b", branch_name],
                check=True,
                capture_output=True,
            )
            created = True
    return {
        "original_branch": original_branch,
        "active_branch": current_branch(repo_dir),
        "created": created,
    }


def _git_path(repo_dir: Path, pathspec: str) -> Path:
    completed = run_git(repo_dir, ["rev-parse", "--git-path", pathspec])
    resolved = (completed.stdout or "").strip()
    if not resolved:
        raise _cli().PackageError(
            f"git rev-parse --git-path {pathspec} returned an empty path."
        )
    candidate = Path(resolved)
    if not candidate.is_absolute():
        candidate = (repo_dir / candidate).resolve()
    return candidate


def _git_common_dir(repo_dir: Path) -> Path:
    completed = run_git(repo_dir, ["rev-parse", "--git-common-dir"])
    resolved = (completed.stdout or "").strip()
    if not resolved:
        raise _cli().PackageError("git rev-parse --git-common-dir returned no path.")
    candidate = Path(resolved)
    if not candidate.is_absolute():
        candidate = (repo_dir / candidate).resolve()
    return candidate


def _main_repo_root(repo_dir: Path) -> Path:
    common_dir = _git_common_dir(repo_dir)
    if common_dir.name == ".git":
        return common_dir.parent.resolve()
    return repo_dir.resolve()


def _worker_key(controller_branch: str) -> str:
    branch = str(controller_branch or "").strip()
    if not branch:
        branch = "optimize"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-._")
    if not slug:
        slug = "optimize"
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12]
    return f"{slug[:48]}-{digest}"


def worker_branch_name(controller_branch: str) -> str:
    return f"{WORKER_BRANCH_PREFIX}{_worker_key(controller_branch)}"


def worker_worktree_path(
    repo_dir: Path,
    *,
    controller_branch: str,
) -> Path:
    storage_root = _worker_storage_root(repo_dir)
    return storage_root / _worker_key(controller_branch)


def _worker_storage_root(repo_dir: Path) -> Path:
    main_repo_root = _main_repo_root(repo_dir)
    repo_name = re.sub(r"[^A-Za-z0-9._-]+", "-", main_repo_root.name).strip("-._")
    if not repo_name:
        repo_name = "repo"
    return (
        main_repo_root.parent / f".{repo_name}-{WORKER_WORKTREE_STORAGE_DIRNAME}"
    ).resolve()


def _legacy_worker_storage_root(repo_dir: Path) -> Path:
    return (_git_common_dir(repo_dir) / WORKER_WORKTREE_STORAGE_DIRNAME).resolve()


def _worker_storage_roots(repo_dir: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in (
        _worker_storage_root(repo_dir),
        _legacy_worker_storage_root(repo_dir),
    ):
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        roots.append(candidate)
    return tuple(roots)


def _list_worktrees(repo_dir: Path) -> list[dict[str, str]]:
    completed = run_git(repo_dir, ["worktree", "list", "--porcelain"])
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in (completed.stdout or "").splitlines():
        line = str(raw_line or "").strip()
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        if raw_line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"worktree": raw_line.split(" ", 1)[1].strip()}
            continue
        if raw_line.startswith("HEAD "):
            current["head"] = raw_line.split(" ", 1)[1].strip()
            continue
        if raw_line.startswith("branch "):
            current["branch"] = raw_line.split(" ", 1)[1].strip()
            continue
        if line == "detached":
            current["detached"] = "true"
    if current:
        entries.append(current)
    return entries


def _resolve_existing_worktree_for_branch(
    repo_dir: Path,
    *,
    branch_name: str,
) -> Path | None:
    branch_ref = f"refs/heads/{branch_name}"
    for entry in _list_worktrees(repo_dir):
        if entry.get("branch") != branch_ref:
            continue
        worktree_raw = str(entry.get("worktree") or "").strip()
        if not worktree_raw:
            continue
        candidate = Path(worktree_raw).resolve()
        if candidate.exists():
            return candidate
    return None


def _resolve_existing_worktree_for_path(
    repo_dir: Path,
    *,
    worktree_path: Path,
) -> dict[str, str] | None:
    target = worktree_path.resolve()
    for entry in _list_worktrees(repo_dir):
        worktree_raw = str(entry.get("worktree") or "").strip()
        if not worktree_raw:
            continue
        candidate = Path(worktree_raw).resolve()
        if candidate == target:
            return entry
    return None


def _parse_gitdir_pointer(metadata_path: Path) -> Path | None:
    try:
        lines = metadata_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    first_line = lines[0].strip() if lines else ""
    if not first_line.lower().startswith("gitdir:"):
        return None
    raw_target = first_line.split(":", 1)[1].strip()
    if not raw_target:
        return None
    candidate = Path(raw_target)
    if not candidate.is_absolute():
        candidate = (metadata_path.parent / candidate).resolve()
    return candidate


def inspect_worker_git_metadata(worker_repo_dir: Path) -> dict[str, str | bool]:
    git_path, hidden_path = _worker_git_paths(worker_repo_dir)
    worker_root_exists = worker_repo_dir.exists() or worker_repo_dir.is_symlink()
    git_exists = git_path.exists()
    hidden_exists = hidden_path.exists()

    metadata_path: Path | None = None
    status = "missing"
    if git_exists and hidden_exists:
        status = "conflicting"
    elif git_exists:
        metadata_path = git_path
        status = "broken_visible"
    elif hidden_exists:
        metadata_path = hidden_path
        status = "broken_hidden"

    gitdir_path = _parse_gitdir_pointer(metadata_path) if metadata_path else None
    admin_dir_exists = bool(gitdir_path and gitdir_path.is_dir())

    if status == "broken_visible" and admin_dir_exists:
        status = "healthy_visible"
    elif status == "broken_hidden" and admin_dir_exists:
        status = "healthy_hidden"

    return {
        "status": status,
        "worker_root_exists": worker_root_exists,
        "git_path_exists": git_exists,
        "hidden_path_exists": hidden_exists,
        "git_metadata_path": str(metadata_path) if metadata_path else "",
        "gitdir_path": str(gitdir_path) if gitdir_path else "",
        "admin_dir_exists": admin_dir_exists,
    }


def _remove_worker_root_path(repo_dir: Path, *, worker_root: Path) -> bool:
    if not (worker_root.exists() or worker_root.is_symlink()):
        return False

    allowed_roots = _worker_storage_roots(repo_dir)
    parent_dir = worker_root.parent.resolve()
    if not any(
        _is_relative_to(parent_dir, storage_root) for storage_root in allowed_roots
    ):
        raise _cli().PackageError(
            "Refusing to remove optimize worker path outside "
            f"{', '.join(str(root) for root in allowed_roots)}: {worker_root}"
        )

    try:
        if worker_root.is_symlink():
            worker_root.unlink()
        elif worker_root.is_dir():
            shutil.rmtree(worker_root)
        else:
            worker_root.unlink(missing_ok=True)
    except OSError as exc:
        raise _cli().PackageError(
            f"Failed to remove stale optimize worker path {worker_root}: {exc}"
        ) from exc
    return True


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
    except ValueError:
        return False
    return True


def _remove_registered_worktree(repo_dir: Path, *, worker_root: Path) -> None:
    worker_health = inspect_worker_git_metadata(worker_root)
    if str(worker_health.get("status") or "").strip() == "healthy_hidden":
        restore_worker_git_metadata(worker_root)

    completed = run_git(
        repo_dir,
        ["worktree", "remove", "--force", str(worker_root)],
        check=False,
        capture_output=True,
    )
    if completed.returncode == 0:
        run_git(repo_dir, ["worktree", "prune"], check=False, capture_output=True)
        return

    if worker_root.exists() or worker_root.is_symlink():
        _remove_worker_root_path(repo_dir, worker_root=worker_root)
    run_git(repo_dir, ["worktree", "prune"], check=False, capture_output=True)

    if _resolve_existing_worktree_for_path(repo_dir, worktree_path=worker_root):
        detail = (completed.stderr or completed.stdout or "").strip()
        raise _cli().PackageError(
            "Failed to relocate optimize worker worktree "
            f"{worker_root}: {detail or completed.returncode}"
        )


def _remove_orphaned_worker_root(repo_dir: Path, *, worker_root: Path) -> bool:
    if not (worker_root.exists() or worker_root.is_symlink()):
        return False
    if _resolve_existing_worktree_for_path(repo_dir, worktree_path=worker_root):
        return False
    return _remove_worker_root_path(repo_dir, worker_root=worker_root)


def _is_operational_worktree(worker_repo_dir: Path) -> bool:
    completed = run_git(
        worker_repo_dir,
        ["rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0 and (completed.stdout or "").strip() == "true"


def ensure_worker_worktree(
    repo_dir: Path,
    *,
    controller_branch: str,
    start_commit: str | None = None,
) -> dict[str, str | bool]:
    worker_branch = worker_branch_name(controller_branch)
    baseline_commit = str(start_commit or "").strip() or head_sha(repo_dir)

    if not branch_exists(repo_dir, worker_branch):
        run_git(
            repo_dir,
            ["branch", worker_branch, baseline_commit],
            check=True,
            capture_output=True,
        )
        created_branch = True
    else:
        created_branch = False

    desired_worker_root = worker_worktree_path(
        repo_dir,
        controller_branch=controller_branch,
    )
    desired_worker_root.parent.mkdir(parents=True, exist_ok=True)
    restored_git = False
    worker_health = inspect_worker_git_metadata(desired_worker_root)
    worker_status = str(worker_health.get("status") or "").strip()
    if worker_status == "healthy_hidden":
        restored_git = restore_worker_git_metadata(desired_worker_root) or restored_git
    elif worker_status in {
        "broken_visible",
        "broken_hidden",
        "conflicting",
    } or (worker_status == "missing" and bool(worker_health.get("worker_root_exists"))):
        _remove_worker_root_path(repo_dir, worker_root=desired_worker_root)

    run_git(repo_dir, ["worktree", "prune"], check=False, capture_output=True)
    existing = _resolve_existing_worktree_for_branch(
        repo_dir,
        branch_name=worker_branch,
    )
    worker_root = existing
    created_worktree = False
    if worker_root is not None:
        if worker_root.resolve() != desired_worker_root.resolve():
            _remove_registered_worktree(repo_dir, worker_root=worker_root)
            run_git(repo_dir, ["worktree", "prune"], check=False, capture_output=True)
            worker_root = None
        else:
            existing_health = inspect_worker_git_metadata(worker_root)
            if str(existing_health.get("status") or "").strip() == "healthy_hidden":
                restored_git = restore_worker_git_metadata(worker_root) or restored_git
            if not _is_operational_worktree(worker_root):
                _remove_worker_root_path(repo_dir, worker_root=worker_root)
                run_git(
                    repo_dir, ["worktree", "prune"], check=False, capture_output=True
                )
                worker_root = None
    if worker_root is None:
        worker_root = desired_worker_root
        worker_root.parent.mkdir(parents=True, exist_ok=True)
        if worker_root.exists() or worker_root.is_symlink():
            if not _remove_orphaned_worker_root(repo_dir, worker_root=worker_root):
                entry = _resolve_existing_worktree_for_path(
                    repo_dir,
                    worktree_path=worker_root,
                )
                branch_ref = str(entry.get("branch") or "").strip() if entry else ""
                raise _cli().PackageError(
                    "Optimize worker worktree path is already registered"
                    f"{f' to {branch_ref}' if branch_ref else ''}: {worker_root}"
                )
        run_git(
            repo_dir,
            ["worktree", "add", "--force", str(worker_root), worker_branch],
            check=True,
            capture_output=True,
        )
        created_worktree = True

    restored_git = restore_worker_git_metadata(worker_root) or restored_git
    if not _is_operational_worktree(worker_root):
        state = inspect_worker_git_metadata(worker_root)
        raise _cli().PackageError(
            "Optimize worker worktree is not a valid git worktree after recovery: "
            f"{worker_root} (status={state.get('status') or 'unknown'}, "
            f"gitdir={state.get('gitdir_path') or 'missing'})"
        )
    return {
        "worker_branch": worker_branch,
        "worker_root": str(worker_root),
        "created_branch": created_branch,
        "created_worktree": created_worktree,
        "restored_git_metadata": restored_git,
    }


def clean_worker_untracked(worker_repo_dir: Path) -> None:
    restore_worker_git_metadata(worker_repo_dir)
    run_git(
        worker_repo_dir,
        ["clean", "-fd"],
        check=True,
        capture_output=True,
    )


def reset_worker_to_commit(worker_repo_dir: Path, *, commit_sha: str) -> None:
    restore_worker_git_metadata(worker_repo_dir)
    run_git(
        worker_repo_dir,
        ["reset", "--hard", commit_sha],
        check=True,
        capture_output=True,
    )


def _worker_git_paths(worker_repo_dir: Path) -> tuple[Path, Path]:
    return (
        worker_repo_dir / ".git",
        worker_repo_dir / WORKER_GIT_HIDDEN_BASENAME,
    )


def restore_worker_git_metadata(worker_repo_dir: Path) -> bool:
    git_path, hidden_path = _worker_git_paths(worker_repo_dir)
    if git_path.exists():
        return False
    if not hidden_path.exists():
        return False
    try:
        hidden_path.rename(git_path)
    except OSError as exc:
        raise _cli().PackageError(
            f"Failed to restore worker git metadata at {worker_repo_dir}: {exc}"
        ) from exc
    return True


@contextmanager
def _temporary_unset_env(keys: tuple[str, ...]):
    original: dict[str, str] = {}
    for key in keys:
        if key in os.environ:
            original[key] = os.environ[key]
            os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in original.items():
            os.environ[key] = value


@contextmanager
def with_worker_git_disabled(worker_repo_dir: Path):
    restore_worker_git_metadata(worker_repo_dir)
    git_path, hidden_path = _worker_git_paths(worker_repo_dir)
    if hidden_path.exists() and git_path.exists():
        raise _cli().PackageError(
            f"Conflicting worker git metadata paths: {git_path} and {hidden_path}"
        )
    if not git_path.exists():
        raise _cli().PackageError(
            f"Worker git metadata missing at {git_path}; cannot disable git tools."
        )
    try:
        git_path.rename(hidden_path)
    except OSError as exc:
        raise _cli().PackageError(
            f"Failed to hide worker git metadata at {git_path}: {exc}"
        ) from exc
    try:
        with _temporary_unset_env(WORKER_GIT_ENV_KEYS):
            yield
    finally:
        if hidden_path.exists() and not git_path.exists():
            try:
                hidden_path.rename(git_path)
            except OSError as exc:
                raise _cli().PackageError(
                    f"Failed to restore worker git metadata at {git_path}: {exc}"
                ) from exc
        elif not git_path.exists():
            raise _cli().PackageError(
                f"Worker git metadata missing after worker run: {git_path}"
            )


def ensure_local_excludes(repo_dir: Path, patterns: list[str]) -> None:
    exclude_path = _git_path(repo_dir, "info/exclude")
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = exclude_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    updated = list(existing)
    changed = False
    for pattern in patterns:
        value = str(pattern or "").strip()
        if not value or value in existing or value in updated:
            continue
        updated.append(value)
        changed = True
    if changed:
        exclude_path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def list_changed_paths(repo_dir: Path) -> list[dict[str, str]]:
    completed = run_git(repo_dir, ["status", "--porcelain"])
    entries: list[dict[str, str]] = []
    for line in (completed.stdout or "").splitlines():
        if len(line) < 3:
            continue
        status = line[:2]
        path_text = line[3:].strip()
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1].strip()
        path_text = path_text.strip('"').replace("\\", "/")
        if not path_text:
            continue
        entries.append({"status": status, "path": path_text})
    return entries


def list_untracked_paths(repo_dir: Path) -> list[str]:
    completed = run_git(
        repo_dir,
        ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    entries: set[str] = set()
    for item in (completed.stdout or "").split("\0"):
        path_text = str(item or "").strip().replace("\\", "/")
        if path_text:
            entries.add(path_text)
    return sorted(entries)


def _cleanup_targets(repo_dir: Path, paths: list[str]) -> list[Path]:
    repo_root = repo_dir.resolve()
    targets: list[Path] = []
    for rel_path in sorted({item for item in paths if str(item or "").strip()}):
        raw = str(rel_path).strip()
        candidate_rel = Path(raw)
        if candidate_rel.is_absolute():
            continue
        target = (repo_root / candidate_rel).resolve()
        try:
            target.relative_to(repo_root)
        except ValueError:
            continue
        targets.append(target)
    return targets


def cleanup_paths(repo_dir: Path, paths: list[str]) -> None:
    for target in _cleanup_targets(repo_dir, paths):
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        except OSError:
            pass


def commit_paths(repo_dir: Path, *, paths: list[str], message: str) -> str:
    if not paths:
        raise _cli().PackageError("No paths were provided for optimize commit.")
    unique_paths = sorted({path for path in paths if str(path or "").strip()})
    run_git(repo_dir, ["add", "--", *unique_paths], check=True, capture_output=True)
    completed = subprocess.run(
        [
            "git",
            "-c",
            "user.name=FermiLink",
            "-c",
            "user.email=fermilink@local",
            "commit",
            "-m",
            message,
        ],
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise _cli().PackageError(
            f"git commit failed: {detail or completed.returncode}"
        )
    return head_sha(repo_dir)


def reset_to_commit(
    repo_dir: Path, *, commit_sha: str, cleanup_paths_list: list[str]
) -> None:
    cleanup_paths(repo_dir, cleanup_paths_list)
    run_git(repo_dir, ["reset", "--hard", commit_sha], check=True, capture_output=True)


@contextmanager
def temporary_optimize_agents(
    repo_dir: Path,
    *,
    provider: str,
    content: str,
):
    repo_agents = repo_dir / "AGENTS.md"
    original_agents_exists = repo_agents.exists()
    original_agents_text = ""
    if original_agents_exists and repo_agents.is_file():
        try:
            original_agents_text = repo_agents.read_text(encoding="utf-8")
        except OSError:
            original_agents_text = ""

    provider_candidates = list(_WORKSPACE_INSTRUCTION_ALIAS_PROVIDERS)
    provider_name = str(provider or "").strip()
    if provider_name and provider_name not in provider_candidates:
        provider_candidates.append(provider_name)

    alias_names: set[str] = set()
    for candidate in provider_candidates:
        alias_name = get_provider_agent(candidate).workspace_instruction_alias_name()
        if isinstance(alias_name, str) and alias_name.strip():
            alias_names.add(alias_name.strip())

    alias_states: dict[str, tuple[bool, bool, str]] = {}
    for alias_name in sorted(alias_names):
        alias_path = repo_dir / alias_name
        if alias_path.exists() or alias_path.is_symlink():
            if alias_path.is_symlink():
                try:
                    alias_states[alias_name] = (True, True, os.readlink(alias_path))
                except OSError:
                    alias_states[alias_name] = (True, True, "")
            else:
                try:
                    alias_states[alias_name] = (
                        True,
                        False,
                        alias_path.read_text(encoding="utf-8"),
                    )
                except OSError:
                    alias_states[alias_name] = (True, False, "")
        else:
            alias_states[alias_name] = (False, False, "")

    repo_agents.write_text(
        _temporary_optimize_agents_content(content),
        encoding="utf-8",
    )
    for candidate in provider_candidates:
        get_provider_agent(candidate).ensure_workspace_instruction_alias(repo_dir)
    try:
        yield
    finally:
        if original_agents_exists:
            repo_agents.write_text(original_agents_text, encoding="utf-8")
        else:
            try:
                repo_agents.unlink(missing_ok=True)
            except OSError:
                pass

        for alias_name in sorted(alias_states):
            alias_path = repo_dir / alias_name
            try:
                alias_path.unlink(missing_ok=True)
            except OSError:
                pass
            alias_state = alias_states.get(alias_name)
            if not alias_state or not alias_state[0]:
                continue
            _, was_symlink, stored = alias_state
            try:
                if was_symlink:
                    os.symlink(stored or "AGENTS.md", alias_path)
                else:
                    alias_path.write_text(stored, encoding="utf-8")
            except OSError:
                pass
