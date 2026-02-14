from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable


def _resolve_package_registry(
    *,
    app_root: Path,
    resolve_scipkg_root: Callable[[], Path],
    load_registry: Callable[[Path], dict[str, Any]],
    normalize_package_id_safe: Callable[[str | None], str | None],
    logger: logging.Logger,
) -> tuple[list[str], str | None, Path]:
    """Load installed package ids and active package from registry."""

    scipkg_root = app_root / "scientific_packages"
    try:
        scipkg_root = resolve_scipkg_root()
        registry = load_registry(scipkg_root)
    except Exception as exc:
        logger.warning("Failed to load scientific package registry: %s", exc)
        return [], None, scipkg_root

    packages = registry.get("packages", {})
    package_ids: list[str] = []
    if isinstance(packages, dict):
        for raw_id in packages.keys():
            normalized = normalize_package_id_safe(str(raw_id))
            if normalized:
                package_ids.append(normalized)
    package_ids = sorted(set(package_ids))

    active_raw = registry.get("active_package")
    active_package_id = (
        normalize_package_id_safe(active_raw) if isinstance(active_raw, str) else None
    )
    if active_package_id not in package_ids:
        active_package_id = None
    return package_ids, active_package_id, scipkg_root


def _get_session_auto_route_flag(
    *,
    user_session: Any,
    session_package_auto_key: str,
    package_router_auto_default: bool,
) -> bool:
    """Return session auto-route setting with default initialization."""

    value = user_session.get(session_package_auto_key)
    if isinstance(value, bool):
        return value
    user_session.set(session_package_auto_key, package_router_auto_default)
    return package_router_auto_default


def _resolve_package_for_turn(
    user_text: str,
    *,
    user_session: Any,
    resolve_package_registry: Callable[[], tuple[list[str], str | None, Path]],
    load_router_config: Callable[[Path], dict[str, Any]],
    normalize_package_id_safe: Callable[[str | None], str | None],
    get_session_auto_route_flag: Callable[[], bool],
    route_package_candidate: Callable[[str, list[str], str | None, dict[str, Any]], dict[str, Any]],
    resolve_default_package_id: Callable[[list[str], str | None, dict[str, Any]], str | None],
    session_package_id_key: str,
    session_package_source_key: str,
    package_source_manual: str,
    package_source_auto: str,
    package_source_default: str,
    package_source_none: str,
    package_router_enabled: bool,
    package_router_sticky: bool,
    package_router_switch_margin: int,
) -> dict[str, Any]:
    """Resolve package selection for the current user message."""

    package_ids, active_package_id, scipkg_root = resolve_package_registry()
    config = load_router_config(scipkg_root)
    package_set = set(package_ids)
    resolution_context = {
        "scipkg_root": str(scipkg_root),
        "installed_package_count": len(package_ids),
    }

    current_package_id = user_session.get(session_package_id_key)
    if isinstance(current_package_id, str):
        current_package_id = normalize_package_id_safe(current_package_id)
    else:
        current_package_id = None
    current_source = user_session.get(session_package_source_key)
    if not isinstance(current_source, str) or not current_source:
        current_source = package_source_none

    if current_package_id not in package_set:
        current_package_id = None
        current_source = package_source_none

    if current_source == package_source_manual and current_package_id:
        return {
            "package_id": current_package_id,
            "source": package_source_manual,
            "reason": "manual_pin",
            "changed": False,
            **resolution_context,
        }

    if not package_ids:
        return {
            "package_id": None,
            "source": package_source_none,
            "reason": "no_packages",
            "changed": current_package_id is not None,
            **resolution_context,
        }

    auto_enabled = get_session_auto_route_flag() and package_router_enabled
    if auto_enabled:
        decision = route_package_candidate(
            user_text=user_text,
            package_ids=package_ids,
            current_package_id=current_package_id,
            config=config,
        )
        candidate = decision.get("selected_package_id")
        if isinstance(candidate, str) and candidate in package_set:
            if (
                package_router_sticky
                and current_package_id
                and current_package_id != candidate
                and int(decision.get("margin", 0)) < package_router_switch_margin
            ):
                return {
                    "package_id": current_package_id,
                    "source": current_source or package_source_auto,
                    "reason": "sticky_keep_current",
                    "changed": False,
                    **resolution_context,
                }
            return {
                "package_id": candidate,
                "source": package_source_auto,
                "reason": decision.get("reason", "matched"),
                "changed": candidate != current_package_id
                or current_source != package_source_auto,
                **resolution_context,
            }

    if current_package_id:
        return {
            "package_id": current_package_id,
            "source": current_source or package_source_default,
            "reason": "keep_current",
            "changed": False,
            **resolution_context,
        }

    fallback = resolve_default_package_id(package_ids, active_package_id, config)
    return {
        "package_id": fallback,
        "source": package_source_default if fallback else package_source_none,
        "reason": "default_fallback" if fallback else "no_default",
        "changed": bool(fallback),
        **resolution_context,
    }


