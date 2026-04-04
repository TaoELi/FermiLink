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
    storage_root = _git_common_dir(repo_dir) / WORKER_WORKTREE_STORAGE_DIRNAME
    return storage_root / _worker_key(controller_branch)


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

    run_git(repo_dir, ["worktree", "prune"], check=False, capture_output=True)
    existing = _resolve_existing_worktree_for_branch(
        repo_dir,
        branch_name=worker_branch,
    )
    worker_root = existing
    created_worktree = False
    if worker_root is None:
        worker_root = worker_worktree_path(
            repo_dir,
            controller_branch=controller_branch,
        )
        worker_root.parent.mkdir(parents=True, exist_ok=True)
        run_git(
            repo_dir,
            ["worktree", "add", "--force", str(worker_root), worker_branch],
            check=True,
            capture_output=True,
        )
        created_worktree = True

    restored_git = restore_worker_git_metadata(worker_root)
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

    agent = get_provider_agent(provider)
    alias_name = agent.workspace_instruction_alias_name()
    alias_path = (
        repo_dir / alias_name if isinstance(alias_name, str) and alias_name else None
    )
    alias_state: tuple[bool, bool, str] | None = None
    if isinstance(alias_path, Path) and alias_path.exists():
        if alias_path.is_symlink():
            try:
                alias_state = (True, True, os.readlink(alias_path))
            except OSError:
                alias_state = (True, True, "")
        else:
            try:
                alias_state = (True, False, alias_path.read_text(encoding="utf-8"))
            except OSError:
                alias_state = (True, False, "")
    else:
        alias_state = (False, False, "")

    repo_agents.write_text(content, encoding="utf-8")
    agent.ensure_workspace_instruction_alias(repo_dir)
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

        if isinstance(alias_path, Path):
            try:
                alias_path.unlink(missing_ok=True)
            except OSError:
                pass
            if alias_state and alias_state[0]:
                existed, was_symlink, stored = alias_state
                if existed:
                    try:
                        if was_symlink:
                            os.symlink(stored or "AGENTS.md", alias_path)
                        else:
                            alias_path.write_text(stored, encoding="utf-8")
                    except OSError:
                        pass
