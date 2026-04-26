from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from fermilink.optimize import git as optimize_git


WORKER_BRANCH_PREFIX = "fermilink-implement-worker/"
WORKER_WORKTREE_STORAGE_DIRNAME = "fermilink-implement-worktrees"
WORKER_GIT_HIDDEN_BASENAME = optimize_git.WORKER_GIT_HIDDEN_BASENAME

run_git = optimize_git.run_git
current_branch = optimize_git.current_branch
head_sha = optimize_git.head_sha
branch_exists = optimize_git.branch_exists
ensure_clean_repo = optimize_git.ensure_clean_repo
ensure_local_excludes = optimize_git.ensure_local_excludes
list_changed_paths = optimize_git.list_changed_paths
list_untracked_paths = optimize_git.list_untracked_paths
cleanup_paths = optimize_git.cleanup_paths
commit_paths = optimize_git.commit_paths
reset_to_commit = optimize_git.reset_to_commit
with_worker_git_disabled = optimize_git.with_worker_git_disabled
restore_worker_git_metadata = optimize_git.restore_worker_git_metadata
inspect_worker_git_metadata = optimize_git.inspect_worker_git_metadata
reset_worker_to_commit = optimize_git.reset_worker_to_commit
clean_worker_untracked = optimize_git.clean_worker_untracked
temporary_implement_agents = optimize_git.temporary_optimize_agents
cleanup_stale_temporary_implement_agents = (
    optimize_git.cleanup_stale_temporary_optimize_agents
)


def _cli():
    from fermilink import cli

    return cli


def _git_toplevel(repo_dir: Path) -> Path:
    completed = run_git(repo_dir, ["rev-parse", "--show-toplevel"])
    raw = str(completed.stdout or "").strip()
    if not raw:
        return repo_dir.resolve()
    return Path(raw).resolve()


def _worker_key(controller_branch: str) -> str:
    branch = str(controller_branch or "").strip() or "implement"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-._")
    if not slug:
        slug = "implement"
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12]
    return f"{slug[:48]}-{digest}"


def worker_branch_name(controller_branch: str) -> str:
    return f"{WORKER_BRANCH_PREFIX}{_worker_key(controller_branch)}"


def worker_storage_root(repo_dir: Path) -> Path:
    root = _git_toplevel(repo_dir)
    repo_name = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-._") or "repo"
    return (root.parent / f".{repo_name}-{WORKER_WORKTREE_STORAGE_DIRNAME}").resolve()


def worker_worktree_path(repo_dir: Path, *, controller_branch: str) -> Path:
    return worker_storage_root(repo_dir) / _worker_key(controller_branch)


def checkout_implement_branch(
    repo_dir: Path,
    *,
    branch_name: str,
) -> dict[str, str | bool | None]:
    original_branch = current_branch(repo_dir)
    created = False
    if original_branch != branch_name:
        if branch_exists(repo_dir, branch_name):
            run_git(repo_dir, ["checkout", branch_name])
        else:
            run_git(repo_dir, ["checkout", "-b", branch_name])
            created = True
    return {
        "original_branch": original_branch,
        "active_branch": current_branch(repo_dir),
        "created": created,
    }


def _list_worktrees(repo_dir: Path) -> list[dict[str, str]]:
    completed = run_git(repo_dir, ["worktree", "list", "--porcelain"])
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in str(completed.stdout or "").splitlines():
        if not raw_line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if raw_line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"worktree": raw_line.split(" ", 1)[1].strip()}
        elif raw_line.startswith("HEAD "):
            current["head"] = raw_line.split(" ", 1)[1].strip()
        elif raw_line.startswith("branch "):
            current["branch"] = raw_line.split(" ", 1)[1].strip()
    if current:
        entries.append(current)
    return entries


def _existing_worktree_for_branch(repo_dir: Path, *, branch_name: str) -> Path | None:
    branch_ref = f"refs/heads/{branch_name}"
    for entry in _list_worktrees(repo_dir):
        if entry.get("branch") != branch_ref:
            continue
        raw = str(entry.get("worktree") or "").strip()
        if not raw:
            continue
        path = Path(raw).resolve()
        if path.exists():
            return path
    return None


def _remove_path(path: Path) -> None:
    if not (path.exists() or path.is_symlink()):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def ensure_worker_worktree(
    repo_dir: Path,
    *,
    controller_branch: str,
    start_commit: str | None = None,
) -> dict[str, str | bool]:
    worker_branch = worker_branch_name(controller_branch)
    baseline_commit = str(start_commit or "").strip() or head_sha(repo_dir)
    if not branch_exists(repo_dir, worker_branch):
        run_git(repo_dir, ["branch", worker_branch, baseline_commit])
        created_branch = True
    else:
        created_branch = False

    desired_root = worker_worktree_path(repo_dir, controller_branch=controller_branch)
    desired_root.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing_worktree_for_branch(repo_dir, branch_name=worker_branch)
    if existing is not None and existing.resolve() != desired_root.resolve():
        run_git(
            repo_dir,
            ["worktree", "remove", "--force", str(existing)],
            check=False,
        )
        existing = None
    if existing is None:
        if desired_root.exists() or desired_root.is_symlink():
            _remove_path(desired_root)
        run_git(
            repo_dir,
            ["worktree", "add", "--force", str(desired_root), worker_branch],
        )
        created_worktree = True
        worker_root = desired_root
    else:
        worker_root = existing
        created_worktree = False
    restore_worker_git_metadata(worker_root)
    return {
        "worker_branch": worker_branch,
        "worker_root": str(worker_root),
        "created_branch": created_branch,
        "created_worktree": created_worktree,
    }
