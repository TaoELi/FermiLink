from __future__ import annotations

import argparse
from pathlib import Path


def _cli():
    from fermilink import cli

    return cli


def _ensure_exec_repo_ready(repo_dir: Path, args: argparse.Namespace) -> None:
    """Enforce repository prerequisites shared by `exec`, `chat`, `loop`, and workflows."""

    cli = _cli()
    runner_app = cli._load_runner_app_module()
    if args.init_git and args.no_init_git:
        raise cli.PackageError("Cannot combine --init-git and --no-init-git.")

    if not runner_app._is_valid_git_repo(repo_dir):
        if args.no_init_git:
            raise cli.PackageError(
                "Current directory is not a git repository. Run `git init` or use --init-git."
            )
        initialize = bool(args.init_git)
        if not initialize:
            if not cli.sys.stdin.isatty():
                raise cli.PackageError(
                    "Current directory is not a git repository. Re-run with --init-git."
                )
            answer = input("Current directory is not a git repo. Run `git init` now? [y/N]: ")
            initialize = answer.strip().lower() in {"y", "yes"}
        if not initialize:
            raise cli.PackageError(
                "Aborted: git repository required for fermilink exec/chat."
            )
        runner_app._ensure_git_repo(repo_dir)

    source_dir = runner_app._resolve_source_dir()
    runner_app._ensure_template_agents_file(source_dir, repo_dir)


def _resolve_project_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _resolve_exec_like_user_prompt(args: argparse.Namespace) -> tuple[str, str | None]:
    cli = _cli()
    prompt_tokens = getattr(args, "prompt", None)
    command_name = str(getattr(args, "command", "command"))
    if not isinstance(prompt_tokens, list) or not prompt_tokens:
        raise cli.PackageError(f"Prompt is required for fermilink {command_name}.")

    if len(prompt_tokens) == 1:
        candidate_path = Path(str(prompt_tokens[0])).expanduser()
        if not candidate_path.is_absolute():
            candidate_path = (Path.cwd() / candidate_path).resolve()
        try:
            is_file = candidate_path.is_file()
        except OSError:
            # Treat invalid/too-long path-like values as plain prompt text.
            is_file = False
        if is_file:
            if candidate_path.suffix.lower() == ".pdf":
                raise cli.PackageError(
                    f"PDF prompt files are not supported yet: {candidate_path}. "
                    "Convert the content to markdown or text first."
                )
            try:
                content = candidate_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise cli.PackageError(f"Failed to read prompt file: {candidate_path}: {exc}") from exc
            text = content.strip()
            if not text:
                raise cli.PackageError(f"Prompt file is empty: {candidate_path}")
            return text, str(candidate_path)

    text = " ".join(str(token) for token in prompt_tokens).strip()
    if not text:
        raise cli.PackageError(f"Prompt is required for fermilink {command_name}.")
    return text, None
