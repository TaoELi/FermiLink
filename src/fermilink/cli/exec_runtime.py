from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading
import time


def _cli():
    from fermilink import cli

    return cli


_STOP_REQUEST_CONTEXT = threading.local()


def _swap_stop_requested_checker(
    checker: Callable[[], bool] | None,
) -> Callable[[], bool] | None:
    previous = getattr(_STOP_REQUEST_CONTEXT, "checker", None)
    _STOP_REQUEST_CONTEXT.checker = checker
    return previous if callable(previous) else None


def _has_stop_requested_checker() -> bool:
    return callable(getattr(_STOP_REQUEST_CONTEXT, "checker", None))


def _is_stop_requested() -> bool:
    checker = getattr(_STOP_REQUEST_CONTEXT, "checker", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return False


def _set_last_wait_stop_requested(value: bool) -> None:
    _STOP_REQUEST_CONTEXT.last_wait_stop_requested = bool(value)


def _consume_last_wait_stop_requested() -> bool:
    raw_value = getattr(_STOP_REQUEST_CONTEXT, "last_wait_stop_requested", False)
    _STOP_REQUEST_CONTEXT.last_wait_stop_requested = False
    return bool(raw_value)


def _inject_exec_option_before_prompt(
    command: list[str], *option_tokens: str
) -> list[str]:
    """Insert option tokens before the final prompt argument."""

    if not command:
        return command
    prompt_arg = command[-1]
    return [*command[:-1], *option_tokens, prompt_arg]


def _wait_process_with_optional_stop(
    process,
    *,
    stop_poll_seconds: float = 0.1,
    terminate_grace_seconds: float = 5.0,
) -> int:
    poll_fn = getattr(process, "poll", None)
    if not callable(poll_fn):
        wait_fn = getattr(process, "wait", None)
        if callable(wait_fn):
            return_code = wait_fn()
            _set_last_wait_stop_requested(False)
            return int(return_code)
        _set_last_wait_stop_requested(False)
        return 0

    stop_requested = False
    terminate_deadline: float | None = None
    kill_sent = False

    while True:
        return_code = poll_fn()
        if return_code is not None:
            _set_last_wait_stop_requested(stop_requested)
            return int(return_code)

        if _is_stop_requested():
            stop_requested = True
            if terminate_deadline is None:
                try:
                    process.terminate()
                except Exception:
                    pass
                terminate_deadline = time.monotonic() + max(0.0, terminate_grace_seconds)
            elif not kill_sent and time.monotonic() >= terminate_deadline:
                try:
                    process.kill()
                except Exception:
                    pass
                kill_sent = True

        time.sleep(max(0.01, stop_poll_seconds))


def _stream_exec_process_output(process) -> int:
    cli = _cli()

    def _pump(stream: object, *, is_stderr: bool) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            text = line.rstrip("\n")
            print(
                text, file=cli.sys.stderr if is_stderr else cli.sys.stdout, flush=True
            )
        stream.close()

    stdout_thread = cli.threading.Thread(
        target=_pump, args=(process.stdout,), kwargs={"is_stderr": False}, daemon=True
    )
    stderr_thread = cli.threading.Thread(
        target=_pump, args=(process.stderr,), kwargs={"is_stderr": True}, daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    return_code = _wait_process_with_optional_stop(process)
    stdout_thread.join()
    stderr_thread.join()
    return int(return_code)


def _stream_exec_process_output_with_capture(
    process,
) -> tuple[int, str, str]:
    cli = _cli()
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _pump(stream: object, *, is_stderr: bool) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            if is_stderr:
                stderr_lines.append(line)
            else:
                stdout_lines.append(line)
            text = line.rstrip("\n")
            print(
                text, file=cli.sys.stderr if is_stderr else cli.sys.stdout, flush=True
            )
        stream.close()

    stdout_thread = cli.threading.Thread(
        target=_pump, args=(process.stdout,), kwargs={"is_stderr": False}, daemon=True
    )
    stderr_thread = cli.threading.Thread(
        target=_pump, args=(process.stderr,), kwargs={"is_stderr": True}, daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    return_code = _wait_process_with_optional_stop(process)
    stdout_thread.join()
    stderr_thread.join()
    return int(return_code), "".join(stdout_lines), "".join(stderr_lines)


def _should_use_direct_terminal_stream() -> bool:
    """Return whether Codex output should stream directly to the terminal.

    Direct passthrough preserves Codex's native rich TTY rendering (colors,
    sections, progress updates). Fallback piping is used in non-interactive
    contexts (tests, redirected output, background jobs).
    """

    cli = _cli()
    try:
        return bool(
            cli.sys.stdin.isatty()
            and cli.sys.stdout.isatty()
            and cli.sys.stderr.isatty()
        )
    except Exception:
        return False


def _run_exec_chat_turn(
    *,
    repo_dir: Path,
    prompt: str,
    sandbox: str | None,
    codex_bin: str | None,
    provider: str = "codex",
    sandbox_policy: str = "enforce",
    model: str | None = None,
) -> dict[str, object]:
    """Run one provider turn shared by `chat`, `loop`, and workflow planning/reporting."""

    cli = _cli()
    _consume_last_wait_stop_requested()
    provider_bin = cli.resolve_provider_binary(provider, codex_bin=codex_bin)
    with cli.tempfile.TemporaryDirectory(prefix="fermilink-chat-") as temp_dir:
        last_message_path = Path(temp_dir) / "last_message.txt"
        try:
            cmd = cli.build_exec_command(
                provider=provider,
                provider_bin=provider_bin,
                repo_dir=repo_dir,
                prompt=prompt,
                sandbox_policy=sandbox_policy,
                sandbox_mode=sandbox,
                model=model,
                json_output=False,
            )
        except NotImplementedError as exc:
            raise cli.PackageError(str(exc)) from exc

        cmd = cli._inject_exec_option_before_prompt(cmd, "--color", "always")
        cmd = cli._inject_exec_option_before_prompt(
            cmd, "--output-last-message", str(last_message_path)
        )

        runner_app = cli._load_runner_app_module()
        env = cli.os.environ.copy()
        env = runner_app._sanitize_env(env)
        env = runner_app._normalize_codex_home(env)

        stdout_text = ""
        stderr_text = ""
        stop_checker_active = cli._has_stop_requested_checker()
        if cli._should_use_direct_terminal_stream() and not stop_checker_active:
            try:
                completed = cli.subprocess.run(
                    cmd,
                    cwd=str(repo_dir),
                    check=False,
                    env=env,
                )
            except FileNotFoundError as exc:
                env_key = cli.provider_bin_env_key(provider)
                raise cli.PackageError(
                    f"{provider} CLI not found: {provider_bin}. "
                    f"Install the provider CLI or set {env_key}."
                ) from exc
            return_code = int(completed.returncode)
            stop_requested = False
        else:
            try:
                process = cli.subprocess.Popen(
                    cmd,
                    cwd=str(repo_dir),
                    stdout=cli.subprocess.PIPE,
                    stderr=cli.subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )
            except FileNotFoundError as exc:
                env_key = cli.provider_bin_env_key(provider)
                raise cli.PackageError(
                    f"{provider} CLI not found: {provider_bin}. "
                    f"Install the provider CLI or set {env_key}."
                ) from exc
            return_code, stdout_text, stderr_text = (
                cli._stream_exec_process_output_with_capture(process)
            )
            stop_requested = _consume_last_wait_stop_requested()

        assistant_text = ""
        try:
            assistant_text = last_message_path.read_text(encoding="utf-8").strip()
        except OSError:
            assistant_text = ""

        if not assistant_text and stdout_text:
            web_app = cli._load_web_router_module()
            assistant_text = cli._collect_second_guess_assistant_text(
                stdout_text, web_app=web_app
            )

        return {
            "assistant_text": assistant_text,
            "return_code": int(return_code),
            "stderr": stderr_text.strip(),
            "stopped_by_user": bool(stop_requested),
        }


def _run_exec_codex_prompt(
    *,
    repo_dir: Path,
    prompt: str,
    sandbox: str | None,
    codex_bin: str | None,
    provider: str = "codex",
    sandbox_policy: str = "enforce",
    model: str | None = None,
) -> int:
    cli = _cli()
    _consume_last_wait_stop_requested()
    provider_bin = cli.resolve_provider_binary(provider, codex_bin=codex_bin)
    try:
        cmd = cli.build_exec_command(
            provider=provider,
            provider_bin=provider_bin,
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox_policy=sandbox_policy,
            sandbox_mode=sandbox,
            model=model,
            json_output=False,
        )
    except NotImplementedError as exc:
        raise cli.PackageError(str(exc)) from exc
    cmd = cli._inject_exec_option_before_prompt(cmd, "--color", "always")
    runner_app = cli._load_runner_app_module()
    env = cli.os.environ.copy()
    env = runner_app._sanitize_env(env)
    env = runner_app._normalize_codex_home(env)
    stop_checker_active = cli._has_stop_requested_checker()
    if cli._should_use_direct_terminal_stream() and not stop_checker_active:
        try:
            completed = cli.subprocess.run(
                cmd,
                cwd=str(repo_dir),
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            env_key = cli.provider_bin_env_key(provider)
            raise cli.PackageError(
                f"{provider} CLI not found: {provider_bin}. "
                f"Install the provider CLI or set {env_key}."
            ) from exc
        return int(completed.returncode)

    try:
        process = cli.subprocess.Popen(
            cmd,
            cwd=str(repo_dir),
            stdout=cli.subprocess.PIPE,
            stderr=cli.subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError as exc:
        env_key = cli.provider_bin_env_key(provider)
        raise cli.PackageError(
            f"{provider} CLI not found: {provider_bin}. "
            f"Install the provider CLI or set {env_key}."
        ) from exc
    return_code = cli._stream_exec_process_output(process)
    stop_requested = _consume_last_wait_stop_requested()
    if stop_requested and return_code != 0:
        return 130
    return int(return_code)
