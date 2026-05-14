from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import threading
import time

from fermilink.agents import get_provider_agent


def _cli():
    from fermilink import cli

    return cli


_STOP_REQUEST_CONTEXT = threading.local()
_STREAM_HISTORY_LOCK = threading.Lock()
_STREAM_HISTORY_ROOT = Path("projects") / "agent_streams"
_CODEX_STDIN_NOTICE = "Reading additional input from stdin..."
_PROMPT_PREVIEW_HEAD_LINES = 20
_PROMPT_PREVIEW_TAIL_LINES = 10


def _stream_history_path(repo_dir: Path, *, provider: str) -> Path | None:
    root = repo_dir / _STREAM_HISTORY_ROOT
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    safe_provider = "".join(
        char if char.isalnum() or char in {"-", "_"} else "-"
        for char in str(provider or "provider").lower()
    ).strip("-")
    if not safe_provider:
        safe_provider = "provider"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    suffix = f"{time.time_ns() % 1_000_000:06d}"
    return root / f"{stamp}-{safe_provider}-{suffix}.jsonl"


def _display_path(path: Path, *, repo_dir: Path) -> str:
    try:
        return path.relative_to(repo_dir).as_posix()
    except ValueError:
        return str(path)


def _announce_stream_history_path(path: Path | None, *, repo_dir: Path) -> None:
    if path is None:
        return
    cli = _cli()
    display = _display_path(path, repo_dir=repo_dir)
    print(f"[fermilink] stream jsonl: {display}", file=cli.sys.stderr, flush=True)


def _append_stream_history_raw(path: Path | None, raw_line: str) -> None:
    if path is None:
        return
    text = raw_line.strip()
    if not text:
        return
    try:
        json.loads(text)
        payload = text
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = json.dumps(
            {"type": "raw_stdout", "text": raw_line.rstrip("\n")},
            ensure_ascii=False,
        )
    try:
        with _STREAM_HISTORY_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
    except OSError:
        return


def _append_stream_history_event(path: Path | None, event: dict[str, object]) -> None:
    if path is None:
        return
    try:
        with _STREAM_HISTORY_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        return


def _compact_prompt_preview(
    prompt: str,
    *,
    head_lines: int = _PROMPT_PREVIEW_HEAD_LINES,
    tail_lines: int = _PROMPT_PREVIEW_TAIL_LINES,
) -> str:
    lines = str(prompt or "").splitlines()
    if not lines:
        return ""
    if len(lines) <= head_lines + tail_lines:
        return "\n".join(lines)
    omitted = len(lines) - head_lines - tail_lines
    return "\n".join(
        [
            *lines[:head_lines],
            f"... ({omitted} prompt lines omitted; full prompt saved in JSONL)",
            *lines[-tail_lines:],
        ]
    )


def _print_codex_prompt_preview(
    prompt: str,
    *,
    stream_history_path: Path | None,
) -> None:
    _append_stream_history_event(
        stream_history_path,
        {"type": "fermilink.injected_prompt", "text": str(prompt or "")},
    )
    preview = _compact_prompt_preview(prompt)
    if not preview:
        return
    cli = _cli()
    try:
        use_color = bool(cli.sys.stdout.isatty())
    except Exception:
        use_color = False
    label = "[fermilink] injected prompt:"
    if use_color:
        label = f"\033[1;35m{label}\033[0m"
        preview = f"\033[90m{preview}\033[0m"
    print(label, file=cli.sys.stdout, flush=True)
    print(preview, file=cli.sys.stdout, flush=True)


