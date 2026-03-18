from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from fermilink.agents import get_provider_agent


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
