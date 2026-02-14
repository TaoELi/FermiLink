from __future__ import annotations

from typing import Any


def _normalize_status_label(value: str | None) -> str | None:
    """Normalize streaming status text for UI display."""

    if not value:
        return None
    cleaned = str(value).strip()
    return cleaned or None


async def _maybe_update_status(
    status_msg: Any | None,
    status_label: str | None,
    last_status: str | None,
    *,
    normalize_status_label,
    cl_module: Any,
) -> tuple[Any | None, str | None]:
    """Create or update an ephemeral status message if label changed."""

    label = normalize_status_label(status_label)
    if not label or label == last_status:
        return status_msg, last_status
    if status_msg is None:
        status_msg = cl_module.Message(
            content=label,
            author="status",
            type="system_message",
        )
        await status_msg.send()
    else:
        status_msg.content = label
        await status_msg.update()
    return status_msg, label
