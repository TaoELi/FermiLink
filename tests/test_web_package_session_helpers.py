from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from fermilink.web import chat_helpers, package_session_helpers


def _base_second_guess_kwargs():
    return {
        "user_text": "route this request",
        "session_id": "session-1",
        "user_id": "user-1",
        "selected_package_id": "pkg-a",
        "selected_source": "auto",
        "package_second_guess_enabled": True,
        "package_source_manual": "manual",
        "package_source_second_guess": "second_guess",
        "package_second_guess_timeout_seconds": 10.0,
        "package_second_guess_min_confidence": 0.75,
        "resolve_package_registry": lambda: (["pkg-a", "pkg-b"], "pkg-a", Path("/tmp/scipkg")),
        "load_router_config": lambda _root: {},
        "resolve_default_package_id": (
            lambda package_ids, active_package_id, _config: active_package_id or package_ids[0]
        ),
        "build_package_catalog": (
            lambda package_ids, active_package_id, scipkg_root: [
                {"id": package_ids[0]},
                {"id": package_ids[1]},
            ]
        ),
        "build_second_guess_prompt": lambda **_kwargs: "second-guess prompt",
        "resolve_agent_runtime_policy": lambda: SimpleNamespace(
            provider="claude",
            sandbox_policy="enforce",
        ),
        "is_assistant_stream_event": chat_helpers._is_assistant_stream_event,
        "extract_text": chat_helpers._extract_text,
        "extract_first_json_object": lambda text: json.loads(text) if text else None,
        "normalize_package_id_safe": (
            lambda value: value if isinstance(value, str) and value.strip() else None
        ),
        "coerce_confidence": lambda value: float(value),
        "logger": logging.getLogger("test_web_package_session_helpers"),
    }


@pytest.mark.parametrize("event_type", ["agent", "codex"])
def test_run_package_second_guess_accepts_claude_assistant_events(
    event_type: str,
) -> None:
    captured_payload: dict[str, object] = {}

    async def fake_stream_runner(payload: dict[str, object]):
        captured_payload.update(payload)
        yield "meta", json.dumps({"session_id": "session-2"})
        yield event_type, json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"route":"keep","package_id":"pkg-a",'
                                '"confidence":0.91,"reason":"already good"}'
                            ),
                        }
                    ]
                },
            }
        )

    kwargs = _base_second_guess_kwargs()
    kwargs["stream_runner"] = fake_stream_runner
    result = asyncio.run(package_session_helpers._run_package_second_guess(**kwargs))

    assert captured_payload.get("provider") == "claude"
    assert result["package_id"] == "pkg-a"
    assert result["source"] == "auto"
    assert result["switched"] is False
    assert result["consulted"] is True
    assert result["session_id"] == "session-2"
    assert "second_guess_keep" in str(result["note"])


@pytest.mark.parametrize("event_type", ["agent", "codex"])
def test_run_package_second_guess_accepts_direct_json_decision_payload(
    event_type: str,
) -> None:
    async def fake_stream_runner(_payload: dict[str, object]):
        yield "meta", json.dumps({"session_id": "session-3"})
        yield event_type, json.dumps(
            {
                "route": "switch",
                "package_id": "pkg-b",
                "confidence": 0.95,
                "reason": "specialized model family",
            }
        )

    kwargs = _base_second_guess_kwargs()
    kwargs["stream_runner"] = fake_stream_runner
    result = asyncio.run(package_session_helpers._run_package_second_guess(**kwargs))

    assert result["package_id"] == "pkg-b"
    assert result["source"] == "second_guess"
    assert result["switched"] is True
    assert result["consulted"] is True
    assert result["session_id"] == "session-3"
    assert "second_guess_switch(pkg-a->pkg-b" in str(result["note"])
