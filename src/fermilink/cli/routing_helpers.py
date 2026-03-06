from __future__ import annotations

import json
from pathlib import Path

from fermilink.agent_runtime import DEFAULT_PROVIDER
from fermilink.agents import get_provider_agent


def _cli():
    from fermilink import cli

    return cli


def _normalize_installed_package_ids(
    registry_packages: object, *, web_app: object
) -> list[str]:
    package_ids: list[str] = []
    if isinstance(registry_packages, dict):
        for raw_id in registry_packages.keys():
            normalized = web_app._normalize_package_id_safe(str(raw_id))
            if normalized:
                package_ids.append(normalized)
    return sorted(set(package_ids))


def _collect_second_guess_assistant_text(
    raw_stream_text: str, *, web_app: object
) -> str:
    chunks: list[str] = []
    for line in raw_stream_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            item = {}
        item_type = item.get("type") or event.get("type") or ""
        if not isinstance(item_type, str) or not item_type.startswith("agent_message"):
            continue
        text = web_app._extract_text(event) or ""
        if text:
            chunks.append(text)
    return "".join(chunks).strip()


def _run_exec_second_guess(
    *,
    user_text: str,
    repo_dir: Path,
    scipkg_root: Path,
    package_ids: list[str],
    active_package_id: str | None,
    base_package_id: str,
    provider: str = DEFAULT_PROVIDER,
    provider_bin: str | None = None,
    sandbox_policy: str = "enforce",
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, object]:
    cli = _cli()
    agent = get_provider_agent(provider)
    web_app = cli._load_web_router_module()
    package_catalog = web_app._build_package_catalog(
        package_ids=package_ids,
        active_package_id=active_package_id,
        scipkg_root=scipkg_root,
    )
    prompt = web_app._build_second_guess_prompt(
        user_text=user_text,
        current_package_id=base_package_id,
        package_catalog=package_catalog,
    )
    provider_bin_value = cli.resolve_provider_binary(
        provider, provider_bin_override=provider_bin
    )
    preflight_sandbox_mode = "read-only" if sandbox_policy == "enforce" else None
    try:
        cmd = cli.build_exec_command(
            provider=provider,
            provider_bin=provider_bin_value,
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox_policy=sandbox_policy,
            sandbox_mode=preflight_sandbox_mode,
            model=model,
            reasoning_effort=reasoning_effort,
            json_output=agent.uses_json_output_for_second_guess(),
        )
    except NotImplementedError:
        return {
            "package_id": base_package_id,
            "source": "default",
            "switched": False,
            "note": "second_guess_provider_not_implemented",
        }
    timeout = cli.EXEC_SECOND_GUESS_TIMEOUT_SECONDS
    timeout_value = timeout if timeout > 0 else None
    runner_app = cli._load_runner_app_module()
    env = cli.os.environ.copy()
    env = runner_app._sanitize_env(env)
    env = runner_app._normalize_provider_home(env, provider)
    temp_paths: list[Path] = []
    try:
        env, temp_paths = cli._prepare_provider_runtime_env(
            env,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    except RuntimeError as exc:
        raise cli.PackageError(str(exc)) from exc

    try:
        try:
            completed = cli.subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_value,
                env=env,
                cwd=str(repo_dir),
            )
        except cli.subprocess.TimeoutExpired:
            return {
                "package_id": base_package_id,
                "source": "default",
                "switched": False,
                "note": "second_guess_timeout",
            }
        except FileNotFoundError as exc:
            env_key = cli.provider_bin_env_key(provider)
            raise cli.PackageError(
                f"{provider} CLI not found: {provider_bin_value}. "
                f"Install the provider CLI or set {env_key}."
            ) from exc

        if completed.returncode != 0:
            return {
                "package_id": base_package_id,
                "source": "default",
                "switched": False,
                "note": f"second_guess_error:exit_code_{completed.returncode}",
            }

        raw_text = cli._collect_second_guess_assistant_text(
            completed.stdout or "", web_app=web_app
        )
        if not raw_text:
            raw_text = "\n".join(
                part
                for part in (
                    (completed.stdout or "").strip(),
                    (completed.stderr or "").strip(),
                )
                if part
            )
        decision = web_app._extract_first_json_object(raw_text)
        if not isinstance(decision, dict):
            return {
                "package_id": base_package_id,
                "source": "default",
                "switched": False,
                "note": "second_guess_invalid_json",
            }

        route_raw = decision.get("route")
        route = str(route_raw).strip().lower() if route_raw is not None else ""
        suggested_package = web_app._normalize_package_id_safe(
            decision.get("package_id")
        )
        confidence = web_app._coerce_confidence(decision.get("confidence"))
        reason_raw = decision.get("reason")
        reason = str(reason_raw).strip() if isinstance(reason_raw, str) else ""
        reason_short = reason[:240] if reason else ""
        package_set = set(package_ids)

        if route not in {"keep", "switch"}:
            return {
                "package_id": base_package_id,
                "source": "default",
                "switched": False,
                "note": "second_guess_invalid_route",
            }

        if route == "keep":
            return {
                "package_id": base_package_id,
                "source": "default",
                "switched": False,
                "note": (
                    f"second_guess_keep(conf={confidence:.2f}, reason={reason_short})"
                    if reason_short
                    else f"second_guess_keep(conf={confidence:.2f})"
                ),
            }

        if suggested_package not in package_set:
            return {
                "package_id": base_package_id,
                "source": "default",
                "switched": False,
                "note": "second_guess_invalid_target",
            }
        if suggested_package == base_package_id:
            return {
                "package_id": base_package_id,
                "source": "default",
                "switched": False,
                "note": "second_guess_same_target",
            }
        if confidence < cli.EXEC_SECOND_GUESS_MIN_CONFIDENCE:
            return {
                "package_id": base_package_id,
                "source": "default",
                "switched": False,
                "note": f"second_guess_low_confidence({confidence:.2f})",
            }

        return {
            "package_id": suggested_package,
            "source": web_app.PACKAGE_SOURCE_SECOND_GUESS,
            "switched": True,
            "note": (
                f"second_guess_switch({base_package_id}->{suggested_package}, "
                f"conf={confidence:.2f}, reason={reason_short})"
                if reason_short
                else f"second_guess_switch({base_package_id}->{suggested_package}, conf={confidence:.2f})"
            ),
        }
    finally:
        cli._cleanup_temp_paths(temp_paths)


