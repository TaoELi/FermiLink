from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import re
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


# ---------------------------------------------------------------------------
# ANSI color palette for Claude stream rendering
# Approximates the visual style of the Claude Code CLI:
#   thinking  → dim italic dark-gray  (internal reasoning, low emphasis)
#   tool name → bold green            (action header)
#   tool cmd  → cyan                  (command / path detail)
#   tool out  → dark gray             (output, visually subordinate)
#   text      → default terminal      (final assistant response)
# ---------------------------------------------------------------------------
_ANSI = {
    "reset":      "\033[0m",
    "thinking":   "\033[2;3;90m",  # dim + italic + dark-gray
    "tool_label": "\033[1;32m",    # bold green
    "tool_cmd":   "\033[36m",      # cyan
    "tool_out":   "\033[90m",      # dark gray
    "text":       "\033[0m",       # default
}
_ANSI_OFF = {k: "" for k in _ANSI}

# Strip <system-reminder> blocks (and similar XML system noise) from thinking.
_SYSTEM_BLOCK_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def _strip_thinking_noise(text: str) -> str:
    cleaned = _SYSTEM_BLOCK_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _render_claude_stream_event(event: dict, *, use_color: bool = True) -> str | None:
    """Convert one Claude stream-json event to a human-readable string.

    Returns ``None`` for event types that produce no user-visible output
    (e.g. ``system``, ``result``).

    Parameters
    ----------
    event:
        Parsed JSON object from the Claude ``--output-format stream-json`` stream.
    use_color:
        When ``True`` (default), wrap output in ANSI color sequences.
    """

    c = _ANSI if use_color else _ANSI_OFF
    R = c["reset"]
    event_type = event.get("type", "")

    if event_type == "assistant":
        message = event.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if not isinstance(content, list):
            return None
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                text = block.get("text", "").strip()
                if text:
                    parts.append(f"{c['text']}{text}{R}")
            elif btype == "thinking":
                raw = _strip_thinking_noise(block.get("thinking", ""))
                if raw:
                    parts.append(f"{c['thinking']}{raw}{R}")
            elif btype == "tool_use":
                name = block.get("name", "")
                inp = block.get("input")
                if isinstance(inp, dict):
                    cmd = (
                        inp.get("command")
                        or inp.get("file_path")
                        or inp.get("path")
                        or json.dumps(inp, ensure_ascii=False)
                    )
                elif inp is not None:
                    cmd = str(inp)
                else:
                    cmd = ""
                parts.append(f"{c['tool_label']}[{name}]{R} {c['tool_cmd']}{cmd}{R}")
        return "\n".join(parts) if parts else None

    if event_type == "user":
        # Tool results are delivered as user messages.
        message = event.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if not isinstance(content, list):
            return None
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            result_content = block.get("content")
            if isinstance(result_content, str) and result_content.strip():
                parts.append(f"{c['tool_out']}{result_content.strip()}{R}")
            elif isinstance(result_content, list):
                for entry in result_content:
                    if isinstance(entry, dict) and entry.get("type") == "text":
                        text = entry.get("text", "").strip()
                        if text:
                            parts.append(f"{c['tool_out']}{text}{R}")
        return "\n".join(parts) if parts else None

    return None


def _stream_claude_exec_output(process) -> int:
    """Stream Claude ``--output-format stream-json`` output with human-readable rendering.

    Reads each newline-delimited JSON event from the process stdout, renders
    assistant text, thinking blocks, tool calls, and tool results to stdout
    with ANSI colors when stdout is a TTY.  Stderr is forwarded verbatim.
    Falls back to printing the raw line for any non-JSON content.
    """

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
            try:
                event = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                # Not JSON (e.g. startup warnings) — print as-is.
                print(line.rstrip("\n"), file=cli.sys.stdout, flush=True)
                continue
            if isinstance(event, dict):
                rendered = _render_claude_stream_event(event, use_color=use_color)
                if rendered:
                    print(rendered, file=cli.sys.stdout, flush=True)
        stream.close()

    def _pump_stderr(stream) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            print(line.rstrip("\n"), file=cli.sys.stderr, flush=True)
        stream.close()

    stdout_thread = cli.threading.Thread(target=_pump_stdout, args=(process.stdout,), daemon=True)
    stderr_thread = cli.threading.Thread(target=_pump_stderr, args=(process.stderr,), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    return_code = _wait_process_with_optional_stop(process)
    stdout_thread.join()
    stderr_thread.join()
    return int(return_code)


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
    reasoning_effort: str | None = None,
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
                reasoning_effort=reasoning_effort,
                json_output=False,
            )
        except NotImplementedError as exc:
            raise cli.PackageError(str(exc)) from exc

        if provider == "codex":
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
    reasoning_effort: str | None = None,
) -> int:
    cli = _cli()
    _consume_last_wait_stop_requested()
    provider_bin = cli.resolve_provider_binary(provider, codex_bin=codex_bin)

    # Non-codex providers (e.g. claude) use stream-json so each event is
    # written immediately, enabling real-time rendering of thinking/tool use.
    use_json_stream = provider != "codex"

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
    if provider == "codex":
        cmd = cli._inject_exec_option_before_prompt(cmd, "--color", "always")
    runner_app = cli._load_runner_app_module()
    env = cli.os.environ.copy()
    env = runner_app._sanitize_env(env)
    env = runner_app._normalize_codex_home(env)
    stop_checker_active = cli._has_stop_requested_checker()

    # Direct terminal passthrough is only used for Codex (native TTY rendering).
    # Non-codex providers use the pipe path so stream-json events can be parsed
    # and rendered into human-readable output line by line.
    if provider == "codex" and cli._should_use_direct_terminal_stream() and not stop_checker_active:
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
    if use_json_stream:
        return_code = _stream_claude_exec_output(process)
    else:
        return_code = cli._stream_exec_process_output(process)
    stop_requested = _consume_last_wait_stop_requested()
    if stop_requested and return_code != 0:
        return 130
    return int(return_code)