def _prepare_provider_runtime_env(
    env: dict[str, str],
    *,
    provider: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> tuple[dict[str, str], list[Path]]:
    """Apply provider runtime env overrides and return cleanup paths."""

    return get_provider_agent(provider).prepare_runtime_env(
        env,
        model=model,
        reasoning_effort=reasoning_effort,
    )


def _normalize_provider_home(
    runner_app: object,
    env: dict[str, str],
    *,
    provider: str,
) -> dict[str, str]:
    return runner_app._normalize_provider_home(env, provider)


def _cleanup_temp_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


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


def _should_pipe_prompt_via_stdin(provider: str) -> bool:
    return os.name == "nt" and provider == "codex"


def _prepare_prompt_transport(
    command: list[str],
    *,
    provider: str,
    prompt: str,
) -> tuple[list[str], str | None]:
    if not _should_pipe_prompt_via_stdin(provider):
        return command, None
    if not command:
        return command, None
    return [*command[:-1], "-"], prompt


def _write_prompt_to_process_stdin(process: object, prompt_stdin: str | None) -> None:
    if prompt_stdin is None:
        return
    stdin = getattr(process, "stdin", None)
    if stdin is None:
        return
    try:
        stdin.write(prompt_stdin)
        stdin.close()
    except (BrokenPipeError, OSError, ValueError):
        return


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
                terminate_deadline = time.monotonic() + max(
                    0.0, terminate_grace_seconds
                )
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


def _render_provider_stream_event(
    provider: str,
    event: dict,
    *,
    use_color: bool = True,
) -> str | None:
    return get_provider_agent(provider).render_stream_event(
        event,
        use_color=use_color,
    )


def _render_claude_stream_event(event: dict, *, use_color: bool = True) -> str | None:
    return _render_provider_stream_event("claude", event, use_color=use_color)


def _stream_provider_exec_output(
    process,
    *,
    provider: str,
    stream_history_path: Path | None = None,
) -> int:
    """Stream provider stream-json output with human rendering."""

    cli = _cli()
    try:
        use_color = bool(cli.sys.stdout.isatty())
    except Exception:
        use_color = False

    def _pump_stdout(stream) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            stripped = line.strip()
            if not stripped:
                continue
            _append_stream_history_raw(stream_history_path, line)
            try:
                event = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                print(line.rstrip("\n"), file=cli.sys.stdout, flush=True)
                continue
            if isinstance(event, dict):
                rendered = _render_provider_stream_event(
                    provider,
                    event,
                    use_color=use_color,
                )
                if rendered:
                    print(rendered, file=cli.sys.stdout, flush=True)
        stream.close()

    def _pump_stderr(stream) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            text = line.rstrip("\n")
            _append_stream_history_event(
                stream_history_path,
                {"type": "stderr", "text": text},
            )
            if provider == "codex" and text == _CODEX_STDIN_NOTICE:
                continue
            print(text, file=cli.sys.stderr, flush=True)
        stream.close()

    stdout_thread = cli.threading.Thread(
        target=_pump_stdout,
        args=(process.stdout,),
        daemon=True,
    )
    stderr_thread = cli.threading.Thread(
        target=_pump_stderr,
        args=(process.stderr,),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    return_code = 130
    try:
        return_code = _wait_process_with_optional_stop(process)
    except KeyboardInterrupt:
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=5.0)
        except Exception:
            try:
                process.kill()
            except OSError:
                pass
        return_code = 130
    stdout_thread.join(timeout=2.0)
    stderr_thread.join(timeout=2.0)
    return int(return_code)


def _stream_claude_exec_output(process) -> int:
    return _stream_provider_exec_output(process, provider="claude")


def _extract_provider_assistant_text_chunk(
    provider: str,
    event: dict,
) -> tuple[str, bool]:
    return get_provider_agent(provider).extract_assistant_text_chunk(event)


def _extract_assistant_text_chunk(event: dict) -> tuple[str, bool]:
    return _extract_provider_assistant_text_chunk("claude", event)


def _extract_claude_assistant_text(event: dict) -> str:
    text, _is_delta = _extract_provider_assistant_text_chunk("claude", event)
    return text.strip()


def _stream_provider_exec_output_with_capture(
    process,
    *,
    provider: str,
    stream_history_path: Path | None = None,
) -> tuple[int, str, str]:
    """Stream provider stream-json output and capture assistant/stderr text."""

    cli = _cli()
    try:
        use_color = bool(cli.sys.stdout.isatty())
    except Exception:
        use_color = False

    assistant_parts: list[str] = []
    stderr_lines: list[str] = []

    def _pump_stdout(stream) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            stripped = line.strip()
            if not stripped:
                continue
            _append_stream_history_raw(stream_history_path, line)
            try:
                event = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                print(line.rstrip("\n"), file=cli.sys.stdout, flush=True)
                continue
            if isinstance(event, dict):
                assistant_text, is_delta = _extract_provider_assistant_text_chunk(
                    provider,
                    event,
                )
                if assistant_text:
                    if is_delta:
                        assistant_parts.append(assistant_text)
                    else:
                        if assistant_parts and not assistant_parts[-1].endswith("\n"):
                            assistant_parts.append("\n")
                        assistant_parts.append(assistant_text.strip())
                rendered = _render_provider_stream_event(
                    provider,
                    event,
                    use_color=use_color,
                )
                if rendered:
                    print(rendered, file=cli.sys.stdout, flush=True)
        stream.close()

    def _pump_stderr(stream) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            text = line.rstrip("\n")
            _append_stream_history_event(
                stream_history_path,
                {"type": "stderr", "text": text},
            )
            if provider == "codex" and text == _CODEX_STDIN_NOTICE:
                continue
            stderr_lines.append(line)
            print(text, file=cli.sys.stderr, flush=True)
        stream.close()

    stdout_thread = cli.threading.Thread(
        target=_pump_stdout,
        args=(process.stdout,),
        daemon=True,
    )
    stderr_thread = cli.threading.Thread(
        target=_pump_stderr,
        args=(process.stderr,),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    return_code = 130
    try:
        return_code = _wait_process_with_optional_stop(process)
    except KeyboardInterrupt:
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=5.0)
        except Exception:
            try:
                process.kill()
            except OSError:
                pass
        return_code = 130
    stdout_thread.join(timeout=2.0)
    stderr_thread.join(timeout=2.0)
    return int(return_code), "".join(assistant_parts).strip(), "".join(stderr_lines)


def _stream_claude_exec_output_with_capture(process) -> tuple[int, str, str]:
    return _stream_provider_exec_output_with_capture(process, provider="claude")


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
    provider_bin_override: str | None,
    provider: str = "codex",
    sandbox_policy: str = "enforce",
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, object]:
    """Run one provider turn shared by `chat`, `loop`, and workflow planning/reporting."""

    cli = _cli()
    _consume_last_wait_stop_requested()
    agent = get_provider_agent(provider)
    provider_bin = cli.resolve_provider_binary(
        provider,
        provider_bin_override=provider_bin_override,
    )
    with cli.tempfile.TemporaryDirectory(prefix="fermilink-chat-") as temp_dir:
        last_message_path = Path(temp_dir) / "last_message.txt"
        use_json_stream = agent.uses_json_stream()
        try:
            cmd = cli.build_exec_command(
                provider=provider,
                provider_bin=provider_bin,
                repo_dir=repo_dir,
                prompt=prompt,
                sandbox_policy=sandbox_policy,
                sandbox_mode=sandbox,
                model=model,
                reasoning_effort=reasoning_effort,
                json_output=use_json_stream,
            )
        except NotImplementedError as exc:
            raise cli.PackageError(str(exc)) from exc

        cmd = agent.prepare_shared_turn_command(
            cmd,
            last_message_path=last_message_path,
        )
        cmd, prompt_stdin = _prepare_prompt_transport(
            cmd,
            provider=provider,
            prompt=prompt,
        )

        runner_app = cli._load_runner_app_module()
        env = cli.os.environ.copy()
        env = runner_app._sanitize_env(env)
        env = _normalize_provider_home(runner_app, env, provider=provider)
        temp_paths: list[Path] = []
        try:
            env, temp_paths = _prepare_provider_runtime_env(
                env,
                provider=provider,
                model=model,
                reasoning_effort=reasoning_effort,
            )
        except RuntimeError as exc:
            raise cli.PackageError(str(exc)) from exc

        try:
            if use_json_stream:
                stream_history = (
                    _stream_history_path(repo_dir, provider=provider)
                    if provider == "codex"
                    else None
                )
                _announce_stream_history_path(stream_history, repo_dir=repo_dir)
                if provider == "codex":
                    _print_codex_prompt_preview(
                        prompt,
                        stream_history_path=stream_history,
                    )
                try:
                    process = cli.subprocess.Popen(
                        cmd,
                        cwd=str(repo_dir),
                        stdin=(
                            cli.subprocess.PIPE
                            if prompt_stdin is not None
                            else (
                                None
                                if provider == "codex"
                                else cli.subprocess.DEVNULL
                            )
                        ),
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
                _write_prompt_to_process_stdin(process, prompt_stdin)
                return_code, assistant_text, stderr_text = (
                    _stream_provider_exec_output_with_capture(
                        process,
                        provider=provider,
                        stream_history_path=stream_history,
                    )
                )
                stop_requested = _consume_last_wait_stop_requested()
                return {
                    "assistant_text": assistant_text.strip(),
                    "return_code": int(return_code),
                    "stderr": stderr_text.strip(),
                    "stopped_by_user": bool(stop_requested),
                    "stream_history_path": (
                        str(stream_history) if stream_history is not None else ""
                    ),
                }

            stdout_text = ""
            stderr_text = ""
            stop_checker_active = cli._has_stop_requested_checker()
            if (
                agent.supports_direct_terminal_stream()
                and cli._should_use_direct_terminal_stream()
                and not stop_checker_active
            ):
                try:
                    run_kwargs = {
                        "cwd": str(repo_dir),
                        "check": False,
                        "env": env,
                    }
                    if prompt_stdin is not None:
                        run_kwargs["input"] = prompt_stdin
                        run_kwargs["text"] = True
                    completed = cli.subprocess.run(cmd, **run_kwargs)
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
                        stdin=(
                            cli.subprocess.PIPE if prompt_stdin is not None else None
                        ),
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
                _write_prompt_to_process_stdin(process, prompt_stdin)
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
        finally:
            _cleanup_temp_paths(temp_paths)


def _run_exec_provider_prompt(
    *,
    repo_dir: Path,
    prompt: str,
    sandbox: str | None,
    provider_bin_override: str | None,
    provider: str = "codex",
    sandbox_policy: str = "enforce",
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> int:
    cli = _cli()
    _consume_last_wait_stop_requested()
    agent = get_provider_agent(provider)
    provider_bin = cli.resolve_provider_binary(
        provider,
        provider_bin_override=provider_bin_override,
    )

    use_json_stream = agent.uses_json_stream()

    try:
        cmd = cli.build_exec_command(
            provider=provider,
            provider_bin=provider_bin,
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox_policy=sandbox_policy,
            sandbox_mode=sandbox,
            model=model,
            reasoning_effort=reasoning_effort,
            json_output=use_json_stream,
        )
    except NotImplementedError as exc:
        raise cli.PackageError(str(exc)) from exc
    cmd = agent.prepare_one_shot_exec_command(cmd)
    cmd, prompt_stdin = _prepare_prompt_transport(
        cmd,
        provider=provider,
        prompt=prompt,
    )
    runner_app = cli._load_runner_app_module()
    env = cli.os.environ.copy()
    env = runner_app._sanitize_env(env)
    env = _normalize_provider_home(runner_app, env, provider=provider)
    temp_paths: list[Path] = []
    try:
        env, temp_paths = _prepare_provider_runtime_env(
            env,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    except RuntimeError as exc:
        raise cli.PackageError(str(exc)) from exc
    stop_checker_active = cli._has_stop_requested_checker()

    try:
        if (
            not use_json_stream
            and agent.supports_direct_terminal_stream()
            and cli._should_use_direct_terminal_stream()
            and not stop_checker_active
        ):
            try:
                run_kwargs = {
                    "cwd": str(repo_dir),
                    "check": False,
                    "env": env,
                }
                if prompt_stdin is not None:
                    run_kwargs["input"] = prompt_stdin
                    run_kwargs["text"] = True
                completed = cli.subprocess.run(cmd, **run_kwargs)
            except FileNotFoundError as exc:
                env_key = cli.provider_bin_env_key(provider)
                raise cli.PackageError(
                    f"{provider} CLI not found: {provider_bin}. "
                    f"Install the provider CLI or set {env_key}."
                ) from exc
            return int(completed.returncode)

        try:
            stream_history = (
                _stream_history_path(repo_dir, provider=provider)
                if use_json_stream and provider == "codex"
                else None
            )
            _announce_stream_history_path(stream_history, repo_dir=repo_dir)
            if use_json_stream and provider == "codex":
                _print_codex_prompt_preview(
                    prompt,
                    stream_history_path=stream_history,
                )
            process = cli.subprocess.Popen(
                cmd,
                cwd=str(repo_dir),
                stdin=(
                    cli.subprocess.PIPE
                    if prompt_stdin is not None
                    else (
                        None if provider == "codex" else cli.subprocess.DEVNULL
                    )
                ),
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
        _write_prompt_to_process_stdin(process, prompt_stdin)
        if use_json_stream:
            return_code = _stream_provider_exec_output(
                process,
                provider=provider,
                stream_history_path=stream_history,
            )
        else:
            return_code = cli._stream_exec_process_output(process)
        stop_requested = _consume_last_wait_stop_requested()
        if stop_requested and return_code != 0:
            return 130
        return int(return_code)
    finally:
        _cleanup_temp_paths(temp_paths)
