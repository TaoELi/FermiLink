from __future__ import annotations

import argparse
import time
from pathlib import Path


def _cli():
    from fermilink import cli

    return cli


def cmd_chat(args: argparse.Namespace) -> int:
    """Run interactive chat mode.

    Dependency note:
    - This command shares the same routing and execution stack as `exec`.
    - Routing/overlay behavior is resolved through the same helpers used by `exec`.
    """

    cli = _cli()
    repo_dir = Path.cwd().resolve()
    cli._ensure_exec_repo_ready(repo_dir, args)

    scipkg_root = cli.resolve_scipkg_root()
    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(args.sandbox, str) and args.sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = args.sandbox.strip()

    provider_bin = args.codex_bin if provider == "codex" else None
    sandbox_text = (
        f"enforce({sandbox_mode})" if sandbox_policy == "enforce" else "bypass"
    )
    cli._print_tagged("agent", f"provider: {provider}, sandbox: {sandbox_text}")
    cli._print_tagged("chat", "Interactive mode. Type `exit` or `quit` to leave.")

    web_app = cli._load_web_router_module()
    history: list[tuple[str, str]] = []
    current_package_id: str | None = None
    current_source = cli.PACKAGE_SOURCE_NONE

    while True:
        try:
            user_text = input(cli._chat_input_prompt())
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 0
        cli._chat_prompt_spacing_after_input()

        prompt_text = user_text.strip()
        if not prompt_text:
            continue
        lowered = prompt_text.lower()
        if lowered in {"exit", "quit", "/exit", "/quit"}:
            return 0

        cli._ensure_loop_memory(
            repo_dir=repo_dir,
            user_prompt=prompt_text,
            prompt_file=None,
            overwrite=False,
        )

        selection = cli._resolve_exec_package_selection(
            user_prompt=prompt_text,
            scipkg_root=scipkg_root,
            repo_dir=repo_dir,
            requested_package_id=args.package_id,
            provider=provider,
            provider_bin=provider_bin,
            sandbox_policy=sandbox_policy,
            current_package_id=current_package_id,
            current_source=current_source,
        )
        package_id = selection.get("package_id")
        if not isinstance(package_id, str) or not package_id:
            raise cli.PackageError("No package selected for chat turn.")
        source = str(selection.get("source") or cli.PACKAGE_SOURCE_DEFAULT)
        note = str(selection.get("note") or "").strip()
        cli._print_tagged("package", f"Using {package_id} (selection: {source})")
        if note and note not in {"manual_pin", "default_fallback", "matched"}:
            cli._print_tagged("router", note)

        overlay = cli._overlay_exec_package(
            repo_dir=repo_dir,
            scipkg_root=scipkg_root,
            package_id=package_id,
        )
        linked = int(overlay.get("linked_count", 0)) if isinstance(overlay, dict) else 0
        collisions = (
            int(overlay.get("collision_count", 0)) if isinstance(overlay, dict) else 0
        )
        linked_deps = (
            int(overlay.get("linked_dependency_count", 0))
            if isinstance(overlay, dict)
            else 0
        )
        cli._print_tagged(
            "overlay",
            (
                "linked entries: "
                f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
            ),
        )

        prompt_body = web_app._build_prompt(history, prompt_text)
        prompt = f"{cli.UNIFIED_MEMORY_PROMPT_PREFIX}{prompt_body.strip()}\n"
        try:
            run_result = cli._run_exec_chat_turn(
                repo_dir=repo_dir,
                prompt=prompt,
                sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
                codex_bin=provider_bin,
                provider=provider,
                sandbox_policy=sandbox_policy,
            )
        finally:
            cli._cleanup_exec_overlay_symlinks(
                repo_dir=repo_dir, workspace_root=repo_dir
            )

        assistant_text = str(run_result.get("assistant_text") or "").strip()
        return_code = int(run_result.get("return_code") or 0)
        stderr_text = str(run_result.get("stderr") or "").strip()
        if assistant_text:
            assistant_prefix = cli._style_text("Assistant>", "1", "38;5;111")
            print(f"{assistant_prefix} {assistant_text}")
        if return_code != 0:
            if stderr_text:
                print(stderr_text, file=cli.sys.stderr)
            cli._print_tagged(
                "chat",
                f"provider exited with code {return_code}.",
                stderr=True,
            )

        history = web_app._append_history(history, "user", prompt_text)
        if assistant_text:
            history = web_app._append_history(history, "assistant", assistant_text)

        current_package_id = package_id
        current_source = source