async def _run_package_second_guess(
    *,
    user_text: str,
    session_id: str | None,
    user_id: str | None,
    selected_package_id: str | None,
    selected_source: str,
    package_second_guess_enabled: bool,
    package_source_manual: str,
    package_source_second_guess: str,
    package_second_guess_timeout_seconds: float,
    package_second_guess_min_confidence: float,
    resolve_package_registry: Callable[[], tuple[list[str], str | None, Path]],
    load_router_config: Callable[[Path], dict[str, Any]],
    resolve_default_package_id: Callable[[list[str], str | None, dict[str, Any]], str | None],
    build_package_catalog: Callable[[list[str], str | None, Path], list[dict[str, Any]]],
    build_second_guess_prompt: Callable[..., str],
    resolve_agent_runtime_policy: Callable[[], Any],
    stream_runner: Callable[[dict[str, Any]], Any],
    extract_text: Callable[[dict[str, Any]], str | None],
    extract_first_json_object: Callable[[str], dict[str, Any] | None],
    normalize_package_id_safe: Callable[[str | None], str | None],
    coerce_confidence: Callable[[Any], float],
    logger: logging.Logger,
) -> dict[str, Any]:
    """Run AGENTS-guided preflight package routing check."""

    if not package_second_guess_enabled:
        return {
            "package_id": selected_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": session_id,
            "consulted": False,
            "note": "second_guess_disabled",
        }

    if selected_source == package_source_manual:
        return {
            "package_id": selected_package_id,
            "source": package_source_manual,
            "switched": False,
            "session_id": session_id,
            "consulted": False,
            "note": "manual_pin",
        }

    package_ids, active_package_id, scipkg_root = resolve_package_registry()
    if len(package_ids) < 2:
        return {
            "package_id": selected_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": session_id,
            "consulted": False,
            "note": "insufficient_packages",
        }

    package_set = set(package_ids)
    config = load_router_config(scipkg_root)

    base_package_id = (
        selected_package_id
        if isinstance(selected_package_id, str) and selected_package_id in package_set
        else resolve_default_package_id(package_ids, active_package_id, config)
    )

    if not base_package_id:
        return {
            "package_id": selected_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": session_id,
            "consulted": False,
            "note": "no_base_package",
        }

    package_catalog = build_package_catalog(
        package_ids=package_ids,
        active_package_id=active_package_id,
        scipkg_root=scipkg_root,
    )
    preflight_prompt = build_second_guess_prompt(
        user_text=user_text,
        current_package_id=base_package_id,
        package_catalog=package_catalog,
    )

    runtime_policy = resolve_agent_runtime_policy()
    payload: dict[str, Any] = {
        "session_id": session_id,
        "user_prompt": preflight_prompt,
        "package_id": base_package_id,
        "provider": runtime_policy.provider,
    }
    if runtime_policy.sandbox_policy != "bypass":
        payload["sandbox"] = "read-only"
    if isinstance(user_id, str) and user_id.strip():
        payload["user_id"] = user_id

    resolved_session_id = session_id
    assistant_chunks: list[str] = []

    async def _collect() -> None:
        nonlocal resolved_session_id
        async for event_type, data in stream_runner(payload):
            if event_type == "meta":
                try:
                    meta = json.loads(data)
                except json.JSONDecodeError:
                    continue
                maybe_session = meta.get("session_id")
                if isinstance(maybe_session, str) and maybe_session:
                    resolved_session_id = maybe_session
                continue

            if event_type != "codex":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            item = event.get("item")
            if not isinstance(item, dict):
                item = {}
            item_type = item.get("type") or event.get("type") or ""
            if not isinstance(item_type, str):
                continue
            if not item_type.startswith("agent_message"):
                continue
            text = extract_text(event) or ""
            if text:
                assistant_chunks.append(text)

    try:
        timeout = package_second_guess_timeout_seconds
        if timeout > 0:
            await asyncio.wait_for(_collect(), timeout=timeout)
        else:
            await _collect()
    except asyncio.TimeoutError:
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": "second_guess_timeout",
        }
    except Exception as exc:
        logger.warning("Second-guess preflight failed: %s", exc)
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": f"second_guess_error:{exc}",
        }

    raw_text = "".join(assistant_chunks).strip()
    decision = extract_first_json_object(raw_text)
    if not isinstance(decision, dict):
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": "second_guess_invalid_json",
        }

    route_raw = decision.get("route")
    route = str(route_raw).strip().lower() if route_raw is not None else ""
    suggested_package = normalize_package_id_safe(decision.get("package_id"))
    confidence = coerce_confidence(decision.get("confidence"))
    reason_raw = decision.get("reason")
    reason = str(reason_raw).strip() if isinstance(reason_raw, str) else ""
    reason_short = reason[:240] if reason else ""

    if route not in {"keep", "switch"}:
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": "second_guess_invalid_route",
        }

    if route == "keep":
        note = (
            f"second_guess_keep(conf={confidence:.2f}, reason={reason_short})"
            if reason_short
            else f"second_guess_keep(conf={confidence:.2f})"
        )
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": note,
        }

    if suggested_package not in package_set:
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": "second_guess_invalid_target",
        }

    if suggested_package == base_package_id:
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": "second_guess_same_target",
        }

    if confidence < package_second_guess_min_confidence:
        return {
            "package_id": base_package_id,
            "source": selected_source,
            "switched": False,
            "session_id": resolved_session_id,
            "consulted": True,
            "note": f"second_guess_low_confidence({confidence:.2f})",
        }

    note = (
        f"second_guess_switch({base_package_id}->{suggested_package}, "
        f"conf={confidence:.2f}, reason={reason_short})"
        if reason_short
        else f"second_guess_switch({base_package_id}->{suggested_package}, conf={confidence:.2f})"
    )
    return {
        "package_id": suggested_package,
        "source": package_source_second_guess,
        "switched": True,
        "session_id": resolved_session_id,
        "consulted": True,
        "note": note,
    }


