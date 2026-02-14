from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChannelPackage:
    package_id: str
    zip_url: str
    title: str


DATA_DIR = Path(__file__).resolve().parent / "data" / "curated_channels"

CHANNEL_ALIASES = {
    "tel": "tel-research-group",
    "tel-research-group": "tel-research-group",
    "tle-research-group": "tel-research-group",
}


@functools.lru_cache(maxsize=1)
def _available_channel_ids() -> tuple[str, ...]:
    if not DATA_DIR.exists():
        return ("tel-research-group",)
    channel_ids = sorted(path.stem.strip().lower() for path in DATA_DIR.glob("*.json"))
    cleaned = tuple(channel_id for channel_id in channel_ids if channel_id)
    if cleaned:
        return cleaned
    return ("tel-research-group",)


@functools.lru_cache(maxsize=None)
def _load_channel_packages(channel_id: str) -> dict[str, ChannelPackage]:
    channel_path = DATA_DIR / f"{channel_id}.json"
    if not channel_path.is_file():
        raise ValueError(f"Missing curated channel file: {channel_path}")

    try:
        payload = json.loads(channel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid curated channel file: {channel_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Curated channel file must be a JSON object: {channel_path}")

    packages_raw = payload.get("packages")
    if not isinstance(packages_raw, list):
        raise ValueError(f"Curated channel file missing `packages` list: {channel_path}")

    packages: dict[str, ChannelPackage] = {}
    for index, item in enumerate(packages_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"Curated package at index {index} is not an object in {channel_path}"
            )
        package_id = str(item.get("package_id") or "").strip().lower()
        title = str(item.get("title") or "").strip()
        zip_url = str(item.get("zip_url") or "").strip()
        if not package_id or not title or not zip_url:
            raise ValueError(
                f"Curated package at index {index} missing package_id/title/zip_url in {channel_path}"
            )
        packages[package_id] = ChannelPackage(
            package_id=package_id,
            title=title,
            zip_url=zip_url,
        )
    return packages


def normalize_channel_id(channel: str | None) -> str:
    value = (channel or "tel-research-group").strip().lower()
    return CHANNEL_ALIASES.get(value, value)


def list_curated_packages(*, channel: str | None = None) -> dict[str, ChannelPackage]:
    normalized_channel = normalize_channel_id(channel)
    channels = _available_channel_ids()
    if normalized_channel not in channels:
        valid = ", ".join(sorted(channels))
        raise ValueError(f"Unknown channel '{normalized_channel}'. Available channels: {valid}")
    return _load_channel_packages(normalized_channel)


def resolve_curated_package(package_id: str, *, channel: str | None = None) -> ChannelPackage:
    packages = list_curated_packages(channel=channel)
    normalized_channel = normalize_channel_id(channel)
    package_key = package_id.strip().lower()

    payload = packages.get(package_key)
    if payload is None:
        supported = ", ".join(sorted(packages.keys()))
        raise ValueError(
            f"Package '{package_key}' is not published in channel '{normalized_channel}'. Supported packages: {supported}"
        )
    return payload
