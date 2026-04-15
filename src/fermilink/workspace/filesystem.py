from __future__ import annotations

import filecmp
import os
from pathlib import Path
import shutil


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    raise FileNotFoundError(path)


def symlink_matches(target_path: Path, source_path: Path) -> bool:
    if not target_path.is_symlink():
        return False
    resolved = (target_path.parent / target_path.readlink()).resolve()
    return resolved == source_path.resolve()


def ensure_symlink(source_path: Path, target_path: Path, force: bool) -> None:
    if not source_path.exists():
        raise FileNotFoundError(f"Missing source path for symlink: {source_path}")

    if path_exists(target_path):
        if symlink_matches(target_path, source_path):
            return
        if not force:
            raise FileExistsError(
                f"Conflict at {target_path}: already exists. Use --force to overwrite."
            )
        remove_path(target_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    relative_target = Path(os.path.relpath(source_path, start=target_path.parent))
    target_path.symlink_to(relative_target, target_is_directory=source_path.is_dir())


def remove_managed_symlink(
    target_path: Path, expected_source: Path, force: bool = False
) -> None:
    if not path_exists(target_path):
        return
    if force:
        remove_path(target_path)
        return
    if symlink_matches(target_path, expected_source):
        remove_path(target_path)
        return
    raise FileExistsError(
        f"Conflict at {target_path}: expected symlink to {expected_source}. "
        "Use --force to remove anyway."
    )


def files_match(path_a: Path, path_b: Path) -> bool:
    if not path_a.is_file() or not path_b.is_file():
        return False
    try:
        return filecmp.cmp(path_a, path_b, shallow=False)
    except OSError:
        return False


def directories_match(path_a: Path, path_b: Path) -> bool:
    if not path_a.is_dir() or not path_b.is_dir():
        return False
    try:
        entries_a = sorted(path_a.iterdir(), key=lambda p: p.name)
        entries_b = sorted(path_b.iterdir(), key=lambda p: p.name)
    except OSError:
        return False

    if [entry.name for entry in entries_a] != [entry.name for entry in entries_b]:
        return False

    for entry_a, entry_b in zip(entries_a, entries_b):
        if entry_a.is_dir() and entry_b.is_dir():
            if not directories_match(entry_a, entry_b):
                return False
            continue
        if entry_a.is_file() and entry_b.is_file():
            if not files_match(entry_a, entry_b):
                return False
            continue
        return False
    return True


def ensure_copied_directory(source_path: Path, target_path: Path, *, force: bool) -> None:
    if not source_path.is_dir():
        raise FileNotFoundError(
            f"Missing source directory for managed copy: {source_path}"
        )

    if path_exists(target_path):
        if target_path.is_symlink():
            if symlink_matches(target_path, source_path):
                remove_path(target_path)
            elif not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: already exists. "
                    "Use --force to overwrite."
                )
            else:
                remove_path(target_path)
        elif target_path.is_dir():
            if directories_match(target_path, source_path):
                return
            if not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: local directory content differs "
                    "from managed copy. Use --force to overwrite."
                )
            remove_path(target_path)
        else:
            if not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: already exists. "
                    "Use --force to overwrite."
                )
            remove_path(target_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_path, target_path)


def remove_managed_copied_directory(
    target_path: Path,
    expected_source: Path,
    *,
    force: bool,
) -> None:
    if not path_exists(target_path):
        return
    if force:
        remove_path(target_path)
        return

    if target_path.is_symlink():
        if symlink_matches(target_path, expected_source):
            remove_path(target_path)
            return
        raise FileExistsError(
            f"Conflict at {target_path}: expected managed copied directory "
            f"or symlink to {expected_source}. Use --force to remove anyway."
        )
    if target_path.is_dir():
        if directories_match(target_path, expected_source):
            remove_path(target_path)
            return
        raise FileExistsError(
            f"Conflict at {target_path}: expected managed copied directory content. "
            "Use --force to remove anyway."
        )
    raise FileExistsError(
        f"Conflict at {target_path}: expected managed copied directory. "
        "Use --force to remove anyway."
    )
