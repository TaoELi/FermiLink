"""Family-library loading and prompt rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class TransformFamily:
    family_id: str
    name: str
    description: str
    mechanism_tags: list[str]
    use_when: list[str]
    actions: list[str]
    avoid_when: list[str]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TransformFamily":
        return cls(
            family_id=str(payload.get("id") or "").strip(),
            name=str(payload.get("name") or "").strip(),
            description=str(payload.get("description") or "").strip(),
            mechanism_tags=[str(item).strip() for item in payload.get("mechanism_tags", []) if str(item).strip()],
            use_when=[str(item).strip() for item in payload.get("use_when", []) if str(item).strip()],
            actions=[str(item).strip() for item in payload.get("actions", []) if str(item).strip()],
            avoid_when=[str(item).strip() for item in payload.get("avoid_when", []) if str(item).strip()],
        )


def _families_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "families"


def load_family_library() -> list[TransformFamily]:
    families: list[TransformFamily] = []
    for path in sorted(_families_dir().glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = payload.get("families") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                family = TransformFamily.from_payload(item)
                if family.family_id and family.name:
                    families.append(family)
    return families


def render_family_catalog(families: list[TransformFamily]) -> str:
    if not families:
        return "- (no family library loaded)"
    lines: list[str] = []
    for family in families:
        lines.append(f"- `{family.family_id}`: {family.name}")
        if family.description:
            lines.append(f"  description: {family.description}")
        if family.use_when:
            lines.append(f"  use when: {', '.join(family.use_when[:4])}")
        if family.actions:
            lines.append(f"  action grammar: {', '.join(family.actions[:5])}")
    return "\n".join(lines)
