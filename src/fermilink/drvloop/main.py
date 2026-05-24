from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

from fermilink.drvloop.artifacts import record_artifact_changes
from fermilink.drvloop.instructions import materialize_drvloop_instructions
from fermilink.drvloop.memory import ensure_drvloop_memory
from fermilink.drvloop.prompts import (
    DRVLOOP_DONE_TOKEN,
    DRVLOOP_PROMPT_PREFIX,
)


@dataclass(frozen=True)
class DrvloopConfig:
    repo_dir: Path
    user_prompt: str
    prompt_file: str | None = None
    max_iterations: int = 30
    sandbox: str | None = None


def run_drvloop(config: DrvloopConfig) -> int:
    """Run the minimal derivation loop."""

    repo_dir = config.repo_dir.resolve()
    repo_dir.mkdir(parents=True, exist_ok=True)
    try:
        instruction_files = materialize_drvloop_instructions(repo_dir)
    except OSError as exc:
        raise ValueError(
            f"Failed to prepare drvloop agent instructions: {exc}"
        ) from exc
    _print_tagged(
        "drvloop",
        f"instructions: {instruction_files.agents_path.relative_to(repo_dir)}",
    )
    memory_path = ensure_drvloop_memory(
        repo_dir=repo_dir,
        user_prompt=config.user_prompt,
        prompt_file=config.prompt_file,
    )
    _print_tagged("drvloop", f"memory: {memory_path.relative_to(repo_dir)}")

    max_iterations = _positive_int(config.max_iterations, "max_iterations")

    for iteration in range(1, max_iterations + 1):
        _print_tagged("drvloop", f"iteration {iteration}/{max_iterations}")
        artifact_changes = record_artifact_changes(repo_dir, memory_path)
        prompt = build_drvloop_prompt(
            repo_dir=repo_dir,
            user_prompt=config.user_prompt,
            artifact_changes=artifact_changes,
        )
        run_result = _run_provider_turn(
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox=config.sandbox,
        )
        if bool(run_result.get("stopped_by_user")):
            _print_tagged("drvloop", "provider run stopped by user")
            return 130

        assistant_text = str(run_result.get("assistant_text") or "")
        if any(
            line.strip() == DRVLOOP_DONE_TOKEN for line in assistant_text.splitlines()
        ):
            print(DRVLOOP_DONE_TOKEN)
            record_artifact_changes(repo_dir, memory_path)
            return 0

        return_code = int(run_result.get("return_code") or 0)
        if return_code != 0:
            stderr = str(run_result.get("stderr") or "").strip()
            if stderr:
                print(stderr)
            _print_tagged("drvloop", f"provider exited with code {return_code}")
            return return_code

        if iteration >= max_iterations:
            break

    _print_tagged("drvloop", "max iterations reached before DONE")
    return 1


def build_drvloop_prompt(
    *,
    repo_dir: Path,
    user_prompt: str,
    artifact_changes: list[dict[str, object]],
) -> str:
    skill_lines = _discover_skill_lines(repo_dir)
    artifact_lines = _format_artifact_change_lines(artifact_changes)

    parts = [DRVLOOP_PROMPT_PREFIX.rstrip()]
    parts.append("Local derivation skills:\n" + "\n".join(skill_lines))
    parts.append(
        "New or modified derivation artifacts before this turn:\n"
        + "\n".join(artifact_lines)
    )
    parts.append("Request:\n" + user_prompt.strip())
    return "\n\n".join(parts).rstrip() + "\n"


def _discover_skill_lines(repo_dir: Path) -> list[str]:
    skills_root = repo_dir / "skills"
    if not skills_root.is_dir():
        return ["- No local `skills/` directory was found."]
    skill_paths = sorted(skills_root.rglob("SKILL.md"))
    if not skill_paths:
        return ["- `skills/` exists, but no `SKILL.md` files were found."]
    lines: list[str] = []
    for path in skill_paths[:50]:
        try:
            rel = path.relative_to(repo_dir).as_posix()
        except ValueError:
            rel = str(path)
        lines.append(f"- {rel}")
    if len(skill_paths) > 50:
        lines.append(f"- ... {len(skill_paths) - 50} additional skill file(s)")
    return lines


def _format_artifact_change_lines(
    artifact_changes: list[dict[str, object]],
) -> list[str]:
    if not artifact_changes:
        return ["- No new or modified derivation artifacts were detected."]
    lines: list[str] = []
    for item in artifact_changes[:50]:
        path = str(item.get("path") or "")
        status = str(item.get("status") or "changed")
        size = item.get("size_bytes")
        modified = str(item.get("modified_utc") or "")
        lines.append(f"- {path} | {status} | {size} bytes | modified {modified}")
    if len(artifact_changes) > 50:
        lines.append(f"- ... {len(artifact_changes) - 50} additional artifact(s)")
    return lines


def _run_provider_turn(
    *,
    repo_dir: Path,
    prompt: str,
    sandbox: str | None,
) -> dict[str, object]:
    from fermilink import cli

    runtime_policy = cli.resolve_agent_runtime_policy()
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(sandbox, str) and sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = sandbox.strip()
    provider_bin = cli.resolve_provider_binary_override(
        runtime_policy.provider,
        raw_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
    )
    return cli._run_exec_chat_turn(
        repo_dir=repo_dir,
        prompt=prompt,
        sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
        provider_bin_override=provider_bin,
        provider=runtime_policy.provider,
        sandbox_policy=sandbox_policy,
        model=runtime_policy.model,
        reasoning_effort=runtime_policy.reasoning_effort,
    )


def _resolve_prompt(tokens: list[str], repo_dir: Path) -> tuple[str, str | None]:
    if len(tokens) == 1:
        candidate = Path(tokens[0]).expanduser()
        if not candidate.is_absolute():
            candidate = (repo_dir / candidate).resolve()
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                raise ValueError(f"Prompt file is empty: {candidate}")
            return text, str(candidate)
    text = " ".join(tokens).strip()
    if not text:
        raise ValueError("Prompt is required.")
    return text, None


def _positive_int(value: int, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be >= 1.")
    return parsed


def _print_tagged(tag: str, message: str, *, stderr: bool = False) -> None:
    kwargs: dict[str, object] = {"flush": True}
    if stderr:
        kwargs["file"] = sys.stderr
    print(f"[{tag}] {message}", **kwargs)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fermilink.drvloop.main",
        description=(
            "Debug runner for the derivation loop. "
            "The same implementation is used by top-level `fermilink drvloop`."
        ),
    )
    parser.add_argument(
        "prompt",
        nargs="+",
        help="Prompt text or a markdown/text goal file path.",
    )
    parser.add_argument("--max-iterations", type=int, default=30)
    parser.add_argument("--sandbox", default=None)
    return parser


def cmd_drvloop(args: argparse.Namespace) -> int:
    repo_dir = Path.cwd().resolve()
    user_prompt, prompt_file = _resolve_prompt(args.prompt, repo_dir)
    config = DrvloopConfig(
        repo_dir=repo_dir,
        user_prompt=user_prompt,
        prompt_file=prompt_file,
        max_iterations=args.max_iterations,
        sandbox=args.sandbox,
    )
    return run_drvloop(config)


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    return cmd_drvloop(args)


if __name__ == "__main__":
    raise SystemExit(main())