async def _handle_package_command(
    message: Any,
    *,
    cl_module: Any,
    parse_package_command: Callable[[str], tuple[str, list[str]] | None],
    resolve_package_registry: Callable[[], tuple[list[str], str | None, Path]],
    normalize_package_id_safe: Callable[[str | None], str | None],
    get_session_auto_route_flag: Callable[[], bool],
    format_package_list: Callable[[list[str], str | None, str | None], str],
    resolve_package_alias: Callable[[str, list[str]], str | None],
    session_package_id_key: str,
    session_package_source_key: str,
    session_package_auto_key: str,
    package_source_manual: str,
    package_source_none: str,
) -> bool:
    """Handle `/package` command messages."""

    parsed = parse_package_command(message.content)
    if parsed is None:
        return False
    action, args = parsed

    package_ids, active_package_id, _ = resolve_package_registry()
    package_set = set(package_ids)
    current_package_id = cl_module.user_session.get(session_package_id_key)
    if isinstance(current_package_id, str):
        current_package_id = normalize_package_id_safe(current_package_id)
    else:
        current_package_id = None
    if current_package_id not in package_set:
        current_package_id = None

    current_source = cl_module.user_session.get(session_package_source_key)
    if not isinstance(current_source, str) or not current_source:
        current_source = package_source_none
    auto_route = get_session_auto_route_flag()

    if action == "help":
        await cl_module.Message(
            content=(
                "**Package Commands**\n"
                "- `/package list`: list installed packages\n"
                "- `/package current`: show current package for this chat\n"
                "- `/package use <package_id>`: pin package for this chat\n"
                "- `/package auto on|off`: enable/disable auto routing\n"
                "- `/package clear`: clear manual pin/current selection"
            )
        ).send()
        return True

    if action == "list":
        await cl_module.Message(
            content=format_package_list(
                package_ids,
                active_package_id,
                current_package_id,
            )
        ).send()
        return True

    if action == "current":
        current_label = current_package_id or "none"
        await cl_module.Message(
            content=(
                f"Current package: `{current_label}`\n"
                f"Selection source: `{current_source}`\n"
                f"Auto routing: `{'on' if auto_route else 'off'}`"
            )
        ).send()
        return True

    if action == "auto":
        if not args:
            await cl_module.Message(
                content=f"Auto routing is currently `{'on' if auto_route else 'off'}`."
            ).send()
            return True

        option = args[0].strip().lower()
        if option in {"on", "true", "1", "yes"}:
            cl_module.user_session.set(session_package_auto_key, True)
            await cl_module.Message(
                content=(
                    "Auto routing enabled for this chat. "
                    "Manual `/package use ...` pin still takes precedence."
                )
            ).send()
            return True
        if option in {"off", "false", "0", "no"}:
            cl_module.user_session.set(session_package_auto_key, False)
            await cl_module.Message(content="Auto routing disabled for this chat.").send()
            return True
        await cl_module.Message(
            content="Usage: `/package auto on` or `/package auto off`"
        ).send()
        return True

    if action == "clear":
        cl_module.user_session.set(session_package_id_key, None)
        cl_module.user_session.set(session_package_source_key, package_source_none)
        await cl_module.Message(
            content=(
                "Cleared current package selection for this chat. "
                "Next request will use auto/default routing."
            )
        ).send()
        return True

    if action == "use":
        target_raw = " ".join(args).strip() if args else ""
        if not target_raw:
            await cl_module.Message(content="Usage: `/package use <package_id>`").send()
            return True
        resolved = resolve_package_alias(target_raw, package_ids)
        if not resolved:
            available = ", ".join(f"`{item}`" for item in package_ids) or "none"
            await cl_module.Message(
                content=(
                    f"Unknown package `{target_raw}`.\n"
                    f"Available packages: {available}"
                )
            ).send()
            return True
        cl_module.user_session.set(session_package_id_key, resolved)
        cl_module.user_session.set(session_package_source_key, package_source_manual)
        await cl_module.Message(
            content=f"Pinned package `{resolved}` for this chat session."
        ).send()
        return True

    await cl_module.Message(content="Unknown `/package` command. Use `/package help`.").send()
    return True
