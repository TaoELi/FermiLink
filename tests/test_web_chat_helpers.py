from __future__ import annotations

from fermilink.web import chat_helpers


def test_extract_text_supports_claude_assistant_content_blocks() -> None:
    payload = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Hello "},
                {"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}},
                {"type": "text", "text": "world"},
            ]
        },
    }
    assert chat_helpers._extract_text(payload) == "Hello world"


def test_is_assistant_stream_event_supports_codex_and_claude() -> None:
    codex_payload = {
        "item": {"type": "agent_message_delta"},
        "delta": "done",
    }
    claude_payload = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "done"}]},
    }
    assert chat_helpers._is_assistant_stream_event(codex_payload) is True
    assert chat_helpers._is_assistant_stream_event(claude_payload) is True


def test_is_assistant_stream_event_ignores_non_assistant_types() -> None:
    assert chat_helpers._is_assistant_stream_event({"type": "user"}) is False
    assert chat_helpers._is_assistant_stream_event({"type": "result"}) is False
    assert chat_helpers._is_assistant_stream_event({"type": "stream_error"}) is False