def cmd_loop(args: argparse.Namespace) -> int:
    """Run autonomous loop mode.

    Dependency note:
    - `loop` reuses the same routing + execution pathway as `exec`.
    - `research`/`reproduce` orchestration depends on this command for task execution.
    """

    cli = _cli()

    def _record_loop_outcome(
        *,
        status: str,
        reason: str,
        provider_exit_code: int | None = None,
    ) -> None:
        setattr(
            args,
            "_fermilink_loop_outcome",
            {
                "status": status,
                "reason": reason,
                "provider_exit_code": provider_exit_code,
            },
        )

    repo_dir = Path.cwd().resolve()
    cli._ensure_exec_repo_ready(repo_dir, args)

    cli._cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)

    user_prompt, prompt_file = cli._resolve_exec_like_user_prompt(args)
    workflow_prompt_preamble = getattr(args, "workflow_prompt_preamble", None)
    if isinstance(workflow_prompt_preamble, str) and workflow_prompt_preamble.strip():
        user_prompt = f"{workflow_prompt_preamble.strip()}\n\n{user_prompt}"
    memory_path = cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt=user_prompt,
        prompt_file=prompt_file,
    )
    cli._print_tagged("loop", f"memory: {memory_path.relative_to(repo_dir)}")

    max_iterations_raw = getattr(args, "max_iterations", 10)
    try:
        max_iterations = int(max_iterations_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--max-iterations must be an integer.") from exc
    if max_iterations < 1:
        raise cli.PackageError("--max-iterations must be >= 1.")

    wait_seconds_raw = getattr(args, "wait_seconds", 0.0)
    try:
        wait_seconds = float(wait_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--wait-seconds must be a number.") from exc
    if wait_seconds < 0:
        raise cli.PackageError("--wait-seconds must be >= 0.")

    max_wait_seconds_raw = getattr(args, "max_wait_seconds", 600.0)
    try:
        max_wait_seconds = float(max_wait_seconds_raw)
    except (TypeError, ValueError) as exc:
        raise cli.PackageError("--max-wait-seconds must be a number.") from exc
    if max_wait_seconds < 0:
        raise cli.PackageError("--max-wait-seconds must be >= 0.")

    scipkg_root = cli.resolve_scipkg_root()
    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(args.sandbox, str) and args.sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = args.sandbox.strip()

    provider_bin = args.codex_bin if provider == "codex" else None
    selection = cli._resolve_exec_package_selection(
        user_prompt=user_prompt,
        scipkg_root=scipkg_root,
        repo_dir=repo_dir,
        requested_package_id=args.package_id,
        provider=provider,
        provider_bin=provider_bin,
        sandbox_policy=sandbox_policy,
    )
    package_id = selection.get("package_id")
    if not isinstance(package_id, str) or not package_id:
        raise cli.PackageError("No package selected for loop execution.")

    source = str(selection.get("source") or "default")
    note = str(selection.get("note") or "").strip()
    cli._print_tagged("package", f"Using {package_id} (selection: {source})")
    if note and note not in {"manual_pin", "default_fallback", "matched"}:
        cli._print_tagged("router", note)
    sandbox_text = (
        f"enforce({sandbox_mode})" if sandbox_policy == "enforce" else "bypass"
    )
    cli._print_tagged("agent", f"provider: {provider}, sandbox: {sandbox_text}")

    overlay = cli._overlay_exec_package(
        repo_dir=repo_dir,
        scipkg_root=scipkg_root,
        package_id=package_id,
    )
    linked = int(overlay.get("linked_count", 0)) if isinstance(overlay, dict) else 0
    collisions = (
        int(overlay.get("collision_count", 0)) if isinstance(overlay, dict) else 0
    )
    linked_deps = (
        int(overlay.get("linked_dependency_count", 0))
        if isinstance(overlay, dict)
        else 0
    )
    cli._print_tagged(
        "overlay",
        (
            "linked entries: "
            f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
        ),
    )

    prompt = f"{cli.LOOP_PROMPT_PREFIX}{user_prompt.strip()}\n"
    try:
        for iteration in range(1, max_iterations + 1):
            cli._print_tagged("loop", f"iteration {iteration}/{max_iterations}")
            run_result = cli._run_exec_chat_turn(
                repo_dir=repo_dir,
                prompt=prompt,
                sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
                codex_bin=provider_bin,
                provider=provider,
                sandbox_policy=sandbox_policy,
            )

            assistant_text = str(run_result.get("assistant_text") or "")
            done = any(
                line.strip() == cli.LOOP_DONE_TOKEN
                for line in assistant_text.splitlines()
            )
            if done:
                _record_loop_outcome(status="done", reason="done_token")
                print(cli.LOOP_DONE_TOKEN)
                return 0

            return_code = int(run_result.get("return_code") or 0)
            if return_code != 0:
                _record_loop_outcome(
                    status="provider_failure",
                    reason=f"provider_exit_code_{return_code}",
                    provider_exit_code=return_code,
                )
                return return_code

            if iteration < max_iterations:
                suggested_wait = cli._extract_loop_wait_seconds(assistant_text)
                wait_source = "agent" if suggested_wait is not None else "default"
                requested_wait = (
                    suggested_wait if suggested_wait is not None else wait_seconds
                )
                effective_wait = min(requested_wait, max_wait_seconds)
                if effective_wait > 0:
                    if requested_wait > max_wait_seconds:
                        cli._print_tagged(
                            "loop",
                            (
                                "sleeping "
                                f"{effective_wait:.1f}s before next iteration "
                                f"(source: {wait_source}, capped by --max-wait-seconds)"
                            ),
                        )
                    else:
                        cli._print_tagged(
                            "loop",
                            (
                                "sleeping "
                                f"{effective_wait:.1f}s before next iteration "
                                f"(source: {wait_source})"
                            ),
                        )
                    time.sleep(effective_wait)
    finally:
        cli._cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)

    cli._print_tagged(
        "loop",
        f"max iterations reached ({max_iterations}) without {cli.LOOP_DONE_TOKEN}.",
        stderr=True,
    )
    _record_loop_outcome(
        status="incomplete_max_iterations",
        reason="max_iterations_reached",
    )
    return 1


def cmd_exec(args: argparse.Namespace) -> int:
    """Run single-turn execution mode.

    Dependency note:
    - This is the base execution primitive reused by `chat` and `loop`.
    """

    cli = _cli()
    user_prompt, prompt_file = cli._resolve_exec_like_user_prompt(args)

    repo_dir = Path.cwd().resolve()
    cli._ensure_exec_repo_ready(repo_dir, args)
    cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt=user_prompt,
        prompt_file=prompt_file,
        overwrite=False,
    )
    prompt = f"{cli.UNIFIED_MEMORY_PROMPT_PREFIX}{user_prompt.strip()}\n"

    scipkg_root = cli.resolve_scipkg_root()
    runtime_policy = cli.resolve_agent_runtime_policy()
    provider = runtime_policy.provider
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(args.sandbox, str) and args.sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = args.sandbox.strip()

    provider_bin = args.codex_bin if provider == "codex" else None
    selection = cli._resolve_exec_package_selection(
        user_prompt=user_prompt,
        scipkg_root=scipkg_root,
        repo_dir=repo_dir,
        requested_package_id=args.package_id,
        provider=provider,
        provider_bin=provider_bin,
        sandbox_policy=sandbox_policy,
    )
    package_id = selection.get("package_id")
    if not isinstance(package_id, str) or not package_id:
        raise cli.PackageError("No package selected for execution.")

    source = str(selection.get("source") or "default")
    note = str(selection.get("note") or "").strip()
    cli._print_tagged("package", f"Using {package_id} (selection: {source})")
    if note and note not in {"manual_pin", "default_fallback", "matched"}:
        cli._print_tagged("router", note)
    sandbox_text = (
        f"enforce({sandbox_mode})" if sandbox_policy == "enforce" else "bypass"
    )
    cli._print_tagged("agent", f"provider: {provider}, sandbox: {sandbox_text}")

    overlay = cli._overlay_exec_package(
        repo_dir=repo_dir,
        scipkg_root=scipkg_root,
        package_id=package_id,
    )
    linked = int(overlay.get("linked_count", 0)) if isinstance(overlay, dict) else 0
    collisions = (
        int(overlay.get("collision_count", 0)) if isinstance(overlay, dict) else 0
    )
    linked_deps = (
        int(overlay.get("linked_dependency_count", 0))
        if isinstance(overlay, dict)
        else 0
    )
    cli._print_tagged(
        "overlay",
        (
            "linked entries: "
            f"{linked}, linked dependencies: {linked_deps}, collisions: {collisions}"
        ),
    )

    try:
        return cli._run_exec_codex_prompt(
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
            codex_bin=provider_bin,
            provider=provider,
            sandbox_policy=sandbox_policy,
        )
    finally:
        cli._cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)
