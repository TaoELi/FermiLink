from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import time

from fermilink.exploop.artifacts import record_artifact_changes
from fermilink.exploop.instructions import materialize_exploop_instructions
from fermilink.exploop.memory import append_to_memory_section, ensure_exploop_memory
from fermilink.exploop.pid import is_pid_alive
from fermilink.exploop.prompts import (
    EXPLOOP_DONE_TOKEN,
    EXPLOOP_PROMPT_PREFIX,
    extract_pid_numbers,
    extract_wait_seconds,
)


@dataclass(frozen=True)
class ExploopConfig:
    repo_dir: Path
    user_prompt: str
    prompt_file: str | None = None
    max_iterations: int = 10
    wait_seconds: float = 1.0
    max_wait_seconds: float = 6000.0
    sandbox: str | None = None


def run_exploop(config: ExploopConfig) -> int:
    """Run the minimal experimental measurement loop."""

    repo_dir = config.repo_dir.resolve()
    repo_dir.mkdir(parents=True, exist_ok=True)
    try:
        instruction_files = materialize_exploop_instructions(repo_dir)
    except OSError as exc:
        raise ValueError(
            f"Failed to prepare exploop agent instructions: {exc}"
        ) from exc
    _print_tagged(
        "exploop",
        f"instructions: {instruction_files.agents_path.relative_to(repo_dir)}",
    )
    memory_path = ensure_exploop_memory(
        repo_dir=repo_dir,
        user_prompt=config.user_prompt,
        prompt_file=config.prompt_file,
    )
    _print_tagged("exploop", f"memory: {memory_path.relative_to(repo_dir)}")

    max_iterations = _positive_int(config.max_iterations, "max_iterations")
    wait_seconds = _nonnegative_float(config.wait_seconds, "wait_seconds")
    max_wait_seconds = _nonnegative_float(config.max_wait_seconds, "max_wait_seconds")

    for iteration in range(1, max_iterations + 1):
        _print_tagged("exploop", f"iteration {iteration}/{max_iterations}")
        artifact_changes = record_artifact_changes(repo_dir, memory_path)
        prompt = build_exploop_prompt(
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
            _print_tagged("exploop", "provider run stopped by user")
            return 130

        assistant_text = str(run_result.get("assistant_text") or "")
        if any(
            line.strip() == EXPLOOP_DONE_TOKEN for line in assistant_text.splitlines()
        ):
            print(EXPLOOP_DONE_TOKEN)
            record_artifact_changes(repo_dir, memory_path)
            return 0

        return_code = int(run_result.get("return_code") or 0)
        if return_code != 0:
            stderr = str(run_result.get("stderr") or "").strip()
            if stderr:
                print(stderr)
            _print_tagged("exploop", f"provider exited with code {return_code}")
            return return_code

        pid_numbers = extract_pid_numbers(assistant_text)
        if pid_numbers:
            still_alive = _wait_for_pids(
                pid_numbers,
                poll_seconds=wait_seconds if wait_seconds > 0 else 1.0,
                max_wait_seconds=max_wait_seconds,
            )
            if still_alive:
                append_to_memory_section(
                    memory_path,
                    heading="### Progress log",
                    lines=[
                        "- PID polling reached max wait; still-running PID(s): "
                        + ", ".join(str(pid) for pid in still_alive)
                    ],
                )
                record_artifact_changes(repo_dir, memory_path)
                _print_tagged(
                    "exploop",
                    "stopping before next agent turn because measurement PID(s) "
                    "are still running",
                )
                return 1
            if iteration >= max_iterations:
                record_artifact_changes(repo_dir, memory_path)
                break
            continue

        if iteration >= max_iterations:
            break

        suggested_wait = extract_wait_seconds(assistant_text)
        requested_wait = suggested_wait if suggested_wait is not None else wait_seconds
        sleep_seconds = min(requested_wait, max_wait_seconds)
        if sleep_seconds > 0:
            _print_tagged("exploop", f"sleeping {sleep_seconds:.1f}s")
            time.sleep(sleep_seconds)

    _print_tagged("exploop", "max iterations reached before DONE")
    return 1


def build_exploop_prompt(
    *,
    repo_dir: Path,
    user_prompt: str,
    artifact_changes: list[dict[str, object]],
) -> str:
    skill_lines = _discover_skill_lines(repo_dir)
    artifact_lines = _format_artifact_change_lines(artifact_changes)

    parts = [EXPLOOP_PROMPT_PREFIX.rstrip()]
    parts.append("Local measurement skills:\n" + "\n".join(skill_lines))
    parts.append(
        "New or modified measurement artifacts before this turn:\n"
        + "\n".join(artifact_lines)
    )
    parts.append("Original request:\n" + user_prompt.strip())
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
        return ["- No new or modified measurement artifacts were detected."]
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


def _wait_for_pids(
    pid_numbers: list[int],
    *,
    poll_seconds: float,
    max_wait_seconds: float,
    log_interval_seconds: float = 60.0,
) -> list[int]:
    started = time.monotonic()
    alive = [pid for pid in pid_numbers if is_pid_alive(pid)]
    if not alive:
        _print_tagged("exploop", "measurement PID(s) already finished")
        return []

    log_interval = max(float(log_interval_seconds), 0.0)
    next_log_at = started + log_interval
    status_interval = (
        f"{log_interval:.0f}s" if log_interval >= 1.0 else f"{log_interval:.1f}s"
    )
    _print_tagged(
        "exploop",
        "measurement running; polling PID(s) every "
        f"{max(poll_seconds, 0.1):.1f}s and showing status every "
        f"{status_interval}: "
        + ", ".join(str(pid) for pid in alive),
    )

    while alive:
        elapsed = time.monotonic() - started
        remaining = max_wait_seconds - elapsed
        if remaining <= 0:
            _print_tagged(
                "exploop",
                "PID polling reached max wait with still-running PID(s): "
                + ", ".join(str(pid) for pid in alive),
            )
            return alive
        time.sleep(min(max(poll_seconds, 0.1), remaining))
        alive = [pid for pid in alive if is_pid_alive(pid)]
        if alive:
            now = time.monotonic()
            if log_interval <= 0 or now >= next_log_at:
                elapsed = now - started
                remaining = max(max_wait_seconds - elapsed, 0.0)
                _print_tagged(
                    "exploop",
                    f"still waiting for measurement PID(s) after {elapsed:.1f}s: "
                    + ", ".join(str(pid) for pid in alive)
                    + f" ({remaining:.1f}s until max wait)",
                )
                if log_interval > 0:
                    while next_log_at <= now:
                        next_log_at += log_interval

    waited = time.monotonic() - started
    _print_tagged("exploop", f"measurement PID polling complete after {waited:.1f}s")
    return []


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


def _nonnegative_float(value: float, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be >= 0.")
    return parsed


def _print_tagged(tag: str, message: str) -> None:
    print(f"[{tag}] {message}")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fermilink.exploop.main",
        description=(
            "Debug runner for the experimental measurement loop. "
            "The same implementation is used by top-level `fermilink exploop`."
        ),
    )
    parser.add_argument(
        "prompt",
        nargs="+",
        help="Prompt text or a markdown/text goal file path.",
    )
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--wait-seconds", type=float, default=1.0)
    parser.add_argument("--max-wait-seconds", type=float, default=6000.0)
    parser.add_argument("--sandbox", default=None)
    return parser


def cmd_exploop(args: argparse.Namespace) -> int:
    repo_dir = Path.cwd().resolve()
    user_prompt, prompt_file = _resolve_prompt(args.prompt, repo_dir)
    config = ExploopConfig(
        repo_dir=repo_dir,
        user_prompt=user_prompt,
        prompt_file=prompt_file,
        max_iterations=args.max_iterations,
        wait_seconds=args.wait_seconds,
        max_wait_seconds=args.max_wait_seconds,
        sandbox=args.sandbox,
    )
    return run_exploop(config)


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    return cmd_exploop(args)


if __name__ == "__main__":
    raise SystemExit(main())