def _resolve_exec_package_selection(
    *,
    user_prompt: str,
    scipkg_root: Path,
    repo_dir: Path,
    requested_package_id: str | None,
    provider: str = DEFAULT_PROVIDER,
    provider_bin: str | None = None,
    sandbox_policy: str = "enforce",
    model: str | None = None,
    reasoning_effort: str | None = None,
    current_package_id: str | None = None,
    current_source: str = "none",
) -> dict[str, object]:
    """Resolve package routing for `exec`, `chat`, `loop`, and workflow agent turns."""

    cli = _cli()
    web_app = cli._load_web_router_module()
    registry = cli.load_registry(scipkg_root)
    packages_payload = registry.get("packages", {})
    package_ids = cli._normalize_installed_package_ids(
        packages_payload, web_app=web_app
    )
    active_raw = registry.get("active_package")
    active_package_id = (
        web_app._normalize_package_id_safe(active_raw)
        if isinstance(active_raw, str)
        else None
    )
    package_set = set(package_ids)
    if active_package_id not in package_set:
        active_package_id = None

    normalized_current = (
        web_app._normalize_package_id_safe(current_package_id)
        if isinstance(current_package_id, str)
        else None
    )
    if normalized_current not in package_set:
        normalized_current = None
        current_source = cli.PACKAGE_SOURCE_NONE
    elif not isinstance(current_source, str) or not current_source.strip():
        current_source = cli.PACKAGE_SOURCE_NONE

    if not package_ids:
        raise cli.PackageError(
            "No installed scientific packages found. Run `fermilink install <package> --activate` first."
        )

    if requested_package_id:
        normalized_requested = cli.normalize_package_id(requested_package_id)
        if normalized_requested not in package_set:
            available = ", ".join(package_ids)
            raise cli.PackageError(
                f"Unknown package '{normalized_requested}'. Available: {available}"
            )
        return {
            "package_id": normalized_requested,
            "source": cli.PACKAGE_SOURCE_MANUAL,
            "reason": "manual_pin",
            "note": "manual_pin",
        }

    config = web_app._load_router_config(scipkg_root)
    selected_package_id: str | None = None
    selected_source = cli.PACKAGE_SOURCE_NONE
    selected_reason = "no_selection"

    if current_source == cli.PACKAGE_SOURCE_MANUAL and normalized_current:
        return {
            "package_id": normalized_current,
            "source": cli.PACKAGE_SOURCE_MANUAL,
            "reason": "manual_pin",
            "note": "manual_pin",
        }

    if cli.EXEC_ROUTER_ENABLED and cli.EXEC_ROUTER_AUTO_DEFAULT:
        decision = web_app._route_package_candidate(
            user_text=user_prompt,
            package_ids=package_ids,
            current_package_id=normalized_current,
            config=config,
        )
        candidate = decision.get("selected_package_id")
        if isinstance(candidate, str) and candidate in package_set:
            if (
                bool(getattr(web_app, "PACKAGE_ROUTER_STICKY", False))
                and normalized_current
                and normalized_current != candidate
                and int(decision.get("margin", 0))
                < int(getattr(web_app, "PACKAGE_ROUTER_SWITCH_MARGIN", 2))
            ):
                selected_package_id = normalized_current
                selected_source = (
                    current_source
                    if current_source != cli.PACKAGE_SOURCE_NONE
                    else cli.PACKAGE_SOURCE_AUTO
                )
                selected_reason = "sticky_keep_current"
            else:
                selected_package_id = candidate
                selected_source = cli.PACKAGE_SOURCE_AUTO
                selected_reason = str(decision.get("reason", "matched"))

    if not selected_package_id:
        if normalized_current:
            selected_package_id = normalized_current
            selected_source = (
                current_source
                if current_source != cli.PACKAGE_SOURCE_NONE
                else cli.PACKAGE_SOURCE_DEFAULT
            )
            selected_reason = "keep_current"
        else:
            fallback = web_app._resolve_default_package_id(
                package_ids=package_ids,
                active_package_id=active_package_id,
                config=config,
            )
            if not fallback:
                raise cli.PackageError(
                    "No default package could be resolved from registry/router rules."
                )
            selected_package_id = fallback
            selected_source = cli.PACKAGE_SOURCE_DEFAULT
            selected_reason = "default_fallback"

    note = selected_reason
    if (
        cli.EXEC_SECOND_GUESS_ENABLED
        and selected_source != cli.PACKAGE_SOURCE_MANUAL
        and len(package_ids) >= 2
    ):
        second_guess = cli._run_exec_second_guess(
            user_text=user_prompt,
            repo_dir=repo_dir,
            scipkg_root=scipkg_root,
            package_ids=package_ids,
            active_package_id=active_package_id,
            base_package_id=selected_package_id,
            provider=provider,
            provider_bin=provider_bin,
            sandbox_policy=sandbox_policy,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        switched = bool(second_guess.get("switched"))
        second_package = second_guess.get("package_id")
        if (
            switched
            and isinstance(second_package, str)
            and second_package in package_set
        ):
            selected_package_id = second_package
            selected_source = str(
                second_guess.get("source") or cli.PACKAGE_SOURCE_SECOND_GUESS
            )
        note = str(second_guess.get("note") or note)

    return {
        "package_id": selected_package_id,
        "source": selected_source,
        "reason": selected_reason,
        "note": note,
    }
