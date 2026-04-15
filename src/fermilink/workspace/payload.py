from __future__ import annotations

from importlib import resources
from pathlib import Path
import shutil

from fermilink.workspace import filesystem as workspace_fs


REQUIRED_PAYLOAD_ITEMS = ("README.md", "src")
PAYLOAD_INTERNAL_NAMES = {"__pycache__"}
AGENTS_FILENAME = "AGENTS.md"
AGENTS_ALIAS_FILENAMES = ("CLAUDE.md", "GEMINI.md")
COPIED_PAYLOAD_DIRECTORIES = {"skills"}
INIT_TEMPLATE_AGENTS_REL_PATH = Path("src/fermilink/init_template/AGENTS.md")


def ensure_valid_payload_root(payload_root: Path) -> None:
    if is_valid_payload_root(payload_root):
        return
    raise FileNotFoundError(
        f"Invalid payload root {payload_root}. "
        f"Expected: {', '.join(REQUIRED_PAYLOAD_ITEMS)}"
    )


def is_valid_payload_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    return all((path / item).exists() for item in REQUIRED_PAYLOAD_ITEMS)


def resolve_payload_agents_source(payload_root: Path) -> Path:
    template_source = payload_root / INIT_TEMPLATE_AGENTS_REL_PATH
    if template_source.is_file():
        return template_source
    fallback = payload_root / AGENTS_FILENAME
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(
        f"Missing {AGENTS_FILENAME} template source under payload root {payload_root}"
    )


def managed_agents_symlink_sources(
    payload_root: Path, agents_source: Path, payload_entry_source: Path
) -> tuple[Path, ...]:
    candidates = [agents_source, payload_entry_source, payload_root / AGENTS_FILENAME]
    ordered: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        key = candidate.resolve()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return tuple(ordered)


def ensure_agents_aliases(destination: Path, *, force: bool) -> None:
    agents_path = destination / AGENTS_FILENAME
    if not agents_path.is_file():
        raise FileNotFoundError(
            f"Missing {AGENTS_FILENAME} in initialized workspace: {agents_path}"
        )
    for alias_name in AGENTS_ALIAS_FILENAMES:
        workspace_fs.ensure_symlink(agents_path, destination / alias_name, force=force)


def remove_agents_aliases(destination: Path, *, force: bool) -> None:
    agents_path = destination / AGENTS_FILENAME
    for alias_name in AGENTS_ALIAS_FILENAMES:
        workspace_fs.remove_managed_symlink(
            destination / alias_name,
            agents_path,
            force=force,
        )


def ensure_agents_file(
    source_path: Path,
    target_path: Path,
    *,
    force: bool,
    managed_symlink_sources: tuple[Path, ...],
) -> None:
    if not source_path.is_file():
        raise FileNotFoundError(
            f"Missing source file for {AGENTS_FILENAME}: {source_path}"
        )

    if workspace_fs.path_exists(target_path):
        if target_path.is_symlink():
            if any(
                workspace_fs.symlink_matches(target_path, source_candidate)
                for source_candidate in managed_symlink_sources
            ):
                workspace_fs.remove_path(target_path)
            elif not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: already exists. "
                    "Use --force to overwrite."
                )
            else:
                workspace_fs.remove_path(target_path)
        elif target_path.is_file():
            if workspace_fs.files_match(target_path, source_path):
                return
            if not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: local file content differs from "
                    f"managed {AGENTS_FILENAME}. Use --force to overwrite."
                )
            workspace_fs.remove_path(target_path)
        else:
            if not force:
                raise FileExistsError(
                    f"Conflict at {target_path}: already exists. "
                    "Use --force to overwrite."
                )
            workspace_fs.remove_path(target_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)


def remove_managed_agents_file(
    target_path: Path,
    expected_source: Path,
    *,
    force: bool,
    managed_symlink_sources: tuple[Path, ...],
) -> None:
    if not workspace_fs.path_exists(target_path):
        return
    if force:
        workspace_fs.remove_path(target_path)
        return

    if target_path.is_symlink():
        if any(
            workspace_fs.symlink_matches(target_path, source_candidate)
            for source_candidate in managed_symlink_sources
        ):
            workspace_fs.remove_path(target_path)
            return
        raise FileExistsError(
            f"Conflict at {target_path}: expected managed {AGENTS_FILENAME} symlink. "
            "Use --force to remove anyway."
        )
    if target_path.is_file():
        if workspace_fs.files_match(target_path, expected_source):
            workspace_fs.remove_path(target_path)
            return
        raise FileExistsError(
            f"Conflict at {target_path}: expected managed {AGENTS_FILENAME} file "
            "content. Use --force to remove anyway."
        )
    raise FileExistsError(
        f"Conflict at {target_path}: expected managed {AGENTS_FILENAME} file/symlink. "
        "Use --force to remove anyway."
    )


def repo_root_fallback() -> Path:
    for parent in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (parent / "pyproject.toml").is_file() and (parent / "src").is_dir():
            return parent
    return Path(__file__).resolve().parents[3]


def resolve_payload_root() -> Path:
    try:
        candidate = resources.files("fermilink").joinpath("_workspace_payload")
        candidate_path = Path(str(candidate))
    except Exception:
        candidate_path = None

    if candidate_path is not None and is_valid_payload_root(candidate_path):
        return candidate_path

    repo_root = repo_root_fallback()
    if is_valid_payload_root(repo_root):
        return repo_root

    searched: list[str] = []
    if candidate_path is not None:
        searched.append(str(candidate_path))
    searched.append(str(repo_root))
    raise FileNotFoundError(
        "Could not locate FermiLink workspace payload. "
        f"Searched: {', '.join(searched)}"
    )


def iter_payload_entries(payload_root: Path) -> list[Path]:
    return [
        entry
        for entry in sorted(payload_root.iterdir(), key=lambda p: p.name)
        if entry.name not in PAYLOAD_INTERNAL_NAMES and not entry.name.startswith(".")
    ]
